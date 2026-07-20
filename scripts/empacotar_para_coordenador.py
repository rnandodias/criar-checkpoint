#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
empacotar_para_coordenador.py — Etapa de handoff (pré-publicação).

Gera um pacote de revisão para o coordenador ANTES de subir o checkpoint na
plataforma (Etapa 5). NÃO usa LLM — é 100% determinístico: lê os arquivos que já
existem em output/<slug>_nivel_<n>/, monta um documento de instruções decision-ready
e empacota tudo num ZIP.

Produz, dentro da pasta do projeto:
  - instrucoes_coordenador.txt                 (folha de decisão para o coordenador)
  - revisao_coordenador_<slug>_nivel_<n>.zip   (instruções + provas + relatórios)

O documento de instruções é de LEITURA (o coordenador não devolve nada preenchido).
Explica o que há no pacote e como revisar cada prova:
  - Teórica: como revisar; a escolha das 10 que ficam ativas é feita na plataforma
    (status ativo/inativo), não neste documento.
  - Prática: etapas + aviso de não alterar as marcações do arquivo + os pontos que o
    QA deixou em aberto (extraídos do relatório), apenas para consideração.

Uso:
  python scripts/empacotar_para_coordenador.py --carreira "IA para Automação de Processos" --nivel 1
"""
from __future__ import annotations

import argparse
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

OUTPUT_BASE = Path(__file__).resolve().parent.parent / "output"

# Arquivos que entram no ZIP (nome no disco -> rótulo no índice das instruções).
ARQUIVOS_PACOTE = [
    ("prova_teorica.txt", "prova teórica (múltipla escolha)"),
    ("prova_pratica.txt", "prova prática (projeto por etapas + datasets)"),
    ("prova_teorica_relatorio.md", "relatório do QA da teórica"),
    ("prova_pratica_relatorio.md", "relatório do QA da prática"),
]


def _slugify(s: str) -> str:
    """Mesmo slug usado nos demais scripts do pipeline — mantém compat de path."""
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "geral"


def _parse_questoes_teorica(txt: str) -> list:
    """Extrai (num, curso, dificuldade, titulo) de cada EXERCÍCIO da teórica."""
    header = re.compile(
        r"^EXERC[IÍ]CIO\s+(\d+)\s+\(curso:\s*(.+?)\)\s*\[dificuldade:\s*([^\]]+)\]",
        re.MULTILINE,
    )
    matches = list(header.finditer(txt))
    questoes = []
    for i, m in enumerate(matches):
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        bloco = txt[m.end():fim]
        tit = re.search(r"^\s*T[ií]tulo:\s*(.+)$", bloco, re.MULTILINE)
        questoes.append({
            "num": m.group(1).strip(),
            "curso": m.group(2).strip(),
            "dificuldade": m.group(3).strip(),
            "titulo": tit.group(1).strip() if tit else "(sem título)",
        })
    return questoes


def _parse_etapas_pratica(txt: str) -> list:
    """Lista os títulos das etapas da prática (cabeçalhos '## Nª Etapa: ...')."""
    return re.findall(r"^##\s+(\d+ª\s+Etapa:.*)$", txt, re.MULTILINE)


def _extrair_pendencias(relatorio_md: str) -> str:
    """Extrai a seção 'Decisões pendentes' de um relatório de QA (.md).

    Captura o conteúdo a partir do primeiro cabeçalho '## ...' que contenha
    'decis' e 'pendent' (case-insensitive) até o próximo '## ' ou o fim do arquivo.
    Retorna '' se não houver seção de pendências.
    """
    if not relatorio_md:
        return ""
    linhas = relatorio_md.splitlines()
    inicio = None
    for i, ln in enumerate(linhas):
        if ln.startswith("## ") and "decis" in ln.lower() and "pendent" in ln.lower():
            inicio = i + 1
            break
    if inicio is None:
        return ""
    corpo = []
    for ln in linhas[inicio:]:
        if ln.startswith("## "):
            break
        corpo.append(ln)
    return "\n".join(corpo).strip()


def _ler(base: Path, nome: str) -> str:
    p = base / nome
    return p.read_text(encoding="utf-8") if p.exists() else ""


SEP = "=" * 64
SUB = "-" * 64


def _limpar_md(t: str) -> str:
    """Deixa um trecho de markdown legível como texto puro (Bloco de Notas):
    remove os `**` de negrito e troca marcadores de lista numerada `N.` por `-`."""
    t = t.replace("**", "")
    linhas = [re.sub(r"^(\s*)\d+\.\s+", r"\1- ", ln) for ln in t.splitlines()]
    return "\n".join(linhas)


def montar_instrucoes(carreira: str, nivel: int, base: Path) -> str:
    teorica = _ler(base, "prova_teorica.txt")
    pratica = _ler(base, "prova_pratica.txt")
    rel_pratica = _ler(base, "prova_pratica_relatorio.md")

    questoes = _parse_questoes_teorica(teorica)
    etapas = _parse_etapas_pratica(pratica)
    pend_pratica = _extrair_pendencias(rel_pratica)

    inclusos = [(n, r) for n, r in ARQUIVOS_PACOTE if (base / n).exists()]

    L = []
    L.append(SEP)
    L.append(f"CHECKPOINT PARA REVISÃO — {carreira}, Nível {nivel}")
    L.append(SEP)
    L.append("")
    L.append(f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    L.append("")
    L.append(
        "Este pacote reúne as atividades de checkpoint deste nível para a sua revisão\n"
        "antes da publicação na plataforma. É material de leitura: você não precisa\n"
        "preencher nem devolver nada por aqui."
    )
    L.append("")
    L.append(SUB)
    L.append("O QUE HÁ NESTE PACOTE")
    L.append(SUB)
    for n, r in inclusos:
        L.append(f"  - {n:<28} {r}")
    L.append("")

    # ---- Prova teórica ----
    n_q = len(questoes)
    L.append(SUB)
    L.append("COMO REVISAR A PROVA TEÓRICA")
    L.append(SUB)
    L.append(
        "- Revise o conteúdo (enunciado, alternativas e justificativas) em prova_teorica.txt.\n"
        f"- A prova tem {n_q if n_q else 'várias'} questões; na publicação o nível fica com 10 ativas.\n"
        "  Escolha as 10 melhores e deixe as demais como INATIVAS direto na plataforma — o\n"
        "  status ativo/inativo de cada questão é definido lá, não neste documento.\n"
        "- A ordem das alternativas é embaralhada pela plataforma a cada abertura da questão,\n"
        "  então a posição da resposta correta não importa."
    )
    L.append("")

    # ---- Prova prática ----
    L.append(SUB)
    L.append("COMO REVISAR A PROVA PRÁTICA")
    L.append(SUB)
    L.append(
        "- prova_pratica.txt traz o enunciado do projeto por etapas e os datasets já\n"
        "  embutidos (em blocos CSV). Revise o conteúdo normalmente."
    )
    if etapas:
        L.append("  Etapas:")
        for e in etapas:
            L.append(f"    - {e}")
    L.append(
        "- NÃO altere a estrutura/marcações do arquivo: os cabeçalhos (#, ##) e os blocos\n"
        "  **Pergunta-chave:**, **Sua missão:**, **Ferramentas:** e a matriz de cobertura\n"
        "  são pontos de corte usados na importação para a plataforma — mexer neles quebra\n"
        "  o upload. Se identificar uma correção de conteúdo, aponte-a à parte; não reescreva\n"
        "  o arquivo."
    )
    if pend_pratica:
        L.append("")
        L.append("- Pontos que o QA deixou em aberto, apenas para a sua consideração")
        L.append("  (detalhes no relatório da prática):")
        L.append("")
        L.append(_limpar_md(pend_pratica))
    L.append("")

    # ---- Relatórios ----
    L.append(SUB)
    L.append("RELATÓRIOS DE QA")
    L.append(SUB)
    L.append(
        "Os dois relatórios (.md) detalham o que a revisão automática encontrou e o que já\n"
        "foi corrigido em cada prova — servem de contexto. O que já está resolvido não\n"
        "precisa ser revisto."
    )
    L.append("")
    L.append(SEP)
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Empacota o checkpoint para revisão do coordenador (pré-publicação).")
    ap.add_argument("--carreira", required=True, help='Nome oficial da carreira, ex.: "IA para Automação de Processos".')
    ap.add_argument("--nivel", type=int, default=1, help="Nível do checkpoint (1, 2 ou 3).")
    ap.add_argument("--output_dir", default="", help="Override manual da pasta do projeto (opcional).")
    args = ap.parse_args()

    if args.output_dir:
        base = Path(args.output_dir).resolve()
    else:
        slug = _slugify(args.carreira)
        base = OUTPUT_BASE / f"{slug}_nivel_{args.nivel}"

    if not base.is_dir():
        raise SystemExit(f"[ERRO] Pasta do projeto não encontrada: {base}")

    # Provas são obrigatórias; relatórios são opcionais (mas recomendados).
    faltando = [n for n in ("prova_teorica.txt", "prova_pratica.txt") if not (base / n).exists()]
    if faltando:
        raise SystemExit(f"[ERRO] Arquivos obrigatórios ausentes em {base}: {', '.join(faltando)}")

    instrucoes = montar_instrucoes(args.carreira, args.nivel, base)
    instr_path = base / "instrucoes_coordenador.txt"
    instr_path.write_text(instrucoes, encoding="utf-8")
    print(f"[OK] Instruções geradas: {instr_path}")

    slug = _slugify(args.carreira)
    zip_path = base / f"revisao_coordenador_{slug}_nivel_{args.nivel}.zip"
    arcs = [("instrucoes_coordenador.txt", "instrucoes_coordenador.txt")]
    for nome, _rot in ARQUIVOS_PACOTE:
        if (base / nome).exists():
            arcs.append((nome, nome))

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, arcname in arcs:
            zf.write(base / nome, arcname)

    print(f"[OK] ZIP criado: {zip_path}")
    print("     Conteúdo:")
    for _, arcname in arcs:
        print(f"       - {arcname}")


if __name__ == "__main__":
    main()
