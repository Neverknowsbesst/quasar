"""Búsqueda BM25 en Python puro: sin dependencias ni servicios externos."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

PALABRAS_VACIAS = set(
    """a al algo ante como con cual cuando de del desde donde el ella en entre era es esta este esto
    fue ha hay la las le les lo los mas me mi muy no o para pero por que se si sin sobre su sus
    tambien te tiene un una uno unos y ya the and of to in is""".split()
)


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def tokenizar(texto: str) -> list[str]:
    texto = normalizar(texto)
    tokens = [t for t in re.findall(r"[a-z0-9]+", texto) if t not in PALABRAS_VACIAS]
    # Códigos de error como "E-104" o "F 07" también se indexan pegados: "e104".
    tokens += [re.sub(r"[\s\-_]", "", m) for m in re.findall(r"\b[a-z]{1,3}[\s\-_]?\d{2,4}\b", texto)]
    return tokens


class IndiceBM25:
    def __init__(self, documentos: list[dict], campo_texto: str, k1: float = 1.5, b: float = 0.75):
        self.documentos = documentos
        self.k1, self.b = k1, b
        self.frecuencias = [Counter(tokenizar(d[campo_texto])) for d in documentos]
        self.largos = [sum(f.values()) for f in self.frecuencias]
        self.largo_medio = (sum(self.largos) / len(documentos)) if documentos else 0
        apariciones: Counter = Counter()
        for frecuencia in self.frecuencias:
            apariciones.update(frecuencia.keys())
        n = len(documentos)
        self.idf = {t: math.log(1 + (n - a + 0.5) / (a + 0.5)) for t, a in apariciones.items()}

    def buscar(self, consulta: str, k: int = 5) -> list[tuple[float, dict]]:
        terminos = set(tokenizar(consulta))
        puntuados = []
        for documento, frecuencia, largo in zip(self.documentos, self.frecuencias, self.largos):
            puntaje = 0.0
            for t in terminos:
                if t in frecuencia:
                    f = frecuencia[t]
                    puntaje += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * largo / self.largo_medio))
            if puntaje > 0:
                puntuados.append((puntaje, documento))
        puntuados.sort(key=lambda p: p[0], reverse=True)
        return puntuados[:k]
