"""
Resumo por vídeo + junção consolidada (simples, rápido e fiel)
------------------------------------------------------------
- openai==1.102.0, python-dotenv
- Lê: ./trilha/analise_de_dados_nivel_{1,2,3}.json
- Gera: ./output/checkpoints/resumos_analise_de_dados_nivel_{1,2,3}.json (+ .jsonl)
- Usa **UMA chamada de LLM por vídeo** (sem chunking na maioria dos casos) e **merge local** em Python
- Sem gerar exercícios. Apenas resumos estruturados e fiéis.

Como usar:
1) pip install openai==1.102.0 python-dotenv
2) .env na raiz: OPENAI_CREDENTIALS="sua_api_key"
3) python scripts/checkpoint_criar_resumos_cursos.py
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from openai import OpenAI
from anthropic import Anthropic

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =========================
# Configuração
# =========================
TEMPERATURE = 0.0
MODEL = "gpt-5"
# Se um vídeo passar desse limite, aplicamos fallback de chunking com overlap leve
SINGLE_PASS_CHAR_LIMIT = 300000
CHUNK_SIZE = 180000
CHUNK_OVERLAP = 8000
MAX_WORKERS = 4  # paralelismo por curso (ajuste se houver rate limit)

# Alternativas (descomente para baratear):
# MODEL = "gpt-4o"
# MODEL = "gpt-4o-mini"; SINGLE_PASS_CHAR_LIMIT = 14000; CHUNK_SIZE = 10000; CHUNK_OVERLAP = 1000; MAX_WORKERS = 6

INPUT_DIR = Path(__file__).resolve().parent.parent / "trilha"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "checkpoints"
INPUT_FILES = [
    # "analise_de_dados_nivel_1.json",
    # "analise_de_dados_nivel_2.json",
    # "analise_de_dados_nivel_3.json",
    # "cientista_de_dados_nivel_1.json",
    # "cientista_de_dados_nivel_2.json",
    # "cientista_de_dados_nivel_3.json",
    # "desenvolvimento_back_end_java_nivel_1.json",
    # "desenvolvimento_back_end_java_nivel_2.json",
    # "desenvolvimento_back_end_java_nivel_3.json",
    # "site_reliability_engineering_nivel_1.json",
    # "site_reliability_engineering_nivel_2.json",
    # "site_reliability_engineering_nivel_3.json",
    # "desenvolvimento_back_end_php_nivel_1.json",
    # "desenvolvimento_back_end_php_nivel_2.json",
    # "desenvolvimento_back_end_php_nivel_3.json",
    # "especialista_ia_nivel_3.json",
    # "engenharia_ia_nivel_2.json",
    # "engenharia_ia_nivel_3.json",
    # "desenvolvimento_back_end_python_nivel_3.json",
    "governanca_de_dados_nivel_1.json"
]

# =========================
# Autenticação
# =========================
load_dotenv()

# Clients lazy (instanciados apenas quando o provider correspondente é usado)
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


def _provider_for(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("claude") or m.startswith("anthropic"):
        return "anthropic"
    return "openai"

# =========================
# Prompts (detalhados, sem exercícios)
# =========================
VIDEO_SUMMARY_SYSTEM = (
    "Você é um sumarizador TÉCNICO para avaliação. Princípios: (1) Fidelidade absoluta ao texto; "
    "(2) Cobertura do conteúdo relevante usado em avaliações; (3) Granularidade objetiva; "
    "(4) Neutralidade; (5) Estrutura consistente; (6) Português do Brasil. "
    "Não invente nada, não crie exercícios, não traga fontes externas."
)

VIDEO_SUMMARY_USER_TEMPLATE = r"""
Resuma o TEXTO a seguir em **JSON válido e compacto**, com este schema exato (sem campos extras):
{
  "objetivos": ["o que o aluno deve alcançar"],
  "topicos": ["principais assuntos abordados"],
  "erros_ou_armadilhas_comuns": ["pontos de atenção, limitações, erros frequentes"],
  "habilidades": [
    {"titulo": "tarefa que o aluno passa a saber executar", "profundidade": "demonstrado|praticado|apenas_mencionado", "trecho_evidencia": "citação curta da transcrição (ou string vazia se apenas_mencionado sem exemplo)"}
  ],
  "ferramentas_ou_bibliotecas": [
    {"titulo": "ferramenta/lib/serviço", "profundidade": "demonstrado|praticado|apenas_mencionado", "trecho_evidencia": "citação curta"}
  ],
  "conceitos_chave": [
    {"titulo": "termo, métrica, padrão", "profundidade": "demonstrado|praticado|apenas_mencionado", "trecho_evidencia": "citação curta"}
  ],
  "exemplos_relevantes": [
    {"titulo": "nome curto do exemplo", "profundidade": "demonstrado|praticado|apenas_mencionado", "trecho_evidencia": "citação curta descrevendo o exemplo"}
  ]
}

Regras de classificação (CRÍTICO — afeta avaliação posterior; SEJA RIGOROSO):

- "demonstrado": **só classifique assim se houver EVIDÊNCIA OBSERVÁVEL na transcrição** de um dos seguintes:
  (a) código/comando concreto exibido (ex.: `import pandas as pd`, `pd.read_csv('x')`, `SELECT * FROM t`)
  (b) ação executada passo-a-passo descrita (ex.: "abro o Draw.io, arrasto o componente X, conecto em Y")
  (c) saída/resultado mostrado (ex.: "olhem o resultado: o DataFrame tem 30 linhas")
  (d) dataset/arquivo manipulado com nome e operação (ex.: "vamos abrir clientes.csv e calcular a média da coluna idade")
  Frases tipo "vamos usar X", "utilizaremos X", "traremos para uma aplicação X", "reporta via X" são **NÃO** demonstrado — são apenas_mencionado, mesmo que o instrutor diga que vai usar.

- "praticado": o curso PROPÕE ao aluno executar uma ação com o item (exercício, atividade, quiz prático, instrução clara "agora você faz X").

- "apenas_mencionado": o item é citado/nomeado/recomendado, mas a transcrição NÃO contém código nem passo-a-passo executado nem resultado mostrado.
  Inclui aqui: "domínio em SQL é desejável", "usaremos Power BI", "ferramenta como Tableau", "você precisa conhecer Python".

EXEMPLOS DE DECISÃO (siga rigorosamente):
- Transcrição: "import pandas as pd\ndf = pd.read_parquet('dados.parquet')" → Pandas: **demonstrado**
- Transcrição: "Utilizaremos a linguagem Python ao longo do curso" → Python: **apenas_mencionado**
- Transcrição: "abriremos o Draw.io e arrastaremos o componente até a área de desenho" → Draw.io: **demonstrado**
- Transcrição: "podemos relatar via Power BI ou outra ferramenta de BI" → Power BI: **apenas_mencionado**
- Transcrição: "agora é sua vez: crie uma planilha Excel com 3 colunas" → Excel: **praticado**
- Transcrição: "existem ferramentas como SQL Server, Snowflake e Redshift" → todos: **apenas_mencionado**

Na dúvida, classifique como **apenas_mencionado**. Falsos demonstrados são MUITO piores que falsos mencionados.

Regras gerais:
- Mantenha nomes de classes, funções, comandos, libs e serviços exatamente como aparecem.
- "trecho_evidencia": citação curta (1-2 frases) ou paráfrase próxima do que foi dito/feito. Se "apenas_mencionado" e sem exemplo, retorne string vazia.
- Se houver datasets, registre nomes e colunas em exemplos_relevantes (com profundidade adequada).
- Se houver métricas (R², MAE, RMSE, MAPE etc.), registre em conceitos_chave.
- Se um item não aparecer no texto, retorne lista vazia para esse campo.
- Listas com 3 a 10 itens, quando possível.
- Não duplique itens dentro do mesmo campo.

TEXTO:
{TEXTO}
"""

# =========================
# Utilitários
# =========================

def _dedup_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items or []:
        x = (x or "").strip()
        if not x:
            continue
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

# Campos com lista de strings simples
STRING_KEYS = [
    "objetivos",
    "topicos",
    "erros_ou_armadilhas_comuns",
]
# Campos enriquecidos: lista de objetos {titulo, profundidade, trecho_evidencia}
OBJECT_KEYS = [
    "habilidades",
    "ferramentas_ou_bibliotecas",
    "conceitos_chave",
    "exemplos_relevantes",
]
SCHEMA_KEYS = STRING_KEYS + OBJECT_KEYS

PROFUNDIDADES = ("demonstrado", "praticado", "apenas_mencionado")
PROFUNDIDADE_RANK = {"demonstrado": 3, "praticado": 2, "apenas_mencionado": 1}

EMPTY_SCHEMA: Dict[str, List[Any]] = {k: [] for k in SCHEMA_KEYS}


def _coerce_string_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _coerce_object_item(x: Any) -> Optional[Dict[str, str]]:
    """Normaliza um item de campo enriquecido para {titulo, profundidade, trecho_evidencia}."""
    if isinstance(x, str):
        t = x.strip()
        if not t:
            return None
        return {"titulo": t, "profundidade": "apenas_mencionado", "trecho_evidencia": ""}
    if not isinstance(x, dict):
        return None
    titulo = str(x.get("titulo", "") or "").strip()
    if not titulo:
        return None
    prof = str(x.get("profundidade", "") or "").strip().lower()
    if prof not in PROFUNDIDADES:
        prof = "apenas_mencionado"
    trecho = str(x.get("trecho_evidencia", "") or "").strip()
    return {"titulo": titulo, "profundidade": prof, "trecho_evidencia": trecho}


def _coerce_object_list(v: Any) -> List[Dict[str, str]]:
    if not isinstance(v, list):
        return []
    out: List[Dict[str, str]] = []
    for x in v:
        item = _coerce_object_item(x)
        if item:
            out.append(item)
    return out


def _coerce_schema(data: Any) -> Dict[str, List[Any]]:
    """Garante que o JSON tenha o schema correto:
    - STRING_KEYS: lista de strings
    - OBJECT_KEYS: lista de {titulo, profundidade, trecho_evidencia}
    """
    if not isinstance(data, dict):
        return {k: [] for k in SCHEMA_KEYS}
    result: Dict[str, List[Any]] = {}
    for k in STRING_KEYS:
        result[k] = _coerce_string_list(data.get(k, []))
    for k in OBJECT_KEYS:
        result[k] = _coerce_object_list(data.get(k, []))
    return result


def _merge_object_lists(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Dedup por título (case-insensitive). Mantém o item de maior profundidade.
    Se profundidades iguais, concatena evidências distintas (separadas por ' || ')."""
    by_key: Dict[str, Dict[str, str]] = {}
    for it in items or []:
        key = it["titulo"].lower()
        atual = by_key.get(key)
        if atual is None:
            by_key[key] = dict(it)
            continue
        rank_novo = PROFUNDIDADE_RANK.get(it["profundidade"], 0)
        rank_atual = PROFUNDIDADE_RANK.get(atual["profundidade"], 0)
        if rank_novo > rank_atual:
            # Substitui mantendo evidência do item mais profundo
            by_key[key] = dict(it)
        elif rank_novo == rank_atual:
            # Mesma profundidade: concatena evidências distintas
            ev_existing = atual.get("trecho_evidencia", "").strip()
            ev_novo = it.get("trecho_evidencia", "").strip()
            if ev_novo and ev_novo not in ev_existing:
                if ev_existing:
                    atual["trecho_evidencia"] = f"{ev_existing} || {ev_novo}"
                else:
                    atual["trecho_evidencia"] = ev_novo
    return list(by_key.values())


def _merge_summaries(json_list: List[Dict[str, List[Any]]]) -> Dict[str, List[Any]]:
    merged: Dict[str, List[Any]] = {k: [] for k in SCHEMA_KEYS}
    for j in json_list:
        j = _coerce_schema(j)
        for k in SCHEMA_KEYS:
            merged[k].extend(j.get(k, []))
    for k in STRING_KEYS:
        merged[k] = _dedup_keep_order(merged[k])
    for k in OBJECT_KEYS:
        merged[k] = _merge_object_lists(merged[k])
    return merged


def _split_with_overlap(text: str, size: int, overlap: int) -> List[str]:
    text = (text or "").strip()
    if len(text) <= size:
        return [text]
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        chunk = text[i : i + size]
        chunks.append(chunk)
        if i + size >= n:
            break
        i += size - overlap
        if i < 0:
            i = 0
    return chunks

# =========================
# OpenAI helper
# =========================

def _model_supports_temperature(model: str) -> bool:
    """gpt-5* e reasoning models (o1, o3, o4) só aceitam temperature default (1)."""
    m = (model or "").lower()
    return not (m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"))


def _split_system_user(messages: List[Dict[str, str]]) -> tuple:
    """Anthropic recebe `system` separado dos `messages`. Extrai e retorna (system_str, user_messages)."""
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    others = [m for m in messages if m.get("role") != "system"]
    return ("\n\n".join(system_parts), others)


def call_chat(messages: List[Dict[str, str]], *, retries: int = 3, backoff: float = 2.0) -> str:
    provider = _provider_for(MODEL)
    for attempt in range(retries):
        try:
            if provider == "anthropic":
                system, user_msgs = _split_system_user(messages)
                resp = _get_anthropic_client().messages.create(
                    model=MODEL,
                    max_tokens=8192,
                    system=system,
                    messages=user_msgs,
                    temperature=TEMPERATURE,
                )
                return "".join(b.text for b in resp.content if hasattr(b, "text"))
            kwargs: Dict[str, Any] = {"model": MODEL, "messages": messages}
            if _model_supports_temperature(MODEL):
                kwargs["temperature"] = TEMPERATURE
            resp = _get_openai_client().chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:
            if attempt == retries - 1:
                print(f"[ERRO] call_chat falhou após {retries} tentativas: {type(e).__name__}: {e}")
                raise
            wait = backoff * (2 ** attempt)
            print(f"[RETRY {attempt + 1}/{retries}] {type(e).__name__}: {e} — aguardando {wait:.1f}s")
            time.sleep(wait)
    return ""


def safe_json_loads(s: str) -> Any:
    if not s:
        return None
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].strip()
    try:
        return json.loads(s)
    except Exception:
        try:
            start, end = s.index("{"), s.rindex("}") + 1
            return json.loads(s[start:end])
        except Exception:
            return None

# =========================
# Núcleo: resumo por VÍDEO
# =========================

def summarize_video_text(video_text: str) -> Dict[str, List[str]]:
    text = (video_text or "").strip()
    if not text:
        return {**EMPTY_SCHEMA}

    # Caminho 1: texto curto o bastante -> 1 chamada
    if len(text) <= SINGLE_PASS_CHAR_LIMIT:
        user_prompt = VIDEO_SUMMARY_USER_TEMPLATE.replace("{TEXTO}", text)
        messages = [
            {"role": "system", "content": VIDEO_SUMMARY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        content = call_chat(messages)
        data = safe_json_loads(content)
        return _coerce_schema(data)

    # Caminho 2: fallback com chunking + merge local (com overlap p/ manter contexto)
    partials: List[Dict[str, List[str]]] = []
    for ch in _split_with_overlap(text, CHUNK_SIZE, CHUNK_OVERLAP):
        user_prompt = VIDEO_SUMMARY_USER_TEMPLATE.replace("{TEXTO}", ch)
        messages = [
            {"role": "system", "content": VIDEO_SUMMARY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        content = call_chat(messages)
        data = safe_json_loads(content)
        partials.append(_coerce_schema(data))
    return _merge_summaries(partials)

# =========================
# Pipeline por CURSO
# =========================

def summarize_course(course: Dict[str, Any]) -> Dict[str, Any]:
    transcricoes: List[str] = course.get("transcricao", []) or []
    if not transcricoes:
        return {"id": course.get("id"), "nome": course.get("nome"), "link": course.get("link"), "resumo": {**EMPTY_SCHEMA}}

    # Paraleliza por vídeo para acelerar
    results: List[Dict[str, List[str]]] = []
    nome_curso = course.get("nome") or course.get("id")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(summarize_video_text, t): idx for idx, t in enumerate(transcricoes, 1)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                # Em caso de falha de 1 vídeo, não interrompe o curso inteiro
                print(f"[ERRO] Vídeo {idx} de '{nome_curso}' falhou: {type(e).__name__}: {e}")
                results.append({**EMPTY_SCHEMA})

    merged_course = _merge_summaries(results)

    return {
        "id": course.get("id"),
        "nome": course.get("nome"),
        "link": course.get("link"),
        "resumo": merged_course,
    }

# =========================
# Leitura/gravação por nível
# =========================

def process_level_file(path: Path) -> List[Dict[str, Any]]:
    courses = json.loads(path.read_text(encoding="utf-8"))
    out: List[Dict[str, Any]] = []
    for c in courses:
        print(f"Resumindo curso: {c.get('id')} - {c.get('nome')}")
        out.append(summarize_course(c))
    return out

# =========================
# Main
# =========================

def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera resumos estruturados por curso a partir das transcrições.",
    )
    parser.add_argument(
        "--input_files",
        type=str,
        help=(
            "Lista de arquivos de entrada em trilha/ separados por vírgula "
            "(ex.: governanca_de_dados_nivel_1.json). "
            "Se omitido, usa a constante INPUT_FILES do topo do script."
        ),
    )
    parser.add_argument(
        "--carreira",
        type=str,
        help="Atalho: monta o nome do arquivo como <carreira>_nivel_<nivel>.json (requer --nivel).",
    )
    parser.add_argument(
        "--nivel",
        type=int,
        choices=[1, 2, 3],
        help="Nível da carreira (1, 2 ou 3). Requer --carreira.",
    )
    return parser.parse_args()


def _resolve_input_files(args: argparse.Namespace) -> List[str]:
    if args.input_files:
        return [f.strip() for f in args.input_files.split(",") if f.strip()]
    if args.carreira and args.nivel:
        return [f"{args.carreira}_nivel_{args.nivel}.json"]
    if args.carreira or args.nivel:
        raise SystemExit("--carreira e --nivel devem ser usados juntos.")
    return list(INPUT_FILES)


def main():
    args = _parse_cli_args()
    input_files = _resolve_input_files(args)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for fname in input_files:
        fpath = INPUT_DIR / fname
        if not fpath.exists():
            print(f"[AVISO] Arquivo não encontrado: {fpath}")
            continue
        print(f"\n=== Processando nível: {fname} ===")
        summaries = process_level_file(fpath)
        out_json = OUTPUT_DIR / f"resumos_{fname}"
        out_json.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Resumos salvos em: {out_json}")
        out_jsonl = OUTPUT_DIR / f"resumos_{fname}.jsonl"
        with out_jsonl.open("w", encoding="utf-8") as f:
            for item in summaries:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"[OK] JSONL salvo em: {out_jsonl}")

if __name__ == "__main__":
    main()
