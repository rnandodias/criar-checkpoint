"""
Upload de atividades de única escolha em cursos avulsos — automação Playwright.

FERRAMENTA ISOLADA. Não faz parte do pipeline de criação de checkpoints e não
importa nada dele: as funções de automação do admin foram COPIADAS de
scripts/upload_checkpoint_alura.py de propósito, para que as duas possam evoluir
(e ser removidas) de forma independente. Mexer aqui não afeta o checkpoint, e
vice-versa. Se a Alura mudar o HTML do admin, os seletores precisam ser
atualizados nos dois lugares.

O QUE FAZ
    Lê os arquivos de questões de um curso e cria as atividades de única escolha
    na seção correspondente, JÁ ATIVAS.

COMO ORGANIZAR OS ARQUIVOS
    output/cursos_avulsos/<curso_id>/<Nome da seção>.txt

    O nome do arquivo (sem extensão) precisa ser exatamente o nome da seção como
    ela aparece na plataforma — é ele que diz onde as questões vão. As seções já
    devem existir: esta ferramenta não cria seção.

    Exemplo:
        output/cursos_avulsos/6440/Fundamentos de IA.txt

FORMATO DO ARQUIVO
    O mesmo das provas teóricas:

        EXERCÍCIO 1 (curso: ...) [dificuldade: 1/5]
        Título: ...

        Pergunta: ...

        A) alternativa
        Justificativa: Correta, pois ...

        B) alternativa
        Justificativa: Incorreta, pois ...
        -------------------------------------------------------------------

    A alternativa correta é a que tem a justificativa começando por "Correta".

USO
    python cursos_avulsos/upload_atividades_avulsas.py --curso_id 6440
    python cursos_avulsos/upload_atividades_avulsas.py --curso_id 6440 --secao "Fundamentos de IA"
    python cursos_avulsos/upload_atividades_avulsas.py --curso_id 6440 --dry-run
    python cursos_avulsos/upload_atividades_avulsas.py --curso_id 6440 --indices 1,5

PRÉ-REQUISITOS
    EMAIL e PASSWORD no .env (credenciais da Alura).
"""
from __future__ import annotations
import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeoutError

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Raiz do repositório = pasta acima desta.
BASE_CURSOS = Path(__file__).resolve().parent.parent / "output" / "cursos_avulsos"


# =============================================================================
# Copiado de scripts/upload_checkpoint_alura.py — ver nota no topo do arquivo.
# =============================================================================

def _settle(page: Page, timeout: int = 15_000) -> None:
    """Espera a página assentar, sem transformar rede lenta em falha fatal.

    Tenta `networkidle` (rede parada por 500ms) e, se estourar, cai para
    `domcontentloaded` — que já terá ocorrido. O admin da Alura carrega trackers e
    analytics que às vezes nunca deixam a rede ociosa, e isso derrubava o upload no
    meio do caminho com a mensagem enganosa de "login falhou". Onde o networkidle
    funciona o comportamento é idêntico ao anterior; onde não, seguimos em frente."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PWTimeoutError:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)


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
        _settle(page, timeout=15_000)
    except PWTimeoutError as e:
        raise RuntimeError(
            "Login na Alura falhou. Verifique EMAIL/PASSWORD no .env, "
            "presença de captcha ou 2FA, ou rode com --headful para inspecionar."
        ) from e


def _section_id_por_nome(page: Page, course_id: int, nome_secao: str) -> int:
    """Vai para /sections, procura a linha da seção e extrai o section_id do href de Editar."""
    sections_url = f"https://cursos.alura.com.br/admin/courses/v2/{course_id}/sections"
    page.goto(sections_url, wait_until="domcontentloaded", timeout=60_000)
    _settle(page, timeout=15_000)

    # Casamento EXATO e recusa em caso de ambiguidade.
    #
    # A versão anterior usava has-text (substring) e query_selector (primeiro resultado).
    # Num curso que já tinha "Prova teórica (ANTIGO)", isso silenciosamente devolvia a
    # seção antiga — e o conteúdo novo foi publicado no lugar errado sem nenhum aviso.
    # Escolher sozinho entre candidatos é justamente o que não se pode fazer aqui: se há
    # mais de uma seção com esse nome, quem decide é a pessoa que está operando.
    achados = []  # (section_id, nome_exato)
    for linha in page.query_selector_all("tr:has(a[href*='/sections/']), li:has(a[href*='/sections/'])"):
        link = linha.query_selector("a[href*='/sections/']")
        if not link:
            continue
        m = re.search(r"/sections/(\d+)", link.get_attribute("href") or "")
        if not m:
            continue
        sid = int(m.group(1))
        texto = (linha.inner_text() or "").replace("\t", "\n")
        # o nome da seção é a primeira linha não numérica do bloco
        nomes = [p.strip() for p in texto.splitlines() if p.strip()]
        nome_exato = next((p for p in nomes if p and not p.isdigit()), "")
        if nome_exato == nome_secao and not any(s == sid for s, _ in achados):
            achados.append((sid, nome_exato))

    if len(achados) == 1:
        return achados[0][0]
    if not achados:
        raise RuntimeError(
            f"Nenhuma seção com o nome EXATO '{nome_secao}' em {sections_url}. "
            f"Confira o nome na plataforma — nomes parecidos não contam.")
    raise RuntimeError(
        f"{len(achados)} seções com o nome exato '{nome_secao}': "
        + ", ".join(f"id={s}" for s, _ in achados)
        + ". Renomeie ou remova as duplicadas antes de subir — o script não escolhe por você.")


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

        # Título — tolera **Título:** (markdown bold)
        m_titulo = re.search(r"(?:\*\*)?Título(?:\*\*)?:\s*(.+)", bloco)
        titulo = m_titulo.group(1).strip().strip("*").strip() if m_titulo else ""

        # Pergunta — tudo entre "Pergunta:" e a primeira "A)" (tolera **)
        m_perg = re.search(
            r"(?:\*\*)?Pergunta(?:\*\*)?:\s*(.*?)\n\s*(?:\*\*)?A\)",
            bloco, re.DOTALL,
        )
        pergunta = m_perg.group(1).strip().strip("*").strip() if m_perg else ""

        # Alternativas A/B/C/D — tolera **A)**, **Justificativa:** etc.
        alternativas: List[dict] = []
        letras = ["A", "B", "C", "D"]
        for i, letra in enumerate(letras):
            # Match: <LETRA>) <texto até newline+Justificativa>\nJustificativa: <texto até próxima letra ou fim>
            proxima = letras[i + 1] if i + 1 < len(letras) else None
            stop = rf"\n(?:\*\*)?{proxima}\)" if proxima else r"\Z"
            patt = (
                rf"\n(?:\*\*)?{letra}\)(?:\*\*)?\s*(?P<texto>.*?)\n\s*"
                rf"(?:\*\*)?Justificativa(?:\*\*)?:\s*(?P<just>.*?)(?={stop})"
            )
            m = re.search(patt, bloco, re.DOTALL)
            if not m:
                continue
            texto = re.sub(r"\s+", " ", m.group("texto")).strip().strip("*").strip()
            just = re.sub(r"\s+", " ", m.group("just")).strip().strip("*").strip()
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
    _settle(page, timeout=15_000)

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

    _settle(page, timeout=20_000)
    print(f"  → Em {page.url}")

    print(f"  → Selecionando tipo SINGLE_CHOICE (subopção 'Única escolha sobre o conteúdo da aula')")
    # Dropdown hierárquico: "Única escolha" é só cabeçalho (value=""); usamos a
    # subopção concreta com data-task-enum=SINGLE_CHOICE e value!=''.
    sel_res = _selecionar_tipo_por_task_enum(page, "SINGLE_CHOICE", require_value=True)
    print(f"     ✓ '{sel_res.get('label')}' (value={sel_res.get('value')})")

    # Aguarda DOM atualizar (campos específicos de única escolha podem aparecer depois)
    _settle(page, timeout=10_000)
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

    _settle(page, timeout=20_000)

    # Confirma que o salvamento realmente aconteceu.
    #
    # Clicar em 'Salvar' sem erro NÃO significa que a atividade foi criada: quando a
    # validação do formulário falha, a página continua em /task/create e nada é
    # gravado. A versão anterior imprimia "criada" nesse caso — um falso positivo que
    # só apareceu ao conferir a seção na plataforma e dar falta de uma questão. Como a
    # plataforma não apaga atividade, é melhor abortar alto do que seguir mentindo:
    # quem for retomar conta com o log para saber o que existe.
    if "/task/create" in page.url or "/task/edit" in page.url:
        # import local: a função já importa Path mais abaixo, o que torna o nome local
        # em todo o escopo — usá-lo antes daquele ponto levantaria UnboundLocalError.
        from pathlib import Path
        tmp = Path(__file__).resolve().parent / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        html_path = tmp / f"falha_salvar_{stamp}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(tmp / f"falha_salvar_{stamp}.png"), full_page=True)
        raise RuntimeError(
            f"A atividade '{exercicio['titulo']}' NÃO foi salva: a página continua em "
            f"{page.url} (o formulário provavelmente rejeitou algum campo). "
            f"HTML e screenshot em {tmp}. Rode o comando de novo — o que já existe "
            f"é pulado automaticamente."
        )

    print(f"  ✓ Atividade '{exercicio['titulo']}' criada — URL atual: {page.url}")
    return page.url


def _listar_tarefas_da_secao(page: Page, course_id: int, section_id: int) -> List[dict]:
    """Lista todas as tarefas (atividades) de uma seção. Retorna [{ordem, tipo, titulo, edit_url}]."""
    tasks_url = f"https://cursos.alura.com.br/admin/course/v2/{course_id}/section/{section_id}/tasks"
    page.goto(tasks_url, wait_until="domcontentloaded", timeout=60_000)
    _settle(page, timeout=15_000)
    js = """
    () => Array.from(document.querySelectorAll('a[href*="/task/edit/"]')).map(a => {
        const tr = a.closest('tr');
        const cells = tr ? Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim()) : [];
        return {
            ordem: cells[0] || '',
            tipo: cells[1] || '',
            titulo: cells[2] || '',
            edit_url: a.getAttribute('href') || '',
        };
    }).filter(t => t.edit_url)
    """
    return page.evaluate(js)


def _definir_status_tarefa(page: Page, edit_url: str, status: str) -> str:
    """Abre a página de edição da tarefa, muda Status (ACTIVE|INACTIVE) e salva.
    Retorna a URL final."""
    if status not in ("ACTIVE", "INACTIVE"):
        raise ValueError(f"Status inválido: {status}")
    full_url = edit_url if edit_url.startswith("http") else f"https://cursos.alura.com.br{edit_url}"
    page.goto(full_url, wait_until="domcontentloaded", timeout=60_000)
    _settle(page, timeout=15_000)

    candidatos_select = [
        "select[id='task.status']",
        "select[name='status']",
    ]
    selected = False
    for sel in candidatos_select:
        try:
            page.select_option(sel, value=status, timeout=5_000)
            selected = True
            break
        except PWTimeoutError:
            continue
        except Exception:
            continue
    if not selected:
        raise RuntimeError(f"Select de Status não encontrado em {full_url}")

    saved = False
    for sel in ["button:has-text('Salvar')", "input[type='submit'][value*='Salvar']", "button[type='submit']"]:
        try:
            page.click(sel, timeout=5_000)
            saved = True
            break
        except PWTimeoutError:
            continue
    if not saved:
        raise RuntimeError(f"Botão 'Salvar' não encontrado em {full_url}")
    _settle(page, timeout=15_000)
    return page.url


# =============================================================================
# Específico desta ferramenta
# =============================================================================

# Regras que o admin da Alura impõe ao salvar uma atividade. Não estão documentadas
# em lugar nenhum: cada uma foi descoberta por uma atividade recusada. Conferir aqui
# antes de abrir o navegador evita o pior cenário — parar no meio de uma seção com
# parte das questões já criadas, já que a plataforma não apaga atividade.
MIN_CHARS_PERGUNTA = 40   # "O enunciado precisa de, no mínimo, 40 caracteres."
MIN_CHARS_ALTERNATIVA = 2  # alternativa de 1 caractere é tratada como vazia


def validar_regras_plataforma(nome_secao: str, exercicios: List[dict]) -> List[str]:
    """Devolve a lista de problemas que fariam o admin recusar a atividade."""
    problemas = []
    for k, ex in enumerate(exercicios, start=1):
        onde = f"'{nome_secao}' q{k} ({ex.get('titulo', '?')[:40]})"
        pergunta = (ex.get("pergunta") or "").strip()
        if len(pergunta) < MIN_CHARS_PERGUNTA:
            problemas.append(
                f"{onde}: pergunta com {len(pergunta)} caracteres, mínimo {MIN_CHARS_PERGUNTA} "
                f"→ \"{pergunta}\"")
        alts = ex.get("alternativas") or []
        if len(alts) < 2:
            problemas.append(f"{onde}: só {len(alts)} alternativa(s)")
        for alt in alts:
            texto = (alt.get("texto") or "").strip()
            if len(texto) < MIN_CHARS_ALTERNATIVA:
                problemas.append(
                    f"{onde}: alternativa [{alt.get('letra')}] com {len(texto)} caractere(s) "
                    f"→ {texto!r} (a plataforma trata como vazia)")
        corretas = [a for a in alts if a.get("correta")]
        if len(corretas) != 1:
            problemas.append(f"{onde}: {len(corretas)} alternativas marcadas como corretas, esperado 1")
    return problemas


def descobrir_arquivos(curso_id: int, secao: str = "") -> List[Path]:
    """Arquivos de questões do curso. O nome do arquivo é o nome da seção."""
    pasta = BASE_CURSOS / str(curso_id)
    if not pasta.is_dir():
        raise RuntimeError(
            f"Pasta não encontrada: {pasta}\n"
            f"Crie-a e coloque um arquivo .txt por seção, com o nome exato da seção.")
    arquivos = sorted(p for p in pasta.glob("*.txt") if p.is_file())
    if secao:
        arquivos = [p for p in arquivos if p.stem == secao]
        if not arquivos:
            disponiveis = ", ".join(f"'{p.stem}'" for p in sorted(pasta.glob("*.txt"))) or "(nenhum)"
            raise RuntimeError(f"Nenhum arquivo para a seção '{secao}'. Disponíveis: {disponiveis}")
    if not arquivos:
        raise RuntimeError(f"Nenhum arquivo .txt em {pasta}")
    return arquivos


# Tipos de atividade que esta ferramenta pode desativar. Uma seção costuma ter vídeos e
# explicações que nada têm a ver com as questões; desativar um vídeo por engano seria um
# estrago real, então a lista é fechada em vez de "tudo o que não está no arquivo".
TIPOS_QUESTAO = ("única escolha", "unica escolha")


def _eh_questao(tipo: str) -> bool:
    return (tipo or "").strip().lower() in TIPOS_QUESTAO


def subir_curso(curso_id: int, secao: str = "", indices: str = "", limite: int = 0,
                headless: bool = False, dry_run: bool = False,
                desativar_orfas: bool = False) -> None:
    arquivos = descobrir_arquivos(curso_id, secao)

    print(f"=== Curso {curso_id} — {len(arquivos)} seção(ões) a processar ===")
    planos = []
    for arq in arquivos:
        exercicios = _parse_prova_teorica(arq.read_text(encoding="utf-8"))
        if indices:
            escolhidos = {int(i.strip()) for i in indices.split(",") if i.strip().isdigit()}
            exercicios = [e for k, e in enumerate(exercicios, start=1) if k in escolhidos]
        if limite > 0:
            exercicios = exercicios[:limite]
        planos.append((arq.stem, exercicios))
        print(f"  - '{arq.stem}': {len(exercicios)} questão(ões) em {arq.name}")

    # Validação antes de qualquer escrita: uma questão recusada no meio da execução
    # deixa a seção pela metade, e o que já subiu não pode ser desfeito.
    problemas = []
    for nome_secao, exercicios in planos:
        problemas.extend(validar_regras_plataforma(nome_secao, exercicios))
    if problemas:
        print(f"\n✗ {len(problemas)} problema(s) que a plataforma recusaria:\n")
        for p in problemas:
            print(f"  - {p}")
        print("\nCorrija os arquivos e rode de novo. Nada foi enviado.")
        raise SystemExit(1)
    print("  ✓ formato conferido — nenhuma questão seria recusada pela plataforma")

    if dry_run:
        print("\n[dry-run] Nada foi enviado. Conferência do que seria criado:")
        for nome_secao, exercicios in planos:
            print(f"\n  Seção '{nome_secao}':")
            for k, ex in enumerate(exercicios, start=1):
                correta = next((a for a in ex["alternativas"] if a["correta"]), None)
                marca = correta["letra"] if correta else "?"
                print(f"    {k:>2}. {ex['titulo'][:60]}  (correta: {marca}, "
                      f"{len(ex['alternativas'])} alternativas)")
        return

    load_dotenv()
    email, password = os.getenv("EMAIL"), os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Defina EMAIL e PASSWORD no .env (credenciais da Alura).")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_context().new_page()

        print("\n[1/2] Login...")
        _login(page, email, password)
        print("      ✓ OK")

        criadas_total = puladas_total = desativadas_total = 0
        for nome_secao, exercicios in planos:
            print(f"\n[2/2] Seção '{nome_secao}'")
            # Nome exato: se houver mais de uma seção com o mesmo nome, a função
            # levanta erro em vez de escolher — publicar na seção errada é um
            # estrago que não se desfaz, porque a plataforma não apaga atividade.
            section_id = _section_id_por_nome(page, curso_id, nome_secao)
            print(f"      section_id = {section_id}")

            # Idempotência: o que já está na seção não é recriado. A plataforma NÃO
            # apaga atividade, então uma duplicata é permanente — e rodar de novo
            # depois de acrescentar seções novas à pasta é o caminho natural de uso.
            # A conferência é contra a própria plataforma, e não contra um registro
            # local, que ficaria desatualizado se alguém criasse algo à mão.
            tarefas_secao = _listar_tarefas_da_secao(page, curso_id, section_id)
            existentes = {t["titulo"].strip() for t in tarefas_secao}
            a_criar, ja_existem = [], []
            for ex in exercicios:
                (ja_existem if ex["titulo"].strip() in existentes else a_criar).append(ex)

            puladas_total += len(ja_existem)
            if ja_existem:
                print(f"      {len(ja_existem)} já existe(m) na seção e será(ão) pulada(s):")
                for ex in ja_existem:
                    print(f"        · {ex['titulo'][:65]}")
            if a_criar:
                print(f"      {len(a_criar)} a criar.")
                for k, ex in enumerate(a_criar, start=1):
                    print(f"\n  ── Questão {k}/{len(a_criar)}: {ex['titulo'][:60]} ──")
                    _criar_atividade_unica_escolha(page, curso_id, section_id, ex)
                    criadas_total += 1
            else:
                print("      nada a criar nesta seção.")

            # O arquivo passa a ser a fonte da verdade: questão que está na seção mas
            # não no arquivo vira INACTIVE. Só questões — vídeos e explicações da seção
            # nunca entram nesta conta. Desativar é reversível; criar não é, e apagar
            # não existe na plataforma. Por isso esta é a forma de "remover" uma questão.
            if desativar_orfas:
                titulos_arquivo = {ex["titulo"].strip() for ex in exercicios}
                orfas = [t for t in tarefas_secao
                         if _eh_questao(t.get("tipo")) and t["titulo"].strip() not in titulos_arquivo]
                if orfas:
                    print(f"\n      {len(orfas)} questão(ões) fora do arquivo → INACTIVE:")
                    for t in orfas:
                        print(f"        · {t['titulo'][:65]}")
                        _definir_status_tarefa(page, t["edit_url"], "INACTIVE")
                        desativadas_total += 1
                else:
                    print("      nenhuma questão órfã nesta seção.")

        print(f"\n✓ {criadas_total} atividade(s) criada(s) e ATIVAS.")
        if puladas_total:
            print(f"  {puladas_total} pulada(s) por já existirem.")
        if desativadas_total:
            print(f"  {desativadas_total} questão(ões) antiga(s) passada(s) para INACTIVE.")
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cria atividades de única escolha em cursos avulsos (ferramenta isolada).")
    ap.add_argument("--curso_id", type=int, required=True,
                    help="ID do curso no admin (/admin/courses/v2/<id>).")
    ap.add_argument("--secao", default="",
                    help="Processa apenas a seção com este nome. Omitido: todas as da pasta.")
    ap.add_argument("--indices", default="",
                    help="Lista CSV 1-based de questões a criar (ex.: '1,5,7'). Útil para retomar.")
    ap.add_argument("--limite", type=int, default=0,
                    help="Cria no máximo N questões por seção (0 = todas).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Só lê e mostra o que seria criado, sem abrir o navegador.")
    ap.add_argument("--headless", action="store_true",
                    help="Roda sem janela visível (default: janela visível).")
    ap.add_argument("--desativar-orfas", dest="desativar_orfas", action="store_true",
                    help="Passa para INACTIVE as questões de única escolha que estão na seção "
                         "mas não constam do arquivo. Só afeta questões — vídeos e explicações "
                         "nunca são tocados. Use quando o arquivo substitui o conteúdo anterior.")
    args = ap.parse_args()

    subir_curso(args.curso_id, args.secao, args.indices, args.limite,
                args.headless, args.dry_run, args.desativar_orfas)


if __name__ == "__main__":
    main()
