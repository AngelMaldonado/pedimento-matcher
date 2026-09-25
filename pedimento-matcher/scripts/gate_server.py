#!/usr/bin/env python3
"""
Paso 8 (interfaz web) — servidor local minimo (solo libreria estandar de
Python, cero dependencias externas -- corre en una maquina sin dev tools)
que presenta las preguntas de `gate_definitions.construir_preguntas` como un
stepper visual: una pregunta a la vez, con la evidencia real (crop de
Factura, crop de Proforma, fotos de Previo) al lado de las opciones.

No decide nada por si solo -- al terminar la ultima pregunta escribe
`gates/respuestas_vN.json` (mismo esquema que si se hubiera llenado a mano)
y se apaga. `apply_gates.py` sigue siendo el que aplica esas respuestas;
este script solo reemplaza la recoleccion manual via AskUserQuestion por
una pagina web.

Uso:
    python3 gate_server.py <directorio> [--port 8765] [--no-browser]

Lee:
    matching/match_vN.json, diff/diff_vN.json (Paso 6/7)
    facturas/invoice_vN/{invoice_vN.json,crops/*}
    proformas/proforma_vN/{proforma_vN.json,crops/*}
    previo/merged.json, previo/fotos_vN/previo_vN.json

Escribe:
    gates/respuestas_vN.json   (version siguiente a la mas alta existente)
"""
import glob
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_definitions import construir_preguntas, etiqueta_opcion  # noqa: E402
from merge_diff_gates import find_latest, find_latest_flat  # noqa: E402


def cargar_contexto(directorio):
    match_path = find_latest_flat(os.path.join(directorio, "matching", "match_v*.json"), "match")
    diff_path = find_latest_flat(os.path.join(directorio, "diff", "diff_v*.json"), "diff")
    if not match_path:
        sys.exit("No se encontro matching/match_vN.json -- corre Paso 6 primero.")
    if not diff_path:
        sys.exit("No se encontro diff/diff_vN.json -- corre Paso 7 primero.")

    invoice_json_path = find_latest(os.path.join(directorio, "facturas", "invoice_v*"), "{base}.json")
    proforma_json_path = find_latest(os.path.join(directorio, "proformas", "proforma_v*"), "{base}.json")
    merged_path = os.path.join(directorio, "previo", "merged.json")
    previo_json_path = find_latest(os.path.join(directorio, "previo", "fotos_v*"), "previo_v{v}.json")
    if not invoice_json_path or not proforma_json_path:
        sys.exit("Falta invoice_vN.json o proforma_vN.json -- corre Paso 3/5 primero.")
    if not os.path.isfile(merged_path) or not previo_json_path:
        sys.exit("Falta previo/merged.json o previo_vN.json -- corre Paso 2/4 primero.")

    matching = json.load(open(match_path, encoding="utf-8"))
    diff = json.load(open(diff_path, encoding="utf-8"))
    invoice_data = json.load(open(invoice_json_path, encoding="utf-8"))
    proforma_data = json.load(open(proforma_json_path, encoding="utf-8"))
    merged = json.load(open(merged_path, encoding="utf-8"))
    previo_data = json.load(open(previo_json_path, encoding="utf-8"))

    return {
        "matching": matching,
        "diff": diff,
        "invoice_dir": os.path.dirname(invoice_json_path),
        "invoice_by_partida": {p["partida"]: p for p in invoice_data["partidas"]},
        "proforma_dir": os.path.dirname(proforma_json_path),
        "proforma_by_seccion": {s["seccion"]: s for s in proforma_data["secciones"]},
        "partida_to_seccion": {p["partida"]: p["seccion"] for p in matching["pares"]},
        "merged": merged,
        "previo_by_partida": {p["partida"]: p for p in previo_data["partidas"]},
    }


def siguiente_version_respuestas(directorio):
    existentes = glob.glob(os.path.join(directorio, "gates", "respuestas_v*.json"))
    versiones = []
    for f in existentes:
        m = re.match(r".*respuestas_v(\d+)\.json$", f)
        if m:
            versiones.append(int(m.group(1)))
    return (max(versiones) + 1) if versiones else 1


def campo_evidencia_para_ui(ctx, alcance):
    """Arma, por partida, las URLs de media que la UI necesita: crop de
    Factura, crop de Proforma (via el mapeo Partida->Seccion de Paso 6) y el
    numero de fotos de Previo (la UI pide cada foto por indice a /media/previo)."""
    partidas = alcance.get("partidas_afectadas") or ([alcance["partida"]] if "partida" in alcance else [])
    out = {}
    for partida in partidas:
        seccion = alcance.get("seccion") or ctx["partida_to_seccion"].get(partida)
        previo = ctx["previo_by_partida"].get(partida, {})
        merged_entry = ctx["merged"]["partidas"].get(f"P{partida}", {})
        out[str(partida)] = {
            "partida": partida,
            "seccion": seccion,
            "tiene_crop_factura": partida in ctx["invoice_by_partida"],
            "tiene_crop_proforma": seccion in ctx["proforma_by_seccion"] if seccion else False,
            "n_fotos_previo": len(merged_entry.get("fotos", [])),
            "previo_checklist": {
                k: previo.get(k)
                for k in ("np", "marca", "modelo", "codigo_producto", "lote", "numero_serie", "pais_origen", "cantidad")
                if k in previo
            },
            "previo_notas": previo.get("notas_calidad", []),
        }
    return out


def construir_preguntas_ui(ctx):
    preguntas = construir_preguntas(ctx["matching"], ctx["diff"])
    out = []
    for preg in preguntas:
        out.append(
            {
                "clave": preg["clave"],
                "tipo": preg["tipo"],
                "pregunta": preg["pregunta"],
                "recomendada": preg["recomendada"],
                "opciones": [{"clave": k, "label": v} for k, v in preg["opciones"].items()],
                "alcance": preg["alcance"],
                "detalle_tabla": preg.get("detalle_tabla"),
                "evidencia": campo_evidencia_para_ui(ctx, preg["alcance"]),
            }
        )
    return out


INDEX_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Paso 8 -- Revision de gates</title>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
html, body { height: 100%; }
body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; margin: 0; background: #f4f5f7; color: #1a1a1a; font-size: 16px; display: flex; flex-direction: column; }
header { background: #101828; color: #fff; padding: 18px 28px; flex-shrink: 0; }
header h1 { margin: 0; font-size: 20px; }
.progress-bar { height: 4px; background: #263049; }
.progress-bar .fill { height: 100%; background: #4c8dff; transition: width .2s; }
.layout { flex: 1; display: flex; min-height: 0; }
.sidebar { width: 360px; flex-shrink: 0; background: #fff; border-right: 1px solid #e2e4e9; display: flex; flex-direction: column; }
.sidebar-content { flex: 1; overflow-y: auto; padding: 22px 20px; }
.sidebar-nav { flex-shrink: 0; border-top: 1px solid #e2e4e9; padding: 12px 16px; background: #fafbfd; }
.sidebar-nav .dots { display: flex; gap: 6px; justify-content: center; margin-bottom: 10px; flex-wrap: wrap; }
button.dot { width: 24px; height: 24px; border-radius: 50%; border: 1.5px solid #d5d9e0; background: #fff; font-size: 11px; font-weight: 700; display: flex; align-items: center; justify-content: center; cursor: pointer; color: #666; padding: 0; }
button.dot.done { background: #0f7a3d; border-color: #0f7a3d; color: #fff; }
button.dot.current { border-color: #4c8dff; border-width: 2px; color: #1a56db; }
.sidebar-nav .nav-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.sidebar-nav .count { font-size: 12px; color: #777; white-space: nowrap; }
button.nav-btn { font-size: 13.5px; padding: 9px 14px; border-radius: 6px; border: 1px solid #d5d9e0; background: #fff; cursor: pointer; }
button.nav-btn:hover { border-color: #4c8dff; }
button.nav-btn:disabled { opacity: .4; cursor: default; }
button.nav-btn.primary { background: #1a56db; border-color: #0f4fa1; color: #fff; }
button.nav-btn.primary:hover { background: #0f4fa1; }
.main-panel { flex: 1; overflow-y: auto; padding: 28px; }
.main-panel-inner { max-width: 820px; }
.card { background: #fff; border: 1px solid #e2e4e9; border-radius: 10px; padding: 22px 26px; margin-bottom: 18px; }
.partida-heading { font-size: 13px; font-weight: 700; color: #4c8dff; margin: 0 0 6px 0; }
.pregunta-txt { font-size: 17px; font-weight: 600; margin: 0 0 6px 0; }
.tipo-tag { display: inline-block; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; color: #555; background: #eef1f6; border-radius: 10px; padding: 2px 9px; margin-bottom: 10px; }
.opciones { display: flex; flex-direction: column; gap: 10px; margin-top: 16px; }
button.opcion { text-align: left; font-size: 14.5px; padding: 12px 14px; border-radius: 8px; border: 1.5px solid #d5d9e0; background: #fff; cursor: pointer; }
button.opcion:hover { border-color: #4c8dff; background: #f5f8ff; }
button.opcion.recomendada { border-color: #0f7a3d; }
button.opcion.elegida { border-color: #4c8dff; background: #eaf1ff; }
button.opcion .rec-badge { display: inline-block; margin-left: 6px; font-size: 10.5px; font-weight: 700; color: #0f7a3d; background: #e5f6ec; border-radius: 8px; padding: 1px 6px; }
.evidencia h3 { font-size: 18px; margin: 0 0 14px 0; padding-bottom: 10px; color: #1a1a1a; border-bottom: 1px solid #d5d9e0; }
h4.ev-section { font-size: 12.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: #555; margin: 20px 0 8px 0; }
h4.ev-section:first-of-type { margin-top: 0; }
img.ev-crop { max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; cursor: zoom-in; display: block; margin-bottom: 8px; }
.fotos-previo { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.fotos-previo img { width: 90px; height: 90px; object-fit: cover; border: 1px solid #ddd; border-radius: 4px; cursor: zoom-in; }
.checklist-mini { font-size: 13px; color: #333; margin: 10px 0; line-height: 1.8; }
.checklist-mini b { color: #000; }
.checklist-mini .sep { display: inline-block; margin: 0 10px; color: #bbb; }
.notas { font-size: 12.5px; color: #777; font-style: italic; margin-top: 10px; }
.notas li { margin-bottom: 10px; line-height: 1.6; }
table.detalle { border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 14px; background: #fff; }
table.detalle th, table.detalle td { border: 1px solid #e2e4e9; padding: 6px 9px; text-align: left; }
table.detalle th { background: #eef1f6; }
button.ev-toggle-btn { font-size: 12.5px; padding: 5px 10px; border-radius: 5px; border: 1px solid #d5d9e0; background: #fff; cursor: pointer; white-space: nowrap; }
button.ev-toggle-btn:hover { border-color: #4c8dff; background: #f5f8ff; }
tr.ev-row td { background: #fafbfd; padding: 16px 18px; }
.done-screen { text-align: center; padding: 60px 20px; }
.done-screen h2 { font-size: 22px; }
.overlay { display: none; position: fixed; inset: 0; background: rgba(10,12,20,.85); align-items: center; justify-content: center; z-index: 50; }
.overlay img { max-width: 92vw; max-height: 88vh; background: #fff; border-radius: 4px; }
.overlay .close-btn { position: absolute; top: 18px; right: 28px; color: #fff; font-size: 28px; cursor: pointer; background: none; border: none; }
</style>
</head>
<body>
<div class="progress-bar"><div class="fill" id="progressFill" style="width:0%"></div></div>
<header>
  <h1>Revision de casos que necesitan una decision humana</h1>
</header>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-content" id="sidebarContent"></div>
    <div class="sidebar-nav" id="sidebarNav"></div>
  </aside>
  <main class="main-panel" id="mainPanel"></main>
</div>

<div class="overlay" id="modal" onclick="closeModal()">
  <button class="close-btn" onclick="closeModal()">&times;</button>
  <img id="modalImg" src="" onclick="event.stopPropagation()">
</div>

<script>
var PREGUNTAS = [];
var RESPUESTAS = {};
var IDX = 0;

function openModal(src) {
  document.getElementById('modalImg').src = src;
  document.getElementById('modal').style.display = 'flex';
}
function closeModal() {
  document.getElementById('modal').style.display = 'none';
}
document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeModal(); });

function crop(tipo, id) { return '/media/' + tipo + '/' + id + '.png'; }
function fotoPrevio(partida, i) { return '/media/previo/' + partida + '/' + i; }

function renderEvidenciaPartida(ev) {
  var html = '<div class="evidencia">';
  html += '<h3>Evidencia &middot; Partida ' + ev.partida + (ev.seccion ? (' / Seccion ' + ev.seccion) : '') + '</h3>';

  if (ev.tiene_crop_factura) {
    html += '<h4 class="ev-section">Factura</h4>';
    html += '<img class="ev-crop" src="' + crop('factura', ev.partida) + '" onclick="openModal(this.src)">';
  }
  if (ev.tiene_crop_proforma) {
    html += '<h4 class="ev-section">Proforma</h4>';
    html += '<img class="ev-crop" src="' + crop('proforma', ev.seccion) + '" onclick="openModal(this.src)">';
  }
  if (ev.n_fotos_previo > 0) {
    html += '<h4 class="ev-section">Previos</h4>';
    html += '<div class="fotos-previo">';
    for (var i = 0; i < ev.n_fotos_previo; i++) {
      html += '<img src="' + fotoPrevio(ev.partida, i) + '" onclick="openModal(this.src)">';
    }
    html += '</div>';
    var ck = ev.previo_checklist || {};
    var ckTxt = Object.keys(ck).map(function(k) { return '<b>' + k + '</b>: ' + ck[k]; }).join('<span class="sep">&middot;</span>');
    if (ckTxt) html += '<div class="checklist-mini">' + ckTxt + '</div>';
    if (ev.previo_notas && ev.previo_notas.length) {
      html += '<ul class="notas">' + ev.previo_notas.map(function(n) { return '<li>' + n + '</li>'; }).join('') + '</ul>';
    }
  }
  html += '</div>';
  return html;
}

function truncar(s, n) { return s.length > n ? s.slice(0, n) + '…' : s; }

function renderSidebar() {
  var preg = PREGUNTAS[IDX];
  var contentHtml = '<span class="tipo-tag">' + preg.tipo.replace(/_/g, ' ') + '</span>';
  if (!preg.detalle_tabla && preg.alcance && preg.alcance.partida) {
    var ev = preg.evidencia[preg.alcance.partida];
    var seccion = preg.alcance.seccion || (ev && ev.seccion);
    contentHtml += '<p class="partida-heading">Partida ' + preg.alcance.partida + (seccion ? ' / Seccion ' + seccion : '') + '</p>';
  }
  contentHtml += '<p class="pregunta-txt">' + preg.pregunta + '</p>';
  contentHtml += '<div class="opciones">';
  preg.opciones.forEach(function(op) {
    var rec = op.clave === preg.recomendada;
    var elegida = RESPUESTAS[preg.clave] === op.clave;
    contentHtml += '<button class="opcion' + (rec ? ' recomendada' : '') + (elegida ? ' elegida' : '') + '" onclick="elegir(\\'' + op.clave + '\\')">' +
      op.label + (rec ? '<span class="rec-badge">Recomendado</span>' : '') + '</button>';
  });
  contentHtml += '</div>';
  document.getElementById('sidebarContent').innerHTML = contentHtml;

  var dotsHtml = PREGUNTAS.map(function(p, i) {
    var answered = RESPUESTAS.hasOwnProperty(p.clave);
    var cls = 'dot' + (i === IDX ? ' current' : '') + (answered ? ' done' : '');
    var contenido = answered
      ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>'
      : (i + 1);
    return '<button class="' + cls + '" onclick="irA(' + i + ')" title="' + truncar(p.pregunta, 60) + '">' + contenido + '</button>';
  }).join('');
  var respondidas = Object.keys(RESPUESTAS).length;
  var esUltima = IDX === PREGUNTAS.length - 1;
  var siguienteLabel = esUltima ? (respondidas === PREGUNTAS.length ? 'Finalizar' : 'Siguiente') : 'Siguiente';
  document.getElementById('sidebarNav').innerHTML =
    '<div class="dots">' + dotsHtml + '</div>' +
    '<div class="nav-row">' +
    '<button class="nav-btn" onclick="paso(-1)" ' + (IDX === 0 ? 'disabled' : '') + '>Anterior</button>' +
    '<span class="count">' + respondidas + ' / ' + PREGUNTAS.length + '</span>' +
    '<button class="nav-btn primary" onclick="paso(1)">' + siguienteLabel + '</button>' +
    '</div>';

  document.getElementById('progressFill').style.width = Math.round((respondidas / PREGUNTAS.length) * 100) + '%';
}

function renderMain() {
  var panel = document.getElementById('mainPanel');
  var preg = PREGUNTAS[IDX];
  var html = '<div class="main-panel-inner">';

  var partidas = Object.keys(preg.evidencia);
  if (preg.detalle_tabla) {
    var cols = Object.keys(preg.detalle_tabla[0]);
    html += '<table class="detalle"><thead><tr>' + cols.map(function(k) { return '<th>' + k + '</th>'; }).join('') + '<th>Evidencia</th></tr></thead><tbody>';
    preg.detalle_tabla.forEach(function(row) {
      var pid = row.partida;
      html += '<tr>' + cols.map(function(k) { return '<td>' + row[k] + '</td>'; }).join('') +
        '<td><button class="ev-toggle-btn" id="ev-btn-' + pid + '" onclick="toggleEvidenciaFila(' + pid + ')">Abrir evidencia</button></td></tr>';
      html += '<tr class="ev-row" id="ev-row-' + pid + '" style="display:none"><td colspan="' + (cols.length + 1) + '">' +
        renderEvidenciaPartida(preg.evidencia[pid]) + '</td></tr>';
    });
    html += '</tbody></table>';
  } else {
    html += renderEvidenciaPartida(preg.evidencia[partidas[0]]);
  }

  html += '</div>';
  panel.innerHTML = html;
}

function toggleEvidenciaFila(partidaId) {
  var row = document.getElementById('ev-row-' + partidaId);
  var btn = document.getElementById('ev-btn-' + partidaId);
  var abierta = row.style.display !== 'none';
  row.style.display = abierta ? 'none' : 'table-row';
  btn.textContent = abierta ? 'Abrir evidencia' : 'Cerrar evidencia';
}

function elegir(opcionClave) {
  RESPUESTAS[PREGUNTAS[IDX].clave] = opcionClave;
  renderSidebar();
}

function irA(i) {
  IDX = i;
  renderMain();
  renderSidebar();
}

function paso(delta) {
  if (delta > 0 && IDX === PREGUNTAS.length - 1) {
    if (Object.keys(RESPUESTAS).length === PREGUNTAS.length) {
      finalizar();
    } else {
      IDX = PREGUNTAS.findIndex(function(p) { return !RESPUESTAS.hasOwnProperty(p.clave); });
      renderMain();
      renderSidebar();
    }
    return;
  }
  var next = IDX + delta;
  if (next < 0 || next >= PREGUNTAS.length) return;
  IDX = next;
  renderMain();
  renderSidebar();
}

function finalizar() {
  document.getElementById('sidebarContent').innerHTML = '<p style="font-size:14px;color:#555;">Todo respondido.</p>';
  document.getElementById('sidebarNav').innerHTML = '';
  document.getElementById('mainPanel').innerHTML = '<div class="main-panel-inner"><div class="card done-screen"><h2>Listo.</h2><p>Guardando respuestas...</p></div></div>';
  enviarTodo();
}

function enviarTodo() {
  fetch('/api/responder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ respuestas: RESPUESTAS })
  }).then(function(r) { return r.json(); }).then(function(data) {
    document.getElementById('mainPanel').innerHTML = '<div class="main-panel-inner"><div class="card done-screen"><h2>Respuestas guardadas</h2>' +
      '<p>Se escribio <code>' + data.archivo + '</code>. El pipeline continua solo desde aqui -- ' +
      'no hace falta correr nada a mano.</p>' +
      '<p>Puedes cerrar esta pestana -- este servidor ya se esta apagando.</p></div></div>';
  }).catch(function(err) {
    document.getElementById('mainPanel').innerHTML = '<div class="main-panel-inner"><div class="card done-screen"><h2>Error al guardar</h2><p>' + err + '</p></div></div>';
  });
}

fetch('/api/preguntas').then(function(r) { return r.json(); }).then(function(data) {
  PREGUNTAS = data;
  var startIdx = parseInt(new URLSearchParams(location.search).get('idx') || '0', 10);
  if (startIdx > 0 && startIdx < PREGUNTAS.length) IDX = startIdx;
  renderMain();
  renderSidebar();
});
</script>
</body>
</html>
"""


def hacer_handler(ctx, directorio, preguntas_ui, shutdown_event):
    invoice_dir = ctx["invoice_dir"]
    proforma_dir = ctx["proforma_dir"]
    invoice_by_partida = ctx["invoice_by_partida"]
    proforma_by_seccion = ctx["proforma_by_seccion"]
    merged = ctx["merged"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            if not path or not os.path.isfile(path):
                self.send_error(404, "no encontrado")
                return
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/":
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/preguntas":
                self._json(preguntas_ui)
                return

            m = re.match(r"^/media/factura/(\d+)\.png$", path)
            if m:
                partida = int(m.group(1))
                info = invoice_by_partida.get(partida)
                self._file(os.path.join(invoice_dir, info["crop"]) if info else None)
                return

            m = re.match(r"^/media/proforma/(\d+)\.png$", path)
            if m:
                seccion = int(m.group(1))
                info = proforma_by_seccion.get(seccion)
                self._file(os.path.join(proforma_dir, info["crop"]) if info else None)
                return

            m = re.match(r"^/media/previo/(\d+)/(\d+)$", path)
            if m:
                partida, idx = int(m.group(1)), int(m.group(2))
                entry = merged["partidas"].get(f"P{partida}")
                if not entry or idx >= len(entry["fotos"]):
                    self.send_error(404, "no encontrado")
                    return
                fpath = os.path.join(directorio, entry["path"], entry["fotos"][idx])
                self._file(fpath)
                return

            self.send_error(404, "no encontrado")

        def do_POST(self):
            if self.path != "/api/responder":
                self.send_error(404, "no encontrado")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            respuestas = body.get("respuestas", {})

            claves_esperadas = {p["clave"] for p in preguntas_ui}
            if set(respuestas.keys()) != claves_esperadas:
                self._json({"error": "respuestas incompletas o con claves desconocidas"}, status=400)
                return
            preguntas_por_clave = {p["clave"]: p for p in preguntas_ui}
            for clave, opcion in respuestas.items():
                opciones_validas = {o["clave"] for o in preguntas_por_clave[clave]["opciones"]}
                if opcion not in opciones_validas:
                    self._json({"error": f"opcion invalida para {clave}: {opcion}"}, status=400)
                    return

            version = siguiente_version_respuestas(directorio)
            out_dir = os.path.join(directorio, "gates")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"respuestas_v{version}.json")
            salida = {
                "version": f"respuestas_v{version}",
                "fuente": "gate_server.py (interfaz web, corrida real)",
                "respuestas": respuestas,
            }
            json.dump(salida, open(out_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

            self._json({"ok": True, "archivo": os.path.relpath(out_path, directorio)})
            shutdown_event.set()

    return Handler


def main():
    argv = sys.argv[1:]
    directorio = "."
    port = 8765
    abrir_navegador = True
    i = 0
    posicionales = []
    while i < len(argv):
        if argv[i] == "--port":
            port = int(argv[i + 1])
            i += 2
        elif argv[i] == "--no-browser":
            abrir_navegador = False
            i += 1
        else:
            posicionales.append(argv[i])
            i += 1
    if posicionales:
        directorio = posicionales[0]

    ctx = cargar_contexto(directorio)
    preguntas_ui = construir_preguntas_ui(ctx)

    shutdown_event = threading.Event()
    handler_cls = hacer_handler(ctx, directorio, preguntas_ui, shutdown_event)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)

    url = f"http://127.0.0.1:{port}/"
    print(f"Servidor de gates en {url}")
    print(f"{len(preguntas_ui)} preguntas por responder. Ctrl+C para cancelar sin guardar.")

    def esperar_y_apagar():
        shutdown_event.wait()
        httpd.shutdown()

    threading.Thread(target=esperar_y_apagar, daemon=True).start()
    if abrir_navegador:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCancelado, no se escribio gates/respuestas_vN.json.")
        sys.exit(1)

    print("Respuestas guardadas. Servidor apagado.")


if __name__ == "__main__":
    main()
