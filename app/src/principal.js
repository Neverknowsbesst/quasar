// Dentro de Tauri la página se sirve desde tauri://; el motor Python escucha en localhost.
// En modo navegador (python -m quasar_motor --interfaz ...) la API está en el mismo origen.
const API = window.__TAURI_INTERNALS__ ? "http://127.0.0.1:8765" : "";

const $ = (selector, raiz = document) => raiz.querySelector(selector);
const $$ = (selector, raiz = document) => [...raiz.querySelectorAll(selector)];

async function api(ruta, { metodo = "GET", cuerpo, encabezados = {} } = {}) {
  const opciones = { method: metodo, headers: { ...encabezados } };
  if (cuerpo instanceof Blob) {
    opciones.body = cuerpo;
  } else if (cuerpo !== undefined) {
    opciones.body = JSON.stringify(cuerpo);
    opciones.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(API + ruta, opciones);
  const datos = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(datos.error || `Error ${res.status}`);
  return datos;
}

function escapar(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function notificar(mensaje, esError = false) {
  const el = $("#notificacion");
  el.textContent = mensaje;
  el.classList.toggle("error", esError);
  el.classList.add("visible");
  clearTimeout(notificar.temporizador);
  notificar.temporizador = setTimeout(() => el.classList.remove("visible"), 3200);
}

const formatearFecha = (ts) =>
  new Date(ts * 1000).toLocaleString("es-CL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

// ---------- Markdown mínimo (títulos, listas, negrita, código, citas) ----------

function enLinea(texto) {
  return escapar(texto)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([MB]\d+)\]/g, '<span class="ref" data-ref="$1">$1</span>');
}

function markdown(fuente) {
  const salida = [];
  let lista = null;
  let parrafo = [];
  const cerrarParrafo = () => {
    if (parrafo.length) salida.push(`<p>${enLinea(parrafo.join(" "))}</p>`);
    parrafo = [];
  };
  const cerrarLista = () => {
    if (lista) salida.push(`</${lista}>`);
    lista = null;
  };
  for (const cruda of fuente.split("\n")) {
    const linea = cruda.trimEnd();
    let m;
    if (!linea.trim()) {
      cerrarParrafo();
      cerrarLista();
    } else if ((m = linea.match(/^(#{1,4})\s+(.*)$/))) {
      cerrarParrafo();
      cerrarLista();
      const nivel = Math.min(Math.max(m[1].length, 2), 3);
      salida.push(`<h${nivel}>${enLinea(m[2])}</h${nivel}>`);
    } else if ((m = linea.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/))) {
      cerrarParrafo();
      const tipo = m[1] ? "ol" : "ul";
      if (lista !== tipo) {
        cerrarLista();
        salida.push(`<${tipo}>`);
        lista = tipo;
      }
      salida.push(`<li>${enLinea(m[2])}</li>`);
    } else if ((m = linea.match(/^>\s?(.*)$/))) {
      cerrarParrafo();
      cerrarLista();
      salida.push(`<blockquote>${enLinea(m[1])}</blockquote>`);
    } else {
      if (lista) cerrarLista();
      parrafo.push(linea.trim());
    }
  }
  cerrarParrafo();
  cerrarLista();
  return salida.join("");
}

// ---------- Navegación y estado del motor ----------

function mostrar(vista) {
  $$(".nav-item").forEach((b) => b.classList.toggle("activa", b.dataset.vista === vista));
  $$(".vista").forEach((v) => v.classList.toggle("activa", v.id === `vista-${vista}`));
  ({ manuales: cargarManuales, bitacora: cargarArreglos, historial: cargarHistorial, ajustes: cargarAjustes })[vista]?.();
  if (vista === "diagnostico") $("#pregunta").focus();
}
$$(".nav-item").forEach((b) => b.addEventListener("click", () => mostrar(b.dataset.vista)));

async function actualizarEstado() {
  const motor = $("#motor-estado");
  try {
    const e = await api("/api/estado");
    motor.className = `motor-estado ${e.llm_listo ? "bien" : "local"}`;
    $("#motor-texto").textContent = e.llm_listo ? `Claude · ${e.modelo}` : "Modo local (sin API key)";
    $("#contador-manuales").textContent = e.manuales || "";
    $("#contador-arreglos").textContent = e.arreglos || "";
    return true;
  } catch {
    motor.className = "motor-estado caido";
    $("#motor-texto").textContent = "Conectando con el motor…";
    return false;
  }
}

async function iniciar() {
  // El motor Python puede tardar un par de segundos en levantar.
  for (let i = 0; i < 40 && !(await actualizarEstado()); i++) await new Promise((r) => setTimeout(r, 500));
  setInterval(actualizarEstado, 15000);
}

// ---------- Diagnóstico ----------

let hiloId = null;
const hilo = $("#hilo");
const pregunta = $("#pregunta");

function bajarHilo() {
  const vista = $("#vista-diagnostico");
  vista.scrollTo({ top: vista.scrollHeight, behavior: "smooth" });
}

function dibujarRespuesta(textoPregunta, r) {
  const el = document.createElement("article");
  el.className = "respuesta";
  const fuentes = r.fuentes
    .map(
      (f) => `<div class="fuente" data-ref="${escapar(f.ref)}">
        <div class="fuente-cabeza"><span class="ref">${escapar(f.ref)}</span>
          <span class="fuente-titulo">${escapar(f.titulo)}${f.pagina ? ` · pág. ${f.pagina}` : ""}</span></div>
        <div class="fuente-extracto">${escapar(f.extracto)}</div></div>`
    )
    .join("");
  const pasos = r.pasos
    .map((p) => `<li><code>${escapar(p.herramienta)}</code> ${escapar(p.entrada.consulta ?? "")}</li>`)
    .join("");
  el.innerHTML = `
    <div class="respuesta-meta">
      <span class="etiqueta ${r.modo === "claude" ? "" : "local"}">${r.modo === "claude" ? "Claude" : "Búsqueda local"}</span>
      <span>${r.fuentes.length} fuente${r.fuentes.length === 1 ? "" : "s"}</span>
    </div>
    ${r.aviso ? `<p class="aviso">${escapar(r.aviso)}</p>` : ""}
    <div class="md">${markdown(r.respuesta)}</div>
    ${fuentes ? `<div class="fuentes">${fuentes}</div>` : ""}
    ${pasos ? `<details class="pasos"><summary>Pasos del agente (${r.pasos.length})</summary><ol>${pasos}</ol></details>` : ""}
    <div class="respuesta-acciones">
      <button class="btn fantasma chico" data-accion="registrar">Registrar arreglo en bitácora</button>
    </div>`;
  el.querySelector('[data-accion="registrar"]').addEventListener("click", () => abrirFormArreglo({ sintoma: textoPregunta }));
  // Al pasar sobre una cita, resalta su fuente.
  el.querySelectorAll(".md .ref").forEach((ref) => {
    const fuente = el.querySelector(`.fuente[data-ref="${ref.dataset.ref}"]`);
    if (!fuente) return;
    ref.title = fuente.querySelector(".fuente-titulo").textContent;
    ref.addEventListener("mouseenter", () => (fuente.style.borderColor = "var(--acento)"));
    ref.addEventListener("mouseleave", () => (fuente.style.borderColor = ""));
  });
  return el;
}

async function consultar(texto) {
  texto = texto.trim();
  if (!texto) return;
  $("#diagnostico-vacio")?.remove();
  $("#nueva-consulta").hidden = false;
  const burbuja = document.createElement("div");
  burbuja.className = "pregunta";
  burbuja.textContent = texto;
  const espera = document.createElement("div");
  espera.className = "pensando";
  espera.innerHTML = `<span class="pulso"><i></i><i></i><i></i></span> Buscando en manuales y bitácora…`;
  hilo.append(burbuja, espera);
  pregunta.value = "";
  $("#boton-diagnosticar").disabled = true;
  bajarHilo();
  try {
    const r = await api("/api/consultar", { metodo: "POST", cuerpo: { pregunta: texto, hilo_id: hiloId } });
    hiloId = r.hilo_id;
    espera.replaceWith(dibujarRespuesta(texto, r));
  } catch (err) {
    espera.remove();
    notificar(err.message, true);
  } finally {
    $("#boton-diagnosticar").disabled = false;
    bajarHilo();
    pregunta.focus();
  }
}

$("#redactor").addEventListener("submit", (e) => {
  e.preventDefault();
  consultar(pregunta.value);
});
pregunta.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    consultar(pregunta.value);
  }
});
$$(".sugerencia").forEach((s) => s.addEventListener("click", () => consultar(s.dataset.pregunta)));
$("#nueva-consulta").addEventListener("click", () => location.reload());

// ---------- Manuales ----------

async function cargarManuales() {
  const lista = $("#lista-manuales");
  const manuales = await api("/api/manuales").catch(() => []);
  if (!manuales.length) {
    lista.innerHTML = `<div class="lista-vacia">Aún no hay manuales.<br />
      <button class="btn" id="cargar-ejemplo">Cargar manual de ejemplo</button></div>`;
    $("#cargar-ejemplo").addEventListener("click", async () => {
      try {
        await api("/api/manuales/ejemplo", { metodo: "POST" });
        notificar("Manual de ejemplo cargado");
        cargarManuales();
        actualizarEstado();
      } catch (err) {
        notificar(err.message, true);
      }
    });
    return;
  }
  lista.innerHTML = manuales
    .map((m) => {
      const extension = (m.nombre.split(".").pop() || "").toUpperCase();
      return `<div class="fila">
        <div class="icono-archivo">${escapar(extension)}</div>
        <div class="fila-principal">
          <div class="fila-titulo">${escapar(m.nombre)}</div>
          <div class="fila-sub">${m.paginas} página${m.paginas === 1 ? "" : "s"} · ${m.fragmentos} fragmentos · ${formatearFecha(m.creado_en)}</div>
        </div>
        <button class="btn-icono" data-eliminar="${m.id}" title="Eliminar">Eliminar</button>
      </div>`;
    })
    .join("");
  $$("[data-eliminar]", lista).forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Eliminar este manual de la base de conocimiento?")) return;
      await api(`/api/manuales/${b.dataset.eliminar}`, { metodo: "DELETE" });
      cargarManuales();
      actualizarEstado();
    })
  );
}

async function subir(archivos) {
  for (const archivo of archivos) {
    notificar(`Indexando ${archivo.name}…`);
    try {
      const r = await api("/api/manuales", {
        metodo: "POST",
        cuerpo: archivo,
        encabezados: { "X-Nombre-Archivo": encodeURIComponent(archivo.name), "Content-Type": "application/octet-stream" },
      });
      notificar(`${r.nombre}: ${r.fragmentos} fragmentos indexados`);
    } catch (err) {
      notificar(`${archivo.name}: ${err.message}`, true);
    }
  }
  cargarManuales();
  actualizarEstado();
}

const zona = $("#zona-subida");
$("#selector-archivos").addEventListener("change", (e) => {
  subir([...e.target.files]);
  e.target.value = "";
});
zona.addEventListener("dragover", (e) => {
  e.preventDefault();
  zona.classList.add("encima");
});
zona.addEventListener("dragleave", () => zona.classList.remove("encima"));
zona.addEventListener("drop", (e) => {
  e.preventDefault();
  zona.classList.remove("encima");
  subir([...e.dataTransfer.files]);
});

// ---------- Bitácora ----------

const formArreglo = $("#form-arreglo");

function abrirFormArreglo(valores = {}) {
  mostrar("bitacora");
  formArreglo.reset();
  for (const [campo, valor] of Object.entries(valores)) formArreglo.elements[campo].value = valor;
  formArreglo.hidden = false;
  formArreglo.elements[valores.sintoma ? "solucion" : "maquina"].focus();
}

$("#alternar-arreglo").addEventListener("click", () => (formArreglo.hidden ? abrirFormArreglo() : (formArreglo.hidden = true)));
$("#cancelar-arreglo").addEventListener("click", () => (formArreglo.hidden = true));
formArreglo.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/arreglos", { metodo: "POST", cuerpo: Object.fromEntries(new FormData(formArreglo)) });
    formArreglo.hidden = true;
    notificar("Arreglo registrado en la bitácora");
    cargarArreglos();
    actualizarEstado();
  } catch (err) {
    notificar(err.message, true);
  }
});

let temporizadorBusqueda;
$("#buscar-arreglos").addEventListener("input", () => {
  clearTimeout(temporizadorBusqueda);
  temporizadorBusqueda = setTimeout(cargarArreglos, 200);
});

async function cargarArreglos() {
  const consulta = $("#buscar-arreglos").value.trim();
  const arreglos = await api(`/api/arreglos${consulta ? `?q=${encodeURIComponent(consulta)}` : ""}`).catch(() => []);
  const lista = $("#lista-arreglos");
  if (!arreglos.length) {
    lista.innerHTML = `<div class="lista-vacia">${consulta ? "Sin coincidencias." : "La bitácora está vacía. Registra el primer arreglo."}</div>`;
    return;
  }
  lista.innerHTML = arreglos
    .map(
      (a) => `<div class="arreglo">
      <div class="arreglo-cuerpo">
        <div class="arreglo-cabeza">
          <span class="arreglo-maquina">${escapar(a.maquina || "Sin máquina")}</span>
          ${a.codigo_error ? `<span class="codigo">${escapar(a.codigo_error)}</span>` : ""}
          <span class="tenue chico">${formatearFecha(a.creado_en)}${a.tecnico ? ` · ${escapar(a.tecnico)}` : ""}</span>
        </div>
        <p><span class="rotulo">Síntoma</span>${escapar(a.sintoma)}</p>
        <p><span class="rotulo">Solución</span>${escapar(a.solucion)}</p>
      </div>
      <button class="btn-icono" data-eliminar="${a.id}" title="Eliminar">Eliminar</button>
    </div>`
    )
    .join("");
  $$("[data-eliminar]", lista).forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Eliminar este registro de la bitácora?")) return;
      await api(`/api/arreglos/${b.dataset.eliminar}`, { metodo: "DELETE" });
      cargarArreglos();
      actualizarEstado();
    })
  );
}

// ---------- Historial ----------

async function cargarHistorial() {
  const consultas = await api("/api/historial").catch(() => []);
  const lista = $("#lista-historial");
  lista.innerHTML = consultas.length
    ? consultas
        .map(
          (c) => `<details class="hist">
        <summary><span class="fila-titulo">${escapar(c.pregunta)}</span>
          <span class="etiqueta ${c.modo === "claude" ? "" : "local"}">${c.modo === "claude" ? "Claude" : "Local"}</span>
          <span class="tenue chico">${formatearFecha(c.creado_en)}</span></summary>
        <div class="md">${markdown(c.respuesta)}</div>
      </details>`
        )
        .join("")
    : `<div class="lista-vacia">Todavía no hay consultas.</div>`;
}

// ---------- Ajustes ----------

const formAjustes = $("#form-ajustes");

async function cargarAjustes() {
  const a = await api("/api/ajustes").catch(() => null);
  if (!a) return;
  formAjustes.elements.modelo.value = a.modelo;
  formAjustes.elements.clave_api.value = "";
  $("#pista-clave").textContent = a.pista_clave
    ? `Clave guardada (${a.pista_clave}). Deja el campo vacío para mantenerla.`
    : "Se guarda solo en este equipo.";
}

formAjustes.addEventListener("submit", async (e) => {
  e.preventDefault();
  const cuerpo = { modelo: formAjustes.elements.modelo.value };
  const clave = formAjustes.elements.clave_api.value.trim();
  if (clave) cuerpo.clave_api = clave;
  try {
    await api("/api/ajustes", { metodo: "PUT", cuerpo });
    notificar("Ajustes guardados");
    cargarAjustes();
    actualizarEstado();
  } catch (err) {
    notificar(err.message, true);
  }
});

iniciar();
pregunta.focus();
