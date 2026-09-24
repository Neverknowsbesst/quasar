"""Base de conocimiento: une el almacenamiento con los índices de búsqueda."""

from __future__ import annotations

import threading

from .almacen import Almacen
from .busqueda import IndiceBM25
from .ingesta import extraer_paginas, fragmentar_paginas

CAMPOS_ARREGLO = ("id", "maquina", "codigo_error", "sintoma", "solucion", "tecnico", "creado_en")


def texto_arreglo(arreglo: dict) -> str:
    return " ".join(arreglo[campo] for campo in ("maquina", "codigo_error", "sintoma", "solucion"))


class Conocimiento:
    def __init__(self, almacen: Almacen):
        self.almacen = almacen
        self._candado = threading.Lock()
        self.reconstruir()

    def reconstruir(self) -> None:
        manuales = IndiceBM25(self.almacen.todos_los_fragmentos(), "texto")
        arreglos = self.almacen.listar_arreglos()
        for arreglo in arreglos:
            arreglo["texto_busqueda"] = texto_arreglo(arreglo)
        with self._candado:
            self._manuales = manuales
            self._arreglos = IndiceBM25(arreglos, "texto_busqueda")

    def agregar_manual(self, nombre_archivo: str, datos: bytes) -> dict:
        fragmentos = fragmentar_paginas(extraer_paginas(nombre_archivo, datos))
        if not fragmentos:
            raise ValueError("No se pudo extraer texto del archivo (¿es un PDF escaneado?).")
        manual_id = self.almacen.agregar_manual(nombre_archivo, fragmentos)
        self.reconstruir()
        return {"id": manual_id, "nombre": nombre_archivo, "fragmentos": len(fragmentos)}

    def eliminar_manual(self, manual_id: int) -> None:
        self.almacen.eliminar_manual(manual_id)
        self.reconstruir()

    def agregar_arreglo(self, **campos) -> int:
        arreglo_id = self.almacen.agregar_arreglo(**campos)
        self.reconstruir()
        return arreglo_id

    def eliminar_arreglo(self, arreglo_id: int) -> None:
        self.almacen.eliminar_arreglo(arreglo_id)
        self.reconstruir()

    def buscar_manuales(self, consulta: str, k: int = 5) -> list[dict]:
        with self._candado:
            resultados = self._manuales.buscar(consulta, k)
        return [
            {"ref": f"M{doc['id']}", "manual": doc["manual"], "pagina": doc["pagina"], "texto": doc["texto"], "puntaje": round(puntaje, 2)}
            for puntaje, doc in resultados
        ]

    def buscar_arreglos(self, consulta: str, k: int = 3) -> list[dict]:
        with self._candado:
            resultados = self._arreglos.buscar(consulta, k)
        return [{"ref": f"B{doc['id']}", **{campo: doc[campo] for campo in CAMPOS_ARREGLO}} for _, doc in resultados]
