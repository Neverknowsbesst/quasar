import argparse
import os
import threading
import time
from pathlib import Path

from .servidor import servir


def principal() -> None:
    analizador = argparse.ArgumentParser(prog="quasar_motor", description="Motor de diagnóstico Quasar")
    analizador.add_argument("--direccion", default="127.0.0.1")
    analizador.add_argument("--puerto", type=int, default=8765)
    analizador.add_argument("--interfaz", type=Path, help="Sirve también la interfaz desde esta carpeta (modo navegador).")
    analizador.add_argument("--vigilar-padre", action="store_true", help="Termina si el proceso padre (la app) muere.")
    argumentos = analizador.parse_args()
    if argumentos.vigilar_padre:
        vigilar_padre()
    servir(argumentos.direccion, argumentos.puerto, argumentos.interfaz)


def vigilar_padre() -> None:
    """Si la app de escritorio se cierra de golpe, el motor no debe quedar huérfano (Linux/macOS)."""
    padre = os.getppid()

    def bucle():
        while os.getppid() == padre:
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=bucle, daemon=True).start()


if __name__ == "__main__":
    principal()
