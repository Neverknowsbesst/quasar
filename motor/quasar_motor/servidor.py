"""API HTTP local (solo biblioteca estándar) que consume la interfaz de escritorio."""

from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .agente import Ajustes, Motor
from .almacen import Almacen, directorio_datos
from .conocimiento import Conocimiento
from .ingesta import FormatoNoSoportado

DIR_EJEMPLOS = Path(__file__).resolve().parent.parent / "ejemplos"
MANUAL_EJEMPLO = DIR_EJEMPLOS / "manual_sierra_huincha_SH-900.md"
MAX_SUBIDA = 60 * 1024 * 1024


class ErrorApi(Exception):
    def __init__(self, estado: int, mensaje: str):
        super().__init__(mensaje)
        self.estado = estado


def crear_motor(base: Path | None = None) -> Motor:
    base = base or directorio_datos()
    almacen = Almacen(base / "quasar.db")
    return Motor(Conocimiento(almacen), Ajustes(base / "config.json"))


def crear_manejador(motor: Motor, dir_interfaz: Path | None):
    bc = motor.bc

    def estado(_s, _cuerpo):
        return {**bc.almacen.totales(), **motor.ajustes.publicos()}

    def ver_ajustes(_s, _cuerpo):
        return motor.ajustes.publicos()

    def guardar_ajustes(_s, cuerpo):
        motor.ajustes.actualizar(clave_api=cuerpo.get("clave_api"), modelo=cuerpo.get("modelo"))
        return motor.ajustes.publicos()

    def listar_manuales(_s, _cuerpo):
        return bc.almacen.listar_manuales()

    def subir_manual(s, _cuerpo):
        nombre = unquote(s.headers.get("X-Nombre-Archivo", "")).strip()
        if not nombre:
            raise ErrorApi(400, "Falta el encabezado X-Nombre-Archivo.")
        largo = int(s.headers.get("Content-Length", 0))
        if largo > MAX_SUBIDA:
            raise ErrorApi(413, "El archivo supera 60 MB.")
        try:
            return bc.agregar_manual(Path(nombre).name, s.rfile.read(largo))
        except (FormatoNoSoportado, ValueError) as exc:
            raise ErrorApi(400, str(exc)) from exc

    def cargar_ejemplo(_s, _cuerpo):
        if any(m["nombre"] == MANUAL_EJEMPLO.name for m in bc.almacen.listar_manuales()):
            raise ErrorApi(409, "El manual de ejemplo ya está cargado.")
        return bc.agregar_manual(MANUAL_EJEMPLO.name, MANUAL_EJEMPLO.read_bytes())

    def eliminar_manual(_s, _cuerpo, manual_id):
        bc.eliminar_manual(int(manual_id))
        return {"ok": True}

    def listar_arreglos(s, _cuerpo):
        consulta = parse_qs(urlparse(s.path).query).get("q", [""])[0].strip()
        return bc.buscar_arreglos(consulta, 50) if consulta else bc.almacen.listar_arreglos()

    def agregar_arreglo(_s, cuerpo):
        campos = {c: str(cuerpo.get(c, "")).strip() for c in ("maquina", "codigo_error", "sintoma", "solucion", "tecnico")}
        if not campos["sintoma"] or not campos["solucion"]:
            raise ErrorApi(400, "El síntoma y la solución son obligatorios.")
        return {"id": bc.agregar_arreglo(**campos)}

    def eliminar_arreglo(_s, _cuerpo, arreglo_id):
        bc.eliminar_arreglo(int(arreglo_id))
        return {"ok": True}

    def consultar(_s, cuerpo):
        pregunta = str(cuerpo.get("pregunta", "")).strip()
        if not pregunta:
            raise ErrorApi(400, "Describe la falla para poder diagnosticarla.")
        return motor.consultar(pregunta, cuerpo.get("hilo_id")).a_dict()

    def historial(_s, _cuerpo):
        return bc.almacen.listar_consultas()

    rutas = [
        ("GET", r"/api/estado", estado),
        ("GET", r"/api/ajustes", ver_ajustes),
        ("PUT", r"/api/ajustes", guardar_ajustes),
        ("GET", r"/api/manuales", listar_manuales),
        ("POST", r"/api/manuales", subir_manual),
        ("POST", r"/api/manuales/ejemplo", cargar_ejemplo),
        ("DELETE", r"/api/manuales/(\d+)", eliminar_manual),
        ("GET", r"/api/arreglos", listar_arreglos),
        ("POST", r"/api/arreglos", agregar_arreglo),
        ("DELETE", r"/api/arreglos/(\d+)", eliminar_arreglo),
        ("POST", r"/api/consultar", consultar),
        ("GET", r"/api/historial", historial),
    ]

    class Manejador(BaseHTTPRequestHandler):
        server_version = "Quasar/0.1"

        def log_message(self, formato, *args):  # silencioso salvo errores
            pass

        def _enviar(self, codigo: int, carga: bytes, tipo: str) -> None:
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(carga)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Nombre-Archivo")
            self.end_headers()
            self.wfile.write(carga)

        def _json(self, codigo: int, datos) -> None:
            self._enviar(codigo, json.dumps(datos, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_OPTIONS(self):
            self._enviar(204, b"", "text/plain")

        def _despachar(self, metodo: str) -> None:
            ruta = urlparse(self.path).path
            if metodo == "GET" and not ruta.startswith("/api/"):
                return self._estatico(ruta)
            for metodo_ruta, patron, funcion in rutas:
                coincidencia = re.fullmatch(patron, ruta)
                if metodo_ruta == metodo and coincidencia:
                    try:
                        cuerpo = {}
                        if self.headers.get("Content-Type", "").startswith("application/json"):
                            crudo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                            cuerpo = json.loads(crudo or b"{}")
                        return self._json(200, funcion(self, cuerpo, *coincidencia.groups()))
                    except ErrorApi as exc:
                        return self._json(exc.estado, {"error": str(exc)})
                    except Exception as exc:  # noqa: BLE001
                        return self._json(500, {"error": f"Error interno: {exc}"})
            self._json(404, {"error": "Ruta no encontrada."})

        def _estatico(self, ruta: str) -> None:
            if dir_interfaz is None:
                return self._json(404, {"error": "Interfaz no disponible."})
            destino = (dir_interfaz / (ruta.lstrip("/") or "index.html")).resolve()
            if not destino.is_relative_to(dir_interfaz) or not destino.is_file():
                return self._json(404, {"error": "No encontrado."})
            tipo = mimetypes.guess_type(destino.name)[0] or "application/octet-stream"
            self._enviar(200, destino.read_bytes(), tipo)

        # Nombres exigidos por http.server
        def do_GET(self):
            self._despachar("GET")

        def do_POST(self):
            self._despachar("POST")

        def do_PUT(self):
            self._despachar("PUT")

        def do_DELETE(self):
            self._despachar("DELETE")

    return Manejador


def servir(direccion: str = "127.0.0.1", puerto: int = 8765, dir_interfaz: Path | None = None) -> None:
    motor = crear_motor()
    interfaz = dir_interfaz.resolve() if dir_interfaz else None
    servidor = ThreadingHTTPServer((direccion, puerto), crear_manejador(motor, interfaz))
    print(f"Motor Quasar escuchando en http://{direccion}:{puerto}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
