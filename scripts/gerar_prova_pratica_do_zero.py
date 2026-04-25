"""
=============================
Geradores do Checkpoint — 3 (Prática)
=========================================================
openai==1.102.0 | python-dotenv
=============================
scripts/gerar_prova_pratica_do_zero.py
=============================

Como rodar (exemplos):

Prática (nível 1, TXT; domínios e ferramentas padrão; resumos via CLI):
python scripts/gerar_prova_pratica_do_zero.py \
  --nivel 1 \
  --carreira "Analista de Dados" \
  --resumos_arquivo output/checkpoints/resumos_analista_de_dados_nivel_1.json

Forçar modo de dados (opcional):
  --modo_dados com        # sempre com datasets (30-120)
  --modo_dados sem        # nunca criar datasets
  --modo_dados auto       # (padrão) decide automaticamente pelo contexto

Opcional:
  --domains_arquivo trilha/domains.json
  --ferramentas_arquivo trilha/ferramentas.json
  --verbose               # mostra tempos por fase

Exemplo de uso:
python scripts/gerar_prova_pratica_do_zero.py --nivel 1 --carreira "governanca_de_dados" --resumos_arquivo output/checkpoints/resumos_governanca_de_dados_nivel_all.json --modo_dados auto --verbose


Saída:
output/cursos_checkpoint/prova_pratica_<slug_carreira>_nivel_<n>.txt
"""

from __future__ import annotations
import argparse
import csv as _csv
import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from anthropic import Anthropic

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =========================
# Config de modelos
# =========================
# Geração da prova prática: top de linha (segue restrições rigorosas de escopo)
MODEL_GEN = "claude-opus-4-7"
TEMP = 0.0

# Alternativas:
# MODEL_GEN = "claude-sonnet-4-6"  # ~30% do custo
# MODEL_GEN = "gpt-5" / "gpt-4o-2024-08-06"
SINGLE_PASS_CHAR_LIMIT = 300_000

INPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "checkpoints"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "cursos_checkpoint"

# Schema novo (etapa 2): resumo por curso é {tema_central, conteudos_testaveis[], ferramentas_usadas[]}.

# Regras de datasets (para carreiras de dados)
MIN_ROWS = 30
MAX_ROWS = 120

# =========================
# Domínios padrão
# =========================
DOMAINS_DEFAULT = [
    "Clínica Médica Voll - Clínica especializada em serviços médicos e exames",
    "Bytebank - Banco digital que oferece serviços bancários online",
    "Buscante - Buscador e e-commerce de livros variados",
    "Playcatch - Plataforma de streaming de música, similar ao Spotify",
    "ADOPET - Site de adoção de animais de estimação",
    "Organo - Plataforma para criação e gestão de organogramas empresariais",
    "Screen Match - Plataforma de streaming de vídeos, similar ao YouTube",
    "Techsafe - Empresa especializada em segurança tecnológica e cibersegurança",
    "Cookin'UP - Aplicativo que compartilha receitas culinárias e dicas de cozinha",
    "Meteora - Loja online de roupas e acessórios",
    "Checklist - Plataforma de gestão de tarefas e checklists para equipes",
    "CodeChella - Organização de um festival de música com diversas atrações",
    "Serenatto - Café & Bistrô que oferece uma variedade de refeições e bebidas",
    "Hermex Log - Empresa de logística especializada em serviços de entrega",
    "Gatito Petshop - Loja que oferece produtos e serviços para animais de estimação",
    "Jornada Milhas - Plataforma para compras de passagens aéreas utilizando milhas",
    "Fokus - Aplicativo para aumentar a produtividade utilizando a técnica Pomodoro",
    "Meu Pequeno Grimorio - Loja especializada em livros de literatura fantástica e esotérica",
    "Luz & Cena - Cinema com exibição e sinopses de filmes em cartaz",
    "UseDev - E-commerce de produtos geeks",
    "Petpark - E-commerce + serviços para animais com agendamento",
    "CodeConnect - Rede social para programadores",
    "Zoop - Plataforma de e-commerce com estoque e pagamentos integrados",
    "Runner Circle - Rede social para corredores",
    "HomeHub - Painel de casa inteligente (IoT)",
    "Listin - Gerenciador de listas de supermercado",
    "SwiftBank - Banco digital com serviços financeiros",
    "Indexa - Gestor inteligente de contatos",
    "Cinetopia - Catálogo e recomendações de filmes",
    "Clickbonus - Clube de vantagens e recompensas",
    "Calmaria Spas - Marketplace de spas e bem-estar",
    "Jornada Viagens - Comparador e reserva de viagens",
    "VideoFlowNow - Vídeos curtos e lives (engajamento por IA)",
    "WaveCast - Publicação/monetização de podcasts",
    "Freelando - Marketplace de freelas",
    "TRATOTECH - Classificados de eletrônicos",
    "Dev.Spot - Portfólios/link tree para devs",
]

# =========================
# Utils
# =========================
def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "geral"

def _slug_or_default(carreira: str) -> str:
    return _slugify(carreira)

def _resumos_path_default(nivel: int, carreira: str) -> Path:
    slug = _slug_or_default(carreira)
    candidate = INPUT_DIR / f"resumos_{slug}_nivel_{nivel}.json"
    if candidate.exists():
        return candidate
    # fallback legado comum
    return INPUT_DIR / f"resumos_analista_de_dados_nivel_{nivel}.json"

def _load_resumos_via_cli(path: str, nivel: int, carreira: str) -> List[Dict[str, Any]]:
    if path:
        p = Path(path)
    else:
        p = _resumos_path_default(nivel, carreira)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo de resumos não encontrado: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def _resumos_compactos(resumos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Passa o resumo direto — etapa 2 já filtrou (só conteudos_testaveis)."""
    return [
        {
            "id": c.get("id"),
            "nome": c.get("nome"),
            "link": c.get("link"),
            "resumo": c.get("resumo", {}) or {},
        }
        for c in resumos
    ]


def _derivar_ferramentas_permitidas(resumos: List[Dict[str, Any]], ferramentas_cli: Optional[List[str]]) -> List[str]:
    """Consolida `ferramentas_usadas` de todos os cursos. A etapa 2 já filtrou para incluir
    APENAS ferramentas com uso real, então não há threshold/contagem aqui."""
    if isinstance(ferramentas_cli, list) and ferramentas_cli:
        return ferramentas_cli

    seen: Dict[str, str] = {}
    for c in resumos:
        for f in (c.get("resumo", {}) or {}).get("ferramentas_usadas", []) or []:
            s = str(f).strip()
            if s and s.lower() not in seen:
                seen[s.lower()] = s

    if not seen:
        # Fallback conservador: carreira sem ferramentas demonstradas
        return ["Planilhas (Excel/Google Sheets)", "Editor de texto (Word/Google Docs)", "Draw.io"]
    return sorted(seen.values())


def _perfil_carreira(resumos: List[Dict[str, Any]]) -> str:
    """Classifica a carreira por proporção de conteúdos procedimentais.
    >50% dos conteudos_testaveis com tipo 'procedimental' → programatica; senão → conceitual."""
    total = 0
    procedimentais = 0
    for c in resumos:
        for ct in (c.get("resumo", {}) or {}).get("conteudos_testaveis", []) or []:
            total += 1
            if str(ct.get("tipo", "")).strip().lower() == "procedimental":
                procedimentais += 1
    if total == 0:
        return "conceitual"
    return "programatica" if (procedimentais / total) > 0.5 else "conceitual"


def _carreira_envolve_dados(carreira: str, ferramentas: List[str], resumos: List[Dict[str, Any]]) -> bool:
    carreira_l = (carreira or "").lower()
    gatilhos_nome = ["dados", "data", "analytics", "cientist", "engenharia de dados", "bi", "etl"]
    if any(g in carreira_l for g in gatilhos_nome):
        return True
    ferr_l = [f.lower() for f in ferramentas]
    gatilhos_ferr = ["sql", "pandas", "spark", "hive", "power bi", "tableau", "dbt", "airflow"]
    if any(any(g in f for g in gatilhos_ferr) for f in ferr_l):
        return True
    # Inspeciona tópicos e habilidades dos conteudos_testaveis
    for c in resumos:
        for ct in (c.get("resumo") or {}).get("conteudos_testaveis", []) or []:
            joined = (str(ct.get("topico", "")) + " " + str(ct.get("habilidade", ""))).lower()
            if any(g in joined for g in ["sql", "dataset", "pandas", "etl", "pipeline", "visualização", "visualizacao", "bi"]):
                return True
    return False

# =========================
# Prompts (TXT direto)
# =========================
def system_prompt_aula3_txt() -> str:
    return """Você é uma pessoa especialista em desenho instrucional e avaliação prática baseada em competências.
Sua tarefa é gerar a **Aula 3 – “03.Prova prática”** de um curso de Checkpoint, utilizando **exclusivamente** as informações presentes nos **resumos dos cursos do nível** (entrada do usuário).

Regras gerais (mantenha TODAS):
- **Autoridade da lista de ferramentas (REGRA DURA)**: a lista "Ferramentas permitidas" recebida no user prompt é DEFINITIVA. Use APENAS ferramentas dessa lista. **NÃO extraia** ferramentas adicionais dos resumos. Os resumos foram pré-filtrados para você por profundidade pedagógica, mas mesmo assim podem conter itens classificados como "apenas_mencionado" — esses NÃO foram ensinados de verdade no curso e estão PROIBIDOS na prova. Se uma ferramenta não está na lista "Ferramentas permitidas", ela não foi ensinada e não pode aparecer.
- **Perfil da carreira (REGRA DURA)**: o user prompt informa o `perfil` da carreira como `programatica` ou `conceitual`.
  - Se **conceitual** (governança, papéis, processos, políticas): prefira **entregáveis documentais e diagramáticos** — planilhas, documentos, fluxogramas, organogramas, políticas escritas, glossários em planilha. Use linguagens de programação ou bibliotecas (Python, Pandas, etc.) APENAS quando a tarefa não puder ser representada de outra forma — e mesmo aí, mantenha o uso minimalista (1 etapa no máximo, com script curto).
  - Se **programática** (back-end, ciência de dados, ML): priorize código, scripts, configurações e datasets, com etapas técnicas mais densas.
- **Profundidade dos itens**: nos resumos, cada habilidade/conceito/exemplo vem com `profundidade` ∈ {`demonstrado`, `praticado`, `apenas_mencionado`}. Construa entregáveis APENAS sobre o que está como `demonstrado` ou `praticado`. Itens `apenas_mencionado` podem ser referenciados em texto, mas nunca pedidos como tarefa.
- **Cobertura do nível**: inclua ao longo das etapas ao menos **um item** que mobilize **cada curso** do nível (faça mapeamento ao final). NÃO UTILIZE FERRAMENTAS DE NUVEM (AWS, Amazon, Azure, GCP, Google Cloud Platform e qualquer serviço derivado destes grandes serviços) que geram custos para os alunos.
- **Não invente conteúdo**: não introduza ferramentas, conceitos ou técnicas que **não apareçam** nos resumos; apenas adapte e combine o que já foi visto.
- **Ferramentas e prática**: cada etapa deve **usar ao menos uma ferramenta da lista "Ferramentas permitidas"**. Se a lista contém apenas ferramentas não-programáticas (planilhas, documentos, diagramação), as etapas devem ser entregas documentais/diagramáticas — **não** force scripts Python ou consultas SQL.
- **Dados (APENAS quando aplicável)**:
  - **Somente gere datasets se a carreira envolver dados** (ex.: Análise/Engenharia de Dados, BI, Ciência de Dados). Caso contrário, **não gere datasets** e foque em artefatos coerentes (APIs, scripts, CLI, configs, deploy, testes, automações, documentação técnica).
  - Se gerar datasets, forneça **um ou mais arquivos** **in-line** (CSV ou JSON) com **entre 30 e 120 linhas de dados** (não conte o cabeçalho). Use dados **fictícios e não sensíveis**.
  - Quando houver datasets, inclua **dicionário de dados** curto em texto antes do bloco.
- **Domínio e imersão**: selecione **um único domínio** adequado ao projeto e **use o MESMO domínio em todas as etapas**; explique brevemente esse domínio no topo.
- **Pergunta-chave**: cada etapa termina com **uma pergunta-chave única**.
- **Linguagem**: neutra, sem masculino genérico; prefira “A empresa te contratou”, “A equipe que você integra”, etc.; **não** use “Você foi contratado”.
- **Escalonar dificuldade**: da 1ª para a 4ª etapa, **aumente a complexidade** e integre mais conceitos.
- **Dicas**: inclua **“Dicas de troubleshooting”** por etapa (sem resolver completamente).
- **Sem links externos**.

Formato de SAÍDA (TEXTO PURO) — siga exatamente:

# 03.Prova prática
**Domínio escolhido:** <nome do domínio> — <explicação breve do domínio>
**Ferramentas exigidas ao longo da aula:** <lista simples separada por vírgulas>

## Descrição do projeto
<parágrafos descrevendo o projeto, objetivos, entregáveis esperados, linguagem neutra>

## Antes de começar
## Dedicação
O tempo esperado para você investir no desenvolvimento do projeto é de [COLOCAR UMA ESTIMATIVA DE HORAS NO FORMATO: XX a XX] horas de dedicação. Se você estiver demorando muito mais que isso, pode ser que esteja indo longe demais.

## Dúvidas?
É normal surgirem dúvidas no meio da implementação. Peça ajuda! Utilize ferramentas de IA, Google e documentações das ferramentas, quanto do próprio [Fórum da Alura](https://cursos.alura.com.br/forum/todos/1?hasAccessMGM=true).

## Preparando o ambiente
<pré-requisitos, instalações, bibliotecas e instruções de uso (SQL/Pandas/BI/CLI/APIs/etc.)>
[Se a carreira envolver dados: listar datasets e dicionário(s) de dados; em seguida, incluir blocos de código com os arquivos:]
### <nome_do_arquivo_1.csv ou .json> — <breve descrição>
<dicionário de dados em lista curta>
```csv
<conteúdo CSV com entre 30 e 120 linhas de dados>
```
[se houver outro dataset, repetir o bloco; se a carreira NÃO envolver dados, não incluir blocos ```csv/```json]

## 1ª Etapa: <título curto da etapa>
<contexto e objetivo>
**Pergunta-chave:** "<pergunta única>"
**Sua missão:**
1. <passo>
2. <passo>
3. <passo>
**Ferramentas:** <lista curta>
---
**Dicas de troubleshooting para a 1ª etapa:**
* <dica>
* <dica>
* <dica>

## 2ª Etapa: <título curto da etapa>
... (mesmo padrão, aumentando a complexidade)

## 3ª Etapa: <título curto da etapa>
... (mesmo padrão)

## 4ª Etapa: <título curto da etapa>
... (mesmo padrão; integração de múltiplos conceitos)

## Matriz de cobertura (auditoria)
Liste cada curso do nível em uma linha, no formato:
- <nome do curso>: conceitos_alvo resumidos → etapas relacionadas (ex.: "1ª, 3ª")
"""

def user_prompt_aula3_txt(
    nivel_str: str,
    carreira_str: str,
    domains_list_formatada: str,
    ferramentas_permitidas: List[str],
    resumos_json_do_nivel: str,
    carreira_env_dados: bool,
    perfil_carreira: str = "conceitual",
) -> str:
    ferr = json.dumps(ferramentas_permitidas, ensure_ascii=False)
    return (
        f"Gere a **Aula 3 – Prova prática** seguindo integralmente as regras do sistema e retornando **apenas TEXTO** no formato exigido.\n\n"
        f"Contexto do nível:\n- Nível: {nivel_str}\n- Carreira: {carreira_str}\n- Perfil da carreira: {perfil_carreira.upper()}\n- Carreira envolve dados? {'SIM' if carreira_env_dados else 'NÃO'}\n\n"
        "Domínios disponíveis (escolha **um** e mantenha-o em todas as etapas):\n"
        + domains_list_formatada
        + "\n\nFerramentas permitidas (use apenas dentre estas):\n"
        + ferr
        + "\n\nResumos dos cursos (fonte única de verdade):\n```json\n"
        + resumos_json_do_nivel
        + "\n```\n\n"
        "Observações finais:\n"
        "IMPORTANTE: A carreira segue uma evolução em níveis (1, 2 e 3), onde o nível 1 é o mais básico, o 2 o intermediário e o 3 o nível mais avançado. Considere isso no momente de elaborar o projeto, trazendo um nível de complexidade condizente com o nível do aluno. Ou seja, o nível 3 precisa de projetos maiores e mais elaborados.\n"
        "- Se **envolver dados**, gere um ou mais datasets **in-line** (CSV/JSON) com 30–120 linhas (não sensível) em blocos ```csv/```json.\n"
        "- Se **NÃO envolver dados**, **não** gere datasets; foque em tarefas coerentes (APIs, scripts, configs, automações, testes, etc.).\n"
        "- Se o **perfil é CONCEITUAL**, prefira entregáveis documentais/diagramáticos (planilhas, organogramas, fluxogramas, glossários, políticas escritas). Use programação só se indispensável e em uma única etapa.\n"
        "- Escalone a dificuldade (1ª→4ª etapa) e inclua a Matriz de cobertura no final."
    )

# =========================
# OpenAI helper
# =========================
def _model_supports_temperature(model: str) -> bool:
    """Modelos que NÃO aceitam temperature customizada:
    OpenAI: gpt-5*, reasoning (o1, o3, o4).
    Anthropic: claude-opus-4-7+ (Sonnet 4.6 e Haiku 4.5 ainda aceitam)."""
    m = (model or "").lower()
    return not (
        m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")
        or m.startswith("claude-opus-4-7") or m.startswith("claude-opus-5")
    )


def _provider_for(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("claude") or m.startswith("anthropic"):
        return "anthropic"
    return "openai"


_openai_client: Optional[OpenAI] = None
_anthropic_client: Optional[Anthropic] = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        key = os.getenv("OPENAI_CREDENTIALS")
        if not key:
            raise RuntimeError("Defina OPENAI_CREDENTIALS no .env para usar modelos OpenAI.")
        _openai_client = OpenAI(api_key=key)
    return _openai_client


def _get_anthropic_client() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("Defina ANTHROPIC_API_KEY no .env para usar modelos Claude.")
        _anthropic_client = Anthropic(api_key=key)
    return _anthropic_client


USAGE_TOTALS: Dict[str, int] = {
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "input_tokens": 0,
    "output_tokens": 0,
}


def _accumulate_usage(usage: Dict[str, int]) -> None:
    for k in USAGE_TOTALS:
        USAGE_TOTALS[k] += int(usage.get(k, 0) or 0)


def _chat(client: Any, model: str, system: str, user: str) -> str:
    """Roteia OpenAI vs Anthropic. Para Anthropic usa prompt caching no system prompt
    (que é grande e não muda entre runs)."""
    if _provider_for(model) == "anthropic":
        kwargs_a: Dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if _model_supports_temperature(model):
            kwargs_a["temperature"] = TEMP
        resp = _get_anthropic_client().messages.create(**kwargs_a)
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        _accumulate_usage({
            "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        })
        return text
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if _model_supports_temperature(model):
        kwargs["temperature"] = TEMP
    resp = _get_openai_client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _print_usage_summary() -> None:
    if not any(USAGE_TOTALS.values()):
        return
    cache_read = USAGE_TOTALS["cache_read_input_tokens"]
    cache_create = USAGE_TOTALS["cache_creation_input_tokens"]
    inp = USAGE_TOTALS["input_tokens"]
    out = USAGE_TOTALS["output_tokens"]
    print()
    print("=" * 60)
    print("[Uso Anthropic — totais]")
    print(f"  Input (não cacheado):       {inp:>10,} tokens")
    print(f"  Cache create (escrita):     {cache_create:>10,} tokens")
    print(f"  Cache read (hit):           {cache_read:>10,} tokens (~90% off)")
    print(f"  Output:                     {out:>10,} tokens")
    print("=" * 60)

# =========================
# Ajuste LOCAL de datasets (CSV/JSON) no TXT
# =========================
def _looks_like_date(s: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s.strip()))

def _parse_csv(csv_text: str) -> Tuple[List[str], List[List[str]]]:
    buf = io.StringIO(csv_text.strip())
    reader = _csv.reader(buf)
    rows = list(reader)
    if not rows:
        return [], []
    header = rows[0]
    data = rows[1:] if len(rows) > 1 else []
    return header, data

def _to_csv(header: List[str], rows: List[List[str]]) -> str:
    out = io.StringIO()
    writer = _csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()

def _infer_col_types(header: List[str], rows: List[List[str]]) -> List[str]:
    types = []
    for col_i, _ in enumerate(header):
        col_vals = [r[col_i] for r in rows if col_i < len(r)]
        t = "text"
        if col_vals and all(re.fullmatch(r"-?\d+", (v or "").strip() or "0") for v in col_vals):
            t = "int"
        elif col_vals and all(re.fullmatch(r"-?\d+(\.\d+)?", (v or "").strip() or "0.0") for v in col_vals):
            t = "float"
        elif col_vals and all(_looks_like_date(v) for v in col_vals if (v or "").strip()):
            t = "date"
        else:
            uniq = set([(v or "").strip() for v in col_vals if (v or "").strip()])
            if 0 < len(uniq) <= 15:
                t = "cat"
        types.append(t)
    return types

def _col_ranges(types: List[str], rows: List[List[str]]) -> Dict[int, Tuple[Any, Any]]:
    ranges: Dict[int, Tuple[Any, Any]] = {}
    for i, t in enumerate(types):
        vals = []
        for r in rows:
            if i >= len(r):
                continue
            v = (r[i] or "").strip()
            if not v:
                continue
            if t == "int":
                vals.append(int(v))
            elif t == "float":
                vals.append(float(v))
            elif t == "date":
                try:
                    vals.append(datetime.strptime(v, "%Y-%m-%d"))
                except Exception:
                    pass
        if vals:
            ranges[i] = (min(vals), max(vals))
    return ranges

def _synthesize_value(t: str, rng: Tuple[Any, Any], cats: List[str], base_text: str, row_idx: int) -> str:
    if t == "int":
        lo, hi = rng if rng else (0, 100)
        if lo == hi: hi = lo + 10
        return str(random.randint(lo, hi))
    if t == "float":
        lo, hi = rng if rng else (0.0, 100.0)
        if lo == hi: hi = lo + 10.0
        return f"{random.uniform(lo, hi):.2f}"
    if t == "date":
        lo, hi = rng if rng else (datetime(2023,1,1), datetime(2023,3,1))
        span = (hi - lo).days or 30
        d = lo + timedelta(days=row_idx % (span+1))
        return d.strftime("%Y-%m-%d")
    if t == "cat" and cats:
        return random.choice(cats)
    base = base_text or "valor"
    return f"{base}_{row_idx}"

def _extend_or_trim_csv(csv_text: str) -> str:
    header, data = _parse_csv(csv_text)
    if not header:
        return csv_text.strip()
    data = [row for row in data if any((c or "").strip() for c in row)]
    if len(data) > MAX_ROWS:
        data = data[:MAX_ROWS]
        return _to_csv(header, data)
    if MIN_ROWS <= len(data) <= MAX_ROWS:
        return _to_csv(header, data)
    types = _infer_col_types(header, data) if data else ["text"] * len(header)
    ranges = _col_ranges(types, data) if data else {}
    cats_by_col: Dict[int, List[str]] = {}
    for i, t in enumerate(types):
        if t == "cat":
            cats_by_col[i] = list({(r[i].strip() if (i < len(r) and r[i]) else "") for r in data if (i < len(r) and r[i] and r[i].strip())})
    base_texts = [re.sub(r"\W+", "_", (h or "").lower()).strip("_") for h in header]
    if not data:
        for r_idx in range(MIN_ROWS):
            row = []
            for col_i, t in enumerate(types):
                row.append(_synthesize_value(t, ranges.get(col_i), cats_by_col.get(col_i, []), base_texts[col_i], r_idx))
            data.append(row)
        return _to_csv(header, data)
    seed_n = len(data)
    for r_idx in range(seed_n, MIN_ROWS):
        base = data[r_idx % seed_n]
        row = []
        for col_i, t in enumerate(types):
            base_val = base[col_i] if col_i < len(base) else ""
            rng = ranges.get(col_i)
            cats = cats_by_col.get(col_i, [])
            if t in ("int", "float", "date", "cat"):
                row.append(_synthesize_value(t, rng, cats, base_texts[col_i], r_idx))
            else:
                base_clean = (base_val or "").strip() or base_texts[col_i] or "valor"
                row.append(f"{base_clean}_{r_idx}")
        data.append(row)
    return _to_csv(header, data[:MAX_ROWS])

def _extend_or_trim_json_records(json_text: str) -> str:
    try:
        arr = json.loads(json_text)
        if not isinstance(arr, list):
            return json_text
        arr = [x for x in arr if isinstance(x, dict) and x]
        if len(arr) > MAX_ROWS:
            arr = arr[:MAX_ROWS]
            return json.dumps(arr, ensure_ascii=False)
        if MIN_ROWS <= len(arr) <= MAX_ROWS:
            return json.dumps(arr, ensure_ascii=False)
        if not arr:
            arr = [{"id": i+1, "valor": f"valor_{i+1}"} for i in range(MIN_ROWS)]
            return json.dumps(arr, ensure_ascii=False)
        keys = set()
        for o in arr:
            keys.update(o.keys())
        keys = list(keys)
        types: Dict[str, str] = {}
        for k in keys:
            vals = [o.get(k) for o in arr if k in o]
            t = "text"
            if vals and all(isinstance(v, int) for v in vals):
                t = "int"
            elif vals and all(isinstance(v, (int, float)) for v in vals):
                t = "float"
            elif vals and all(isinstance(v, str) and _looks_like_date(v) for v in vals):
                t = "date"
            elif vals and len({v for v in vals if v is not None}) <= 15:
                t = "cat"
            types[k] = t
        while len(arr) < MIN_ROWS:
            i = len(arr)
            new_obj: Dict[str, Any] = {}
            for k in keys:
                t = types.get(k, "text")
                if t == "int":
                    new_obj[k] = random.randint(0, 1000)
                elif t == "float":
                    new_obj[k] = round(random.uniform(0, 1000), 2)
                elif t == "date":
                    new_obj[k] = (datetime(2023, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
                elif t == "cat":
                    cats = list({o.get(k) for o in arr if k in o})
                    new_obj[k] = random.choice(cats) if cats else f"cat_{i%7}"
                else:
                    new_obj[k] = f"{k}_{i}"
            arr.append(new_obj)
        return json.dumps(arr[:MAX_ROWS], ensure_ascii=False)
    except Exception as e:
        print(f"[AVISO] _extend_or_trim_json_records: JSON inválido mantido como está ({type(e).__name__}: {e})")
        return json_text

def _fix_datasets_in_txt(txt: str) -> str:
    """
    Procura blocos ```csv ... ``` e ```json ... ``` e ajusta 30–120 linhas.
    Retorna o TXT com blocos substituídos quando aplicável.
    """
    def repl_csv(m):
        content = m.group(1)
        fixed = _extend_or_trim_csv(content)
        return "```csv\n" + fixed + "```"

    def repl_json(m):
        content = m.group(1)
        fixed = _extend_or_trim_json_records(content)
        return "```json\n" + fixed + "```"

    # Ajusta CSV
    txt = re.sub(r"```csv\s+([\s\S]*?)```", lambda m: repl_csv(m), txt, flags=re.IGNORECASE)
    # Ajusta JSON
    txt = re.sub(r"```json\s+([\s\S]*?)```", lambda m: repl_json(m), txt, flags=re.IGNORECASE)
    return txt

# =========================
# Barra de progresso simples
# =========================
def _render_bar(current: int, total: int, width: int = 28) -> str:
    if total <= 0:
        total = 1
    ratio = max(0.0, min(1.0, current / total))
    filled = int(ratio * width)
    bar = "█" * filled + " " * (width - filled)
    pct = int(ratio * 100)
    return f"|{bar}| {current}/{total} ({pct}%)"

def _progress(title: str, current: int, total: int):
    print(f"\r{title} {_render_bar(current, total)}", end="", flush=True)

def _progress_done():
    print()

# =========================
# Núcleo
# =========================
def gerar_aula3_txt(
    nivel: int,
    carreira: str,
    resumos_arquivo: str,
    domains: List[str],
    ferramentas_cli: Optional[List[str]],
    modo_dados: str = "auto",  # "auto" | "com" | "sem"
    verbose: bool = False,
) -> str:
    t_phase = {}
    t_start = time.perf_counter()

    load_dotenv()
    # Clients OpenAI/Anthropic são lazy — instanciados em _chat() conforme o MODEL.
    client = None

    # Fase 1 — Preparar insumos
    print("Fase 1/4: Preparando insumos...")
    t1 = time.perf_counter()
    resumos = _load_resumos_via_cli(resumos_arquivo, nivel, carreira)
    ferramentas = _derivar_ferramentas_permitidas(resumos, ferramentas_cli)
    resumos_json = json.dumps(_resumos_compactos(resumos), ensure_ascii=False)
    if len(resumos_json) > SINGLE_PASS_CHAR_LIMIT:
        resumos_json = resumos_json[:SINGLE_PASS_CHAR_LIMIT]
    if not domains:
        domains = DOMAINS_DEFAULT
    domains_list_formatada = "\n".join([f"- {d}" for d in domains])

    # Decide se envolve dados
    envolve_dados_auto = _carreira_envolve_dados(carreira, ferramentas, resumos)
    if modo_dados == "com":
        envolve_dados = True
    elif modo_dados == "sem":
        envolve_dados = False
    else:
        envolve_dados = envolve_dados_auto

    # Classifica perfil da carreira (programatica vs conceitual)
    perfil = _perfil_carreira(resumos)
    print(f"  → Perfil da carreira: {perfil} | envolve dados: {envolve_dados}")

    _progress("  → Insumos prontos", 1, 1)
    _progress_done()
    t_phase["prep"] = time.perf_counter() - t1

    # Fase 2 — Geração (1 chamada)
    print(f"Fase 2/4: Gerando texto da prova ({MODEL_GEN})...")
    t2 = time.perf_counter()
    _progress("  → Solicitando ao modelo", 0, 1)
    txt = _chat(
        client,
        MODEL_GEN,
        system_prompt_aula3_txt(),
        user_prompt_aula3_txt(
            nivel_str=f"Nível {nivel}",
            carreira_str=carreira or "",
            domains_list_formatada=domains_list_formatada,
            ferramentas_permitidas=ferramentas,
            resumos_json_do_nivel=resumos_json,
            carreira_env_dados=envolve_dados,
            perfil_carreira=perfil,
        ),
    )
    _progress("  → Solicitando ao modelo", 1, 1)
    _progress_done()
    t_phase["gen"] = time.perf_counter() - t2

    # Fase 3 — Pós-processo local (ajuste datasets 30–120, se houver)
    print("Fase 3/4: Ajustando datasets (se houver)...")
    t3 = time.perf_counter()
    # Contabiliza blocos antes para barra
    csv_blocks = len(re.findall(r"```csv\s+([\s\S]*?)```", txt, flags=re.IGNORECASE))
    json_blocks = len(re.findall(r"```json\s+([\s\S]*?)```", txt, flags=re.IGNORECASE))
    total_blocks = csv_blocks + json_blocks
    done_blocks = 0
    if total_blocks == 0:
        _progress("  → Nenhum dataset detectado", 1, 1)
        _progress_done()
    else:
        # processa CSV
        def repl_csv_progress(m):
            nonlocal done_blocks
            fixed = _extend_or_trim_csv(m.group(1))
            done_blocks += 1
            _progress("  → Ajustando datasets", done_blocks, total_blocks)
            return "```csv\n" + fixed + "```"
        txt = re.sub(r"```csv\s+([\s\S]*?)```", lambda m: repl_csv_progress(m), txt, flags=re.IGNORECASE)
        # processa JSON
        def repl_json_progress(m):
            nonlocal done_blocks
            fixed = _extend_or_trim_json_records(m.group(1))
            done_blocks += 1
            _progress("  → Ajustando datasets", done_blocks, total_blocks)
            return "```json\n" + fixed + "```"
        txt = re.sub(r"```json\s+([\s\S]*?)```", lambda m: repl_json_progress(m), txt, flags=re.IGNORECASE)
        if done_blocks < total_blocks:
            _progress("  → Ajustando datasets", total_blocks, total_blocks)
        _progress_done()
    t_phase["post"] = time.perf_counter() - t3

    # Fase 4 — Finalização
    print("Fase 4/4: Finalizando...")
    t_phase["total"] = time.perf_counter() - t_start
    if verbose:
        print(f"[Tempos] prep={t_phase['prep']:.1f}s | gen={t_phase['gen']:.1f}s | post={t_phase['post']:.1f}s | total={t_phase['total']:.1f}s")
    return txt

# =========================
# CLI
# =========================
def main():
    parser = argparse.ArgumentParser(description="Gerar Aula 3 (Prova prática) — TXT, datasets apenas quando fizer sentido para a carreira, ajuste local 30–120, progresso e tempo total.")
    parser.add_argument("--nivel", type=int, choices=[1,2,3], required=True)
    parser.add_argument("--carreira", type=str, default="")
    parser.add_argument("--resumos_arquivo", type=str, required=True, help="Caminho para JSON de resumos do nível (da carreira).")
    parser.add_argument("--domains_arquivo", type=str, default="")
    parser.add_argument("--ferramentas_arquivo", type=str, default="")
    parser.add_argument("--modo_dados", type=str, choices=["auto","com","sem"], default="auto", help="auto (padrão): detecta pelo contexto; com: força datasets; sem: nunca gera datasets.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Domínios
    if args.domains_arquivo:
        p = Path(args.domains_arquivo)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de domínios não encontrado: {p}")
        domains = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(domains, list):
            raise ValueError("O arquivo de domínios deve conter uma lista JSON de strings.")
    else:
        domains = DOMAINS_DEFAULT

    # Ferramentas permitidas
    if args.ferramentas_arquivo:
        p = Path(args.ferramentas_arquivo)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de ferramentas não encontrado: {p}")
        ferramentas_cli = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(ferramentas_cli, list):
            raise ValueError("O arquivo de ferramentas deve conter uma lista JSON de strings.")
    else:
        ferramentas_cli = None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    txt = gerar_aula3_txt(
        nivel=args.nivel,
        carreira=args.carreira,
        resumos_arquivo=args.resumos_arquivo,
        domains=domains,
        ferramentas_cli=ferramentas_cli,
        modo_dados=args.modo_dados,
        verbose=args.verbose,
    )
    elapsed = time.perf_counter() - t0

    carreira_slug = _slugify(args.carreira)
    base = f"prova_pratica_{carreira_slug}_nivel_{args.nivel}"
    out_path = OUTPUT_DIR / f"{base}.txt"
    out_path.write_text(txt, encoding="utf-8")
    print(f"[OK] TXT salvo em: {out_path}")

    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    print(f"[Tempo total] {mins} min {secs} s")
    _print_usage_summary()

if __name__ == "__main__":
    main()
