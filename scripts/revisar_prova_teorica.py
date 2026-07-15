"""
=============================
Etapa 3.5 — Revisor + auto-correção da prova teórica
=============================
scripts/revisar_prova_teorica.py

Depois que `gerar_prova_teorica_do_zero.py` produz o TXT, este script:
1. Analisa cada exercício em batch (Opus 4-8) contra dimensões objetivas + semânticas.
2. Se ≥50% dos exercícios têm issues da mesma categoria → escape hatch (variante 3):
   gera um reforço específico e re-roda `gerar_prova_teorica_do_zero.py` com --reforco_extra.
3. Senão → auto-corrige exercício a exercício (variante 2).
4. Salva backup `.pre_revisao.txt`, sobrescreve o TXT e gera relatório em markdown.

Reusa utilidades de `gerar_prova_teorica_do_zero.py` e `upload_checkpoint_alura.py`.

Uso:
    python scripts/revisar_prova_teorica.py --carreira "Engenharia de Dados" --nivel 1
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

# Reuso das utilidades dos scripts vizinhos
from gerar_prova_teorica_do_zero import (  # noqa: E402
    _chat,
    _anthropic_messages_batch,
    _accumulate_usage,
    _print_usage_summary,
    _get_anthropic_client,
    _slugify,
    _safe_json_loads,
    OUTPUT_BASE,
)
from upload_checkpoint_alura import _parse_prova_teorica  # noqa: E402


MODEL_REVISOR = "claude-opus-4-8"
TEMPERATURE_REVISOR = 0.0

# Limite mínimo (proporção do total) para considerar um padrão como "sistêmico".
LIMIAR_SISTEMICO = 0.5

# Categorias possíveis retornadas pelo revisor. Usadas para agrupar issues e detectar padrão sistêmico.
CATEGORIAS_VALIDAS = {
    "tamanho_enunciado",
    "tamanho_alternativa",
    "tamanho_justificativa",
    "linguagem_nao_neutra",
    "meta_comentario",
    "contexto_pobre",
    "alternativa_correta_incorreta",
    "alternativa_incorreta_trivial",
    "fora_do_resumo",
    "outros",
}


# =========================
# Prompts do revisor
# =========================

def system_prompt_revisor() -> str:
    return """Você é um revisor sênior de provas educacionais de múltipla escolha para a plataforma Alura.

Sua tarefa: analisar UM exercício de múltipla escolha e reportar problemas em formato JSON estruturado.

Dimensões OBJETIVAS (mecânicas — fáceis de auto-corrigir):
- Enunciado (contexto + pergunta norteadora) deve ter entre 120 e 180 palavras. Muito curto = perde contexto; muito longo = cansa.
- Cada alternativa deve ter ≤ 45 palavras.
- Cada justificativa deve ter ≤ 30 palavras.
- Linguagem neutra literal: "pessoa desenvolvedora", "A empresa te contratou". PROIBIDOS: "Você foi contratado", masculino genérico ("o desenvolvedor", "o usuário").
- Ausência de meta-comentários: o texto NÃO pode conter frases sobre o próprio texto (ex: "A linguagem neutra é mantida...", "Conforme as regras..."). As diretrizes ficam aplicadas, nunca citadas.

Dimensões SEMÂNTICAS (podem exigir olhar humano):
- A alternativa marcada como correta REALMENTE é correta? Ou o gabarito está errado?
- As alternativas incorretas são plausíveis? Uma pessoa desatenta poderia cair? Ou são trivialmente erradas?
- O contexto (empresa fictícia) é rico e concreto? Ou é vago/genérico?
- O exercício adere ao resumo do curso citado? Ou inventa conceitos que não foram ensinados?
- O nível da carreira está adequado? Nível 1 = mais explícito, contexto rico; Nível 3 = expectativa profissional, denso.

Formato de saída (JSON estrito, sem markdown, sem comentários):
{
  "exercicio_n": <número do exercício>,
  "issues": [
    {
      "tipo": "<slug curto>",
      "categoria": "<uma de: tamanho_enunciado, tamanho_alternativa, tamanho_justificativa, linguagem_nao_neutra, meta_comentario, contexto_pobre, alternativa_correta_incorreta, alternativa_incorreta_trivial, fora_do_resumo, outros>",
      "severidade": "<baixa|media|alta>",
      "descricao": "<1 frase objetiva>",
      "auto_fix_possivel": <true|false>,
      "sugestao": "<instrução acionável para corrigir; se auto_fix_possivel=false, apontar para revisão humana>"
    }
  ]
}

Se o exercício estiver íntegro, retorne {"exercicio_n": N, "issues": []}.
"""


def user_prompt_revisor(exercicio_bloco: str, resumo_curso: str, nivel: int, exercicio_n: int) -> str:
    return f"""Analise o exercício abaixo. Nível da carreira: {nivel}.

EXERCÍCIO {exercicio_n}:
```
{exercicio_bloco}
```

RESUMO DO CURSO CITADO (fonte da verdade para checar aderência):
```json
{resumo_curso}
```

Retorne SOMENTE o JSON no formato especificado. Nada mais.
"""


# =========================
# Parse do TXT em blocos
# =========================

SEPARADOR_BLOCOS = "\n-------------------------------------------------------------------\n"

def _split_blocos(txt: str) -> List[str]:
    """Split conservador: aceita 20+ hífens como separador (mesmo do parser do uploader)."""
    partes = re.split(r"\n-{20,}\s*\n", txt)
    return [p.strip() for p in partes if p.strip()]


def _extrair_header(bloco: str) -> Dict[str, Any]:
    """Extrai N, nome do curso e dificuldade do header 'EXERCÍCIO N (curso: X) [dificuldade: N/5]'."""
    m = re.match(r"EXERCÍCIO\s+(\d+)\s*\(curso:\s*(.+?)\)\s*(?:\[dificuldade:\s*(\d+)/5\])?", bloco)
    if not m:
        return {"n": None, "curso": "", "dificuldade": None}
    return {
        "n": int(m.group(1)),
        "curso": m.group(2).strip(),
        "dificuldade": int(m.group(3)) if m.group(3) else None,
    }


# =========================
# Análise via LLM
# =========================

def _resumo_by_nome_curso(resumos: List[Dict[str, Any]], nome: str) -> str:
    """Busca no arquivo de resumos o curso pelo nome (fuzzy). Retorna JSON compacto do resumo."""
    for c in resumos:
        if c.get("nome", "").strip().lower() == nome.strip().lower():
            return json.dumps(c, ensure_ascii=False, indent=2)
    # Fallback: match parcial
    for c in resumos:
        if nome.strip().lower() in c.get("nome", "").strip().lower():
            return json.dumps(c, ensure_ascii=False, indent=2)
    return json.dumps({"aviso": f"resumo do curso '{nome}' não encontrado"}, ensure_ascii=False)


def analisar_em_batch(
    blocos: List[str],
    resumos: List[Dict[str, Any]],
    nivel: int,
) -> List[Dict[str, Any]]:
    """Submete todos os exercícios em 1 batch Anthropic. Retorna lista de análises {exercicio_n, issues[]}."""
    items: List[Tuple[str, str, str, str]] = []
    for i, bloco in enumerate(blocos):
        header = _extrair_header(bloco)
        n = header["n"] or (i + 1)
        curso = header["curso"]
        resumo_json = _resumo_by_nome_curso(resumos, curso)
        items.append((
            f"rev_{i}",
            system_prompt_revisor(),
            "",  # user_static vazio
            user_prompt_revisor(bloco, resumo_json, nivel, n),
        ))

    print(f"[Fase A] Analisando {len(items)} exercícios em batch...")
    respostas = _anthropic_messages_batch(
        model=MODEL_REVISOR,
        items=items,
        temperature=TEMPERATURE_REVISOR,
        max_tokens=4096,
    )

    analises: List[Dict[str, Any]] = []
    for i in range(len(blocos)):
        raw = respostas.get(f"rev_{i}", "")
        parsed = _safe_json_loads(raw)
        if not parsed or not isinstance(parsed, dict):
            # tenta extrair JSON do meio do texto
            m = re.search(r"\{[\s\S]*\}", raw)
            parsed = _safe_json_loads(m.group(0)) if m else None
        if not parsed or not isinstance(parsed, dict):
            print(f"  [AVISO] Análise do exercício {i+1} não retornou JSON válido — marcando sem issues.")
            parsed = {"exercicio_n": i + 1, "issues": []}
        parsed.setdefault("exercicio_n", i + 1)
        parsed.setdefault("issues", [])
        # Sanitiza categorias desconhecidas
        for issue in parsed["issues"]:
            if issue.get("categoria") not in CATEGORIAS_VALIDAS:
                issue["categoria"] = "outros"
        analises.append(parsed)
    return analises


# =========================
# Detecção de padrão sistêmico + geração de reforço
# =========================

def detectar_padrao_sistemico(analises: List[Dict[str, Any]]) -> Optional[str]:
    """Retorna a categoria dominante se ≥LIMIAR_SISTEMICO dos exercícios tiverem issue dessa categoria.
    Senão None."""
    total = len(analises)
    if total == 0:
        return None
    contagem: Dict[str, int] = {}
    for a in analises:
        cats = {issue.get("categoria", "outros") for issue in a.get("issues", [])}
        for c in cats:
            contagem[c] = contagem.get(c, 0) + 1
    if not contagem:
        return None
    cat_top, count_top = max(contagem.items(), key=lambda kv: kv[1])
    if count_top / total >= LIMIAR_SISTEMICO:
        return cat_top
    return None


REFORCOS_POR_CATEGORIA = {
    "tamanho_enunciado": (
        "REFORÇO CRÍTICO: os enunciados anteriores ficaram com 30-50 palavras — MUITO abaixo do exigido. "
        "Cada enunciado (contexto + pergunta norteadora) DEVE ter entre 120 e 180 palavras. "
        "Contexto DEVE cobrir: (a) apresentação da empresa e área, (b) situação técnica concreta que ela enfrenta, "
        "(c) restrições ou dores, (d) a decisão que a pessoa profissional precisa tomar. "
        "Enunciados abaixo de 120 palavras são REJEITADOS. NÃO seja conciso."
    ),
    "tamanho_alternativa": (
        "REFORÇO: alternativas devem ter entre 15 e 45 palavras — nem tão curtas que fiquem triviais, "
        "nem longas que se destaquem. Todas as 4 alternativas de UMA questão devem ter tamanho similar."
    ),
    "tamanho_justificativa": (
        "REFORÇO: cada justificativa é UMA frase única, ≤30 palavras, explicando por que a alternativa "
        "é correta ou incorreta sem revelar a resposta em outras justificativas."
    ),
    "linguagem_nao_neutra": (
        "REFORÇO: linguagem neutra é OBRIGATÓRIA e LITERAL: 'pessoa desenvolvedora', 'a equipe que você integra', "
        "'A empresa te contratou'. PROIBIDOS: 'o desenvolvedor', 'o usuário', 'você foi contratado'."
    ),
    "meta_comentario": (
        "REFORÇO: PROIBIDO escrever observações sobre o próprio texto (ex: 'A linguagem neutra é mantida...', "
        "'Conforme as regras...'). As diretrizes devem estar APLICADAS silenciosamente."
    ),
    "contexto_pobre": (
        "REFORÇO: cada contexto de exercício DEVE mencionar concretamente: sistema envolvido, dado/artefato "
        "em jogo, restrição operacional. Evite genéricos como 'a empresa X está pensando em usar Y'. Traga "
        "a dor real que a pessoa profissional resolveria."
    ),
    "alternativa_correta_incorreta": (
        "REFORÇO: verifique DUAS VEZES o gabarito. A alternativa marcada como 'Correta' deve ser factualmente "
        "correta segundo o resumo do curso. Justificativas 'Correta' e 'Incorreta' devem bater com a realidade técnica."
    ),
    "alternativa_incorreta_trivial": (
        "REFORÇO: alternativas incorretas devem ser plausíveis — armadilhas reais que uma pessoa em treinamento cometeria. "
        "Nada de erros grosseiros óbvios. Boas distratoras usam conceitos parecidos mas com aplicação errada."
    ),
    "fora_do_resumo": (
        "REFORÇO: use APENAS conceitos, ferramentas e técnicas presentes nos resumos dos cursos. Nada de "
        "inventar cenários com tecnologias que não foram ensinadas."
    ),
    "outros": (
        "REFORÇO: revise cuidadosamente cada exercício para que respeite as regras gerais do prompt "
        "(tamanho, linguagem neutra, ausência de meta-comentários, fidelidade aos resumos)."
    ),
}


def gerar_reforco_para_padrao(categoria: str, contagem: int, total: int) -> str:
    txt = REFORCOS_POR_CATEGORIA.get(categoria, REFORCOS_POR_CATEGORIA["outros"])
    return (
        f"# Reforço automático — {categoria} ({contagem}/{total} exercícios afetados)\n\n"
        f"{txt}"
    )


# =========================
# Auto-correção de 1 bloco
# =========================

SYSTEM_PROMPT_CORRETOR = """Você é um editor sênior de questões de múltipla escolha. Recebe um exercício com problemas identificados e devolve o exercício REESCRITO corrigindo APENAS os problemas apontados, mantendo:
- O mesmo domínio (empresa fictícia) e cenário geral
- O mesmo conceito abordado (o que a questão testa)
- A mesma alternativa correta (o gabarito não muda)
- 4 alternativas (A, B, C, D) com justificativas

Formato de saída (LITERAL, sem markdown, sem crases, sem comentários fora do texto):
Título: <título curto>
Pergunta: <contexto rico entre 120 e 180 palavras>

<pergunta norteadora, 1 frase única ≤30 palavras>
A) <alternativa, ≤45 palavras>
Justificativa: <Correta|Incorreta>, <razão em 1 frase ≤30 palavras>
B) <...>
Justificativa: <...>
C) <...>
Justificativa: <...>
D) <...>
Justificativa: <...>

Regras invioláveis:
- Linguagem neutra literal ("pessoa desenvolvedora", "A empresa te contratou"). Nunca masculino genérico ou "Você foi contratado".
- Zero meta-comentários. As regras devem estar APLICADAS, nunca citadas.
- Fidelidade ao resumo do curso (nada inventado).
"""


def _reconstruir_bloco(header_original: str, corpo_corrigido: str) -> str:
    """Concatena header + corpo corrigido preservando o header original (N, curso, dificuldade)."""
    corpo = corpo_corrigido.strip()
    return f"{header_original}\n{corpo}"


def corrigir_exercicio(bloco_original: str, issues: List[Dict[str, Any]], resumo_curso: str) -> Optional[str]:
    """Chama LLM corretor com feedback específico. Retorna novo bloco (com header) ou None se falhar."""
    linhas = bloco_original.split("\n", 1)
    header = linhas[0]
    corpo = linhas[1] if len(linhas) > 1 else ""

    issues_texto = "\n".join(
        f"- [{i.get('categoria','?')} / {i.get('severidade','?')}] {i.get('descricao','')}"
        f"\n  Sugestão: {i.get('sugestao','')}"
        for i in issues
    )

    user_prompt = f"""EXERCÍCIO ATUAL (corpo, sem o header):
```
{corpo}
```

PROBLEMAS IDENTIFICADOS:
{issues_texto}

RESUMO DO CURSO (não invente nada fora daqui):
```json
{resumo_curso}
```

Reescreva o exercício no formato exigido. Não altere a alternativa correta.
"""

    try:
        novo_corpo = _chat(
            None,
            MODEL_REVISOR,
            SYSTEM_PROMPT_CORRETOR,
            "",
            user_prompt,
            temperature=TEMPERATURE_REVISOR,
        )
    except Exception as e:
        print(f"  [ERRO] Correção falhou: {type(e).__name__}: {e}")
        return None

    novo_bloco = _reconstruir_bloco(header, novo_corpo)
    # Sanity check: o novo bloco tem A) B) C) D)?
    if not all(f"\n{letra})" in novo_bloco or f"\n{letra}) " in novo_bloco for letra in ["A", "B", "C", "D"]):
        print(f"  [AVISO] Correção retornou bloco sem 4 alternativas — mantendo original.")
        return None
    return novo_bloco


# =========================
# Escape hatch — rerun automático da etapa 3
# =========================

def rerodar_etapa_3(
    projeto_dir: Path,
    carreira: str,
    nivel: int,
    resumos_arquivo: str,
    max_questoes: int,
    min_por_curso: int,
    max_por_curso: int,
    domains_window: int,
    reforco_texto: str,
) -> bool:
    """Grava reforço em arquivo e chama gerar_prova_teorica_do_zero.py --reforco_extra ..."""
    reforco_path = projeto_dir / "_reforco_teorica.txt"
    reforco_path.write_text(reforco_texto, encoding="utf-8")
    print(f"[Escape hatch] Reforço gravado em {reforco_path.name}. Re-rodando etapa 3...")

    cmd = [
        sys.executable,
        str(_SCRIPT_DIR / "gerar_prova_teorica_do_zero.py"),
        "--carreira", carreira,
        "--nivel", str(nivel),
        "--resumos_arquivo", resumos_arquivo,
        "--max_questoes", str(max_questoes),
        "--min_por_curso", str(min_por_curso),
        "--max_por_curso", str(max_por_curso),
        "--domains_window", str(domains_window),
        "--reforco_extra", str(reforco_path),
    ]
    try:
        proc = subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[Escape hatch] Rerun falhou: {e}")
        return False
    return True


# =========================
# Relatório
# =========================

def gerar_relatorio(
    analises: List[Dict[str, Any]],
    correcoes: Dict[int, str],  # idx (0-based) -> "sucesso" | "falha" | "nao_aplicavel"
    padrao_sistemico: Optional[str],
    rerun_disparado: bool,
) -> str:
    total = len(analises)
    total_com_issues = sum(1 for a in analises if a["issues"])
    total_corrigidos = sum(1 for v in correcoes.values() if v == "sucesso")
    total_falha_corr = sum(1 for v in correcoes.values() if v == "falha")

    linhas: List[str] = []
    linhas.append("# Relatório de revisão — prova teórica\n")
    linhas.append(f"- Total de exercícios: **{total}**")
    linhas.append(f"- Com pelo menos 1 issue: **{total_com_issues}**")
    linhas.append(f"- Auto-corrigidos: **{total_corrigidos}**")
    linhas.append(f"- Correção falhou (revisão humana): **{total_falha_corr}**")
    if padrao_sistemico:
        linhas.append(f"- **Padrão sistêmico detectado:** `{padrao_sistemico}`")
        if rerun_disparado:
            linhas.append(f"  → Escape hatch acionado: rerun automático da etapa 3 com reforço.")
        else:
            linhas.append(f"  → Escape hatch NÃO acionado (rerun já rodou uma vez; agora aponto para revisão humana).")
    linhas.append("")

    if total_com_issues:
        linhas.append("## Exercícios com issues\n")
        for a in analises:
            if not a["issues"]:
                continue
            n = a["exercicio_n"]
            status = correcoes.get(n - 1, "nao_aplicavel")
            emoji = {"sucesso": "✅", "falha": "⚠️", "nao_aplicavel": "ℹ️"}.get(status, "•")
            linhas.append(f"### Exercício {n} — {emoji} {status}")
            for issue in a["issues"]:
                categoria = issue.get("categoria", "?")
                sev = issue.get("severidade", "?")
                desc = issue.get("descricao", "")
                sug = issue.get("sugestao", "")
                fixa = issue.get("auto_fix_possivel", False)
                linhas.append(f"- [{categoria} / {sev}] {desc}")
                if sug:
                    linhas.append(f"  - Sugestão: {sug}")
                linhas.append(f"  - Auto-fix possível: {'sim' if fixa else '**NÃO** — revisão humana'}")
            linhas.append("")
    else:
        linhas.append("## Nenhum issue detectado.\n")

    return "\n".join(linhas)


# =========================
# CLI
# =========================

def main():
    parser = argparse.ArgumentParser(description="Etapa 3.5 — Revisor + auto-correção da prova teórica.")
    parser.add_argument("--carreira", type=str, required=True, help="Nome oficial da carreira (usado para derivar o slug).")
    parser.add_argument("--nivel", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--resumos_arquivo", type=str, default="", help="Override do resumos.json; se omitido, deriva do projeto.")
    parser.add_argument("--max_questoes", type=int, default=20, help="Passado ao rerun se escape hatch disparar.")
    parser.add_argument("--min_por_curso", type=int, default=1)
    parser.add_argument("--max_por_curso", type=int, default=3)
    parser.add_argument("--domains_window", type=int, default=3)
    parser.add_argument("--pular-revisao", action="store_true", help="Opt-out global: só copia backup e sai.")
    parser.add_argument("--nested", action="store_true", help="(Interno) Marca este run como nested — bloqueia novo escape hatch.")
    args = parser.parse_args()

    load_dotenv()

    slug = _slugify(args.carreira)
    projeto_dir = OUTPUT_BASE / f"{slug}_nivel_{args.nivel}"
    txt_path = projeto_dir / "prova_teorica.txt"
    if not txt_path.exists():
        raise SystemExit(f"TXT da prova teórica não encontrado: {txt_path}")

    if args.pular_revisao:
        print(f"[Revisor] --pular-revisao passado; nada a fazer.")
        return

    resumos_arquivo = args.resumos_arquivo or str(projeto_dir / "resumos.json")
    if not Path(resumos_arquivo).exists():
        raise SystemExit(f"Resumos não encontrados: {resumos_arquivo}")

    resumos = json.loads(Path(resumos_arquivo).read_text(encoding="utf-8"))
    if not isinstance(resumos, list):
        raise SystemExit("Formato de resumos inesperado (esperava lista).")

    txt = txt_path.read_text(encoding="utf-8")
    blocos = _split_blocos(txt)
    print(f"[Revisor] Exercícios detectados: {len(blocos)}")

    t0 = time.perf_counter()

    # Fase A — análise em batch
    analises = analisar_em_batch(blocos, resumos, args.nivel)

    # Fase B — decisão de rota
    total_issues = sum(len(a["issues"]) for a in analises)
    print(f"[Revisor] Issues encontradas: {total_issues}")
    padrao = detectar_padrao_sistemico(analises)
    rerun_disparado = False

    if padrao and not args.nested:
        contagem = sum(1 for a in analises if any(i.get("categoria") == padrao for i in a["issues"]))
        print(f"[Revisor] Padrão sistêmico: '{padrao}' em {contagem}/{len(analises)} exercícios ≥ limiar.")
        reforco = gerar_reforco_para_padrao(padrao, contagem, len(analises))
        # Backup antes do rerun
        backup_path = projeto_dir / "prova_teorica.pre_revisao.txt"
        backup_path.write_text(txt, encoding="utf-8")
        # Dispara rerun (que reescreve prova_teorica.txt)
        ok = rerodar_etapa_3(
            projeto_dir=projeto_dir,
            carreira=args.carreira,
            nivel=args.nivel,
            resumos_arquivo=resumos_arquivo,
            max_questoes=args.max_questoes,
            min_por_curso=args.min_por_curso,
            max_por_curso=args.max_por_curso,
            domains_window=args.domains_window,
            reforco_texto=reforco,
        )
        rerun_disparado = ok
        if ok:
            # Chama a si mesmo com --nested (para revisar o novo TXT sem outro escape hatch)
            nested_cmd = [
                sys.executable, str(_SCRIPT_DIR / "revisar_prova_teorica.py"),
                "--carreira", args.carreira,
                "--nivel", str(args.nivel),
                "--resumos_arquivo", resumos_arquivo,
                "--max_questoes", str(args.max_questoes),
                "--min_por_curso", str(args.min_por_curso),
                "--max_por_curso", str(args.max_por_curso),
                "--domains_window", str(args.domains_window),
                "--nested",
            ]
            print("[Revisor] Rerun OK. Revisando o TXT novo em modo --nested...")
            try:
                subprocess.run(nested_cmd, check=True)
            except subprocess.CalledProcessError as e:
                print(f"[Revisor] Revisão nested falhou: {e}")
            return
        else:
            print("[Revisor] Rerun falhou — caindo em modo relatório manual.")

    # Fase C — auto-correção individual
    correcoes: Dict[int, str] = {}
    blocos_novos = list(blocos)
    for i, a in enumerate(analises):
        if not a["issues"]:
            continue
        issues_auto = [it for it in a["issues"] if it.get("auto_fix_possivel")]
        if not issues_auto:
            correcoes[i] = "falha"
            continue
        header = _extrair_header(blocos[i])
        curso = header["curso"]
        resumo_json = _resumo_by_nome_curso(resumos, curso)
        print(f"[Revisor] Corrigindo exercício {i+1}...")
        novo = corrigir_exercicio(blocos[i], issues_auto, resumo_json)
        if novo:
            blocos_novos[i] = novo
            correcoes[i] = "sucesso"
        else:
            correcoes[i] = "falha"

    # Escreve backup + TXT corrigido
    if any(v == "sucesso" for v in correcoes.values()):
        backup_path = projeto_dir / "prova_teorica.pre_revisao.txt"
        if not backup_path.exists():
            backup_path.write_text(txt, encoding="utf-8")
        sep = SEPARADOR_BLOCOS + "\n"
        novo_txt = sep.join(blocos_novos) + "\n"
        txt_path.write_text(novo_txt, encoding="utf-8")
        print(f"[Revisor] TXT sobrescrito. Backup em {backup_path.name}")
    else:
        print("[Revisor] Nada foi auto-corrigido — TXT preservado.")

    # Relatório
    relatorio = gerar_relatorio(analises, correcoes, padrao, rerun_disparado)
    rel_path = projeto_dir / "prova_teorica_relatorio.md"
    rel_path.write_text(relatorio, encoding="utf-8")
    print(f"[Revisor] Relatório salvo em {rel_path.name}")

    elapsed = time.perf_counter() - t0
    print(f"[Revisor] Tempo total: {int(elapsed // 60)}min {int(elapsed % 60)}s")
    _print_usage_summary()


if __name__ == "__main__":
    main()
