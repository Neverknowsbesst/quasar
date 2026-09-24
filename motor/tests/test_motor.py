import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from quasar_motor import agente
from quasar_motor.busqueda import IndiceBM25, tokenizar
from quasar_motor.ingesta import fragmentar_texto
from quasar_motor.servidor import MANUAL_EJEMPLO, crear_manejador, crear_motor


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    m = crear_motor(tmp_path)
    m.bc.agregar_manual(MANUAL_EJEMPLO.name, MANUAL_EJEMPLO.read_bytes())
    return m


def test_tokenizar_une_codigos_de_error_y_quita_tildes():
    tokens = tokenizar("Código E-104: tensión")
    assert "e104" in tokens and "tension" in tokens and "codigo" in tokens


def test_fragmentar_respeta_el_tamano():
    texto = "\n\n".join(f"Párrafo {i} " + "palabra " * 40 for i in range(20))
    fragmentos = fragmentar_texto(texto, tamano=500, solape=80)
    assert len(fragmentos) > 3
    assert all(len(f) <= 600 for f in fragmentos)


def test_bm25_pone_primero_el_documento_que_coincide():
    indice = IndiceBM25([{"t": "aceite hidráulico caliente"}, {"t": "huincha tensión sensor"}], "t")
    (puntaje, doc), *_ = indice.buscar("tensión de la huincha")
    assert doc["t"].startswith("huincha") and puntaje > 0


def test_busqueda_encuentra_codigo_de_error_en_el_ejemplo(motor):
    resultados = motor.bc.buscar_manuales("E104")
    assert resultados and "E-104" in resultados[0]["texto"]


def test_respuesta_local_usa_manual_y_bitacora(motor):
    motor.bc.agregar_arreglo(maquina="SH-900", codigo_error="E-104", sintoma="Huincha flamea", solucion="Se cambió el sello del cilindro tensor", tecnico="Ana")
    respuesta = motor.consultar("la huincha flamea, código E-104")
    assert respuesta.modo == "local"
    assert "cilindro tensor" in respuesta.respuesta
    assert {f["tipo"] for f in respuesta.fuentes} == {"manual", "arreglo"}
    assert motor.bc.almacen.listar_consultas()[0]["pregunta"].startswith("la huincha")


def bloque(**kw):
    return SimpleNamespace(**kw)


def test_bucle_del_agente_usa_herramientas_y_cita_fuentes(motor, monkeypatch):
    """Simula a Claude: primero pide buscar_manuales, luego responde citando."""
    motor.ajustes.actualizar(proveedor="anthropic", clave_api="sk-prueba")
    llamadas = []

    class MensajesFalsos:
        def create(self, **kwargs):
            llamadas.append(kwargs)
            if len(llamadas) == 1:
                return bloque(stop_reason="tool_use", content=[
                    bloque(type="text", text="Busco."),
                    bloque(type="tool_use", id="tu_1", name="buscar_manuales", input={"consulta": "E-104 tensión"}),
                ])
            ref = kwargs["messages"][-1]["content"][0]["content"].split("]")[0].lstrip("[")
            return bloque(stop_reason="end_turn", content=[bloque(type="text", text=f"## Diagnóstico\nPresión baja [{ref}]")])

    cliente_falso = SimpleNamespace(beta=SimpleNamespace(messages=MensajesFalsos()))
    monkeypatch.setattr("anthropic.Anthropic", lambda **_: cliente_falso)

    respuesta = motor.consultar("error E-104")
    assert respuesta.modo == "anthropic"
    assert respuesta.pasos[0]["herramienta"] == "buscar_manuales"
    assert len(respuesta.fuentes) == 1 and respuesta.fuentes[0]["tipo"] == "manual"
    assert llamadas[0]["model"] == agente.MODELO_POR_DEFECTO["anthropic"]
    assert llamadas[0]["fallbacks"] == "default"
    # El hilo conserva la conversación para preguntas de seguimiento.
    assert len(motor.hilos[respuesta.hilo_id].mensajes) == 4


def llamada_herramienta(id_, nombre, argumentos):
    return bloque(id=id_, type="function", function=bloque(name=nombre, arguments=argumentos))


def test_bucle_openai_usa_herramientas_y_cita_fuentes(motor, monkeypatch):
    """Simula a OpenAI: pide buscar_manuales y buscar_bitacora a la vez, luego responde citando."""
    motor.ajustes.actualizar(proveedor="openai", clave_openai="sk-openai-prueba")
    motor.bc.agregar_arreglo(maquina="SH-900", codigo_error="E-104", sintoma="Huincha flamea", solucion="Cambio de sello", tecnico="")
    llamadas = []

    class CompletionsFalsas:
        def create(self, **kwargs):
            llamadas.append(kwargs)
            if len(llamadas) == 1:
                mensaje = bloque(content=None, refusal=None, tool_calls=[
                    llamada_herramienta("c1", "buscar_manuales", '{"consulta": "E-104"}'),
                    llamada_herramienta("c2", "buscar_bitacora", '{"consulta": "huincha flamea"}'),
                ])
                return bloque(choices=[bloque(message=mensaje, finish_reason="tool_calls")])
            refs = [m["content"].split("]")[0].lstrip("[") for m in kwargs["messages"] if m["role"] == "tool"]
            texto = "## Diagnóstico\nTensión baja " + " ".join(f"[{r}]" for r in refs)
            return bloque(choices=[bloque(message=bloque(content=texto, refusal=None, tool_calls=None), finish_reason="stop")])

    recibido = {}

    def cliente(**kwargs):
        recibido.update(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=CompletionsFalsas()))

    monkeypatch.setattr("openai.OpenAI", cliente)

    respuesta = motor.consultar("la huincha flamea con E-104")
    assert respuesta.modo == "openai" and respuesta.aviso is None
    assert recibido["api_key"] == "sk-openai-prueba"
    assert llamadas[0]["model"] == agente.MODELO_POR_DEFECTO["openai"]
    assert llamadas[0]["messages"][0]["role"] == "system"
    assert [p["herramienta"] for p in respuesta.pasos] == ["buscar_manuales", "buscar_bitacora"]
    assert {f["tipo"] for f in respuesta.fuentes} == {"manual", "arreglo"}
    # Segunda vuelta: el asistente con tool_calls, seguido de un mensaje "tool" por llamada.
    roles = [m["role"] for m in llamadas[1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "tool"]
    assert len(motor.hilos[respuesta.hilo_id].mensajes) == 5


def test_ajustes_por_proveedor(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ajustes = agente.Ajustes(tmp_path / "config.json")
    assert ajustes.proveedor == "openai"  # proveedor por defecto
    ajustes.actualizar(proveedor="anthropic", clave_api="sk-ant-1234")
    assert ajustes.llm_listo
    ajustes.actualizar(proveedor="openai")
    assert not ajustes.llm_listo  # la key de Anthropic no sirve para OpenAI
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-9999")
    publicos = agente.Ajustes(tmp_path / "config.json").publicos()
    assert publicos["llm_listo"] and publicos["pista_clave_openai"] == "…9999" and publicos["pista_clave"] == "…1234"
    with pytest.raises(ValueError):
        ajustes.actualizar(proveedor="otro")


def test_si_falla_la_ia_pasa_a_modo_local(motor, monkeypatch):
    def falla(**_):
        raise RuntimeError("sin red")

    motor.ajustes.actualizar(proveedor="anthropic", clave_api="sk-prueba")
    monkeypatch.setattr("anthropic.Anthropic", lambda **_: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=falla))))
    respuesta = motor.consultar("E-207 aceite caliente")
    assert respuesta.modo == "local" and "Claude" in respuesta.aviso and "sin red" in respuesta.aviso

    motor.ajustes.actualizar(proveedor="openai", clave_openai="sk-openai")
    monkeypatch.setattr("openai.OpenAI", lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=falla))))
    respuesta = motor.consultar("E-207 aceite caliente")
    assert respuesta.modo == "local" and "OpenAI" in respuesta.aviso
    assert motor.hilos[respuesta.hilo_id].mensajes == []


def test_api_http_de_punta_a_punta(motor):
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), crear_manejador(motor, None))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}"

    def llamar(ruta, metodo="GET", cuerpo=None, encabezados=None, crudo=None):
        datos = crudo if crudo is not None else (json.dumps(cuerpo).encode() if cuerpo is not None else None)
        pedido = urllib.request.Request(base + ruta, data=datos, method=metodo, headers=encabezados or {"Content-Type": "application/json"})
        with urllib.request.urlopen(pedido) as r:
            return json.loads(r.read())

    try:
        assert llamar("/api/estado")["manuales"] == 1
        subido = llamar("/api/manuales", "POST", crudo=b"Codigo F-12: falla de encoder en el carro.", encabezados={"X-Nombre-Archivo": "notas.txt"})
        assert subido["fragmentos"] == 1
        llamar("/api/arreglos", "POST", {"maquina": "Carro", "sintoma": "Carro no avanza", "solucion": "Cambio de encoder"})
        assert llamar("/api/arreglos?q=encoder")[0]["maquina"] == "Carro"
        r = llamar("/api/consultar", "POST", {"pregunta": "F-12 encoder"})
        assert r["modo"] == "local" and any(f["titulo"] == "notas.txt" for f in r["fuentes"])
        llamar(f"/api/manuales/{subido['id']}", "DELETE")
        assert llamar("/api/estado")["manuales"] == 1
    finally:
        servidor.shutdown()
