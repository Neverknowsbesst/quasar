"""Lectura de manuales (PDF, TXT, MD) y división en fragmentos para la búsqueda."""

from __future__ import annotations

import io
import re
from pathlib import PurePath

CARACTERES_FRAGMENTO = 900
CARACTERES_SOLAPE = 150
FORMATOS = (".pdf", ".txt", ".md")


class FormatoNoSoportado(ValueError):
    pass


def extraer_paginas(nombre_archivo: str, datos: bytes) -> list[tuple[int | None, str]]:
    """Devuelve [(página, texto)]. Los archivos de texto no tienen página."""
    extension = PurePath(nombre_archivo).suffix.lower()
    if extension == ".pdf":
        from pypdf import PdfReader

        lector = PdfReader(io.BytesIO(datos))
        return [(i + 1, pagina.extract_text() or "") for i, pagina in enumerate(lector.pages)]
    if extension in (".txt", ".md"):
        return [(None, datos.decode("utf-8", errors="replace"))]
    raise FormatoNoSoportado(f"Formato no soportado: {extension or nombre_archivo}. Usa {', '.join(FORMATOS)}.")


def fragmentar_texto(texto: str, tamano: int = CARACTERES_FRAGMENTO, solape: int = CARACTERES_SOLAPE) -> list[str]:
    """Divide por párrafos y agrupa hasta ~tamano caracteres, con solapamiento."""
    texto = re.sub(r"[ \t]+", " ", texto).strip()
    if not texto:
        return []
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    fragmentos: list[str] = []
    actual = ""
    for parrafo in parrafos:
        while len(parrafo) > tamano:  # párrafo gigante: cortarlo en seco
            if actual:
                fragmentos.append(actual)
                actual = ""
            fragmentos.append(parrafo[:tamano])
            parrafo = parrafo[tamano - solape :]
        if actual and len(actual) + len(parrafo) + 2 > tamano:
            fragmentos.append(actual)
            actual = actual[-solape:].split(" ", 1)[-1] + "\n\n" + parrafo
        else:
            actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    if actual:
        fragmentos.append(actual)
    return fragmentos


def fragmentar_paginas(paginas: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    return [(pagina, fragmento) for pagina, texto in paginas for fragmento in fragmentar_texto(texto)]
