"""
=============================
Etapa 4.5 — Revisor + auto-correção da prova prática
=============================
scripts/revisar_prova_pratica.py

Depois que `gerar_prova_pratica_do_zero.py` produz o TXT, este script:
1. Fase A — análise estática (1 chamada Opus 4-6): viabilidade, progressão, cobertura,
   realismo profissional, setup, datasets, sintaxe/coerência, dicas por nível, meta-comentários.
2. Fase B — teste de resolvedor (1 chamada): "aluno tenta resolver mentalmente" e reporta travamentos.
3. Fase C — decisão de rota: se ≥3 seções travam por causa da mesma raiz → escape hatch (variante 3);
   senão auto-corrige seção a seção (variante 2).
4. Fase D — auto-correção: regenera a seção problemática mantendo a estrutura do TXT.
5. Salva backup `.pre_revisao.txt`, sobrescreve o TXT e gera relatório em markdown.

Uso:
    python scripts/revisar_prova_pratica.py --carreira "Engenharia de Dados" --nivel 1
"""
from __future__ import annotations
import argparse
import json
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

from gerar_prova_pratica_do_zero import (  # noqa: E402
    _chat,
    _accumulate_usage,
    _print_usage_summary,
    _slugify,
    _perfil_carreira,
    _carreira_envolve_dados,
    _derivar_ferramentas_permitidas,
    _resumos_compactos,
    OUTPUT_BASE,
    SINGLE_PASS_CHAR_LIMIT,
)
from upload_checkpoint_alura import _parse_prova_pratica, SECOES_PRATICA_ORDER  # noqa: E402


MODEL_REVISOR = "claude-opus-4-6"
TEMPERATURE_REVISOR = 0.0

# Se ≥ este número de seções tiver issues do mesmo tipo raiz, dispara escape hatch.
LIMIAR_SISTEMICO_SECOES = 3

CATEGORIAS_VALIDAS = {
    "viabilidade",
    "progressao_dificuldade",
    "cobertura_cursos",
    "realismo_profissional",
    "setup_incompleto",
    "dataset_sintaxe",
    "dataset_armadilha_nao_descrita",
    "meta_comentario",
    "dicas_insuficientes",
    "ferramenta_fora_da_lista",
    "outros",
}


def _safe_json_loads(s: str) -> Optional[Any]:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _parse_json_tolerante(raw: str) -> Optional[Dict[str, Any]]:
    """Tenta múltiplas estratégias para extrair um objeto JSON válido:
    1. Parse direto do texto todo.
    2. Remove crases de markdown (```json ... ```) se houver e tenta de novo.
    3. Busca a primeira `{` e faz balanceamento de chaves para achar o objeto completo,
       ignorando `{` e `}` dentro de strings. Retorna dict ou None."""
    if not raw:
        return None
    p = _safe_json_loads(raw.strip())
    if isinstance(p, dict):
        return p
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if m:
        p = _safe_json_loads(m.group(1))
        if isinstance(p, dict):
            return p
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for j in range(start, len(raw)):
        c = raw[j]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    p = _safe_json_loads(raw[start:j + 1])
                    if isinstance(p, dict):
                        return p
                    break
    return None


# =========================
# Prompts
# =========================

def system_prompt_revisor_estatico() -> str:
    return """Você é um revisor sênior de provas práticas educacionais para a plataforma Alura.

Sua tarefa: analisar UMA prova prática completa (formato "Aula 3 do Checkpoint") e reportar problemas em JSON estruturado.

Dimensões OBJETIVAS (auto-fixáveis):
- Setup em "Preparando o ambiente" completo? Bastam pra ambiente rodar?
- Datasets (CSV/JSON inline) sintaticamente válidos, 30-120 linhas, dicionário presente?
- Se dataset tem sujeira intencional (duplicatas, nulos, tipos mistos), o enunciado da etapa que trata isso DESCREVE explicitamente esse defeito? Dado estranho sem explicação = issue.
- Ausência de meta-comentários (frases como "a linguagem neutra é mantida", "conforme regras").
- Ferramentas usadas nas etapas estão TODAS na lista "Ferramentas exigidas" do topo.

Dimensões SEMÂNTICAS (podem exigir olhar humano):
- Viabilidade técnica: uma pessoa aluna do nível informado resolve em 8-18h com os cursos concluídos?
- Progressão de dificuldade da 1ª → 4ª etapa: sobe? Ou tudo no mesmo patamar?
- Cobertura dos cursos: a "Matriz de cobertura" no final bate com o que é pedido nas etapas?
- Realismo profissional: o cenário reflete o dia-a-dia REAL da profissão? Ou é um "trabalhinho acadêmico"?
- Dicas de troubleshooting: cada etapa tem o mínimo necessário para o nível? Nível 1 pede mais dicas explícitas; Nível 3 tolera menos.

REGRAS DE SAÍDA (invioláveis):
1. Sua resposta INTEIRA deve ser UM ÚNICO objeto JSON válido. NADA fora do JSON — nem texto explicativo, nem crases de markdown, nem "```json".
2. Primeiro caractere: `{`. Último caractere: `}`.
3. Se a prova estiver íntegra, retorne exatamente: {"issues": []}

Schema:
{
  "issues": [
    {
      "secao": "<nome da seção afetada, ex: '1ª Etapa: Modelagem'; use 'geral' se afeta o TXT todo>",
      "tipo": "<slug curto>",
      "categoria": "<uma de: viabilidade, progressao_dificuldade, cobertura_cursos, realismo_profissional, setup_incompleto, dataset_sintaxe, dataset_armadilha_nao_descrita, meta_comentario, dicas_insuficientes, ferramenta_fora_da_lista, outros>",
      "severidade": "<baixa|media|alta>",
      "descricao": "<1 frase objetiva>",
      "auto_fix_possivel": <true|false>,
      "sugestao": "<instrução acionável>"
    }
  ]
}
"""


def user_prompt_revisor_estatico(txt_prova: str, resumos_json: str, ferramentas: List[str], nivel: int, carreira: str, perfil: str, envolve_dados: bool) -> str:
    return f"""Analise a prova prática abaixo. Contexto:
- Carreira: {carreira}
- Nível: {nivel}
- Perfil da carreira: {perfil}
- Envolve dados: {'sim' if envolve_dados else 'não'}
- Ferramentas permitidas na prova (fonte da verdade): {json.dumps(ferramentas, ensure_ascii=False)}

PROVA PRÁTICA:
```
{txt_prova}
```

RESUMOS DOS CURSOS DO NÍVEL (fonte para checar cobertura e realismo):
```json
{resumos_json}
```

Retorne SOMENTE o JSON no formato especificado.
"""


def system_prompt_resolvedor() -> str:
    return """Você é uma pessoa aluna que acabou de concluir todos os cursos de um nível de carreira da Alura. Você recebeu uma prova prática (Aula 3 do Checkpoint) e vai tentar resolvê-la MENTALMENTE, etapa por etapa.

Sua saída deve ser um relatório honesto do que aconteceria se você tentasse fazer o projeto de verdade:
- Em que ponto você TRAVA? (falta dica, setup impossível, comando que você não conhece, dado sem explicação)
- Onde falta explicação? Qual dica ajudaria?
- Alguma etapa exige ferramenta que não foi ensinada nos cursos?
- Alguma decisão de negócio parece sem contexto suficiente?

Não tente esconder dificuldades. Você é uma pessoa aluna real, não um especialista. Se tudo estiver claro, diga.

REGRAS DE SAÍDA (invioláveis):
1. Sua resposta INTEIRA deve ser UM ÚNICO objeto JSON válido. NADA fora do JSON.
2. Primeiro caractere: `{`. Último caractere: `}`.
3. Se você resolveria tudo tranquilamente, retorne exatamente: {"travamentos": []}

Schema:
{
  "travamentos": [
    {
      "secao": "<nome da seção>",
      "descricao": "<onde travei e por quê>",
      "sugestao": "<que dica/instrução faltou>"
    }
  ]
}
"""


def user_prompt_resolvedor(txt_prova: str, cursos_nomes: List[str], nivel: int, carreira: str) -> str:
    return f"""Você acabou de concluir os cursos abaixo (nível {nivel} da carreira {carreira}):
{json.dumps(cursos_nomes, ensure_ascii=False, indent=2)}

Agora tente resolver esta prova prática mentalmente, etapa por etapa. Reporte SÓ o que impediria uma pessoa aluna real de terminar.

PROVA PRÁTICA:
```
{txt_prova}
```

Retorne SOMENTE o JSON no formato pedido.
"""


# =========================
# Auto-correção de uma seção
# =========================

SYSTEM_PROMPT_CORRETOR_SECAO = """Você é um editor sênior de provas práticas educacionais. Recebe UMA seção de uma prova com problemas identificados e devolve a seção REESCRITA corrigindo APENAS os problemas apontados, mantendo:
- Estrutura da seção (contexto → pergunta-chave → sua missão → ferramentas → dicas de troubleshooting).
- O mesmo cenário geral (empresa fictícia, tema).
- Ferramentas dentro da lista permitida.

Formato de saída: markdown limpo da seção, SEM o header `##` (o header original será preservado). Não inclua texto explicativo antes ou depois — apenas o conteúdo da seção.

Regras invioláveis:
- Linguagem neutra literal ("pessoa desenvolvedora", "A empresa te contratou"). Nunca masculino genérico ou "Você foi contratado".
- Zero meta-comentários. As regras devem estar APLICADAS, nunca citadas.
- Só use ferramentas da lista permitida.
- Se a seção tem datasets, mantenha os blocos ```csv/```json exatamente onde estavam a menos que o problema seja neles.
"""


def corrigir_secao(header: str, conteudo: str, issues: List[Dict[str, Any]], ferramentas: List[str], resumos_json: str) -> Optional[str]:
    issues_texto = "\n".join(
        f"- [{i.get('categoria','?')} / {i.get('severidade','?')}] {i.get('descricao','')}"
        f"\n  Sugestão: {i.get('sugestao','')}"
        for i in issues
    )
    user_prompt = f"""SEÇÃO ATUAL (header: `## {header}`):

```
{conteudo}
```

PROBLEMAS IDENTIFICADOS:
{issues_texto}

Ferramentas permitidas (não invente novas):
{json.dumps(ferramentas, ensure_ascii=False)}

RESUMOS DOS CURSOS (aderência):
```json
{resumos_json[:80000]}
```

Reescreva SOMENTE o conteúdo desta seção. Não inclua o header `## ...` — ele será preservado externamente.
"""
    try:
        novo = _chat(
            None,
            MODEL_REVISOR,
            SYSTEM_PROMPT_CORRETOR_SECAO,
            "",
            user_prompt,
            temperature=TEMPERATURE_REVISOR,
        )
    except Exception as e:
        print(f"  [ERRO] Correção da seção '{header}' falhou: {type(e).__name__}: {e}")
        return None
    return novo.strip()


# =========================
# Substituição de seção no TXT
# =========================

def substituir_secao_no_txt(txt: str, titulo_completo: str, novo_conteudo: str) -> str:
    """Encontra o header `## <titulo_completo>` e substitui o conteúdo entre ele e o próximo header
    top-level (ou "Matriz de cobertura"). Preserva o header original."""
    # Regex para achar o header exato
    padrao_header = re.compile(rf"(^##\s+{re.escape(titulo_completo)}\s*$)", re.MULTILINE)
    m = padrao_header.search(txt)
    if not m:
        print(f"  [AVISO] Header '## {titulo_completo}' não encontrado — substituição pulada.")
        return txt

    header_end = m.end()
    # Próximo header top-level ou Matriz
    resto = txt[header_end:]
    proximos = list(re.finditer(r"^##\s+(.+?)\s*$", resto, re.MULTILINE))
    corte = len(resto)
    for h in proximos:
        titulo_prox = h.group(1).strip()
        if titulo_prox.startswith("Matriz de cobertura") or any(titulo_prox.startswith(p) for p in SECOES_PRATICA_ORDER):
            corte = h.start()
            break

    # Monta novo texto: prefixo + header + \n + novo_conteudo + \n + sufixo
    prefixo = txt[:header_end]
    sufixo = resto[corte:]
    novo = prefixo + "\n" + novo_conteudo.strip() + "\n\n" + sufixo
    return novo


# =========================
# Relatório
# =========================

def gerar_relatorio(
    analise_estatica: Dict[str, Any],
    resultado_resolvedor: Dict[str, Any],
    correcoes: Dict[str, str],  # titulo -> "sucesso" | "falha" | "nao_aplicavel"
    padrao_sistemico: Optional[str],
    rerun_disparado: bool,
) -> str:
    issues = analise_estatica.get("issues", []) or []
    travamentos = resultado_resolvedor.get("travamentos", []) or []

    linhas: List[str] = []
    linhas.append("# Relatório de revisão — prova prática\n")
    linhas.append(f"- Issues (análise estática): **{len(issues)}**")
    linhas.append(f"- Travamentos (teste de resolvedor): **{len(travamentos)}**")
    linhas.append(f"- Seções auto-corrigidas: **{sum(1 for v in correcoes.values() if v == 'sucesso')}**")
    linhas.append(f"- Falhas de correção: **{sum(1 for v in correcoes.values() if v == 'falha')}**")
    if padrao_sistemico:
        linhas.append(f"- **Padrão sistêmico detectado:** `{padrao_sistemico}`")
        linhas.append(f"  → Escape hatch: {'acionado' if rerun_disparado else 'não acionado'}")
    linhas.append("")

    if issues:
        linhas.append("## Análise estática\n")
        for issue in issues:
            secao = issue.get("secao", "geral")
            cat = issue.get("categoria", "?")
            sev = issue.get("severidade", "?")
            desc = issue.get("descricao", "")
            sug = issue.get("sugestao", "")
            fix = issue.get("auto_fix_possivel", False)
            linhas.append(f"### [{secao}] {cat} ({sev})")
            linhas.append(f"- {desc}")
            if sug:
                linhas.append(f"- Sugestão: {sug}")
            linhas.append(f"- Auto-fix: {'sim' if fix else '**NÃO** — revisão humana'}")
            linhas.append("")

    if travamentos:
        linhas.append("## Teste de resolvedor — onde o aluno trava\n")
        for t in travamentos:
            linhas.append(f"### {t.get('secao', 'geral')}")
            linhas.append(f"- {t.get('descricao', '')}")
            sug = t.get("sugestao", "")
            if sug:
                linhas.append(f"- Sugestão: {sug}")
            linhas.append("")

    if not issues and not travamentos:
        linhas.append("## Nada apontado. A prova está íntegra segundo o revisor.\n")

    return "\n".join(linhas)


# =========================
# Escape hatch — rerun da etapa 4
# =========================

REFORCOS_POR_CATEGORIA_PRATICA = {
    "viabilidade": "REFORÇO: para o nível informado, o projeto DEVE ser resolúvel em 8-18h com o que foi ensinado. Não peça técnicas ou ferramentas que os cursos não cobriram.",
    "progressao_dificuldade": "REFORÇO: as 4 etapas DEVEM ter dificuldade crescente. Etapa 1 = uma habilidade isolada; Etapa 4 = integração de múltiplos conceitos.",
    "cobertura_cursos": "REFORÇO: cada curso do nível DEVE ter pelo menos uma habilidade central mobilizada nas etapas. A matriz de cobertura no final DEVE bater com o que é pedido.",
    "realismo_profissional": "REFORÇO CRÍTICO: o cenário DEVE refletir o dia-a-dia real da profissão (entregas em produção, integrações, decisões de arquitetura), não exercícios acadêmicos com aparência profissional.",
    "setup_incompleto": "REFORÇO: 'Preparando o ambiente' DEVE listar TODOS os pacotes, versões e passos para o aluno subir o ambiente e terminar a prova sem procurar em outra fonte.",
    "dataset_sintaxe": "REFORÇO: datasets inline DEVEM ser sintaticamente válidos, ter entre 30 e 120 linhas de dados, e vir com dicionário claro antes do bloco.",
    "dataset_armadilha_nao_descrita": "REFORÇO CRÍTICO: se algum dataset traz sujeira intencional (duplicatas, nulos, tipos mistos, outliers), o enunciado da etapa que trata isso DEVE mencionar explicitamente esse defeito. Nunca traga dado 'estranho' sem explicação.",
    "meta_comentario": "REFORÇO: PROIBIDO frases sobre o próprio texto ('a linguagem neutra é mantida', 'seguindo as regras...'). As diretrizes ficam APLICADAS, nunca citadas.",
    "dicas_insuficientes": "REFORÇO: cada etapa DEVE ter dicas de troubleshooting suficientes para o nível — nível 1 pede mais dicas explícitas; nível 3 tolera menos hand-holding.",
    "ferramenta_fora_da_lista": "REFORÇO: use APENAS ferramentas da lista 'Ferramentas permitidas'. Nada de introduzir bibliotecas ou serviços fora dela.",
    "outros": "REFORÇO: revise as regras gerais do system prompt (viabilidade, realismo, dicas por nível, aderência aos resumos).",
}


def gerar_reforco_para_padrao(categoria: str, secoes_afetadas: List[str]) -> str:
    txt = REFORCOS_POR_CATEGORIA_PRATICA.get(categoria, REFORCOS_POR_CATEGORIA_PRATICA["outros"])
    return (
        f"# Reforço automático — {categoria} (seções afetadas: {', '.join(secoes_afetadas)})\n\n"
        f"{txt}"
    )


def rerodar_etapa_4(
    projeto_dir: Path,
    carreira: str,
    nivel: int,
    resumos_arquivo: str,
    modo_dados: str,
    usar_batch: bool,
    reforco_texto: str,
) -> bool:
    reforco_path = projeto_dir / "_reforco_pratica.txt"
    reforco_path.write_text(reforco_texto, encoding="utf-8")
    print(f"[Escape hatch] Reforço gravado em {reforco_path.name}. Re-rodando etapa 4...")

    cmd = [
        sys.executable,
        str(_SCRIPT_DIR / "gerar_prova_pratica_do_zero.py"),
        "--carreira", carreira,
        "--nivel", str(nivel),
        "--resumos_arquivo", resumos_arquivo,
        "--modo_dados", modo_dados,
        "--reforco_extra", str(reforco_path),
    ]
    if usar_batch:
        cmd.append("--batch")

    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[Escape hatch] Rerun falhou: {e}")
        return False


# =========================
# CLI
# =========================

def main():
    parser = argparse.ArgumentParser(description="Etapa 4.5 — Revisor + auto-correção da prova prática.")
    parser.add_argument("--carreira", type=str, required=True)
    parser.add_argument("--nivel", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--resumos_arquivo", type=str, default="")
    parser.add_argument("--modo_dados", type=str, choices=["auto", "com", "sem"], default="auto")
    parser.add_argument("--batch", action="store_true", help="Passar --batch para o rerun (se disparar).")
    parser.add_argument("--pular-revisao", action="store_true")
    parser.add_argument("--escape-hatch", action="store_true", help="Habilita rerun automático (variante 3). DESLIGADO POR PADRÃO — sem esta flag, só reporta padrão sistêmico e para.")
    parser.add_argument("--nested", action="store_true")
    args = parser.parse_args()

    load_dotenv()

    slug = _slugify(args.carreira)
    projeto_dir = OUTPUT_BASE / f"{slug}_nivel_{args.nivel}"
    txt_path = projeto_dir / "prova_pratica.txt"
    if not txt_path.exists():
        raise SystemExit(f"TXT da prova prática não encontrado: {txt_path}")
    if args.pular_revisao:
        print("[Revisor] --pular-revisao passado; nada a fazer.")
        return

    resumos_arquivo = args.resumos_arquivo or str(projeto_dir / "resumos.json")
    if not Path(resumos_arquivo).exists():
        raise SystemExit(f"Resumos não encontrados: {resumos_arquivo}")

    resumos = json.loads(Path(resumos_arquivo).read_text(encoding="utf-8"))
    ferramentas = _derivar_ferramentas_permitidas(resumos, None)
    perfil = _perfil_carreira(resumos)
    if args.modo_dados == "com":
        envolve_dados = True
    elif args.modo_dados == "sem":
        envolve_dados = False
    else:
        envolve_dados = _carreira_envolve_dados(args.carreira, ferramentas, resumos)

    resumos_json = json.dumps(_resumos_compactos(resumos), ensure_ascii=False)
    if len(resumos_json) > SINGLE_PASS_CHAR_LIMIT:
        resumos_json = resumos_json[:SINGLE_PASS_CHAR_LIMIT]

    txt = txt_path.read_text(encoding="utf-8")
    secoes = _parse_prova_pratica(txt)
    print(f"[Revisor] Seções detectadas: {len(secoes)}")

    t0 = time.perf_counter()
    cursos_nomes = [c.get("nome", "") for c in resumos]

    # Fase A — análise estática
    print("[Fase A] Análise estática...")
    raw_a = _chat(
        None,
        MODEL_REVISOR,
        system_prompt_revisor_estatico(),
        "",
        user_prompt_revisor_estatico(txt, resumos_json, ferramentas, args.nivel, args.carreira, perfil, envolve_dados),
        temperature=TEMPERATURE_REVISOR,
    )
    analise = _parse_json_tolerante(raw_a) or {"issues": []}
    if not isinstance(analise, dict):
        analise = {"issues": []}
    analise.setdefault("issues", [])
    for i in analise["issues"]:
        if i.get("categoria") not in CATEGORIAS_VALIDAS:
            i["categoria"] = "outros"
    print(f"  Issues encontradas: {len(analise['issues'])}")

    # Fase B — teste de resolvedor
    print("[Fase B] Teste de resolvedor...")
    raw_b = _chat(
        None,
        MODEL_REVISOR,
        system_prompt_resolvedor(),
        "",
        user_prompt_resolvedor(txt, cursos_nomes, args.nivel, args.carreira),
        temperature=TEMPERATURE_REVISOR,
    )
    resolvedor = _parse_json_tolerante(raw_b) or {"travamentos": []}
    if not isinstance(resolvedor, dict):
        resolvedor = {"travamentos": []}
    resolvedor.setdefault("travamentos", [])
    print(f"  Travamentos apontados: {len(resolvedor['travamentos'])}")

    # Fase C — decisão de rota
    # Agrega issues + travamentos por categoria para detectar padrão sistêmico
    contagem_cat: Dict[str, set] = {}
    for issue in analise["issues"]:
        cat = issue.get("categoria", "outros")
        sec = issue.get("secao", "geral")
        contagem_cat.setdefault(cat, set()).add(sec)
    for t in resolvedor["travamentos"]:
        # Travamentos que exigem "dicas_insuficientes" e "setup_incompleto"
        cat = "dicas_insuficientes"
        sec = t.get("secao", "geral")
        contagem_cat.setdefault(cat, set()).add(sec)

    padrao_sistemico: Optional[str] = None
    secoes_do_padrao: List[str] = []
    if contagem_cat:
        cat_top, secs_top = max(contagem_cat.items(), key=lambda kv: len(kv[1]))
        if len(secs_top) >= LIMIAR_SISTEMICO_SECOES:
            padrao_sistemico = cat_top
            secoes_do_padrao = sorted(secs_top)

    rerun_disparado = False
    correcoes: Dict[str, str] = {}

    if padrao_sistemico and not args.nested:
        print(f"[Revisor] Padrão sistêmico: '{padrao_sistemico}' em {len(secoes_do_padrao)} seções ≥ limiar.")
        if not args.escape_hatch:
            print(f"[Revisor] --escape-hatch NÃO passado; NÃO vou disparar rerun automático. Padrão sistêmico será reportado no relatório. Para forçar rerun, rode novamente com --escape-hatch.")
            padrao_sistemico = None  # Neutraliza — mas registra no relatório final
        else:
            reforco = gerar_reforco_para_padrao(padrao_sistemico, secoes_do_padrao)
            backup_path = projeto_dir / "prova_pratica.pre_revisao.txt"
            backup_path.write_text(txt, encoding="utf-8")
            ok = rerodar_etapa_4(
                projeto_dir=projeto_dir,
                carreira=args.carreira,
                nivel=args.nivel,
                resumos_arquivo=resumos_arquivo,
                modo_dados=args.modo_dados,
                usar_batch=args.batch,
                reforco_texto=reforco,
            )
            rerun_disparado = ok
            if ok:
                # Chama a si mesmo em --nested para revisar o novo TXT sem novo escape hatch
                nested_cmd = [
                    sys.executable, str(_SCRIPT_DIR / "revisar_prova_pratica.py"),
                    "--carreira", args.carreira,
                    "--nivel", str(args.nivel),
                    "--resumos_arquivo", resumos_arquivo,
                    "--modo_dados", args.modo_dados,
                    "--nested",
                ]
                if args.batch:
                    nested_cmd.append("--batch")
                print("[Revisor] Rerun OK. Revisando o TXT novo em modo --nested...")
                try:
                    subprocess.run(nested_cmd, check=True)
                except subprocess.CalledProcessError as e:
                    print(f"[Revisor] Revisão nested falhou: {e}")
                return
            else:
                print("[Revisor] Rerun falhou — caindo em modo relatório manual.")

    # Fase D — auto-correção por seção
    # Agrupa issues por seção
    issues_por_secao: Dict[str, List[Dict[str, Any]]] = {}
    for issue in analise["issues"]:
        sec = issue.get("secao", "geral")
        if not issue.get("auto_fix_possivel"):
            continue
        issues_por_secao.setdefault(sec, []).append(issue)

    if issues_por_secao:
        secoes_dict = {titulo: conteudo for titulo, conteudo in secoes}
        for sec_titulo, issues_da_sec in issues_por_secao.items():
            # Match por prefixo (ex: "1ª Etapa" pode casar com "1ª Etapa: Modelagem")
            match = None
            for tit in secoes_dict:
                if tit == sec_titulo or tit.startswith(sec_titulo):
                    match = tit
                    break
            if not match:
                correcoes[sec_titulo] = "falha"
                continue
            conteudo = secoes_dict[match]
            print(f"[Revisor] Corrigindo seção '{match}'...")
            novo = corrigir_secao(match, conteudo, issues_da_sec, ferramentas, resumos_json)
            if novo:
                txt = substituir_secao_no_txt(txt, match, novo)
                correcoes[match] = "sucesso"
            else:
                correcoes[match] = "falha"

    # Escreve backup + TXT corrigido
    if any(v == "sucesso" for v in correcoes.values()):
        backup_path = projeto_dir / "prova_pratica.pre_revisao.txt"
        if not backup_path.exists():
            backup_path.write_text(txt_path.read_text(encoding="utf-8"), encoding="utf-8")
        txt_path.write_text(txt, encoding="utf-8")
        print(f"[Revisor] TXT sobrescrito. Backup em {backup_path.name}")
    else:
        print("[Revisor] Nada auto-corrigido — TXT preservado.")

    relatorio = gerar_relatorio(analise, resolvedor, correcoes, padrao_sistemico, rerun_disparado)
    rel_path = projeto_dir / "prova_pratica_relatorio.md"
    rel_path.write_text(relatorio, encoding="utf-8")
    print(f"[Revisor] Relatório salvo em {rel_path.name}")

    elapsed = time.perf_counter() - t0
    print(f"[Revisor] Tempo total: {int(elapsed // 60)}min {int(elapsed % 60)}s")
    _print_usage_summary()


if __name__ == "__main__":
    main()
