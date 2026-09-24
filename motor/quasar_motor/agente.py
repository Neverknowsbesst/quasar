"""El motor (harness): bucle de agente con herramientas (RAG) sobre Claude, con modo local."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field

from .conocimiento import Conocimiento

MODELO_POR_DEFECTO = "claude-opus-5"
# Modelos donde activamos el respaldo del lado del servidor ante un rechazo.
MODELOS_CON_RESPALDO = {"claude-opus-5", "claude-fable-5-1"}
MAX_VUELTAS = 8

INSTRUCCIONES = """Eres Quasar, un asistente de diagnóstico para técnicos de mantenimiento industrial \
(aserraderos, minería, manufactura). Una máquina está detenida y cada minuto cuesta dinero: sé preciso y directo.

El técnico describe la falla con sus propias palabras; puede o no conocer el código de error.
Antes de responder, busca en los manuales del fabricante (buscar_manuales) y en la bitácora de arreglos \
anteriores de la planta (buscar_bitacora). Reformula la búsqueda con sinónimos o posibles códigos si la \
primera no encuentra nada útil.

Responde en español con este formato Markdown:
## Diagnóstico
La causa más probable (y alternativas si hay duda razonable).
## Procedimiento
Pasos numerados, concretos, en el orden en que se ejecutan.
## Precauciones
Seguridad (bloqueo/etiquetado, energía residual, EPP) solo si aplica.

Cita las fuentes en línea con su referencia entre corchetes, por ejemplo [M12] o [B3]. \
Si la documentación no cubre la falla, dilo claramente y sugiere qué revisar o a quién escalar; \
nunca inventes valores de torque, presiones ni códigos."""

HERRAMIENTAS = [
    {
        "name": "buscar_manuales",
        "description": (
            "Busca en los manuales técnicos cargados por la empresa (búsqueda por palabras clave, BM25). "
            "Devuelve fragmentos con su referencia [M#], manual y página. Úsala con códigos de error, "
            "síntomas, nombres de componentes o de la máquina."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "consulta": {"type": "string", "description": "Palabras clave o código de error a buscar."},
                "cantidad": {"type": "integer", "description": "Cantidad de fragmentos (1-8).", "default": 5},
            },
            "required": ["consulta"],
        },
    },
    {
        "name": "buscar_bitacora",
        "description": (
            "Busca en la bitácora de arreglos que ya hicieron los técnicos de la planta. "
            "Devuelve registros [B#] con máquina, código, síntoma y solución aplicada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"consulta": {"type": "string", "description": "Síntoma, máquina o código de error."}},
            "required": ["consulta"],
        },
    },
    {
        "name": "listar_manuales",
        "description": "Lista los manuales disponibles en la base de conocimiento.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


@dataclass
class Hilo:
    id: str
    mensajes: list = field(default_factory=list)


@dataclass
class Respuesta:
    hilo_id: str
    respuesta: str
    modo: str  # "claude" | "local"
    fuentes: list[dict] = field(default_factory=list)
    pasos: list[dict] = field(default_factory=list)
    aviso: str | None = None

    def a_dict(self) -> dict:
        return asdict(self)


class Ajustes:
    """Ajustes persistidos en config.json dentro del directorio de datos."""

    def __init__(self, ruta):
        self.ruta = ruta
        self.valores = {"clave_api": "", "modelo": MODELO_POR_DEFECTO}
        if ruta.exists():
            self.valores.update(json.loads(ruta.read_text()))

    def actualizar(self, **valores) -> None:
        self.valores.update({k: v for k, v in valores.items() if k in ("clave_api", "modelo") and v is not None})
        self.ruta.write_text(json.dumps(self.valores))
        try:
            os.chmod(self.ruta, 0o600)
        except OSError:
            pass

    @property
    def clave_api(self) -> str:
        return self.valores.get("clave_api") or os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def modelo(self) -> str:
        return self.valores.get("modelo") or MODELO_POR_DEFECTO

    @property
    def llm_listo(self) -> bool:
        return bool(self.clave_api or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def publicos(self) -> dict:
        clave = self.clave_api
        return {
            "modelo": self.modelo,
            "llm_listo": self.llm_listo,
            "pista_clave": f"…{clave[-4:]}" if clave else "",
        }


class Motor:
    def __init__(self, conocimiento: Conocimiento, ajustes: Ajustes):
        self.bc = conocimiento
        self.ajustes = ajustes
        self.hilos: dict[str, Hilo] = {}
        self._candado = threading.Lock()

    # --- Herramientas -------------------------------------------------------

    def ejecutar_herramienta(self, nombre: str, argumentos: dict, fuentes: dict) -> str:
        if nombre == "buscar_manuales":
            cantidad = max(1, min(int(argumentos.get("cantidad", 5)), 8))
            resultados = self.bc.buscar_manuales(argumentos.get("consulta", ""), cantidad)
            for r in resultados:
                fuentes[r["ref"]] = {"ref": r["ref"], "tipo": "manual", "titulo": r["manual"], "pagina": r["pagina"], "extracto": r["texto"][:280]}
            if not resultados:
                return "Sin resultados. Prueba con otras palabras clave o sinónimos."
            return "\n\n".join(
                f"[{r['ref']}] {r['manual']}" + (f" · pág. {r['pagina']}" if r["pagina"] else "") + f"\n{r['texto']}" for r in resultados
            )
        if nombre == "buscar_bitacora":
            resultados = self.bc.buscar_arreglos(argumentos.get("consulta", ""))
            for r in resultados:
                titulo = " · ".join(x for x in (r["maquina"], r["codigo_error"]) if x) or "Bitácora"
                fuentes[r["ref"]] = {"ref": r["ref"], "tipo": "arreglo", "titulo": titulo, "pagina": None, "extracto": r["solucion"][:280]}
            if not resultados:
                return "No hay arreglos similares registrados en la bitácora."
            return "\n\n".join(
                f"[{r['ref']}] Máquina: {r['maquina'] or '-'} | Código: {r['codigo_error'] or '-'} | Técnico: {r['tecnico'] or '-'}\n"
                f"Síntoma: {r['sintoma']}\nSolución aplicada: {r['solucion']}"
                for r in resultados
            )
        if nombre == "listar_manuales":
            manuales = self.bc.almacen.listar_manuales()
            return "\n".join(f"- {m['nombre']} ({m['paginas']} págs.)" for m in manuales) or "No hay manuales cargados."
        raise ValueError(f"Herramienta desconocida: {nombre}")

    # --- Bucle del agente ---------------------------------------------------

    def consultar(self, pregunta: str, hilo_id: str | None = None) -> Respuesta:
        with self._candado:
            hilo = self.hilos.get(hilo_id or "") or Hilo(id=uuid.uuid4().hex)
            self.hilos[hilo.id] = hilo

        if not self.ajustes.llm_listo:
            respuesta = self._local(pregunta, hilo.id)
            respuesta.aviso = "Sin API key de Claude: mostrando los fragmentos más relevantes. Configúrala en Ajustes."
        else:
            try:
                respuesta = self._agente(pregunta, hilo)
            except Exception as exc:  # noqa: BLE001 - cualquier fallo del LLM degrada a modo local
                respuesta = self._local(pregunta, hilo.id)
                respuesta.aviso = f"No se pudo consultar a Claude ({_describir_error(exc)}). Mostrando búsqueda local."

        self.bc.almacen.agregar_consulta(pregunta, respuesta.respuesta, respuesta.fuentes, respuesta.modo)
        return respuesta

    def _agente(self, pregunta: str, hilo: Hilo) -> Respuesta:
        import anthropic

        cliente = anthropic.Anthropic(api_key=self.ajustes.clave_api or None)
        modelo = self.ajustes.modelo
        extra = {}
        if modelo in MODELOS_CON_RESPALDO:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

        # Copia: si algo falla a mitad de camino, el hilo queda intacto.
        mensajes = hilo.mensajes + [{"role": "user", "content": pregunta}]
        pasos: list[dict] = []
        fuentes: dict[str, dict] = {}
        for _ in range(MAX_VUELTAS):
            resultado = cliente.beta.messages.create(
                model=modelo,
                max_tokens=16000,
                system=INSTRUCCIONES,
                tools=HERRAMIENTAS,
                messages=mensajes,
                **extra,
            )
            contenido = [b for b in resultado.content if b.type != "fallback"]
            mensajes.append({"role": "assistant", "content": contenido})

            if resultado.stop_reason == "pause_turn":
                continue
            if resultado.stop_reason != "tool_use":
                break

            resultados_herramientas = []
            for bloque in contenido:
                if bloque.type != "tool_use":
                    continue
                try:
                    salida, es_error = self.ejecutar_herramienta(bloque.name, bloque.input, fuentes), False
                except Exception as exc:  # noqa: BLE001 - el error vuelve al modelo como tool_result
                    salida, es_error = f"Error: {exc}", True
                pasos.append({"herramienta": bloque.name, "entrada": bloque.input, "vista_previa": salida[:200]})
                resultados_herramientas.append(
                    {"type": "tool_result", "tool_use_id": bloque.id, "content": salida, "is_error": es_error}
                )
            mensajes.append({"role": "user", "content": resultados_herramientas})
        else:
            raise RuntimeError("el agente superó el máximo de pasos")

        if resultado.stop_reason == "refusal":
            texto = "El modelo no pudo responder esta consulta. Reformúlala describiendo la falla técnica."
        else:
            texto = "\n".join(b.text for b in resultado.content if b.type == "text").strip()
        hilo.mensajes = mensajes
        citadas = [f for ref, f in fuentes.items() if f"[{ref}]" in texto] or list(fuentes.values())
        return Respuesta(hilo.id, texto, "claude", citadas, pasos)

    def _local(self, pregunta: str, hilo_id: str) -> Respuesta:
        manuales = self.bc.buscar_manuales(pregunta, 3)
        arreglos = self.bc.buscar_arreglos(pregunta, 2)
        pasos = [
            {"herramienta": "buscar_manuales", "entrada": {"consulta": pregunta}, "vista_previa": f"{len(manuales)} fragmentos"},
            {"herramienta": "buscar_bitacora", "entrada": {"consulta": pregunta}, "vista_previa": f"{len(arreglos)} registros"},
        ]
        fuentes = []
        partes = []
        if arreglos:
            partes.append("## Arreglos anteriores similares")
            for a in arreglos:
                etiqueta = " · ".join(x for x in (a["maquina"], a["codigo_error"]) if x)
                partes.append(f"- **{etiqueta or 'Registro'}** [{a['ref']}]: {a['sintoma']} → {a['solucion']}")
                fuentes.append({"ref": a["ref"], "tipo": "arreglo", "titulo": etiqueta or "Bitácora", "pagina": None, "extracto": a["solucion"][:280]})
        if manuales:
            partes.append("## Fragmentos relevantes de los manuales")
            for m in manuales:
                donde = m["manual"] + (f", pág. {m['pagina']}" if m["pagina"] else "")
                partes.append(f"**{donde}** [{m['ref']}]\n\n{m['texto']}")
                fuentes.append({"ref": m["ref"], "tipo": "manual", "titulo": m["manual"], "pagina": m["pagina"], "extracto": m["texto"][:280]})
        if not partes:
            partes.append("No se encontró nada relacionado en los manuales ni en la bitácora. Prueba con el código de error o el nombre del componente.")
        return Respuesta(hilo_id, "\n\n".join(partes), "local", fuentes, pasos)


def _describir_error(exc: Exception) -> str:
    try:
        import anthropic
    except ImportError:
        return "falta instalar el paquete anthropic"
    if isinstance(exc, anthropic.AuthenticationError):
        return "API key inválida"
    if isinstance(exc, anthropic.RateLimitError):
        return "límite de uso alcanzado"
    if isinstance(exc, anthropic.APIConnectionError):
        return "sin conexión"
    if isinstance(exc, anthropic.APIStatusError):
        return f"error {exc.status_code}"
    return str(exc) or type(exc).__name__
