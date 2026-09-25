#!/usr/bin/env python3
"""
Paso 10 — Reporte HTML final, autocontenido (imagenes en base64, modales JS
vanilla, sin dependencias externas, sin llamar a ningun servicio).

Lee hallazgos/hallazgos_finales_vN.json + evidencia/vN/index_vN.json (Paso 9)
y arma la tabla de cambios. No decide nada nuevo -- es el ultimo render del
pipeline.

Uso:
    python3 render_reporte_html.py <directorio>

Lee:
    hallazgos/hallazgos_finales_vN.json
    evidencia/vN/index_vN.json
    facturas/invoice_vN/{invoice_vN.json,invoice_vN.pdf,crops/*}
    proformas/proforma_vN/{proforma_vN.json,proforma_vN.pdf,crops/*}
    previo/merged.json

Escribe:
    previo/previo_vN.zip                (fotos de Previo fusionadas, para el link)
    artifacts/rev_artifact_vN.html
"""
import base64
import glob
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_diff_gates import find_latest, find_latest_flat  # noqa: E402


def b64_file(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def extraer_header_proforma(pdf_path: str) -> dict:
    """Nombre del importador + identificador de operacion, leidos de la
    propia pagina 1 de la Proforma/Pedimento (texto nativo) -- nunca inventados."""
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", "1", "-l", "1", pdf_path, "-"],
        check=True, capture_output=True, text=True,
    ).stdout

    m_trafico = re.search(r"TRAFICO:\s*([A-Z0-9]+)", text)
    m_pedimento = re.search(r"NUM\.\s*PEDIMENTO:\s*([\d ]+?)(?:\s{2,}|\n)", text)

    importador = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "RAZON SOCIAL:" in line and i + 1 < len(lines):
            # layout a 2 columnas: la siguiente linea trae el campo de la
            # izquierda (p.ej. "CURP:") y el nombre en la columna derecha,
            # separados por una corrida larga de espacios -- se toma el
            # ultimo segmento no vacio de esa linea.
            partes = [p.strip() for p in re.split(r"\s{2,}", lines[i + 1]) if p.strip()]
            if partes:
                importador = partes[-1]
            break

    return {
        "trafico": m_trafico.group(1) if m_trafico else "sin_evidencia",
        "num_pedimento": re.sub(r"\s+", " ", m_pedimento.group(1)).strip() if m_pedimento else "sin_evidencia",
        "importador": importador or "sin_evidencia",
    }


def zip_previo(base_dir: str, merged: dict, out_path: str) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for pkey, info in sorted(merged["partidas"].items(), key=lambda kv: int(kv[0][1:])):
            carpeta = os.path.join(base_dir, info["path"])
            for nombre in info["fotos"]:
                fpath = os.path.join(carpeta, nombre)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=f"{pkey}/{nombre}")


CAMPO_LABELS = {
    "np": "Número de parte (NP)",
    "marca": "Marca",
    "modelo": "Modelo",
    "codigo_producto": "Código de producto",
    "lote": "Lote",
    "numero_serie": "Número de serie",
    "pais_origen": "País de origen",
    "cantidad": "Cantidad",
    "descripcion": "Descripción",
    "fraccion_arancelaria": "Fracción arancelaria",
}


def campo_label(campo: str) -> str:
    return CAMPO_LABELS.get(campo, campo)


def fold_ascii_html_escape(s) -> str:
    s = "" if s is None else str(s)
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; margin: 0; background: #f4f5f7; color: #1a1a1a; font-size: 16px; }
header.report-header { background: #101828; color: #fff; padding: 24px 32px; }
header.report-header h1 { margin: 0 0 6px 0; font-size: 26px; }
header.report-header .meta { font-size: 16px; color: #cbd2e0; }
header.report-header .links { margin-top: 14px; }
header.report-header .links a { color: #7cc4ff; margin-right: 18px; text-decoration: underline; font-size: 16px; }
header.report-header .links a .open-icon { text-decoration: none; margin-left: 3px; display: inline-block; }
.summary { display: flex; gap: 16px; padding: 18px 32px; flex-wrap: wrap; }
.summary .card { background: #fff; border: 1px solid #e2e4e9; border-radius: 8px; padding: 12px 18px; min-width: 140px; }
.summary .card .n { font-size: 28px; font-weight: 700; }
.summary .card .l { font-size: 14px; color: #666; }
main { padding: 0 32px 48px 32px; }
h2 { font-size: 20px; margin-top: 32px; border-bottom: 2px solid #e2e4e9; padding-bottom: 6px; }
p.intro, p.legend { font-size: 15px; }
p.legend { color: #555; margin-top: 4px; }
p.legend .swatch { display: inline-block; font-size: 12px; font-weight: 700; padding: 1px 7px; border-radius: 9px; margin: 0 4px; }
table { border-collapse: collapse; width: 100%; background: #fff; font-size: 15px; }
th, td { border: 1px solid #e2e4e9; padding: 10px 12px; text-align: left; vertical-align: top; }
th { background: #eef1f6; position: sticky; top: 0; z-index: 1; }
tbody tr:hover { background: #fafbfd; }
tbody tr.deleted { display: none; }
a.link-modal { color: #1a56db; cursor: pointer; font-weight: 600; text-decoration: underline; }
a.link-modal .open-icon { text-decoration: none; margin-left: 2px; }
img.thumb { max-width: 220px; max-height: 140px; display: block; cursor: zoom-in; border: 1px solid #ddd; border-radius: 4px; }
.badge { display: inline-block; font-size: 12px; font-weight: 700; padding: 2px 8px; border-radius: 10px; margin-bottom: 4px; }
.badge-warn { background: #fde2e1; color: #a11c1c; }
.motivo-text { display: block; font-size: 12.5px; font-weight: 400; color: #555; font-style: italic; margin: 2px 0 4px 0; }
.val-dice { color: #a11c1c; }
.val-debe { color: #0f7a3d; font-weight: 600; }
.overlay { display: none; position: fixed; inset: 0; background: rgba(10,12,20,.85); align-items: center; justify-content: center; z-index: 50; flex-direction: column; }
.overlay img { max-width: 92vw; max-height: 84vh; background: #fff; border-radius: 4px; }
.overlay .modal-title { color: #fff; margin-bottom: 10px; font-size: 15px; }
.overlay .close-btn { position: absolute; top: 18px; right: 28px; color: #fff; font-size: 28px; cursor: pointer; background: none; border: none; }
.hidden-store { display: none; }
.resueltos table { font-size: 14px; }
.resueltos td.ok { color: #0f7a3d; }
button.row-action { font-size: 12.5px; padding: 4px 9px; border-radius: 5px; border: 1px solid #d0d4dc; background: #fff; cursor: pointer; margin: 2px 2px 0 0; }
button.row-action:hover { background: #f0f2f5; }
button.row-action.btn-delete { color: #a11c1c; border-color: #f0c4c2; }
button.row-action.btn-restore { color: #0f7a3d; border-color: #bfe3cd; }
.deleted-panel { margin-top: 14px; background: #fff; border: 1px solid #e2e4e9; border-radius: 8px; padding: 12px 18px; font-size: 14px; }
.deleted-panel h3 { margin: 0 0 8px 0; font-size: 15px; }
.deleted-panel ul { margin: 0; padding-left: 18px; }
.deleted-panel li { margin-bottom: 4px; }
.deleted-panel .empty { color: #888; font-style: italic; }
button.btn-save { position: fixed; bottom: 10px; right: 20px; z-index: 40; font-size: 14px; padding: 9px 16px; border-radius: 6px; border: 1px solid #0f4fa1; background: #1a56db; color: #fff; cursor: pointer; font-weight: 600; white-space: nowrap; }
button.btn-save:hover { background: #0f4fa1; }
"""

JS = """
function openModal(src, title) {
  document.getElementById('modalImg').src = src;
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modal').style.display = 'flex';
}
function openModalFromId(elId, title) {
  var el = document.getElementById(elId);
  if (!el) return;
  openModal(el.src, title);
}
function closeModal() {
  document.getElementById('modal').style.display = 'none';
}
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeModal();
});
document.getElementById('modal').addEventListener('click', closeModal);
document.getElementById('modalImg').addEventListener('click', function(e) { e.stopPropagation(); });
document.getElementById('modalTitle').addEventListener('click', function(e) { e.stopPropagation(); });

var LS_KEY = 'pedimento_matcher_eliminados_v1';

function loadDeletedIds() {
  try {
    var raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) { return []; }
}
function saveDeletedIds(ids) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(ids)); } catch (e) {}
}
function renderDeletedPanel() {
  var ids = loadDeletedIds();
  var panel = document.getElementById('deletedList');
  ['deletedCount', 'deletedCount2'].forEach(function(elId) {
    var el = document.getElementById(elId);
    if (el) el.textContent = ids.length;
  });
  if (!panel) return;
  if (ids.length === 0) {
    panel.innerHTML = '<li class="empty">Ningun renglon eliminado.</li>';
    return;
  }
  panel.innerHTML = ids.map(function(id) {
    var row = document.querySelector('tr[data-rowid="' + id + '"]');
    var label = row ? row.getAttribute('data-rowlabel') : id;
    return '<li>' + label + ' <button class="row-action btn-restore" onclick="restoreRow(\\'' + id + '\\')">Restaurar</button></li>';
  }).join('');
}
function deleteRow(id) {
  var row = document.querySelector('tr[data-rowid="' + id + '"]');
  if (row) row.classList.add('deleted');
  var ids = loadDeletedIds();
  if (ids.indexOf(id) === -1) ids.push(id);
  saveDeletedIds(ids);
  renderDeletedPanel();
}
function restoreRow(id) {
  var row = document.querySelector('tr[data-rowid="' + id + '"]');
  if (row) row.classList.remove('deleted');
  var ids = loadDeletedIds().filter(function(x) { return x !== id; });
  saveDeletedIds(ids);
  renderDeletedPanel();
}
function restoreAllRows() {
  loadDeletedIds().forEach(function(id) { restoreRow(id); });
}
function guardarCambios() {
  var clone = document.documentElement.cloneNode(true);
  clone.querySelectorAll('.col-acciones').forEach(function(el) { el.remove(); });
  clone.querySelectorAll('tr.deleted').forEach(function(el) { el.remove(); });
  ['deletedPanel', 'deletedSummaryCard', 'editToolbar'].forEach(function(elId) {
    var el = clone.querySelector('#' + elId);
    if (el) el.remove();
  });
  var html = '<!DOCTYPE html>\\n' + clone.outerHTML;
  var blob = new Blob([html], { type: 'text/html' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = REPORT_FILENAME;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
document.addEventListener('DOMContentLoaded', function() {
  var ids = loadDeletedIds();
  ids.forEach(function(id) {
    var row = document.querySelector('tr[data-rowid="' + id + '"]');
    if (row) row.classList.add('deleted');
  });
  renderDeletedPanel();
});
"""


def main() -> None:
    if len(sys.argv) < 2:
        print("uso: render_reporte_html.py <directorio>", file=sys.stderr)
        sys.exit(1)
    base_dir = sys.argv[1]

    hallazgos_path = find_latest_flat(
        os.path.join(base_dir, "hallazgos", "hallazgos_finales_v*.json"), "hallazgos_finales"
    )
    evidencia_candidatos = []
    for f in glob.glob(os.path.join(base_dir, "evidencia", "v*", "index_v*.json")):
        m = re.search(r"index_v(\d+)\.json$", f)
        if m:
            evidencia_candidatos.append((int(m.group(1)), f))
    evidencia_index_path = sorted(evidencia_candidatos)[-1][1] if evidencia_candidatos else None
    invoice_json_path = find_latest(os.path.join(base_dir, "facturas", "invoice_v*"), "{base}.json")
    invoice_pdf_path = find_latest(os.path.join(base_dir, "facturas", "invoice_v*"), "{base}.pdf")
    proforma_json_path = find_latest(os.path.join(base_dir, "proformas", "proforma_v*"), "{base}.json")
    proforma_pdf_path = find_latest(os.path.join(base_dir, "proformas", "proforma_v*"), "{base}.pdf")
    merged_path = os.path.join(base_dir, "previo", "merged.json")

    faltantes = [
        ("hallazgos_finales_vN.json", hallazgos_path),
        ("evidencia/vN/index_vN.json", evidencia_index_path),
        ("invoice_vN.json", invoice_json_path),
        ("invoice_vN.pdf", invoice_pdf_path),
        ("proforma_vN.json", proforma_json_path),
        ("proforma_vN.pdf", proforma_pdf_path),
    ]
    for nombre, path in faltantes:
        if not path or not os.path.isfile(path):
            print(f"ERROR: falta {nombre} -- corre los pasos previos (1-9) primero.", file=sys.stderr)
            sys.exit(1)
    if not os.path.isfile(merged_path):
        print("ERROR: no se encontro previo/merged.json -- corre Paso 2 primero.", file=sys.stderr)
        sys.exit(1)

    hallazgos_data = json.load(open(hallazgos_path, encoding="utf-8"))
    evidencia_index = {
        (h["partida"], h["campo"]): h
        for h in json.load(open(evidencia_index_path, encoding="utf-8"))["hallazgos"]
    }
    invoice_data = json.load(open(invoice_json_path, encoding="utf-8"))
    proforma_data = json.load(open(proforma_json_path, encoding="utf-8"))
    merged = json.load(open(merged_path, encoding="utf-8"))

    invoice_dir = os.path.dirname(invoice_json_path)
    proforma_dir = os.path.dirname(proforma_json_path)
    invoice_by_partida = {p["partida"]: p for p in invoice_data["partidas"]}
    proforma_by_seccion = {s["seccion"]: s for s in proforma_data["secciones"]}

    header = extraer_header_proforma(proforma_pdf_path)

    previo_zip_path = os.path.join(base_dir, "previo", "previo_v1.zip")
    zip_previo(base_dir, merged, previo_zip_path)

    hallazgos_finales = hallazgos_data["hallazgos_finales"]
    resueltos = hallazgos_data["resueltos_sin_discrepancia"]

    partidas_ref = sorted({h["partida"] for h in hallazgos_finales})
    secciones_ref = sorted({h["seccion"] for h in hallazgos_finales})

    crop_factura_b64 = {
        p: b64_file(os.path.join(invoice_dir, invoice_by_partida[p]["crop"]))
        for p in partidas_ref if p in invoice_by_partida
    }
    crop_proforma_b64 = {
        s: b64_file(os.path.join(proforma_dir, proforma_by_seccion[s]["crop"]))
        for s in secciones_ref if s in proforma_by_seccion
    }

    hidden_store_html = []
    for p, b64 in crop_factura_b64.items():
        hidden_store_html.append(f'<img id="cropf-{p}" src="data:image/png;base64,{b64}">')
    for s, b64 in crop_proforma_b64.items():
        hidden_store_html.append(f'<img id="cropp-{s}" src="data:image/png;base64,{b64}">')

    rows_html = []
    for h in hallazgos_finales:
        p, s, campo = h["partida"], h["seccion"], h["campo"]
        ev = evidencia_index.get((p, campo), {})
        img_path = os.path.join(base_dir, ev["imagen"]) if ev.get("imagen") else None
        ev_b64 = b64_file(img_path) if img_path and os.path.isfile(img_path) else None

        badges = ""
        if h.get("marcado_sospechoso"):
            motivo = fold_ascii_html_escape((h.get("resuelto_por_gate") or {}).get("motivo", ""))
            badges += (
                '<span class="badge badge-warn">VALOR DE PROFORMA SOSPECHOSO</span>'
                f'<span class="motivo-text">{motivo}</span>'
            )

        campo_txt = fold_ascii_html_escape(campo_label(campo))
        dice_txt = fold_ascii_html_escape(h["dice"])
        debe_txt = fold_ascii_html_escape(h["debe_decir"])
        titulo_ev = fold_ascii_html_escape(f"Partida {p} / Seccion {s} -- {campo_label(campo)}: {h['dice']} → {h['debe_decir']}")

        ev_cell = (
            f'<img class="thumb" src="data:image/png;base64,{ev_b64}" '
            f'onclick="openModal(this.src, \'{titulo_ev}\')">'
            if ev_b64 else "(sin imagen de evidencia)"
        )

        p_cell = (
            f'<a class="link-modal" onclick="openModalFromId(\'cropf-{p}\', \'Partida {p} (Factura)\')">{p}<span class="open-icon">&#8599;</span></a>'
            if p in crop_factura_b64 else str(p)
        )
        s_cell = (
            f'<a class="link-modal" onclick="openModalFromId(\'cropp-{s}\', \'Seccion {s} (Proforma)\')">{s}<span class="open-icon">&#8599;</span></a>'
            if s in crop_proforma_b64 else str(s)
        )

        row_id = fold_ascii_html_escape(f"p{p}-s{s}-{campo}")
        row_label = fold_ascii_html_escape(f"Partida {p} / Seccion {s} -- {campo_label(campo)}")

        rows_html.append(f"""
        <tr data-rowid="{row_id}" data-rowlabel="{row_label}">
          <td>{p_cell}</td>
          <td>{s_cell}</td>
          <td>{badges}{campo_txt}</td>
          <td class="val-dice">{dice_txt}</td>
          <td class="val-debe">{debe_txt}</td>
          <td>{ev_cell}</td>
          <td class="col-acciones"><button class="row-action btn-delete" onclick="deleteRow('{row_id}')">Eliminar</button></td>
        </tr>""")

    resueltos_rows = []
    for r in resueltos:
        resueltos_rows.append(f"""
        <tr>
          <td>{r['partida']}</td>
          <td>{fold_ascii_html_escape(campo_label(r['campo']))}</td>
          <td>{fold_ascii_html_escape(r.get('debe_decir_paso7'))}</td>
          <td class="ok">{fold_ascii_html_escape(r['debe_decir_gate'])}</td>
          <td>{fold_ascii_html_escape(r.get('motivo'))}</td>
        </tr>""")

    factura_rel = os.path.relpath(invoice_pdf_path, os.path.join(base_dir, "artifacts"))
    proforma_rel = os.path.relpath(proforma_pdf_path, os.path.join(base_dir, "artifacts"))
    previo_zip_rel = os.path.relpath(previo_zip_path, os.path.join(base_dir, "artifacts"))

    generado = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_trafico = re.sub(r"[^A-Za-z0-9_-]+", "_", header["trafico"]).strip("_") or "reporte"
    export_filename = f"reporte_final_{safe_trafico}.html"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Reporte de cambios -- {fold_ascii_html_escape(header['trafico'])}</title>
<style>{CSS}</style>
</head>
<body>
<header class="report-header">
  <h1>Reporte de cambios a la Proforma</h1>
  <div class="meta">
    Importador: <strong>{fold_ascii_html_escape(header['importador'])}</strong> &middot;
    Num. Pedimento: <strong>{fold_ascii_html_escape(header['num_pedimento'])}</strong> &middot;
    Trafico: <strong>{fold_ascii_html_escape(header['trafico'])}</strong> &middot;
    Generado: {generado}
  </div>
  <div class="links">
    <a href="{factura_rel}" target="_blank">Factura fuente (PDF)<span class="open-icon">&#8599;</span></a>
    <a href="{proforma_rel}" target="_blank">Proforma/Pedimento fuente (PDF)<span class="open-icon">&#8599;</span></a>
    <a href="{previo_zip_rel}">Fotos de Previo (.zip, {len(merged['partidas'])} partidas)<span class="open-icon">&#8599;</span></a>
  </div>
</header>

<div class="summary">
  <div class="card"><div class="n">{len(hallazgos_finales)}</div><div class="l">Hallazgos finales</div></div>
  <div class="card"><div class="n">{sum(1 for h in hallazgos_finales if h.get('marcado_sospechoso'))}</div><div class="l">Marcados sospechosos</div></div>
  <div class="card"><div class="n">{len(resueltos)}</div><div class="l">Revisados y correctos (sin discrepancia)</div></div>
  <div class="card" id="deletedSummaryCard"><div class="n" id="deletedCount">0</div><div class="l">Eliminados de esta vista</div></div>
</div>

<main>
  <h2>Cambios a la proforma</h2>
  <p class="intro">Una fila por hallazgo entre la Partida de Factura y su Campo del checklist.
     Click en el numero de Partida o Seccion para ver el recorte fuente completo;
     click en la imagen de Evidencia para verla en tamano completo.</p>
  <p class="legend">
    <span class="swatch badge-warn">VALOR DE PROFORMA SOSPECHOSO</span>
    marca renglones donde el valor final se decidio con un criterio humano de revision, no con
    una comparacion automatica de columnas -- el texto en cursiva debajo del badge es esa nota
    del revisor, variable y subjetiva por naturaleza, no un dato declarado en ningun documento.
  </p>
  <table>
    <thead>
      <tr>
        <th>Partida Factura</th>
        <th>Seccion Proforma</th>
        <th>Campo</th>
        <th>Dice</th>
        <th>Debe decir</th>
        <th>Evidencia</th>
        <th class="col-acciones">Acciones</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>

  <div class="deleted-panel" id="deletedPanel">
    <h3>Eliminados de esta vista (<span id="deletedCount2">0</span>)</h3>
    <p style="margin:0 0 8px 0; font-size:13px; color:#777;">Solo oculta renglones en tu navegador (no borra datos ni re-genera el reporte). Util para descartar hallazgos ya revisados manualmente.</p>
    <ul id="deletedList"><li class="empty">Ningun renglon eliminado.</li></ul>
    <button class="row-action btn-restore" onclick="restoreAllRows()" style="margin-top:8px;">Restaurar todos</button>
  </div>

  <h2>Revisados y sin discrepancia</h2>
  <p class="resueltos">Estos {len(resueltos)} casos pasaron por una revision humana agrupada, pero al aplicar la
     resolucion el valor final coincidio con lo que la Proforma ya decia -- el problema real
     estaba en Previo (codigo de distribuidor en vez de MPN de fabricante), no en la Proforma.
     No requieren correccion; se listan aparte para trazabilidad, no como pendientes.</p>
  <div class="resueltos">
  <table>
    <thead>
      <tr><th>Partida</th><th>Campo</th><th>Valor descartado</th><th>Valor confirmado</th><th>Motivo de la revision</th></tr>
    </thead>
    <tbody>
      {''.join(resueltos_rows)}
    </tbody>
  </table>
  </div>
</main>

<button class="btn-save" id="editToolbar" onclick="guardarCambios()" title="Descarga una copia sin los botones de Eliminar/Restaurar ni los renglones eliminados -- lista para enviar al agente aduanal.">Guardar cambios</button>

<div class="hidden-store">
  {''.join(hidden_store_html)}
</div>

<div class="overlay" id="modal">
  <button class="close-btn" onclick="closeModal()">&times;</button>
  <div class="modal-title" id="modalTitle"></div>
  <img id="modalImg" src="">
</div>

<script>var REPORT_FILENAME = "{export_filename}";</script>
<script>{JS}</script>
</body>
</html>
"""

    out_path = os.path.join(base_dir, "artifacts", "rev_artifact_v1.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"importador: {header['importador']}")
    print(f"pedimento: {header['num_pedimento']} / trafico: {header['trafico']}")
    print(f"previo zip: {previo_zip_path} ({os.path.getsize(previo_zip_path) / (1024*1024):.1f} MB)")
    print(f"filas tabla principal: {len(hallazgos_finales)}")
    print(f"filas resueltos sin discrepancia: {len(resueltos)}")
    print(f"escrito: {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
