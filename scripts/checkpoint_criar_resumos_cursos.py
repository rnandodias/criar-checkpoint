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
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from openai import OpenAI

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =========================
# Configuração
# =========================
TEMPERATURE = 0.0
MODEL = "gpt-4o"
# Se um vídeo passar desse limite, aplicamos fallback de chunking com overlap leve
SINGLE_PASS_CHAR_LIMIT = 300000  # ~ seguro para 4o, evitando estouro
CHUNK_SIZE = 180000
CHUNK_OVERLAP = 8000
MAX_WORKERS = 4  # paralelismo por curso (ajuste se houver rate limit)

# MODEL = "gpt-4o-mini"
# # Se um vídeo passar desse limite, aplicamos fallback de chunking com overlap leve
# SINGLE_PASS_CHAR_LIMIT = 14000  # ~ seguro para 4o-mini, evitando estouro
# CHUNK_SIZE = 10000
# CHUNK_OVERLAP = 1000
# MAX_WORKERS = 6  # paralelismo por curso (ajuste se houver rate limit)

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
api_key = os.getenv("OPENAI_CREDENTIALS")
if not api_key:
    raise RuntimeError("Defina OPENAI_CREDENTIALS no .env ou no ambiente.")
client = OpenAI(api_key=api_key)

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
  "habilidades": ["tarefas que o aluno passa a saber executar"],
  "ferramentas_ou_bibliotecas": ["ferramentas, libs, serviços citados"],
  "conceitos_chave": ["termos, métricas, fórmulas, padrões"],
  "exemplos_relevantes": ["exemplos práticos mencionados (dados, trechos de código, comandos, nomes de arquivos)"],
  "erros_ou_armadilhas_comuns": ["pontos de atenção, limitações, erros frequentes"]
}

Regras adicionais:
- Mantenha nomes de classes, funções, comandos, libs e serviços exatamente como aparecem.
- Se houver datasets, aponte nomes e principais colunas/atributos.
- Se houver métricas (ex.: R², MAE, RMSE, MAPE), registre.
- Se um item não aparecer no texto, retorne lista vazia para esse campo.
- Listas com 3 a 10 itens, quando possível.

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

SCHEMA_KEYS = [
    "objetivos",
    "topicos",
    "habilidades",
    "ferramentas_ou_bibliotecas",
    "conceitos_chave",
    "exemplos_relevantes",
    "erros_ou_armadilhas_comuns",
]

EMPTY_SCHEMA = {k: [] for k in SCHEMA_KEYS}


def _coerce_schema(data: Any) -> Dict[str, List[str]]:
    """Garante que o JSON tenha o schema correto e valores como listas de strings."""
    if not isinstance(data, dict):
        return {**EMPTY_SCHEMA}
    result: Dict[str, List[str]] = {}
    for k in SCHEMA_KEYS:
        v = data.get(k, [])
        if isinstance(v, list):
            result[k] = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, str) and v.strip():
            result[k] = [v.strip()]
        else:
            result[k] = []
    return result


def _merge_summaries(json_list: List[Dict[str, List[str]]]) -> Dict[str, List[str]]:
    merged = {k: [] for k in SCHEMA_KEYS}
    for j in json_list:
        j = _coerce_schema(j)
        for k in SCHEMA_KEYS:
            merged[k].extend(j.get(k, []))
    # dedup preservando ordem
    for k in SCHEMA_KEYS:
        merged[k] = _dedup_keep_order(merged[k])
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

def call_chat(messages: List[Dict[str, str]], *, retries: int = 3, backoff: float = 2.0) -> str:
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                messages=messages,
            )
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
