"""
Obtém as transcrições dos vídeos dos cursos da Alura e grava o JSON
diretamente em `trilha/<nome_saida>.json`, no formato esperado pelo script
`checkpoint_criar_resumos_cursos.py`.

Adaptado do método `ScrapingAlura.get_course_transcription` do projeto
SCRAPING_FORMAÇÕES, mantendo apenas o estritamente necessário para o fluxo
de checkpoints.

Pré-requisitos:
  pip install -r requirements.txt
  playwright install

Variáveis de ambiente (.env):
  EMAIL=seu_email_alura
  PASSWORD=sua_senha_alura

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
from pathlib import Path
from typing import List, Optional

# No Windows, o stdout padrão é cp1252 e quebra ao printar acentos/unicode.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from bs4 import BeautifulSoup
from tqdm import tqdm
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# Imports locais
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
from _scraping_utils import limpar_texto  # noqa: E402
from carreiras_niveis import (  # noqa: E402
    CARREIRAS_NIVEIS,
    listar_carreiras,
    listar_niveis,
    obter_ids,
)

OUTPUT_DIR = _SCRIPT_DIR.parent / "trilha"


def _login(page, email: str, password: str) -> None:
    page.goto("https://cursos.alura.com.br/loginForm")
    page.fill("#login-email", email)
    page.fill("#password", password)
    page.click("button:has-text('Entrar')")
    try:
        # Espera sair da página de login — URL muda para o dashboard/algum path autenticado.
        page.wait_for_url(
            lambda url: "loginForm" not in url and "login" not in url.rstrip("/").split("/")[-1],
            timeout=30_000,
        )
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError as e:
        raise RuntimeError(
            "Login na Alura falhou (URL continua em /loginForm após 30s). "
            "Verifique EMAIL/PASSWORD no .env, presença de captcha ou 2FA, "
            "ou use --headful para inspecionar visualmente."
        ) from e


def _extrair_transcricoes_do_curso(page, course_id: int) -> Optional[dict]:
    page.goto(f"https://cursos.alura.com.br/admin/courses/v2/{course_id}")
    href = page.get_attribute("text=Ver curso", "href")
    if not href:
        print(f"[AVISO] Não encontrei o botão 'Ver curso' para o ID {course_id}. Pulando...")
        return None
    link = f"https://cursos.alura.com.br{href}"

    page.goto(link, timeout=60_000, wait_until="domcontentloaded")
    try:
        page.wait_for_selector(".courseSectionList", timeout=60_000)
    except PWTimeoutError:
        print(f"[AVISO] Timeout em {link}. Pulando curso {course_id}...")
        return None

    soup = BeautifulSoup(page.content(), "html.parser")
    nome_tag = soup.find("h1")
    nome = nome_tag.strong.get_text() if (nome_tag and nome_tag.strong) else f"Curso {course_id}"

    # Coleta links dos vídeos percorrendo as seções
    videos: List[str] = []
    for item in soup.find_all("li", class_="courseSection-listItem"):
        a_sec = item.find("a", class_="courseSectionList-section")
        if not a_sec or not a_sec.get("href"):
            continue
        aula = f"https://cursos.alura.com.br{a_sec['href']}"
        page.goto(aula, timeout=60_000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(".task-menu-sections-select", timeout=60_000)
        except PWTimeoutError:
            print(f"[AVISO] Timeout em {aula}. Pulando seção...")
            continue
        soup_section = BeautifulSoup(page.content(), "html.parser")
        for video in soup_section.find_all(
            "a", class_="task-menu-nav-item-link task-menu-nav-item-link-VIDEO"
        ):
            if video.get("href"):
                videos.append(f"https://cursos.alura.com.br{video['href']}")

    # Coleta transcrições de cada vídeo
    transcricoes: List[Optional[str]] = []
    for index, video in enumerate(videos):
        page.goto(video, timeout=60_000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("#transcription", timeout=60_000)
        except PWTimeoutError:
            print(f"[AVISO] Timeout em {video}. Pulando vídeo...")
            transcricoes.append(None)
            continue
        soup_video = BeautifulSoup(page.content(), "html.parser")
        title_tag = soup_video.find("h1", class_="task-body-header-title")
        title = title_tag.span.get_text() if (title_tag and title_tag.span) else f"Vídeo {index + 1}"
        section = soup_video.find("section", id="transcription")
        if not section:
            transcricoes.append(None)
            continue
        transcription = section.get_text()
        transcription = transcription.replace("Transcrição", f"Vídeo {index + 1} -{title}")
        transcricoes.append(limpar_texto(transcription))

    return {
        "id": course_id,
        "nome": nome,
        "link": link,
        "transcricao": transcricoes,
    }


def obter_transcricoes(courses_id: List[int], nome_saida: str, headless: bool = True) -> Path:
    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Defina EMAIL e PASSWORD no .env (credenciais da Alura).")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{nome_saida}.json"

    cursos: List[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        _login(page, email, password)

        for course_id in tqdm(courses_id, desc="Cursos"):
            curso = _extrair_transcricoes_do_curso(page, course_id)
            if curso is not None:
                cursos.append(curso)

        browser.close()

    out_path.write_text(json.dumps(cursos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Transcrições salvas em: {out_path}")
    return out_path


def _parse_ids(raw: str) -> List[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta transcrições dos cursos da Alura e grava em trilha/<nome>.json.",
    )
    parser.add_argument("--carreira", type=str, help="Chave de carreira registrada em carreiras_niveis.py")
    parser.add_argument("--nivel", type=int, choices=[1, 2, 3], help="Nível da carreira (1, 2 ou 3).")
    parser.add_argument("--ids", type=str, help="Lista de IDs de cursos separados por vírgula.")
    parser.add_argument("--nome_saida", type=str, help="Nome do arquivo de saída (sem .json).")
    parser.add_argument("--headful", action="store_true", help="Abrir navegador visível (debug).")
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

    obter_transcricoes(ids, nome_saida, headless=not args.headful)


if __name__ == "__main__":
    main()
