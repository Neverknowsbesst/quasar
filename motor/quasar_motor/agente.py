"""El motor (harness): bucle de agente con herramientas (RAG) sobre Claude u OpenAI, con modo local."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field

from .conocimiento import Conocimiento

PROVEEDORES = ("anthropic", "openai")
MODELO_POR_DEFECTO = {"anthropic": "claude-opus-5", "openai": "gpt-5-mini"}
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

# Las mismas herramientas en el formato de OpenAI (function calling).
HERRAMIENTAS_OPENAI = [
    {"type": "function", "function": {"name": h["name"], "description": h["description"], "parameters": h["input_schema"]}}
    for h in HERRAMIENTAS
]


@dataclass
class Hilo:
    id: str
    mensajes: list = field(default_factory=list)
    proveedor: str | None = None


@dataclass
class Respuesta:
    hilo_id: str
    respuesta: str
    modo: str  # "anthropic" | "openai" | "local"
    fuentes: list[dict] = field(default_factory=list)
    pasos: list[dict] = field(default_factory=list)
    aviso: str | None = None

    def a_dict(self) -> dict:
        return asdict(self)


class Ajustes:
    """Ajustes persistidos en config.json dentro del directorio de datos."""

    CLAVES = ("proveedor", "clave_api", "modelo", "clave_openai", "modelo_openai")
    VARIABLES_ENTORNO = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

    def __init__(self, ruta):
        self.ruta = ruta
        self.valores = {"proveedor": "openai"}
        if ruta.exists():
            self.valores.update(json.loads(ruta.read_text()))

    def actualizar(self, **valores) -> None:
        if valores.get("proveedor") not in (None, *PROVEEDORES):
            raise ValueError(f"Proveedor desconocido: {valores['proveedor']}")
        self.valores.update({k: v for k, v in valores.items() if k in self.CLAVES and v is not None})
        self.ruta.write_text(json.dumps(self.valores))
        try:
            os.chmod(self.ruta, 0o600)
        except OSError:
            pass

    @property
    def proveedor(self) -> str:
        proveedor = self.valores.get("proveedor")
        return proveedor if proveedor in PROVEEDORES else "openai"

    def clave_de(self, proveedor: str) -> str:
        campo = "clave_api" if proveedor == "anthropic" else "clave_openai"
        return self.valores.get(campo) or os.environ.get(self.VARIABLES_ENTORNO[proveedor], "")

    def modelo_de(self, proveedor: str) -> str:
        campo = "modelo" if proveedor == "anthropic" else "modelo_openai"
        return self.valores.get(campo) or MODELO_POR_DEFECTO[proveedor]

    @property
    def clave_api(self) -> str:
        return self.clave_de(self.proveedor)

    @property
    def modelo(self) -> str:
        return self.modelo_de(self.proveedor)

    @property
    def llm_listo(self) -> bool:
        if self.proveedor == "anthropic" and os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        return bool(self.clave_api)

    def publicos(self) -> dict:
        def pista(clave: str) -> str:
            return f"…{clave[-4:]}" if clave else ""

        return {
            "proveedor": self.proveedor,
            "modelo": self.modelo,
            "llm_listo": self.llm_listo,
            "modelo_anthropic": self.modelo_de("anthropic"),
            "modelo_openai": self.modelo_de("openai"),
            "pista_clave": pista(self.clave_de("anthropic")),
            "pista_clave_openai": pista(self.clave_de("openai")),
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
            respuesta.aviso = "Sin API key configurada: mostrando los fragmentos más relevantes. Configúrala en Ajustes."
        else:
            proveedor = self.ajustes.proveedor
            try:
                if proveedor == "openai":
                    respuesta = self._agente_openai(pregunta, hilo)
                else:
                    respuesta = self._agente_anthropic(pregunta, hilo)
            except Exception as exc:  # noqa: BLE001 - cualquier fallo del LLM degrada a modo local
                respuesta = self._local(pregunta, hilo.id)
                nombre = "OpenAI" if proveedor == "openai" else "Claude"
                respuesta.aviso = f"No se pudo consultar a {nombre} ({_describir_error(exc)}). Mostrando búsqueda local."

        self.bc.almacen.agregar_consulta(pregunta, respuesta.respuesta, respuesta.fuentes, respuesta.modo)
        return respuesta

    def _preparar_hilo(self, hilo: Hilo, proveedor: str) -> list:
        """Copia de la conversación; si cambió el proveedor, el historial anterior no es compatible."""
        if hilo.proveedor != proveedor:
            hilo.mensajes, hilo.proveedor = [], proveedor
        return list(hilo.mensajes)

    def _ejecutar_llamada(self, nombre: str, argumentos: dict, fuentes: dict, pasos: list) -> tuple[str, bool]:
        try:
            salida, es_error = self.ejecutar_herramienta(nombre, argumentos, fuentes), False
        except Exception as exc:  # noqa: BLE001 - el error vuelve al modelo como resultado de la herramienta
            salida, es_error = f"Error: {exc}", True
        pasos.append({"herramienta": nombre, "entrada": argumentos, "vista_previa": salida[:200]})
        return salida, es_error

    @staticmethod
    def _citadas(fuentes: dict, texto: str) -> list[dict]:
        return [f for ref, f in fuentes.items() if f"[{ref}]" in texto] or list(fuentes.values())

    def _agente_anthropic(self, pregunta: str, hilo: Hilo) -> Respuesta:
        import anthropic

        cliente = anthropic.Anthropic(api_key=self.ajustes.clave_api or None)
        modelo = self.ajustes.modelo
        extra = {}
        if modelo in MODELOS_CON_RESPALDO:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

        # Copia: si algo falla a mitad de camino, el hilo queda intacto.
        mensajes = self._preparar_hilo(hilo, "anthropic") + [{"role": "user", "content": pregunta}]
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
                salida, es_error = self._ejecutar_llamada(bloque.name, bloque.input, fuentes, pasos)
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
        return Respuesta(hilo.id, texto, "anthropic", self._citadas(fuentes, texto), pasos)

    def _agente_openai(self, pregunta: str, hilo: Hilo) -> Respuesta:
        import openai

        cliente = openai.OpenAI(api_key=self.ajustes.clave_de("openai"))
        modelo = self.ajustes.modelo_de("openai")

        # Copia: si algo falla a mitad de camino, el hilo queda intacto.
        mensajes = self._preparar_hilo(hilo, "openai") + [{"role": "user", "content": pregunta}]
        pasos: list[dict] = []
        fuentes: dict[str, dict] = {}
        for _ in range(MAX_VUELTAS):
            resultado = cliente.chat.completions.create(
                model=modelo,
                messages=[{"role": "system", "content": INSTRUCCIONES}, *mensajes],
                tools=HERRAMIENTAS_OPENAI,
                max_completion_tokens=16000,
            )
            eleccion = resultado.choices[0]
            mensaje = eleccion.message
            llamadas = [ll for ll in (mensaje.tool_calls or []) if ll.type == "function"]
            mensajes.append({
                "role": "assistant",
                "content": mensaje.content,
                **({"tool_calls": [
                    {"id": ll.id, "type": "function", "function": {"name": ll.function.name, "arguments": ll.function.arguments}}
                    for ll in llamadas
                ]} if llamadas else {}),
            })
            if not llamadas:
                break
            for llamada in llamadas:
                try:
                    argumentos = json.loads(llamada.function.arguments or "{}")
                except json.JSONDecodeError:
                    salida = "Error: los argumentos no son JSON válido."
                    pasos.append({"herramienta": llamada.function.name, "entrada": {}, "vista_previa": salida})
                else:
                    salida, _ = self._ejecutar_llamada(llamada.function.name, argumentos, fuentes, pasos)
                mensajes.append({"role": "tool", "tool_call_id": llamada.id, "content": salida})
        else:
            raise RuntimeError("el agente superó el máximo de pasos")

        if mensaje.refusal:
            texto = "El modelo no pudo responder esta consulta. Reformúlala describiendo la falla técnica."
        else:
            texto = (mensaje.content or "").strip()
            if eleccion.finish_reason == "length":
                texto += "\n\n_(Respuesta cortada por largo.)_"
        hilo.mensajes = mensajes
        return Respuesta(hilo.id, texto, "openai", self._citadas(fuentes, texto), pasos)

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
    """Traduce los errores de los SDK de Anthropic y OpenAI (que usan los mismos nombres)."""
    modulos = []
    for nombre in ("anthropic", "openai"):
        try:
            modulos.append(__import__(nombre))
        except ImportError:
            pass
    for sdk in modulos:
        if isinstance(exc, sdk.AuthenticationError):
            return "API key inválida"
        if isinstance(exc, sdk.RateLimitError):
            return "límite de uso o saldo agotado"
        if isinstance(exc, sdk.APIConnectionError):
            return "sin conexión"
        if isinstance(exc, sdk.APIStatusError):
            return f"error {exc.status_code}"
    if isinstance(exc, ImportError):
        return f"falta instalar el paquete {exc.name}"
    return str(exc) or type(exc).__name__
