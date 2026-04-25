"""
Utilitários internos usados pelo script de obtenção de transcrições.
Extraído do projeto SCRAPING_FORMAÇÕES (scraping/utils.py), mantendo apenas
o necessário para o fluxo de checkpoints.
"""
from __future__ import annotations
import re
import unicodedata


def _remover_emojis_e_simbolos(texto: str) -> str:
    return "".join(
        c for c in texto
        if not unicodedata.category(c).startswith("So")
        and not unicodedata.category(c).startswith("Sk")
    )


def _remover_caracteres_invisiveis(texto: str) -> str:
    invisiveis = ["​", "‌", "‍", "﻿"]
    for c in invisiveis:
        texto = texto.replace(c, "")
    return texto


def limpar_texto(texto: str) -> str:
    texto = (texto or "").strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = _remover_caracteres_invisiveis(texto)
    texto = _remover_emojis_e_simbolos(texto)
    return texto
