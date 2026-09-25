#!/usr/bin/env python3
"""Paso 3 (soporte determinístico) — renderiza páginas de la Factura a PNG y
recorta cada fila de Partida como imagen, usando bounding boxes de palabras.

La lectura de CAMPOS (Marca, Modelo, Descripción, etc.) la hace Claude por
visión sobre las páginas renderizadas — este script sólo resuelve la
geometría (dónde empieza y termina cada fila) de forma determinística, para
que el recorte no dependa de que la IA "adivine" coordenadas de pixel.

El corte de cada fila se ancla a las líneas divisorias REALES de la tabla
(detectadas en el PNG renderizado como filas de píxeles predominantemente
oscuras que cruzan todo el ancho), no a un padding fijo sobre el texto: el
padding interno de celda varía de fila a fila y de Factura a Factura, así
que un padding arbitrario corta parejo donde la tabla no lo es.

Bug de entorno encontrado y su workaround (dejarlo documentado aquí porque no
es obvio): `pdftotext -bbox` truena con SIGABRT (std::out_of_range) en
poppler 26.04.0 cuando el PDF tiene un campo /Keywords vacío en su Info
dict — es el caso exacto de esta Factura piloto. Workaround: se genera una
copia temporal del PDF con pypdf, reescribiendo /Keywords y /Subject a un
valor no vacío, y el bbox se extrae de esa copia (sólo para geometría; el
texto y el render de página siguen viniendo del PDF original).

Uso:
    python3 prepare_invoice_pages.py <invoice.pdf> <dir_salida> [--dpi 200]

Escribe bajo <dir_salida>:
    pages/page-NN.png              una imagen por página del PDF
    crops/partida-NN.png           un recorte por fila de Partida detectada
    rows.json                      metadata determinística por partida:
                                    numero, pagina, bbox_px (del crop),
                                    ruta del crop y de la página completa
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pypdf
from PIL import Image

# Tolerancia (puntos PDF) para agrupar palabras-dígito en la misma banda de
# columna al autodetectar dónde vive el NO. de partida — no asume ninguna
# posición horizontal fija, porque cada Factura puede traer la tabla en otro
# lugar de la página.
COLUMN_BAND_TOLERANCE_PT = 3.0
COLUMN_BAND_MARGIN_PT = 3.0  # margen extra alrededor de la banda detectada

# Una línea divisoria real de tabla es una fila de píxeles mayormente oscura
# que cruza (casi) todo el ancho de la página — a diferencia de una línea de
# texto, que sólo oscurece fracciones angostas del ancho.
GRID_DARK_LUMINANCE = 150   # 0-255; por debajo de esto un píxel cuenta como "oscuro"
GRID_ROW_DARK_FRACTION = 0.5  # fracción del ancho que debe ser oscura para ser línea


def patch_metadata_copy(pdf_path: Path) -> Path:
    reader = pypdf.PdfReader(str(pdf_path))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.add_metadata({"/Keywords": "x", "/Subject": "x"})
    tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
    with open(tmp, "wb") as f:
        writer.write(f)
    return tmp


def render_pages(pdf_path: Path, out_dir: Path, dpi: int, n_pages: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "pdftoppm", "-png", "-r", str(dpi),
            str(pdf_path), str(out_dir / "page"),
        ],
        check=True,
    )
    # pdftoppm nombra page-1.png..page-13.png (o page-01.png si hay padding
    # automático); normalizamos a page-NN.png con cero a la izquierda.
    width = len(str(n_pages))
    for f in out_dir.glob("page-*.png"):
        m = re.fullmatch(r"page-(\d+)\.png", f.name)
        if not m:
            continue
        n = int(m.group(1))
        target = out_dir / f"page-{n:0{width}d}.png"
        if f != target:
            f.rename(target)


def word_rows(bbox_html_path: Path) -> list[dict]:
    """Extrae, por página, la lista de palabras con su bbox en puntos PDF.

    Formato de -bbox: <page width=".." height="..">...<word xMin=".." ...>texto</word>
    Se parsea con regex simple porque el XML es HTML-plano y no anida.
    """
    html = bbox_html_path.read_text(encoding="utf-8", errors="replace")
    pages = []
    for page_m in re.finditer(
        r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', html, re.S
    ):
        pw, ph, body = float(page_m.group(1)), float(page_m.group(2)), page_m.group(3)
        words = []
        for w in re.finditer(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>',
            body,
        ):
            xmin, ymin, xmax, ymax, text = w.groups()
            words.append({
                "xMin": float(xmin), "yMin": float(ymin),
                "xMax": float(xmax), "yMax": float(ymax),
                "text": text,
            })
        pages.append({"width_pt": pw, "height_pt": ph, "words": words})
    return pages


def find_no_column_band(pages: list[dict], start: int) -> tuple[float, float]:
    """Autodetecta en qué banda vertical (xMin/xMax, en puntos PDF) vive la
    columna "NO." de partida, sin asumir ninguna posición horizontal fija.

    Idea: junta todas las palabras que son enteros puros de todo el
    documento (todas las páginas, en orden), agrúpalas por posición
    horizontal (clustering simple por xMin), y para cada banda mide cuántos
    enteros consecutivos ascendentes arrancando en `start` produce en orden
    de lectura (página, luego Y). La columna NO. es, por construcción, la
    única banda que enumera 1, 2, 3... sin huecos a lo largo de todo el
    documento — cualquier otra columna numérica (Cantidad, Precio, un número
    suelto en la Descripción) no sostiene esa secuencia más que por
    coincidencia aislada. Se elige la banda con la corrida más larga.
    """
    candidates = []
    for page_idx, page in enumerate(pages):
        for w in page["words"]:
            text = w["text"].strip().rstrip(".")
            if text.isdigit():
                candidates.append((page_idx, w["yMin"], w["xMin"], w["xMax"], int(text)))

    if not candidates:
        raise RuntimeError("no se encontró ningún token numérico en todo el documento")

    candidates.sort(key=lambda c: c[2])  # por xMin
    bands: list[list[tuple]] = []
    for c in candidates:
        if bands and c[2] - bands[-1][-1][2] <= COLUMN_BAND_TOLERANCE_PT:
            bands[-1].append(c)
        else:
            bands.append([c])

    best_band = None
    best_run = 0
    best_members: list[tuple] = []
    for band in bands:
        band_sorted = sorted(band, key=lambda c: (c[0], c[1]))  # (pagina, yMin)
        expected = start
        matched = []
        for page_idx, y_min, x_min, x_max, value in band_sorted:
            if value == expected:
                matched.append((page_idx, y_min, x_min, x_max, value))
                expected += 1
        if len(matched) > best_run:
            best_run = len(matched)
            best_band = band
            best_members = matched

    if best_band is None or best_run < 2:
        raise RuntimeError(
            "no se pudo autodetectar la columna NO.: ninguna banda vertical "
            f"produjo una secuencia ascendente creíble desde {start} "
            "(revisar el layout de esta Factura a mano)"
        )

    x_min = min(m[2] for m in best_members) - COLUMN_BAND_MARGIN_PT
    x_max = max(m[3] for m in best_members) + COLUMN_BAND_MARGIN_PT
    return x_min, x_max


def looks_tabular(page_text: str, min_lines: int = 3, min_gaps_per_line: int = 2) -> bool:
    """Heurística para el gate de silencio: ¿esta página TIENE pinta de tabla
    (varias líneas con columnas separadas por corridas largas de espacio, el
    patrón que deja `pdftotext -layout` sobre una tabla) aunque no se le haya
    detectado ninguna fila de Partida? Si sí, vale la pena advertirlo en vez
    de pasar de largo callado.

    Cuenta, por línea, cuántas veces un caracter no-espacio es seguido por
    una corrida de 2+ espacios y luego otro no-espacio (con lookahead, para
    no perder el gap compartido entre dos palabras consecutivas cuando hay
    3+ columnas — un regex encadenado sin lookahead subcuenta ese caso).
    """
    def gaps_in_line(line: str) -> int:
        return len(re.findall(r"\S(?= {2,}\S)", line))

    count = sum(1 for line in page_text.splitlines() if gaps_in_line(line) >= min_gaps_per_line)
    return count >= min_lines


def detect_grid_lines(img: Image.Image) -> list[int]:
    """Posiciones Y (en pixel) de las líneas divisorias horizontales reales
    de la tabla en esta página renderizada.

    Para cada fila de píxeles, mide qué fracción del ancho es oscura. Una
    línea de rejilla la cruza casi entera (fracción alta y pareja); una línea
    de texto sólo ennegrece los trazos de las letras (fracción baja, salvo
    coincidencia rarísima). Agrupa píxeles consecutivos que pasan el umbral
    en una sola línea (el grosor renderizado de una regla son 1-2 px).
    """
    arr = np.array(img.convert("L"))
    dark_fraction = (arr < GRID_DARK_LUMINANCE).mean(axis=1)
    candidate_rows = np.where(dark_fraction > GRID_ROW_DARK_FRACTION)[0]

    lines: list[int] = []
    if candidate_rows.size:
        start = prev = int(candidate_rows[0])
        for y in candidate_rows[1:]:
            y = int(y)
            if y - prev > 2:
                lines.append((start + prev) // 2)
                start = y
            prev = y
        lines.append((start + prev) // 2)
    return lines


def bracket_row(y_anchor_px: float, grid_lines: list[int], img_h: int) -> tuple[int, int]:
    """Línea de rejilla real inmediatamente arriba y abajo de un ancla de
    texto (el NO. de la fila) — el borde verdadero de la celda, sea cual sea
    su padding interno, en vez de un padding fijo adivinado.

    Funciona tanto en páginas con encabezado de columnas repetido (la línea
    justo arriba del ancla es el borde inferior del encabezado, no el borde
    superior de la página) como sin encabezado (el borde superior de la
    página hace de único techo).
    """
    above = max((line for line in grid_lines if line <= y_anchor_px), default=0)
    below = min((line for line in grid_lines if line > y_anchor_px), default=img_h)
    return above, below


def find_row_starts(
    words: list[dict], expected_next: int, x_min: float, x_max: float
) -> list[tuple[int, float]]:
    """Devuelve [(numero_partida, y_top_pt), ...] en orden, anclado en la
    banda de columna NO. autodetectada — sólo acepta el siguiente entero
    esperado en secuencia para no confundir un número de la Descripción o
    del Precio con un NO. de fila.
    """
    starts = []
    for w in sorted(words, key=lambda w: w["yMin"]):
        if not (x_min <= w["xMin"] <= x_max):
            continue
        text = w["text"].strip().rstrip(".")
        if text.isdigit() and int(text) == expected_next:
            starts.append((expected_next, w["yMin"]))
            expected_next += 1
    return starts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--start-partida", type=int, default=1, help="primer NO. esperado (default 1)")
    args = ap.parse_args()

    reader = pypdf.PdfReader(str(args.pdf))
    n_pages = len(reader.pages)

    pages_dir = args.out_dir / "pages"
    crops_dir = args.out_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    render_pages(args.pdf, pages_dir, args.dpi, n_pages)

    patched = patch_metadata_copy(args.pdf)
    try:
        bbox_html = Path(tempfile.mkstemp(suffix=".html")[1])
        subprocess.run(
            ["pdftotext", "-bbox", str(patched), str(bbox_html)],
            check=True,
        )
        pages = word_rows(bbox_html)
    finally:
        patched.unlink(missing_ok=True)

    x_min, x_max = find_no_column_band(pages, args.start_partida)

    scale = args.dpi / 72.0  # puntos PDF -> pixeles del PNG renderizado
    rows_out = []
    warnings: list[str] = []
    expected_next = args.start_partida
    width = len(str(n_pages))

    layout_text = subprocess.run(
        ["pdftotext", "-layout", str(args.pdf), "-"],
        check=True, capture_output=True, text=True,
    ).stdout
    layout_pages = layout_text.split("\f")

    for page_idx, page in enumerate(pages, start=1):
        starts = find_row_starts(page["words"], expected_next, x_min, x_max)
        if not starts:
            page_text = layout_pages[page_idx - 1] if page_idx - 1 < len(layout_pages) else ""
            if looks_tabular(page_text):
                warnings.append(
                    f"pagina {page_idx}: no se detectó ninguna fila de Partida, pero "
                    "pdftotext -layout sugiere contenido tabular (varias líneas con "
                    "columnas alineadas) — revisar a mano, puede haber una partida perdida"
                )
            continue
        png_path = pages_dir / f"page-{page_idx:0{width}d}.png"
        img = Image.open(png_path)
        img_w, img_h = img.size
        grid_lines = detect_grid_lines(img)

        for numero, y_top_pt in starts:
            y_anchor_px = y_top_pt * scale
            top_px, bottom_px = bracket_row(y_anchor_px, grid_lines, img_h)
            if bottom_px <= top_px:
                continue
            crop = img.crop((0, top_px, img_w, bottom_px))
            crop_name = f"partida-{numero:02d}.png"
            crop.save(crops_dir / crop_name)
            rows_out.append({
                "partida": numero,
                "pagina": page_idx,
                "pagina_png": str((pages_dir / f"page-{page_idx:0{width}d}.png").relative_to(args.out_dir)),
                "crop_png": str((crops_dir / crop_name).relative_to(args.out_dir)),
                "bbox_px": [0, top_px, img_w, bottom_px],
            })
        expected_next = starts[-1][0] + 1

    out_json = args.out_dir / "rows.json"
    out_json.write_text(json.dumps(rows_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"paginas: {n_pages}")
    print(f"columna NO. autodetectada en x=[{x_min:.1f}, {x_max:.1f}] pt")
    print(f"filas de partida detectadas: {len(rows_out)}")
    print(f"ultimo NO. visto: {rows_out[-1]['partida'] if rows_out else 'ninguno'}")
    print(f"manifest: {out_json}")
    if warnings:
        print(f"ADVERTENCIAS ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
