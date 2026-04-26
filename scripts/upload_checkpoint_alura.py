"""
Uploader de Checkpoint no admin da Alura — automação Playwright.

Vai sendo construído por etapas, cada uma idempotente o quanto possível.

ETAPA 1 — `criar_secoes`:
  Acessa /admin/courses/v2/<id>/sections e cria 3 seções:
  Apresentação, Prova teórica, Prova prática.

Próximas etapas (TODO conforme o usuário for fornecendo URLs/fluxos):
  - Adicionar atividades em cada seção
  - Carregar conteúdo das provas

Uso:
    python scripts/upload_checkpoint_alura.py --curso_id 5256 --etapa criar_secoes
    python scripts/upload_checkpoint_alura.py --curso_id 5256 --etapa criar_secoes --headless

Pré-requisitos:
    EMAIL e PASSWORD no .env (credenciais Alura).
"""
from __future__ import annotations
import argparse
import os
import re
import sys
import time
from typing import List

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeoutError

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SECOES_PADRAO = ["Apresentação", "Prova teórica", "Prova prática"]
SECAO_PROVA_TEORICA = "Prova teórica"


def _login(page: Page, email: str, password: str) -> None:
    # wait_until="domcontentloaded" evita esperar trackers/analytics que travam o "load"
    page.goto("https://cursos.alura.com.br/loginForm", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#login-email", timeout=20_000)
    page.fill("#login-email", email)
    page.fill("#password", password)
    page.click("button:has-text('Entrar')")
    try:
        page.wait_for_url(
            lambda url: "loginForm" not in url and "login" not in url.rstrip("/").split("/")[-1],
            timeout=30_000,
        )
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError as e:
        raise RuntimeError(
            "Login na Alura falhou. Verifique EMAIL/PASSWORD no .env, "
            "presença de captcha ou 2FA, ou rode com --headful para inspecionar."
        ) from e


def _criar_uma_secao(page: Page, course_id: int, nome: str) -> str:
    """Navega para /sections, clica 'Nova seção', preenche nome, salva.
    Retorna a URL final (geralmente edit da seção criada ou de volta a /sections)."""
    sections_url = f"https://cursos.alura.com.br/admin/courses/v2/{course_id}/sections"
    print(f"  → GET {sections_url}")
    page.goto(sections_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=15_000)

    print(f"  → Clicando em 'Nova seção'")
    # Tenta múltiplos seletores comuns para o link/botão "Nova seção"
    candidatos_botao = [
        "a:has-text('Nova seção')",
        "button:has-text('Nova seção')",
        "text=Nova seção",
    ]
    clicked = False
    for sel in candidatos_botao:
        try:
            page.click(sel, timeout=5_000)
            clicked = True
            break
        except PWTimeoutError:
            continue
    if not clicked:
        raise RuntimeError(
            f"Botão 'Nova seção' não encontrado em {sections_url}. "
            f"Testei: {candidatos_botao}"
        )

    # Espera URL nova
    page.wait_for_url(f"**/admin/courses/v2/{course_id}/newSection", timeout=15_000)
    page.wait_for_load_state("networkidle", timeout=15_000)
    print(f"  → Em {page.url}")

    print(f"  → Preenchendo Nome: '{nome}'")
    candidatos_input = [
        'input[name="name"]',
        'input#name',
        'input[id="name"]',
        'label:has-text("Nome") >> .. >> input',
    ]
    filled = False
    for sel in candidatos_input:
        try:
            page.wait_for_selector(sel, timeout=5_000)
            page.fill(sel, nome)
            filled = True
            break
        except PWTimeoutError:
            continue
    if not filled:
        raise RuntimeError(
            f"Campo 'Nome' não encontrado em {page.url}. Testei: {candidatos_input}"
        )

    print(f"  → Clicando em 'Salvar'")
    candidatos_salvar = [
        "button:has-text('Salvar')",
        "input[type='submit'][value*='Salvar']",
        "button[type='submit']",
    ]
    saved = False
    for sel in candidatos_salvar:
        try:
            page.click(sel, timeout=5_000)
            saved = True
            break
        except PWTimeoutError:
            continue
    if not saved:
        raise RuntimeError(f"Botão 'Salvar' não encontrado em {page.url}.")

    # Aguarda navegação pós-save
    try:
        page.wait_for_url(
            lambda url: "/newSection" not in url,
            timeout=20_000,
        )
    except PWTimeoutError:
        pass
    page.wait_for_load_state("networkidle", timeout=15_000)
    final_url = page.url
    print(f"  ✓ Seção '{nome}' criada — URL final: {final_url}")
    return final_url


def _marcar_secao_como_prova(page: Page, course_id: int, nome_secao: str) -> None:
    """Vai para /sections, abre a edição da seção `nome_secao`, marca o
    checkbox 'É prova?' e salva.

    Idempotente: se o checkbox já está marcado, salva mesmo assim (sem efeito)."""
    sections_url = f"https://cursos.alura.com.br/admin/courses/v2/{course_id}/sections"
    print(f"  → GET {sections_url}")
    page.goto(sections_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=15_000)

    print(f"  → Procurando linha da seção '{nome_secao}' e clicando em 'Editar'")
    # Tenta múltiplas estruturas (tabela, lista, card)
    candidatos_editar = [
        f"tr:has-text('{nome_secao}') a:has-text('Editar')",
        f"tr:has-text('{nome_secao}') button:has-text('Editar')",
        f"li:has-text('{nome_secao}') a:has-text('Editar')",
        f"*:has(> :text-is('{nome_secao}')) >> a:has-text('Editar')",
        f"text='{nome_secao}' >> xpath=ancestor::*[self::tr or self::li or self::div][1] >> a:has-text('Editar')",
    ]
    clicked = False
    for sel in candidatos_editar:
        try:
            page.click(sel, timeout=5_000)
            clicked = True
            break
        except PWTimeoutError:
            continue
    if not clicked:
        raise RuntimeError(
            f"Botão 'Editar' da seção '{nome_secao}' não encontrado em {sections_url}. "
            f"Testei: {candidatos_editar}"
        )

    # Espera a página de edição da seção carregar
    page.wait_for_load_state("networkidle", timeout=15_000)
    print(f"  → Em {page.url}")

    print(f"  → Marcando checkbox 'É prova?'")
    candidatos_checkbox = [
        # Pelo label associado
        "label:has-text('É prova') >> .. >> input[type='checkbox']",
        "label:has-text('É prova') input[type='checkbox']",
        # Pelo texto adjacente
        "input[type='checkbox'][name*='prova' i]",
        "input[type='checkbox'][id*='prova' i]",
        # Genérico: o checkbox próximo ao texto "É prova?"
        "text=É prova? >> xpath=preceding-sibling::input[@type='checkbox'][1]",
        "text=É prova? >> xpath=following-sibling::input[@type='checkbox'][1]",
        "text=É prova? >> xpath=ancestor-or-self::*[1] >> input[type='checkbox']",
    ]
    checked = False
    for sel in candidatos_checkbox:
        try:
            page.check(sel, timeout=5_000)
            checked = True
            print(f"     ✓ Checkbox marcado via seletor: {sel}")
            break
        except PWTimeoutError:
            continue
        except Exception:
            continue
    if not checked:
        # Salva HTML pra diagnóstico antes de levantar
        from pathlib import Path
        tmp = Path(__file__).resolve().parent.parent / "tmp" / "spike"
        tmp.mkdir(parents=True, exist_ok=True)
        html_path = tmp / f"erro_checkbox_eprova_{int(time.time())}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        screenshot = tmp / f"erro_checkbox_eprova_{int(time.time())}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        raise RuntimeError(
            f"Checkbox 'É prova?' não encontrado em {page.url}. "
            f"HTML salvo em {html_path}. Testei: {candidatos_checkbox}"
        )

    print(f"  → Clicando em 'Salvar'")
    candidatos_salvar = [
        "button:has-text('Salvar')",
        "input[type='submit'][value*='Salvar']",
        "button[type='submit']",
    ]
    saved = False
    for sel in candidatos_salvar:
        try:
            page.click(sel, timeout=5_000)
            saved = True
            break
        except PWTimeoutError:
            continue
    if not saved:
        raise RuntimeError(f"Botão 'Salvar' não encontrado em {page.url}.")

    page.wait_for_load_state("networkidle", timeout=15_000)
    print(f"  ✓ Seção '{nome_secao}' marcada como prova — URL final: {page.url}")


def _section_id_por_nome(page: Page, course_id: int, nome_secao: str) -> int:
    """Vai para /sections, procura a linha da seção e extrai o section_id do href de Editar."""
    sections_url = f"https://cursos.alura.com.br/admin/courses/v2/{course_id}/sections"
    page.goto(sections_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=15_000)

    candidatos = [
        f"tr:has-text('{nome_secao}') a:has-text('Editar')",
        f"li:has-text('{nome_secao}') a:has-text('Editar')",
        f"*:has(> :text-is('{nome_secao}')) >> a:has-text('Editar')",
    ]
    for sel in candidatos:
        el = page.query_selector(sel)
        if el:
            href = el.get_attribute("href") or ""
            m = re.search(r"/sections/(\d+)", href)
            if m:
                return int(m.group(1))
    raise RuntimeError(f"section_id não encontrado para '{nome_secao}' em {sections_url}")


# Markdown padrão da atividade "Etapas do projeto" (única atividade da seção Apresentação)
APRESENTACAO_TEMPLATE = """\
Estamos chegando ao final do **Nível {nivel}**! Parabéns por ter chegado até aqui depois de tanto estudo e tantos cursos.

Esse é o último passo, um bem importante. É aqui que você vai mostrar o que aprendeu e, com isso, obter seu **Certificado de Conclusão do Nível {nivel}**.

São duas etapas:

1. Uma prova teórica com um questionário de múltipla escolha sobre os mais diversos assuntos abordados durante o nível. Você precisa acertar pelo menos 50% para poder receber o certificado.
2. Uma prova prática com alguns desafios que você precisa desenvolver. O projeto exige que você use, na prática, assuntos que aprendeu ao longo da trilha.

Pronto(a)? Vamos lá!"""


_JS_SET_CODEMIRROR = """
(args) => {
    const { selectorWrapper, text } = args;
    const wrapper = document.querySelector(selectorWrapper);
    if (!wrapper) return { ok: false, reason: 'wrapper not found: ' + selectorWrapper };
    const cmEl = wrapper.querySelector('.CodeMirror');
    if (!cmEl || !cmEl.CodeMirror) return { ok: false, reason: 'CodeMirror instance not found' };
    cmEl.CodeMirror.setValue(text);
    if (typeof cmEl.CodeMirror.save === 'function') cmEl.CodeMirror.save();
    const ta = wrapper.querySelector('textarea[name]') || wrapper.querySelector('textarea');
    return { ok: true, taValueLen: ta ? (ta.value || '').length : null };
}
"""


def _preencher_codemirror(page: Page, candidatos_wrapper: List[str], conteudo: str) -> str:
    """Preenche um editor EasyMDE/CodeMirror via API. Retorna o seletor que funcionou."""
    last_reason = None
    for sel_wrap in candidatos_wrapper:
        try:
            result = page.evaluate(_JS_SET_CODEMIRROR, {"selectorWrapper": sel_wrap, "text": conteudo})
            if result and result.get("ok"):
                return sel_wrap
            last_reason = result.get("reason") if result else None
        except Exception as e:
            last_reason = str(e)
            continue
    raise RuntimeError(
        f"Não consegui preencher CodeMirror em nenhum wrapper testado: {candidatos_wrapper}. "
        f"Último motivo: {last_reason}"
    )


def _selecionar_tipo_por_task_enum(page: Page, task_enum: str, require_value: bool = True) -> dict:
    """Seleciona a primeira <option> do select de tipos de atividade que tem
    data-task-enum=task_enum. require_value=True exige que value!='' (filtra
    cabeçalhos hierárquicos). Retorna {ok, value, label} ou levanta RuntimeError."""
    js = """
    (args) => {
        const { taskEnum, requireValue } = args;
        const candidatos = ['select#chooseTask', "select[name='tagId']", "select[name='type']"];
        let sel = null;
        for (const s of candidatos) {
            sel = document.querySelector(s);
            if (sel) break;
        }
        if (!sel) return { ok: false, reason: 'select not found' };
        const opt = Array.from(sel.options).find(o =>
            (o.dataset.taskEnum === taskEnum) && (!requireValue || (o.value && o.value !== ''))
        );
        if (!opt) return { ok: false, reason: 'option with task-enum ' + taskEnum + ' not found', available: Array.from(sel.options).map(o => ({ value: o.value, taskEnum: o.dataset.taskEnum, text: (o.text || '').trim() })) };
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        return { ok: true, value: opt.value, label: (opt.text || '').trim() };
    }
    """
    res = page.evaluate(js, {"taskEnum": task_enum, "requireValue": require_value})
    if not res or not res.get("ok"):
        raise RuntimeError(f"Não consegui selecionar tipo {task_enum}: {res}")
    return res


def _selecionar_dropdown_por_label_visivel(page: Page, label_selectors: List[str], opcao_visivel: str) -> str:
    """Tenta vários seletores de <select> e seleciona a opção pelo texto visível.
    Retorna o seletor que funcionou."""
    last_err: Exception | None = None
    for sel in label_selectors:
        try:
            page.wait_for_selector(sel, timeout=5_000)
            page.select_option(sel, label=opcao_visivel)
            return sel
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(
        f"Dropdown não pôde selecionar opção '{opcao_visivel}'. "
        f"Testei: {label_selectors}. Último erro: {last_err}"
    )


def _criar_atividade_explicacao(
    page: Page,
    course_id: int,
    section_id: int,
    titulo: str,
    conteudo_md: str,
) -> str:
    """Cria 1 atividade do tipo 'Explicação' na seção. Retorna a URL final."""
    tasks_url = f"https://cursos.alura.com.br/admin/course/v2/{course_id}/section/{section_id}/tasks"
    print(f"  → GET {tasks_url}")
    page.goto(tasks_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=15_000)

    print(f"  → Clicando em 'Nova atividade'")
    candidatos_botao = [
        "a:has-text('Nova atividade')",
        "button:has-text('Nova atividade')",
        "text=Nova atividade",
    ]
    clicked = False
    for sel in candidatos_botao:
        try:
            page.click(sel, timeout=5_000)
            clicked = True
            break
        except PWTimeoutError:
            continue
    if not clicked:
        raise RuntimeError(f"Botão 'Nova atividade' não encontrado em {tasks_url}.")

    # Em vez de esperar URL específica (Alura usa singular/plural inconsistente),
    # aguarda apenas networkidle e loga a URL real.
    page.wait_for_load_state("networkidle", timeout=20_000)
    print(f"  → Em {page.url}")
    if "/create" not in page.url and "/new" not in page.url:
        # Salva diagnóstico se a URL não parece ser a de criação
        from pathlib import Path
        tmp = Path(__file__).resolve().parent.parent / "tmp" / "spike"
        tmp.mkdir(parents=True, exist_ok=True)
        html_path = tmp / f"erro_pos_nova_atividade_{int(time.time())}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(tmp / f"erro_pos_nova_atividade_{int(time.time())}.png"), full_page=True)
        print(f"  ⚠ URL inesperada após clique. HTML salvo em: {html_path}")

    print(f"  → Selecionando tipo 'Explicação'")
    sel_dropdown = [
        "select[name='type']",
        "select[id*='type']",
        "select[name='taskType']",
        "label:has-text('tipo de atividade') >> .. >> select",
        "label:has-text('tipo de atividade') ~ select",
    ]
    used = _selecionar_dropdown_por_label_visivel(page, sel_dropdown, "Explicação")
    print(f"     ✓ tipo selecionado via: {used}")

    print(f"  → Preenchendo Título: '{titulo}'")
    candidatos_titulo = [
        "input[name='title']",
        "input[name='name']",
        "input[id='title']",
        "input[id='name']",
        "label:has-text('Título') >> .. >> input",
    ]
    filled = False
    for sel in candidatos_titulo:
        try:
            page.wait_for_selector(sel, timeout=5_000)
            page.fill(sel, titulo)
            filled = True
            break
        except PWTimeoutError:
            continue
    if not filled:
        raise RuntimeError(f"Campo de título não encontrado em {page.url}.")

    print(f"  → Preenchendo Conteúdo (EasyMDE/CodeMirror, {len(conteudo_md)} chars)")
    candidatos_wrapper = ["#text.markdownEditor", ".markdown-editor--wrapper"]
    used_wrap = _preencher_codemirror(page, candidatos_wrapper, conteudo_md)
    print(f"     ✓ via wrapper '{used_wrap}'")

    print(f"  → Clicando em 'Salvar'")
    candidatos_salvar = [
        "button:has-text('Salvar')",
        "input[type='submit'][value*='Salvar']",
        "button[type='submit']",
    ]
    saved = False
    for sel in candidatos_salvar:
        try:
            page.click(sel, timeout=5_000)
            saved = True
            break
        except PWTimeoutError:
            continue
    if not saved:
        raise RuntimeError(f"Botão 'Salvar' não encontrado em {page.url}.")

    page.wait_for_load_state("networkidle", timeout=15_000)
    print(f"  ✓ Atividade '{titulo}' criada — URL atual: {page.url}")

    # Volta para /sections como pedido pelo fluxo
    sections_url = f"https://cursos.alura.com.br/admin/courses/v2/{course_id}/sections"
    print(f"  → Retornando para {sections_url}")
    page.goto(sections_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=15_000)
    return page.url


def criar_atividade_apresentacao(course_id: int, nivel: int = 1, headless: bool = False) -> None:
    """Cria a atividade 'Etapas do projeto' (tipo Explicação) na seção 'Apresentação'."""
    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Defina EMAIL e PASSWORD no .env (credenciais da Alura).")

    titulo = "Etapas do projeto"
    conteudo = APRESENTACAO_TEMPLATE.format(nivel=nivel)

    print(f"=== Criando atividade '{titulo}' na seção 'Apresentação' (curso {course_id}, nível {nivel}) ===")
    print(f"Modo: {'headless' if headless else 'headful (janela visível)'}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        print("[1/3] Login...")
        _login(page, email, password)
        print("      ✓ OK")
        print()
        print("[2/3] Descobrindo section_id de 'Apresentação'...")
        section_id = _section_id_por_nome(page, course_id, "Apresentação")
        print(f"      section_id = {section_id}")
        print()
        print("[3/3] Criando atividade 'Explicação'...")
        _criar_atividade_explicacao(page, course_id, section_id, titulo, conteudo)
        print(f"\n✓ Concluído.")
        browser.close()


def _slugify(s: str) -> str:
    """Mesmo slug usado em gerar_prova_teorica_do_zero.py — mantém compat com nome do arquivo."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "geral"


def _resolve_prova_teorica_path(carreira: str, nivel: int, override: str = "") -> "Path":
    from pathlib import Path
    if override:
        p = Path(override)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de prova não encontrado: {p}")
        return p
    base = Path(__file__).resolve().parent.parent / "output" / "cursos_checkpoint"
    slug = _slugify(carreira)
    candidato = base / f"prova_teorica_{slug}_nivel_{nivel}.txt"
    if candidato.exists():
        return candidato
    raise FileNotFoundError(
        f"Arquivo não encontrado: {candidato}. Use --prova_teorica_arquivo para indicar manualmente."
    )


def _parse_prova_teorica(txt: str) -> List[dict]:
    """Parse o TXT da prova teórica em lista de exercícios estruturados."""
    # Normaliza linha-quebra
    txt = txt.replace("\r\n", "\n")
    # Split por separadores (linha de hífens com 20+ caracteres)
    blocos = re.split(r"\n-{20,}\s*\n", txt)
    exercicios: List[dict] = []
    for bloco in blocos:
        bloco = bloco.strip()
        if not bloco or not re.search(r"^EXERCÍCIO\s+\d+", bloco, re.MULTILINE):
            continue

        # Título
        m_titulo = re.search(r"Título:\s*(.+)", bloco)
        titulo = m_titulo.group(1).strip() if m_titulo else ""

        # Pergunta — tudo entre "Pergunta:" e a primeira "A)"
        m_perg = re.search(r"Pergunta:\s*(.*?)\n\s*A\)", bloco, re.DOTALL)
        pergunta = m_perg.group(1).strip() if m_perg else ""

        # Alternativas A/B/C/D
        alternativas: List[dict] = []
        letras = ["A", "B", "C", "D"]
        for i, letra in enumerate(letras):
            # Match: <LETRA>) <texto até newline+Justificativa>\nJustificativa: <texto até próxima letra ou fim>
            proxima = letras[i + 1] if i + 1 < len(letras) else None
            stop = rf"\n{proxima}\)" if proxima else r"\Z"
            patt = (
                rf"\n{letra}\)\s*(?P<texto>.*?)\n\s*Justificativa:\s*(?P<just>.*?)(?={stop})"
            )
            m = re.search(patt, bloco, re.DOTALL)
            if not m:
                continue
            texto = re.sub(r"\s+", " ", m.group("texto")).strip()
            just = re.sub(r"\s+", " ", m.group("just")).strip()
            correta = bool(re.match(r"^Correta\b", just, re.IGNORECASE))
            alternativas.append({
                "letra": letra,
                "texto": texto,
                "justificativa": just,
                "correta": correta,
            })

        if titulo and pergunta and len(alternativas) >= 2:
            # Sanity: deve ter exatamente 1 correta
            n_corretas = sum(1 for a in alternativas if a["correta"])
            exercicios.append({
                "titulo": titulo,
                "pergunta": pergunta,
                "alternativas": alternativas,
                "n_corretas": n_corretas,
            })
    return exercicios


def _adicionar_e_preencher_alternativa(
    page: Page,
    indice: int,
    texto_alt: str,
    justificativa: str,
    correta: bool,
) -> None:
    """Clica 'Adicionar alternativa' (variante single-choice) e preenche os campos
    `alternatives[indice].text`, `alternatives[indice].opinion` (ambos EasyMDE) e
    o radio `alternatives[indice].correct` se aplicável."""
    # 1) Clicar 'Adicionar alternativa' (variante single)
    # O HTML mostra: <input type="button" class="add-alternative" data-type="emptySingleAlternative" value="Adicionar alternativa">
    candidatos_btn = [
        "input.add-alternative[data-type='emptySingleAlternative']",
        "input[type='button'][value='Adicionar alternativa']",
        "input.add-alternative",
    ]
    clicked = False
    for sel in candidatos_btn:
        try:
            page.locator(sel).first.click(timeout=5_000)
            clicked = True
            break
        except PWTimeoutError:
            continue
        except Exception:
            continue
    if not clicked:
        raise RuntimeError("Botão 'Adicionar alternativa' não encontrado.")

    # Espera DOM atualizar (CodeMirror dos novos textareas é montado por JS)
    time.sleep(0.8)

    # 2) Preencher via JS usando os names HTML conhecidos
    js_preencher = """
    (args) => {
        const { idx, textoAlt, justificativa, correta } = args;
        const setMD = (taName, value) => {
            const ta = document.querySelector(`textarea[name="${taName}"]`);
            if (!ta) return { ok: false, reason: 'textarea ' + taName + ' not found' };
            // Procura CodeMirror associado (ancestor com .CodeMirror dentro)
            let parent = ta.parentElement;
            for (let i = 0; i < 6 && parent; i++) {
                const cm = parent.querySelector('.CodeMirror');
                if (cm && cm.CodeMirror) {
                    cm.CodeMirror.setValue(value);
                    if (typeof cm.CodeMirror.save === 'function') cm.CodeMirror.save();
                    return { ok: true, via: 'codemirror' };
                }
                parent = parent.parentElement;
            }
            // fallback
            ta.value = value;
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.dispatchEvent(new Event('change', { bubbles: true }));
            return { ok: true, via: 'textarea-fallback' };
        };
        const resTexto = setMD(`alternatives[${idx}].text`, textoAlt);
        const resOpinion = setMD(`alternatives[${idx}].opinion`, justificativa);

        let okRadio = !correta;
        let radioInfo = null;
        if (correta) {
            const radio = document.querySelector(`input[type="radio"][name="alternatives[${idx}].correct"]`);
            if (radio) {
                radio.click();
                okRadio = true;
                radioInfo = 'clicked';
            } else {
                radioInfo = 'radio not found';
            }
        }
        return {
            ok: resTexto.ok && resOpinion.ok && okRadio,
            resTexto, resOpinion, okRadio, radioInfo,
        };
    }
    """
    res = page.evaluate(js_preencher, {
        "idx": indice,
        "textoAlt": texto_alt,
        "justificativa": justificativa,
        "correta": bool(correta),
    })
    if not res or not res.get("ok"):
        raise RuntimeError(f"Falha ao preencher alternativa {indice}: {res}")


def _criar_atividade_unica_escolha(
    page: Page,
    course_id: int,
    section_id: int,
    exercicio: dict,
) -> str:
    """Cria 1 atividade tipo 'Única escolha' com pergunta e alternativas."""
    tasks_url = f"https://cursos.alura.com.br/admin/course/v2/{course_id}/section/{section_id}/tasks"
    print(f"  → GET {tasks_url}")
    page.goto(tasks_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=15_000)

    print(f"  → Clicando em 'Nova atividade'")
    candidatos = [
        "a:has-text('Nova atividade')",
        "button:has-text('Nova atividade')",
        "text=Nova atividade",
    ]
    clicked = False
    for sel in candidatos:
        try:
            page.click(sel, timeout=5_000)
            clicked = True
            break
        except PWTimeoutError:
            continue
    if not clicked:
        raise RuntimeError(f"Botão 'Nova atividade' não encontrado em {tasks_url}")

    page.wait_for_load_state("networkidle", timeout=20_000)
    print(f"  → Em {page.url}")

    print(f"  → Selecionando tipo SINGLE_CHOICE (subopção 'Única escolha sobre o conteúdo da aula')")
    # Dropdown hierárquico: "Única escolha" é só cabeçalho (value=""); usamos a
    # subopção concreta com data-task-enum=SINGLE_CHOICE e value!=''.
    sel_res = _selecionar_tipo_por_task_enum(page, "SINGLE_CHOICE", require_value=True)
    print(f"     ✓ '{sel_res.get('label')}' (value={sel_res.get('value')})")

    # Aguarda DOM atualizar (campos específicos de única escolha podem aparecer depois)
    page.wait_for_load_state("networkidle", timeout=10_000)
    time.sleep(1.0)

    print(f"  → Preenchendo Título: '{exercicio['titulo']}'")
    candidatos_titulo = [
        "input[name='title']",
        "input[name='name']",
        "input[id='title']",
        "input[id='name']",
        "label:has-text('Título') >> .. >> input",
    ]
    filled = False
    for sel in candidatos_titulo:
        try:
            page.wait_for_selector(sel, timeout=5_000)
            page.fill(sel, exercicio["titulo"])
            filled = True
            break
        except PWTimeoutError:
            continue
    if not filled:
        raise RuntimeError(f"Campo de título não encontrado em {page.url}")

    print(f"  → Preenchendo Enunciado (CodeMirror, {len(exercicio['pergunta'])} chars)")
    candidatos_enunciado = [
        "#text.markdownEditor",
        ".markdown-editor--wrapper",
    ]
    used_wrap = _preencher_codemirror(page, candidatos_enunciado, exercicio["pergunta"])
    print(f"     ✓ enunciado via '{used_wrap}'")

    print(f"  → Adicionando {len(exercicio['alternativas'])} alternativas")
    for i, alt in enumerate(exercicio["alternativas"]):
        marker = " ✓ correta" if alt["correta"] else ""
        print(f"     [{alt['letra']}] {alt['texto'][:60]}...{marker}")
        try:
            _adicionar_e_preencher_alternativa(
                page, i, alt["texto"], alt["justificativa"], alt["correta"]
            )
        except Exception as e:
            from pathlib import Path
            tmp = Path(__file__).resolve().parent.parent / "tmp" / "spike"
            tmp.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            html_path = tmp / f"erro_alternativa_{i}_{ts}.html"
            html_path.write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(tmp / f"erro_alternativa_{i}_{ts}.png"), full_page=True)
            raise RuntimeError(
                f"Falha ao adicionar alternativa {i}: {e}. HTML em {html_path}"
            )

    print(f"  → Clicando em 'Salvar'")
    saved = False
    for sel in ["button:has-text('Salvar')", "input[type='submit'][value*='Salvar']", "button[type='submit']"]:
        try:
            page.click(sel, timeout=5_000)
            saved = True
            break
        except PWTimeoutError:
            continue
    if not saved:
        raise RuntimeError(f"Botão 'Salvar' não encontrado em {page.url}")

    page.wait_for_load_state("networkidle", timeout=20_000)
    print(f"  ✓ Atividade '{exercicio['titulo']}' criada — URL atual: {page.url}")
    return page.url


def criar_atividades_prova_teorica(
    course_id: int,
    carreira: str,
    nivel: int,
    prova_teorica_arquivo: str = "",
    limite: int = 0,
    offset: int = 0,
    headless: bool = False,
) -> None:
    """Cria 1 atividade 'Única escolha' por EXERCÍCIO no arquivo TXT da prova teórica.
    `offset=N` pula os primeiros N (útil para retomada após criação parcial).
    `limite=0` significa todos os restantes. `limite=N` processa apenas os N primeiros."""
    from pathlib import Path
    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Defina EMAIL e PASSWORD no .env (credenciais da Alura).")

    txt_path = _resolve_prova_teorica_path(carreira, nivel, prova_teorica_arquivo)
    print(f"=== Prova teórica → atividades 'Única escolha' (curso {course_id}) ===")
    print(f"Arquivo: {txt_path}")

    txt = txt_path.read_text(encoding="utf-8")
    exercicios = _parse_prova_teorica(txt)
    print(f"Exercícios parseados: {len(exercicios)}")
    if not exercicios:
        raise RuntimeError("Nenhum exercício encontrado no TXT.")

    # Sanity check: cada exercício deveria ter exatamente 1 alternativa correta
    for i, ex in enumerate(exercicios, start=1):
        if ex["n_corretas"] != 1:
            print(f"  ⚠ EXERCÍCIO {i} ('{ex['titulo']}'): {ex['n_corretas']} corretas (esperado 1)")

    if offset and offset > 0:
        exercicios = exercicios[offset:]
        print(f"Offset aplicado: pulando os primeiros {offset}, restam {len(exercicios)}")
    if limite and limite > 0:
        exercicios = exercicios[:limite]
        print(f"Limite aplicado: processando apenas os primeiros {limite}")
    print(f"Modo: {'headless' if headless else 'headful (janela visível)'}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        print("[1/3] Login...")
        _login(page, email, password)
        print("      ✓ OK")
        print()

        print("[2/3] Descobrindo section_id de 'Prova teórica'...")
        section_id = _section_id_por_nome(page, course_id, SECAO_PROVA_TEORICA)
        print(f"      section_id = {section_id}")
        print()

        print(f"[3/3] Criando {len(exercicios)} atividades...")
        for i, ex in enumerate(exercicios, start=1):
            print(f"\n  ── Exercício {i}/{len(exercicios)}: '{ex['titulo']}' ──")
            _criar_atividade_unica_escolha(page, course_id, section_id, ex)

        # Volta para /sections como pedido
        sections_url = f"https://cursos.alura.com.br/admin/courses/v2/{course_id}/sections"
        print(f"\n  → Retornando para {sections_url}")
        page.goto(sections_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=15_000)

        print(f"\n✓ {len(exercicios)} atividades criadas.")
        browser.close()


SECAO_PROVA_PRATICA = "Prova prática"

# Subtítulos do TXT da prova prática que viram atividades (na ordem desejada).
# Match por prefixo: "1ª Etapa" casa com "## 1ª Etapa: Mapeando..." etc.
SECOES_PRATICA_ORDER = [
    "Descrição do projeto",
    "Antes de começar",
    "Preparando o ambiente",
    "1ª Etapa",
    "2ª Etapa",
    "3ª Etapa",
    "4ª Etapa",
]

# "## Matriz de cobertura" é stop-signal — corta tudo daí pra frente.
SECAO_PRATICA_STOP = "Matriz de cobertura"

# Conclusão é hardcoded — não está no TXT. Usuário ajusta o texto por carreira depois.
PRATICA_CONCLUSAO_HARDCODED = """## **Parabéns, Analista! Sua jornada continua!**

Chegamos ao fim deste **Checkpoint para o Nível 1 da Carreira de Análise de Dados**! Se você chegou até aqui, significa que dominou as ferramentas essenciais e as habilidades analíticas fundamentais para um Analista de Dados iniciante. **Parabéns!** Você transformou dados brutos em insights valiosos, resolveu problemas complexos e comunicou suas descobertas de forma eficaz. Isso é o que um verdadeiro profissional de dados faz!

### **Compartilhe seu sucesso!**

Este projeto é uma prova concreta das suas habilidades. Não guarde-o só para você! Compartilhe seu trabalho e aprendizado:

*   **GitHub:** Crie um repositório no GitHub para este projeto. Inclua seu código Python, as consultas SQL, os arquivos CSV (se permitido) e, o mais importante, um `README.md` detalhado. No `README`, explique o cenário de negócio, as perguntas que você respondeu, as ferramentas que utilizou, os desafios que enfrentou (e como os superou!), e os principais insights e recomendações que você gerou. Adicione screenshots do seu dashboard no Power BI!
*   **LinkedIn:** Publique sobre sua experiência com este projeto no LinkedIn. Descreva o que você aprendeu, os desafios superados e como você aplicou seus conhecimentos para resolver um problema de negócio. Marque as ferramentas que você utilizou (Pandas, MySQL, Power BI, Matplotlib) e use hashtags relevantes como #Alura #AnaliseDeDados #DataAnalytics #PowerBI #MySQL #Pandas #CarreiraEmDados.
*   **Portfólio:** Se você tem um portfólio online, adicione este projeto como um dos seus destaques. Ele demonstra sua capacidade de ir do dado bruto à recomendação estratégica.

### **O próximo nível da carreira**

Este projeto é apenas o começo. O mundo dos dados é vasto e cheio de oportunidades. Continue aprimorando suas habilidades e explorando novas áreas.

> Lembre-se: a curiosidade e a paixão por resolver problemas são os maiores combustíveis para um **Analista de Dados**. Continue aprendendo, continue construindo e continue crescendo!

**Sua jornada como Analista de Dados está apenas começando. Avance para o próximo nível!**"""


def _resolve_prova_pratica_path(carreira: str, nivel: int, override: str = "") -> "Path":
    from pathlib import Path
    if override:
        p = Path(override)
        if not p.exists():
            raise FileNotFoundError(f"Arquivo de prova prática não encontrado: {p}")
        return p
    base = Path(__file__).resolve().parent.parent / "output" / "cursos_checkpoint"
    slug = _slugify(carreira)
    candidato = base / f"prova_pratica_{slug}_nivel_{nivel}.txt"
    if candidato.exists():
        return candidato
    raise FileNotFoundError(
        f"Arquivo não encontrado: {candidato}. Use --prova_pratica_arquivo para indicar manualmente."
    )


def _parse_prova_pratica(txt: str) -> List[tuple]:
    """Extrai (titulo_completo, conteudo_md) para cada subtítulo `## ` listado em
    SECOES_PRATICA_ORDER. O conteúdo de cada seção vai do header listado até o
    PRÓXIMO header que esteja na lista (ou até "## Matriz de cobertura").
    Subheaders intermediários (ex.: "## Dedicação" dentro de "Antes de começar")
    permanecem dentro do conteúdo."""
    txt = txt.replace("\r\n", "\n")
    headers = []
    for m in re.finditer(r"^##\s+(.+?)\s*$", txt, re.MULTILINE):
        headers.append({"start": m.start(), "end_line": m.end(), "text": m.group(1).strip()})

    sections: List[tuple] = []
    for i, h in enumerate(headers):
        # Match por prefixo (ex.: "1ª Etapa" casa com "1ª Etapa: Mapeando...")
        if not any(h["text"].startswith(p) for p in SECOES_PRATICA_ORDER):
            continue
        # Encontra próximo header que seja LISTADO ou "Matriz de cobertura"
        end_pos = len(txt)
        for j in range(i + 1, len(headers)):
            next_text = headers[j]["text"]
            if next_text.startswith(SECAO_PRATICA_STOP):
                end_pos = headers[j]["start"]
                break
            if any(next_text.startswith(p) for p in SECOES_PRATICA_ORDER):
                end_pos = headers[j]["start"]
                break
        content = txt[h["end_line"]:end_pos].strip("\n").strip()
        sections.append((h["text"], content))
    return sections


def criar_atividades_prova_pratica(
    course_id: int,
    carreira: str,
    nivel: int,
    prova_pratica_arquivo: str = "",
    limite: int = 0,
    offset: int = 0,
    headless: bool = False,
) -> None:
    """Cria 1 atividade 'Explicação' por subtítulo da prova prática + 1 atividade
    'Conclusão' (texto hardcoded). Total esperado: 8 atividades."""
    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Defina EMAIL e PASSWORD no .env (credenciais da Alura).")

    txt_path = _resolve_prova_pratica_path(carreira, nivel, prova_pratica_arquivo)
    print(f"=== Prova prática → atividades 'Explicação' (curso {course_id}) ===")
    print(f"Arquivo: {txt_path}")

    txt = txt_path.read_text(encoding="utf-8")
    secoes_parseadas = _parse_prova_pratica(txt)
    print(f"Seções parseadas do TXT: {len(secoes_parseadas)}")

    if not secoes_parseadas:
        raise RuntimeError("Nenhuma seção encontrada no TXT da prova prática.")

    # Lista final = parseadas + Conclusão hardcoded
    todas: List[tuple] = list(secoes_parseadas) + [("Conclusão", PRATICA_CONCLUSAO_HARDCODED)]
    print(f"Total de atividades (com Conclusão): {len(todas)}")
    for i, (t, c) in enumerate(todas, start=1):
        print(f"  [{i}] '{t}' ({len(c)} chars)")

    if offset and offset > 0:
        todas = todas[offset:]
        print(f"Offset aplicado: pulando os primeiros {offset}, restam {len(todas)}")
    if limite and limite > 0:
        todas = todas[:limite]
        print(f"Limite aplicado: processando apenas os primeiros {limite}")
    print(f"Modo: {'headless' if headless else 'headful (janela visível)'}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        print("[1/3] Login...")
        _login(page, email, password)
        print("      ✓ OK")
        print()

        print(f"[2/3] Descobrindo section_id de '{SECAO_PROVA_PRATICA}'...")
        section_id = _section_id_por_nome(page, course_id, SECAO_PROVA_PRATICA)
        print(f"      section_id = {section_id}")
        print()

        print(f"[3/3] Criando {len(todas)} atividades...")
        for i, (titulo, conteudo) in enumerate(todas, start=1):
            print(f"\n  ── Atividade {i}/{len(todas)}: '{titulo}' ──")
            _criar_atividade_explicacao(page, course_id, section_id, titulo, conteudo)

        sections_url = f"https://cursos.alura.com.br/admin/courses/v2/{course_id}/sections"
        print(f"\n  → Retornando para {sections_url}")
        page.goto(sections_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=15_000)

        print(f"\n✓ {len(todas)} atividades criadas.")
        browser.close()


def marcar_prova_teorica(course_id: int, nome_secao: str = SECAO_PROVA_TEORICA, headless: bool = False) -> None:
    """Etapa avulsa: marca o checkbox 'É prova?' na seção indicada (default: 'Prova teórica').
    Use quando a seção já existe e só falta marcá-la como prova."""
    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Defina EMAIL e PASSWORD no .env (credenciais da Alura).")

    print(f"=== Marcando seção '{nome_secao}' do curso {course_id} como prova ===")
    print(f"Modo: {'headless' if headless else 'headful (janela visível)'}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        print("[1/2] Login...")
        _login(page, email, password)
        print("      ✓ OK")
        print()
        print(f"[2/2] Marcando seção '{nome_secao}' como prova...")
        _marcar_secao_como_prova(page, course_id, nome_secao)
        print(f"\n✓ Concluído.")
        browser.close()


def criar_secoes(course_id: int, secoes: List[str], headless: bool = False) -> None:
    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Defina EMAIL e PASSWORD no .env (credenciais da Alura).")

    print(f"=== Criando {len(secoes)} seções no curso {course_id} ===")
    print(f"Seções: {secoes}")
    print(f"Modo: {'headless' if headless else 'headful (janela visível)'}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        print("[1/2] Fazendo login na Alura...")
        _login(page, email, password)
        print("      ✓ Login OK")
        print()

        print(f"[2/2] Criando seções no curso {course_id}...")
        for i, nome in enumerate(secoes, start=1):
            print(f"\n  ── Seção {i}/{len(secoes)}: '{nome}' ──")
            try:
                _criar_uma_secao(page, course_id, nome)
            except Exception as e:
                print(f"  ✗ FALHA ao criar '{nome}': {type(e).__name__}: {e}")
                print(f"     URL atual: {page.url}")
                # Salva HTML para diagnóstico
                from pathlib import Path
                tmp = Path(__file__).resolve().parent.parent / "tmp" / "spike"
                tmp.mkdir(parents=True, exist_ok=True)
                html_path = tmp / f"erro_secao_{i}_{int(time.time())}.html"
                html_path.write_text(page.content(), encoding="utf-8")
                screenshot_path = tmp / f"erro_secao_{i}_{int(time.time())}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                print(f"     HTML salvo em: {html_path}")
                print(f"     Screenshot em: {screenshot_path}")
                raise

        # Marca automaticamente a seção 'Prova teórica' como prova (se ela está na lista criada)
        if SECAO_PROVA_TEORICA in secoes:
            print(f"\n  ── Pós-criação: marcando '{SECAO_PROVA_TEORICA}' como prova ──")
            _marcar_secao_como_prova(page, course_id, SECAO_PROVA_TEORICA)

        print(f"\n✓ Todas as {len(secoes)} seções foram criadas.")
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Uploader de Checkpoint no admin da Alura (Playwright).",
    )
    parser.add_argument("--curso_id", type=int, required=True, help="ID do curso de checkpoint (admin/courses/v2/<id>).")
    parser.add_argument(
        "--etapa",
        type=str,
        choices=[
            "criar_secoes",
            "marcar_prova_teorica",
            "criar_atividade_apresentacao",
            "criar_atividades_prova_teorica",
            "criar_atividades_prova_pratica",
        ],
        required=True,
        help="Etapa a executar.",
    )
    parser.add_argument(
        "--nivel",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="Nível da carreira (usado no template da Apresentação e para resolver o caminho do TXT da prova teórica). Default: 1.",
    )
    parser.add_argument(
        "--carreira",
        type=str,
        default="",
        help="Nome da carreira (ex.: 'Governança de Dados'). Usado para resolver o nome do arquivo da prova.",
    )
    parser.add_argument(
        "--prova_teorica_arquivo",
        type=str,
        default="",
        help="Caminho explícito para o TXT da prova teórica. Se omitido, monta a partir de --carreira/--nivel.",
    )
    parser.add_argument(
        "--prova_pratica_arquivo",
        type=str,
        default="",
        help="Caminho explícito para o TXT da prova prática. Se omitido, monta a partir de --carreira/--nivel.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=0,
        help="Limita a quantidade de exercícios processados (0 = todos). Útil para validação inicial.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Pula os primeiros N exercícios (útil para retomar após criação parcial).",
    )
    parser.add_argument(
        "--secoes",
        type=str,
        default="",
        help=f"Lista CSV de nomes de seções. Default: '{','.join(SECOES_PADRAO)}'",
    )
    parser.add_argument("--headless", action="store_true", help="Roda sem janela visível (default: janela visível).")
    args = parser.parse_args()

    if args.etapa == "criar_secoes":
        secoes = (
            [s.strip() for s in args.secoes.split(",") if s.strip()]
            if args.secoes
            else SECOES_PADRAO
        )
        criar_secoes(args.curso_id, secoes, headless=args.headless)
    elif args.etapa == "marcar_prova_teorica":
        marcar_prova_teorica(args.curso_id, headless=args.headless)
    elif args.etapa == "criar_atividade_apresentacao":
        criar_atividade_apresentacao(args.curso_id, nivel=args.nivel, headless=args.headless)
    elif args.etapa == "criar_atividades_prova_teorica":
        if not args.carreira and not args.prova_teorica_arquivo:
            raise SystemExit("--carreira (ou --prova_teorica_arquivo) é obrigatório para esta etapa.")
        criar_atividades_prova_teorica(
            args.curso_id,
            carreira=args.carreira,
            nivel=args.nivel,
            prova_teorica_arquivo=args.prova_teorica_arquivo,
            limite=args.limite,
            offset=args.offset,
            headless=args.headless,
        )
    elif args.etapa == "criar_atividades_prova_pratica":
        if not args.carreira and not args.prova_pratica_arquivo:
            raise SystemExit("--carreira (ou --prova_pratica_arquivo) é obrigatório para esta etapa.")
        criar_atividades_prova_pratica(
            args.curso_id,
            carreira=args.carreira,
            nivel=args.nivel,
            prova_pratica_arquivo=args.prova_pratica_arquivo,
            limite=args.limite,
            offset=args.offset,
            headless=args.headless,
        )


if __name__ == "__main__":
    main()
