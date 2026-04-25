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
from typing import Dict, List, Any, Optional, Tuple
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
MODEL = "claude-opus-4-7"
# Se um vídeo passar desse limite, aplicamos fallback de chunking com overlap leve
SINGLE_PASS_CHAR_LIMIT = 300000
CHUNK_SIZE = 180000
CHUNK_OVERLAP = 8000
MAX_WORKERS = 4  # paralelismo por curso (ajuste se houver rate limit)

# Alternativas (troque MODEL conforme necessidade):
# MODEL = "claude-sonnet-4-6"  # ~30% do custo, ~70% do resultado
# MODEL = "gpt-5"
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
    "Você é um analista pedagógico extraindo CONTEÚDOS TESTÁVEIS de uma aula. "
    "Princípios: (1) Fidelidade ao texto; (2) Foco no que pode virar questão de prova; "
    "(3) Filtro qualitativo: NÃO catalogue tudo que foi dito — inclua APENAS o que foi efetivamente "
    "ensinado (com explicação, exemplo, demonstração ou exercício); (4) Português do Brasil. "
    "Não invente. Não crie exercícios. Não traga fontes externas."
)

# `VIDEO_SUMMARY_USER_STATIC` é a parte cacheável (instruções, schema, exemplos).
# `_render_user_dynamic(transcricao)` retorna a parte variável (a transcrição em si).
# Em providers Anthropic, esse split permite marcar cache_control no static.
VIDEO_SUMMARY_USER_STATIC = r"""
Analise a TRANSCRIÇÃO da aula e extraia os CONTEÚDOS TESTÁVEIS — tópicos que cabem virar questões de prova porque foram efetivamente ensinados.

NÃO INCLUA:
- Menções de passagem ("usaremos X", "existem ferramentas como Y").
- Listas genéricas de "tópicos do curso" sem desenvolvimento.
- Pré-requisitos citados ("é desejável conhecer Python").
- Ferramentas/libs apenas nomeadas, sem uso real demonstrado.

INCLUA quando o tópico tem pelo menos um destes:
- Explicação conceitual desenvolvida (>30s de fala dedicada).
- Demonstração concreta (código rodando, tela exibida, exemplo passo-a-passo, diagrama desenhado).
- Exercício ou atividade proposta ao aluno.
- Discussão de armadilhas/erros típicos.

Retorne JSON válido neste schema EXATO (sem campos extras):
{
  "tema_central": "frase curta sobre o que esta aula aborda (≤20 palavras)",
  "conteudos_testaveis": [
    {
      "topico": "nome curto do tópico",
      "nivel": "central|complementar",
      "tipo": "conceitual|procedimental",
      "habilidade": "verbo de ação + objeto (o que o aluno passa a saber fazer)",
      "evidencia_de_ensino": "1-2 frases descrevendo COMO foi ensinado (aula dedicada, exemplo concreto, demonstração ao vivo, exercício...). NÃO é citação literal.",
      "armadilhas_comuns": ["erro comum 1", "erro comum 2"]
    }
  ],
  "ferramentas_usadas": ["ferramenta1", "ferramenta2"]
}

Definições:
- "nivel": **central** = tópico principal da aula (vídeo gira em torno dele); **complementar** = apoia o central, mas não é o foco.
- "tipo": **conceitual** = entendimento de conceito/distinção/critério; **procedimental** = como executar passo-a-passo (com ferramenta, comando, processo).
- "habilidade": uma frase em verbo de ação. Ex.: "Distinguir os 3 papéis de governança em cenário concreto" ou "Construir glossário em planilha com colunas X, Y, Z".
- "evidencia_de_ensino": descreva a INTENSIDADE PEDAGÓGICA, não cite literalmente. Ex.: "Aula dedica 4 minutos com exemplo de organograma e exercício de classificação."
- "armadilhas_comuns": só inclua se a aula explicitamente menciona erros típicos ou pontos de atenção sobre o tópico. Senão, lista vazia.
- "ferramentas_usadas": lista enxuta de ferramentas que apareceram em algum dos `conteudos_testaveis` com USO REAL na aula. Não inclua ferramentas só citadas.

Tamanho:
- Aula curta (1 ideia): 1-3 conteudos_testaveis.
- Aula longa: até ~8.
- Se nenhum conteúdo cabe ser testado, retorne `conteudos_testaveis: []` e `ferramentas_usadas: []`.

EXEMPLOS DE DECISÃO:
- Aula explica papéis com organograma e exercício de classificação → 1 conteudo central conceitual sobre os papéis.
- Aula mostra `import pandas as pd; df = pd.read_csv(...)` ao vivo → 1 conteudo procedimental sobre leitura de dados com Pandas.
- Aula cita "podemos usar Power BI ou outra ferramenta" sem demonstrar → NADA sobre Power BI.
- Aula propõe ao aluno preencher template de glossário em planilha → 1 conteudo procedimental sobre glossário.

Na dúvida, **omita**. Falsos positivos (incluir ruído) são muito piores que falsos negativos.
"""

# Mantém o template legado por compatibilidade — usado quando provider for OpenAI (sem cache).
VIDEO_SUMMARY_USER_TEMPLATE = VIDEO_SUMMARY_USER_STATIC + "\n\nTRANSCRIÇÃO:\n{TEXTO}\n"


def _render_user_dynamic(transcricao: str) -> str:
    """Parte dinâmica do user prompt (a transcrição). Não vai pra cache."""
    return f"\n\nTRANSCRIÇÃO:\n{transcricao}\n"

# =========================
# Utilitários
# =========================

SCHEMA_NIVEIS = ("central", "complementar")
SCHEMA_TIPOS = ("conceitual", "procedimental")
NIVEL_RANK = {"central": 2, "complementar": 1}

EMPTY_RESUMO: Dict[str, Any] = {
    "tema_central": "",
    "conteudos_testaveis": [],
    "ferramentas_usadas": [],
}


def _coerce_conteudo_testavel(x: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(x, dict):
        return None
    topico = str(x.get("topico", "") or "").strip()
    if not topico:
        return None
    nivel = str(x.get("nivel", "") or "").strip().lower()
    if nivel not in SCHEMA_NIVEIS:
        nivel = "complementar"
    tipo = str(x.get("tipo", "") or "").strip().lower()
    if tipo not in SCHEMA_TIPOS:
        tipo = "conceitual"
    habilidade = str(x.get("habilidade", "") or "").strip()
    evidencia = str(x.get("evidencia_de_ensino", "") or "").strip()
    armadilhas_raw = x.get("armadilhas_comuns", []) or []
    armadilhas: List[str] = []
    if isinstance(armadilhas_raw, list):
        seen_a = set()
        for a in armadilhas_raw:
            s = str(a).strip()
            if s and s.lower() not in seen_a:
                armadilhas.append(s)
                seen_a.add(s.lower())
    return {
        "topico": topico,
        "nivel": nivel,
        "tipo": tipo,
        "habilidade": habilidade,
        "evidencia_de_ensino": evidencia,
        "armadilhas_comuns": armadilhas,
    }


def _coerce_schema(data: Any) -> Dict[str, Any]:
    """Garante o schema novo: tema_central + conteudos_testaveis + ferramentas_usadas."""
    if not isinstance(data, dict):
        return {"tema_central": "", "conteudos_testaveis": [], "ferramentas_usadas": []}
    tema = str(data.get("tema_central", "") or "").strip()
    raw_ct = data.get("conteudos_testaveis", []) or []
    if not isinstance(raw_ct, list):
        raw_ct = []
    conteudos: List[Dict[str, Any]] = []
    for x in raw_ct:
        item = _coerce_conteudo_testavel(x)
        if item:
            conteudos.append(item)
    raw_ferr = data.get("ferramentas_usadas", []) or []
    if not isinstance(raw_ferr, list):
        raw_ferr = []
    seen_f = set()
    ferramentas: List[str] = []
    for f in raw_ferr:
        s = str(f).strip()
        if s and s.lower() not in seen_f:
            ferramentas.append(s)
            seen_f.add(s.lower())
    return {
        "tema_central": tema,
        "conteudos_testaveis": conteudos,
        "ferramentas_usadas": ferramentas,
    }


def _merge_conteudos(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedup por `topico` (case-insensitive). Mantém maior `nivel`,
    concatena evidências distintas, une armadilhas, mantém habilidade mais detalhada."""
    by_key: Dict[str, Dict[str, Any]] = {}
    for it in items or []:
        key = it["topico"].lower()
        atual = by_key.get(key)
        if atual is None:
            by_key[key] = {
                **it,
                "armadilhas_comuns": list(it["armadilhas_comuns"]),
            }
            continue
        if NIVEL_RANK[it["nivel"]] > NIVEL_RANK[atual["nivel"]]:
            atual["nivel"] = it["nivel"]
        # Tipo: se um vídeo classificou como procedimental, prevalece (mais específico)
        if it["tipo"] == "procedimental":
            atual["tipo"] = "procedimental"
        # Habilidade: mantém a mais detalhada (mais longa)
        if len(it["habilidade"]) > len(atual["habilidade"]):
            atual["habilidade"] = it["habilidade"]
        # Evidência: concatena distintas
        ev_atual = atual.get("evidencia_de_ensino", "").strip()
        ev_novo = it.get("evidencia_de_ensino", "").strip()
        if ev_novo and ev_novo.lower() not in ev_atual.lower():
            atual["evidencia_de_ensino"] = (ev_atual + " | " + ev_novo).strip(" |")
        # Armadilhas: une
        seen_arm = {a.lower() for a in atual["armadilhas_comuns"]}
        for a in it["armadilhas_comuns"]:
            if a.lower() not in seen_arm:
                atual["armadilhas_comuns"].append(a)
                seen_arm.add(a.lower())
    return list(by_key.values())


def _merge_summaries(json_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Consolida resumos de N vídeos em 1 resumo de curso."""
    tema_central = ""
    conteudos: List[Dict[str, Any]] = []
    seen_ferr = set()
    ferramentas: List[str] = []
    for j in json_list:
        j = _coerce_schema(j)
        if not tema_central and j["tema_central"]:
            tema_central = j["tema_central"]
        conteudos.extend(j["conteudos_testaveis"])
        for f in j["ferramentas_usadas"]:
            if f.lower() not in seen_ferr:
                ferramentas.append(f)
                seen_ferr.add(f.lower())
    return {
        "tema_central": tema_central,
        "conteudos_testaveis": _merge_conteudos(conteudos),
        "ferramentas_usadas": ferramentas,
    }


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


# Acumulador global de uso para reportar economia de cache no fim do main
USAGE_TOTALS: Dict[str, int] = {
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "input_tokens": 0,
    "output_tokens": 0,
}


def _accumulate_usage(usage: Dict[str, int]) -> None:
    for k in USAGE_TOTALS:
        USAGE_TOTALS[k] += int(usage.get(k, 0) or 0)


def _build_anthropic_request_params(
    *, model: str, system_static: str, user_static: str, user_dynamic: str,
    temperature: float, max_tokens: int,
) -> Dict[str, Any]:
    """Constrói os params da chamada Anthropic com cache_control nos blocks estáticos."""
    system_blocks: List[Dict[str, Any]] = []
    if system_static:
        system_blocks.append({
            "type": "text",
            "text": system_static,
            "cache_control": {"type": "ephemeral"},
        })
    user_content: List[Dict[str, Any]] = []
    if user_static:
        user_content.append({
            "type": "text",
            "text": user_static,
            "cache_control": {"type": "ephemeral"},
        })
    if user_dynamic:
        user_content.append({"type": "text", "text": user_dynamic})
    params: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_content}],
    }
    if system_blocks:
        params["system"] = system_blocks
    return params


def _anthropic_messages_with_cache(
    *, model: str, system_static: str, user_static: str, user_dynamic: str,
    temperature: float = TEMPERATURE, max_tokens: int = 8192,
    retries: int = 3, backoff: float = 2.0,
) -> str:
    """Sync com prompt caching. Retorna texto e acumula usage."""
    client = _get_anthropic_client()
    params = _build_anthropic_request_params(
        model=model, system_static=system_static, user_static=user_static,
        user_dynamic=user_dynamic, temperature=temperature, max_tokens=max_tokens,
    )
    for attempt in range(retries):
        try:
            resp = client.messages.create(**params)
            text = "".join(b.text for b in resp.content if hasattr(b, "text"))
            usage = {
                "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }
            _accumulate_usage(usage)
            return text
        except Exception as e:
            if attempt == retries - 1:
                print(f"[ERRO] anthropic_messages falhou após {retries}: {type(e).__name__}: {e}")
                raise
            wait = backoff * (2 ** attempt)
            print(f"[RETRY {attempt + 1}/{retries}] {type(e).__name__}: {e} — {wait:.1f}s")
            time.sleep(wait)
    return ""


def _anthropic_messages_batch(
    *, model: str,
    items: List[Tuple[str, str, str, str]],
    temperature: float = TEMPERATURE, max_tokens: int = 8192,
    poll_interval: float = 15.0,
) -> Dict[str, str]:
    """Submete batch e bloqueia até concluir. items=[(custom_id, system_static, user_static, user_dynamic)].
    Retorna {custom_id: text}. Errored/expired/canceled fazem retry sync."""
    client = _get_anthropic_client()
    requests = []
    for custom_id, sys_s, user_s, user_d in items:
        params = _build_anthropic_request_params(
            model=model, system_static=sys_s, user_static=user_s, user_dynamic=user_d,
            temperature=temperature, max_tokens=max_tokens,
        )
        requests.append({"custom_id": custom_id, "params": params})

    print(f"[Batch] Submetendo {len(requests)} requests para {model}...")
    batch = client.messages.batches.create(requests=requests)
    print(f"[Batch] ID: {batch.id} | aguardando processamento (poll a cada {poll_interval:.0f}s)...")

    while batch.processing_status != "ended":
        time.sleep(poll_interval)
        batch = client.messages.batches.retrieve(batch.id)
        rc = batch.request_counts
        print(f"[Batch {batch.id[:16]}] proc={rc.processing} ok={rc.succeeded} err={rc.errored} cancel={rc.canceled} exp={rc.expired}")

    print("[Batch] Concluído. Lendo resultados...")
    results: Dict[str, str] = {}
    falhos: List[str] = []
    for entry in client.messages.batches.results(batch.id):
        custom_id = entry.custom_id
        if entry.result.type == "succeeded":
            msg = entry.result.message
            text = "".join(b.text for b in msg.content if hasattr(b, "text"))
            usage = {
                "cache_creation_input_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            }
            _accumulate_usage(usage)
            results[custom_id] = text
        else:
            falhos.append(custom_id)

    if falhos:
        print(f"[Batch] {len(falhos)} requests falharam. Retry síncrono...")
        item_by_id = {it[0]: it for it in items}
        for cid in falhos:
            try:
                _, sys_s, user_s, user_d = item_by_id[cid]
                results[cid] = _anthropic_messages_with_cache(
                    model=model, system_static=sys_s, user_static=user_s, user_dynamic=user_d,
                    temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as e:
                print(f"[Batch] retry de {cid} falhou: {e}")
                results[cid] = ""

    return results


def call_chat(messages: List[Dict[str, str]], *, retries: int = 3, backoff: float = 2.0) -> str:
    """Sync. Para Anthropic usa cache (assume 1º msg system, 2º msg user com {TEXTO} marker)."""
    provider = _provider_for(MODEL)
    if provider == "anthropic":
        # Extrai system + user. Para cache, separa user_static de user_dynamic via TRANSCRIÇÃO marker.
        system_str = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        user_str = "\n\n".join(m["content"] for m in messages if m.get("role") == "user")
        marker = "\n\nTRANSCRIÇÃO:\n"
        if marker in user_str:
            user_static, user_dynamic = user_str.split(marker, 1)
            user_dynamic = marker + user_dynamic
        else:
            user_static, user_dynamic = "", user_str
        return _anthropic_messages_with_cache(
            model=MODEL, system_static=system_str,
            user_static=user_static, user_dynamic=user_dynamic,
            temperature=TEMPERATURE, max_tokens=8192,
            retries=retries, backoff=backoff,
        )
    # OpenAI: comportamento original
    for attempt in range(retries):
        try:
            kwargs: Dict[str, Any] = {"model": MODEL, "messages": messages}
            if _model_supports_temperature(MODEL):
                kwargs["temperature"] = TEMPERATURE
            resp = _get_openai_client().chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:
            if attempt == retries - 1:
                print(f"[ERRO] call_chat falhou após {retries}: {type(e).__name__}: {e}")
                raise
            wait = backoff * (2 ** attempt)
            print(f"[RETRY {attempt + 1}/{retries}] {type(e).__name__}: {e} — {wait:.1f}s")
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

def summarize_video_text(video_text: str) -> Dict[str, Any]:
    text = (video_text or "").strip()
    if not text:
        return dict(EMPTY_RESUMO)

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
    partials: List[Dict[str, Any]] = []
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

def summarize_videos_batch(transcricoes: List[str]) -> List[Dict[str, Any]]:
    """Submete todas as transcrições do curso em 1 batch Anthropic. Trata chunking
    para vídeos longos: cada chunk vira um item do batch; depois merge local."""
    items: List[Tuple[str, str, str, str]] = []
    # mapeamento custom_id -> (idx_video, idx_chunk) para reagrupar
    video_chunks: Dict[int, List[str]] = {}
    for v_idx, text in enumerate(transcricoes):
        text = (text or "").strip()
        if not text:
            video_chunks[v_idx] = []
            continue
        if len(text) <= SINGLE_PASS_CHAR_LIMIT:
            cid = f"v{v_idx}_c0"
            items.append((cid, VIDEO_SUMMARY_SYSTEM, VIDEO_SUMMARY_USER_STATIC, _render_user_dynamic(text)))
            video_chunks[v_idx] = [cid]
        else:
            ids = []
            for c_idx, ch in enumerate(_split_with_overlap(text, CHUNK_SIZE, CHUNK_OVERLAP)):
                cid = f"v{v_idx}_c{c_idx}"
                items.append((cid, VIDEO_SUMMARY_SYSTEM, VIDEO_SUMMARY_USER_STATIC, _render_user_dynamic(ch)))
                ids.append(cid)
            video_chunks[v_idx] = ids

    if not items:
        return [dict(EMPTY_RESUMO) for _ in transcricoes]

    responses = _anthropic_messages_batch(model=MODEL, items=items)

    out: List[Dict[str, Any]] = []
    for v_idx in range(len(transcricoes)):
        cids = video_chunks.get(v_idx, [])
        if not cids:
            out.append(dict(EMPTY_RESUMO))
            continue
        partials = []
        for cid in cids:
            content = responses.get(cid, "")
            data = safe_json_loads(content)
            partials.append(_coerce_schema(data))
        if len(partials) == 1:
            out.append(partials[0])
        else:
            out.append(_merge_summaries(partials))
    return out


def summarize_course(course: Dict[str, Any], batch_mode: bool = True) -> Dict[str, Any]:
    transcricoes: List[str] = course.get("transcricao", []) or []
    if not transcricoes:
        return {"id": course.get("id"), "nome": course.get("nome"), "link": course.get("link"), "resumo": dict(EMPTY_RESUMO)}

    nome_curso = course.get("nome") or course.get("id")

    use_batch = batch_mode and _provider_for(MODEL) == "anthropic" and len(transcricoes) >= 2
    if use_batch:
        print(f"[Etapa 2] Curso '{nome_curso}' — {len(transcricoes)} vídeos via Anthropic Batch")
        try:
            results = summarize_videos_batch(transcricoes)
        except Exception as e:
            print(f"[Etapa 2] Batch falhou ({type(e).__name__}: {e}). Fallback para sync.")
            use_batch = False

    if not use_batch:
        # Paraleliza por vídeo para acelerar (modo sync)
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(summarize_video_text, t): idx for idx, t in enumerate(transcricoes, 1)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    print(f"[ERRO] Vídeo {idx} de '{nome_curso}' falhou: {type(e).__name__}: {e}")
                    results.append(dict(EMPTY_RESUMO))

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

def process_level_file(path: Path, batch_mode: bool = True) -> List[Dict[str, Any]]:
    courses = json.loads(path.read_text(encoding="utf-8"))
    out: List[Dict[str, Any]] = []
    for c in courses:
        print(f"Resumindo curso: {c.get('id')} - {c.get('nome')}")
        out.append(summarize_course(c, batch_mode=batch_mode))
    return out


def _print_usage_summary() -> None:
    """Imprime totais de tokens e estimativa de economia de cache."""
    if not any(USAGE_TOTALS.values()):
        return
    cache_read = USAGE_TOTALS["cache_read_input_tokens"]
    cache_create = USAGE_TOTALS["cache_creation_input_tokens"]
    inp = USAGE_TOTALS["input_tokens"]
    out = USAGE_TOTALS["output_tokens"]
    # Estimativa de economia: cache_read custa 10% do preço normal de input.
    # economia = 0.9 * cache_read tokens em valor de input.
    print()
    print("=" * 60)
    print("[Uso Anthropic — totais]")
    print(f"  Input (não cacheado):       {inp:>10,} tokens")
    print(f"  Cache create (escrita):     {cache_create:>10,} tokens (custa 1.25x input)")
    print(f"  Cache read (hit):           {cache_read:>10,} tokens (custa 0.10x input — economia ~90%)")
    print(f"  Output:                     {out:>10,} tokens")
    print("=" * 60)

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
    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="Desativa modo batch (Anthropic). Usa execução síncrona — apenas para debug.",
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
    batch_mode = not args.no_batch

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for fname in input_files:
        fpath = INPUT_DIR / fname
        if not fpath.exists():
            print(f"[AVISO] Arquivo não encontrado: {fpath}")
            continue
        print(f"\n=== Processando nível: {fname} (batch={'on' if batch_mode else 'off'}) ===")
        summaries = process_level_file(fpath, batch_mode=batch_mode)
        out_json = OUTPUT_DIR / f"resumos_{fname}"
        out_json.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Resumos salvos em: {out_json}")
        out_jsonl = OUTPUT_DIR / f"resumos_{fname}.jsonl"
        with out_jsonl.open("w", encoding="utf-8") as f:
            for item in summaries:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"[OK] JSONL salvo em: {out_jsonl}")

    _print_usage_summary()

if __name__ == "__main__":
    main()
