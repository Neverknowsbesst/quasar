"""Persistencia local en SQLite: manuales, fragmentos, bitácora de arreglos y consultas."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS manuales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    paginas INTEGER NOT NULL DEFAULT 0,
    creado_en REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fragmentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_id INTEGER NOT NULL REFERENCES manuales(id) ON DELETE CASCADE,
    pagina INTEGER,
    texto TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arreglos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina TEXT NOT NULL DEFAULT '',
    codigo_error TEXT NOT NULL DEFAULT '',
    sintoma TEXT NOT NULL,
    solucion TEXT NOT NULL,
    tecnico TEXT NOT NULL DEFAULT '',
    creado_en REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS consultas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pregunta TEXT NOT NULL,
    respuesta TEXT NOT NULL,
    fuentes TEXT NOT NULL DEFAULT '[]',
    modo TEXT NOT NULL,
    creado_en REAL NOT NULL
);
"""


def directorio_datos() -> Path:
    ruta = Path(os.environ.get("QUASAR_DIR_DATOS") or Path.home() / ".quasar")
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


class Almacen:
    def __init__(self, ruta: Path | str):
        self._candado = threading.Lock()
        self._bd = sqlite3.connect(str(ruta), check_same_thread=False)
        self._bd.row_factory = sqlite3.Row
        self._bd.execute("PRAGMA foreign_keys = ON")
        self._bd.executescript(ESQUEMA)
        self._bd.commit()

    def _consultar(self, sql: str, parametros: tuple = ()) -> list[dict]:
        with self._candado:
            return [dict(fila) for fila in self._bd.execute(sql, parametros).fetchall()]

    def _escribir(self, sql: str, parametros: tuple = ()) -> int:
        with self._candado:
            cursor = self._bd.execute(sql, parametros)
            self._bd.commit()
            return cursor.lastrowid

    # --- Manuales -----------------------------------------------------------

    def agregar_manual(self, nombre: str, fragmentos: list[tuple[int | None, str]]) -> int:
        with self._candado:
            cursor = self._bd.execute(
                "INSERT INTO manuales (nombre, paginas, creado_en) VALUES (?, ?, ?)",
                (nombre, len({pagina for pagina, _ in fragmentos}), time.time()),
            )
            manual_id = cursor.lastrowid
            self._bd.executemany(
                "INSERT INTO fragmentos (manual_id, pagina, texto) VALUES (?, ?, ?)",
                [(manual_id, pagina, texto) for pagina, texto in fragmentos],
            )
            self._bd.commit()
            return manual_id

    def listar_manuales(self) -> list[dict]:
        return self._consultar(
            """SELECT m.id, m.nombre, m.paginas, m.creado_en, COUNT(f.id) AS fragmentos
               FROM manuales m LEFT JOIN fragmentos f ON f.manual_id = m.id
               GROUP BY m.id ORDER BY m.creado_en DESC"""
        )

    def eliminar_manual(self, manual_id: int) -> None:
        self._escribir("DELETE FROM manuales WHERE id = ?", (manual_id,))

    def todos_los_fragmentos(self) -> list[dict]:
        return self._consultar(
            """SELECT f.id, f.pagina, f.texto, m.nombre AS manual
               FROM fragmentos f JOIN manuales m ON m.id = f.manual_id"""
        )

    # --- Bitácora -----------------------------------------------------------

    def agregar_arreglo(self, maquina: str, codigo_error: str, sintoma: str, solucion: str, tecnico: str) -> int:
        return self._escribir(
            """INSERT INTO arreglos (maquina, codigo_error, sintoma, solucion, tecnico, creado_en)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (maquina, codigo_error, sintoma, solucion, tecnico, time.time()),
        )

    def listar_arreglos(self) -> list[dict]:
        return self._consultar("SELECT * FROM arreglos ORDER BY creado_en DESC")

    def eliminar_arreglo(self, arreglo_id: int) -> None:
        self._escribir("DELETE FROM arreglos WHERE id = ?", (arreglo_id,))

    # --- Consultas ----------------------------------------------------------

    def agregar_consulta(self, pregunta: str, respuesta: str, fuentes: list[dict], modo: str) -> int:
        return self._escribir(
            "INSERT INTO consultas (pregunta, respuesta, fuentes, modo, creado_en) VALUES (?, ?, ?, ?, ?)",
            (pregunta, respuesta, json.dumps(fuentes, ensure_ascii=False), modo, time.time()),
        )

    def listar_consultas(self, limite: int = 30) -> list[dict]:
        filas = self._consultar("SELECT * FROM consultas ORDER BY creado_en DESC LIMIT ?", (limite,))
        for fila in filas:
            fila["fuentes"] = json.loads(fila["fuentes"])
        return filas

    def totales(self) -> dict:
        (fila,) = self._consultar(
            """SELECT (SELECT COUNT(*) FROM manuales) AS manuales,
                      (SELECT COUNT(*) FROM fragmentos) AS fragmentos,
                      (SELECT COUNT(*) FROM arreglos) AS arreglos"""
        )
        return fila
