"""
Obtém as transcrições dos cursos da Alura via API oficial e grava o JSON
em `trilha/<nome_saida>.json`, no formato esperado pelo script
`checkpoint_criar_resumos_cursos.py`.

Substitui a versão anterior baseada em scraping Playwright (mais lenta, dependia
de EMAIL/PASSWORD e sofria timeouts em páginas com layout novo).

Pré-requisitos:
  pip install -r requirements.txt

Variáveis de ambiente (.env):
  ALURA_API_TOKEN=<token da API de cursos>

Exemplos:
  # Usando a carreira/nível registrados em `carreiras_niveis.py`:
  python scripts/obter_transcricoes_cursos.py --carreira governanca_de_dados --nivel 1

  # Passando IDs e nome de saída explicitamente:
  python scripts/obter_transcricoes_cursos.py \\
      --nome_saida governanca_de_dados_nivel_1 \\
      --ids 3713,4631,4632,3714,4633,3716,4635,3717,5166,4634

  # Só listar as carreiras/níveis disponíveis:
  python scripts/obter_transcricoes_cursos.py --listar

Saída:
  trilha/<nome_saida>.json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# No Windows, o stdout padrão é cp1252 e quebra ao printar acentos/unicode.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv
from tqdm import tqdm

# Imports locais
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
from carreiras_niveis import (  # noqa: E402
    CARREIRAS_NIVEIS,
    listar_carreiras,
    listar_niveis,
    obter_ids,
)

OUTPUT_DIR = _SCRIPT_DIR.parent / "trilha"

API_BASE_URL = "https://cursos.alura.com.br/api/course"
API_TIMEOUT = 60  # segundos por request
# Rate limit da API: 10 req/s teóricos, mas na prática o servidor rejeita antes.
# Modo estritamente conservador: 1 request por segundo (sequencial, sem concorrência).
MIN_INTERVAL_SEC = 1.0
# Retentativas para 429 (rate limit) e 5xx transitórios.
RETRY_MAX_ATTEMPTS = 5
RETRY_BASE_DELAY_SEC = 2.0  # backoff exponencial: 2, 4, 8, 16, 32s

# Atividades cujo `text` compõe a "transcrição" do curso.
# VIDEO           → transcrição das aulas em vídeo (base histórica do scraping).
# HQ_EXPLANATION  → textos curados densos (frameworks, tabelas comparativas).
# TEXT_CONTENT    → "Para saber mais", "Faça como eu fiz", "O que aprendemos?".
KINDS_APROVEITADOS = {"VIDEO", "HQ_EXPLANATION", "TEXT_CONTENT"}


def _get_token() -> str:
    load_dotenv()
    token = os.getenv("ALURA_API_TOKEN")
    if not token:
        raise RuntimeError(
            "Defina ALURA_API_TOKEN no .env para acessar a API de cursos da Alura."
        )
    return token


def _fetch_course(course_id: int, token: str) -> dict:
    """Busca o JSON de um curso na API. Retenta em 429/5xx com backoff exponencial;
    respeita o header Retry-After quando presente. Levanta HTTPError em 4xx não-transitórios
    ou quando esgota RETRY_MAX_ATTEMPTS."""
    url = f"{API_BASE_URL}/{course_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    last_exc: Optional[requests.HTTPError] = None
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        resp = requests.get(url, headers=headers, timeout=API_TIMEOUT)
        if resp.status_code < 400:
            return resp.json()

        # 429 (rate limit) e 5xx (erro transitório do servidor) → retentar.
        # Demais 4xx (401/403/404 etc.) são definitivos: levanta imediatamente.
        transitorio = resp.status_code == 429 or 500 <= resp.status_code < 600
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            last_exc = e
            if not transitorio or attempt == RETRY_MAX_ATTEMPTS:
                raise

        # Calcula espera: Retry-After (segundos) tem precedência sobre backoff exponencial.
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
        else:
            delay = RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))

        print(
            f"    [retry {attempt}/{RETRY_MAX_ATTEMPTS - 1}] curso {course_id} "
            f"HTTP {resp.status_code} — aguardando {delay:.1f}s"
        )
        time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Falha inesperada ao buscar curso {course_id}")


def _extrair_curso(data: dict, course_id: int) -> dict:
    """Converte o payload da API no schema consumido pelo restante do pipeline:
    {id, nome, link, transcricao: [str, ...]}

    Cada item de `transcricao` é o texto de UMA atividade prefixado por
    `"Atividade N - <titulo>\\n"`, para preservar a marcação de fronteira entre
    unidades de conteúdo (padrão herdado do scraping antigo).
    """
    nome = data.get("nome") or f"Curso {course_id}"
    slug = data.get("slug")
    link = f"https://cursos.alura.com.br/course/{slug}" if slug else ""

    transcricoes: List[str] = []
    for aula in data.get("aulas", []) or []:
        for atv in aula.get("atividades", []) or []:
            if atv.get("kind") not in KINDS_APROVEITADOS:
                continue
            text = atv.get("text")
            if not text:
                continue
            title = atv.get("title") or f"Atividade {len(transcricoes) + 1}"
            transcricoes.append(f"Atividade {len(transcricoes) + 1} - {title}\n{text}")

    return {
        "id": course_id,
        "nome": nome,
        "link": link,
        "transcricao": transcricoes,
    }


def obter_transcricoes(courses_id: List[int], nome_saida: str) -> Path:
    token = _get_token()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{nome_saida}.json"

    cursos: List[dict] = []
    last_call = 0.0
    for course_id in tqdm(courses_id, desc="Cursos"):
        # Throttle simples para respeitar o rate limit de 10 req/s.
        elapsed = time.monotonic() - last_call
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)
        last_call = time.monotonic()

        try:
            data = _fetch_course(course_id, token)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            if status in (401, 403):
                raise RuntimeError(
                    f"Token da API recusado (HTTP {status}). Revise ALURA_API_TOKEN no .env."
                ) from e
            print(f"[AVISO] Curso {course_id} falhou (HTTP {status}). Pulando...")
            continue
        except requests.RequestException as e:
            print(f"[AVISO] Curso {course_id} erro de conexão: {e}. Pulando...")
            continue

        curso = _extrair_curso(data, course_id)
        if not curso["transcricao"]:
            print(f"[AVISO] Curso {course_id} ('{curso['nome']}') sem atividades aproveitáveis. Pulando...")
            continue
        cursos.append(curso)

    out_path.write_text(json.dumps(cursos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Transcrições salvas em: {out_path}")
    return out_path


def _parse_ids(raw: str) -> List[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta transcrições dos cursos da Alura (via API) e grava em trilha/<nome>.json.",
    )
    parser.add_argument("--carreira", type=str, help="Chave de carreira registrada em carreiras_niveis.py")
    parser.add_argument("--nivel", type=int, choices=[1, 2, 3], help="Nível da carreira (1, 2 ou 3).")
    parser.add_argument("--ids", type=str, help="Lista de IDs de cursos separados por vírgula.")
    parser.add_argument("--nome_saida", type=str, help="Nome do arquivo de saída (sem .json).")
    parser.add_argument("--listar", action="store_true", help="Listar carreiras e níveis conhecidos e sair.")
    args = parser.parse_args()

    if args.listar:
        print("Carreiras registradas:")
        for car in listar_carreiras():
            print(f"  - {car} → níveis {listar_niveis(car)}")
        return

    if args.ids:
        ids = _parse_ids(args.ids)
        if not args.nome_saida:
            parser.error("--nome_saida é obrigatório quando --ids é usado.")
        nome_saida = args.nome_saida
    elif args.carreira and args.nivel:
        ids = obter_ids(args.carreira, args.nivel)
        nome_saida = args.nome_saida or f"{args.carreira}_nivel_{args.nivel}"
    else:
        parser.error("Use --carreira + --nivel OU --ids + --nome_saida. Use --listar para ver as opções.")

    obter_transcricoes(ids, nome_saida)


if __name__ == "__main__":
    main()
