#!/usr/bin/env python3
"""Paso 5 (soporte determinístico) — renderiza páginas de la Proforma/Pedimento
a PNG y recorta cada BLOQUE de Sección como imagen, usando bounding boxes de
palabras + líneas divisorias reales detectadas por análisis de píxeles.

A diferencia de la Factura (Paso 3), cada Sección no es una fila simple: es un
bloque de varias líneas (fila de cabecera con SEC + columnas, DESCRIPCIÓN,
fila de valores numéricos, MARCA, a veces filas de COMPLEMENTO/IDENTIF., NP/NS,
LOTE y la referencia cruzada "Factura <n> Partida <m>"), cerrado por la barra
gris "OBSERVACIONES A NIVEL PARTIDA" (con o sin texto real debajo) que
antecede al siguiente bloque. El recorte de una Sección va desde la línea
divisoria real inmediatamente arriba de su propia fila de cabecera hasta la
línea divisoria real inmediatamente arriba de la fila de cabecera de la
SIGUIENTE Sección (o, para la última Sección de una página, hasta la línea
divisoria real inmediatamente arriba del pie de página fijo "AGENTE
ADUANAL...") — así el bloque completo queda dentro del recorte, barra gris de
Observaciones incluida, sin depender de contar cuántas filas internas trae
cada bloque (varía: algunas Secciones traen filas extra de IDENTIF./
COMPLEMENTO que otras no traen).

El número SEC de esta Proforma no necesariamente arranca en 1 (a diferencia
del NO. de Factura) — la autodetección de columna busca la corrida ascendente
consecutiva MÁS LARGA en cualquier banda vertical, sin asumir valor de
arranque.

Reusa el mismo workaround que prepare_invoice_pages.py para el bug de entorno
de poppler 26.04.0: `pdftotext -bbox` truena con SIGABRT si el PDF tiene
/Keywords vacío en su Info dict — se genera una copia temporal con pypdf con
metadata parcheada, sólo para extraer bbox; el PDF original y el render de
páginas no se tocan.

Uso:
    python3 prepare_proforma_pages.py <proforma.pdf> <dir_salida> [--dpi 200]

Escribe bajo <dir_salida>:
    pages/page-NN.png              una imagen por página del PDF
    crops/seccion-NNN.png          un recorte por bloque de Sección detectado
    rows.json                      metadata determinística por sección:
                                    numero, pagina, bbox_px, ruta del crop y
                                    de la página completa
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

COLUMN_BAND_TOLERANCE_PT = 3.0
COLUMN_BAND_MARGIN_PT = 3.0

GRID_DARK_LUMINANCE = 150
GRID_ROW_DARK_FRACTION = 0.5


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
        ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(out_dir / "page")],
        check=True,
    )
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


def _leftmost_in_row(page: dict, word: dict, tol_pt: float = 1.5) -> bool:
    """¿Es `word` el token más a la izquierda de su fila (misma altura Y, con
    tolerancia)? La columna SEC es, por construcción, la primera columna de
    la tabla — nada a su izquierda en su propia fila. Esto descarta un falso
    positivo real encontrado en el piloto: el número final de la línea
    "Factura <n> Partida <m>" (p. ej. el "7" de "Partida 7") también es un
    entero puro y también asciende 1, 2, 3... de Sección en Sección, pero
    nunca es el primer token de su línea (esa línea empieza con "Factura")."""
    for other in page["words"]:
        if other is word:
            continue
        if abs(other["yMin"] - word["yMin"]) <= tol_pt and other["xMin"] < word["xMin"]:
            return False
    return True


def find_sec_column_band(pages: list[dict]) -> tuple[float, float, int]:
    """Autodetecta la banda vertical de la columna SEC y su valor de arranque,
    sin asumir que la numeración empieza en 1 (a diferencia de la columna NO.
    de Factura). Busca, en cada banda vertical candidata, la corrida más
    larga de enteros consecutivos ascendentes (cada uno = anterior + 1) en
    orden de lectura (página, luego Y) — la columna SEC es, por construcción,
    la única banda que sostiene una corrida larga así; cualquier otra columna
    numérica del documento (CANTIDAD, TASA, F.P., IMPORTE...) no lo hace más
    que por coincidencia aislada. Sólo se consideran candidatos que son el
    token más a la izquierda de su fila (ver `_leftmost_in_row`) — sin este
    filtro, el sufijo numérico de "Factura <n> Partida <m>" compite con SEC y
    puede ganarle por una sola Sección de diferencia (verificado en el piloto:
    esa banda falsa produjo una corrida de 54 contra las 53 reales)."""
    candidates = []
    for page_idx, page in enumerate(pages):
        for w in page["words"]:
            text = w["text"].strip().rstrip(".")
            if text.isdigit() and _leftmost_in_row(page, w):
                candidates.append((page_idx, w["yMin"], w["xMin"], w["xMax"], int(text)))

    if not candidates:
        raise RuntimeError("no se encontró ningún token numérico en todo el documento")

    candidates.sort(key=lambda c: c[2])
    bands: list[list[tuple]] = []
    for c in candidates:
        if bands and c[2] - bands[-1][-1][2] <= COLUMN_BAND_TOLERANCE_PT:
            bands[-1].append(c)
        else:
            bands.append([c])

    best_run = 0
    best_members: list[tuple] = []
    for band in bands:
        band_sorted = sorted(band, key=lambda c: (c[0], c[1]))
        run = [band_sorted[0]]
        best_local = [band_sorted[0]]
        for prev, cur in zip(band_sorted, band_sorted[1:]):
            if cur[4] == prev[4] + 1:
                run.append(cur)
            else:
                if len(run) > len(best_local):
                    best_local = run
                run = [cur]
        if len(run) > len(best_local):
            best_local = run
        if len(best_local) > best_run:
            best_run = len(best_local)
            best_members = best_local

    if best_run < 2:
        raise RuntimeError(
            "no se pudo autodetectar la columna SEC: ninguna banda vertical "
            "produjo una secuencia ascendente creíble (revisar el layout de "
            "esta Proforma a mano)"
        )

    x_min = min(m[2] for m in best_members) - COLUMN_BAND_MARGIN_PT
    x_max = max(m[3] for m in best_members) + COLUMN_BAND_MARGIN_PT
    start_value = best_members[0][4]
    return x_min, x_max, start_value


def find_block_starts(
    words: list[dict], expected_next: int, x_min: float, x_max: float
) -> list[tuple[int, float]]:
    """Devuelve [(numero_seccion, y_top_pt), ...] en orden, anclado en la
    banda de columna SEC autodetectada — sólo acepta el siguiente entero
    esperado en secuencia."""
    starts = []
    for w in sorted(words, key=lambda w: w["yMin"]):
        if not (x_min <= w["xMin"] <= x_max):
            continue
        text = w["text"].strip().rstrip(".")
        if text.isdigit() and int(text) == expected_next:
            starts.append((expected_next, w["yMin"]))
            expected_next += 1
    return starts


def detect_grid_lines(img: Image.Image) -> list[int]:
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


def nearest_line_at_or_above(y_px: float, grid_lines: list[int], default: int) -> int:
    return max((line for line in grid_lines if line <= y_px), default=default)


def find_footer_anchor_pt(words: list[dict]) -> float | None:
    """yMin (puntos PDF) de la palabra "AGENTE" del pie de página fijo
    ("AGENTE ADUANAL, AGENCIA ADUANAL..."), que se repite en cada página —
    usado como techo para no dejar que el recorte de la última Sección de una
    página se coma el pie. Se busca dinámicamente (no se asume una posición
    fija en puntos), por si un documento de otro formato la desplaza."""
    matches = [w["yMin"] for w in words if w["text"].strip() == "AGENTE"]
    return min(matches) if matches else None


def looks_tabular(page_text: str, min_lines: int = 3, min_gaps_per_line: int = 2) -> bool:
    def gaps_in_line(line: str) -> int:
        return len(re.findall(r"\S(?= {2,}\S)", line))

    count = sum(1 for line in page_text.splitlines() if gaps_in_line(line) >= min_gaps_per_line)
    return count >= min_lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--dpi", type=int, default=200)
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
        subprocess.run(["pdftotext", "-bbox", str(patched), str(bbox_html)], check=True)
        pages = word_rows(bbox_html)
    finally:
        patched.unlink(missing_ok=True)

    x_min, x_max, start_value = find_sec_column_band(pages)

    scale = args.dpi / 72.0
    rows_out = []
    warnings: list[str] = []
    expected_next = start_value
    width = len(str(n_pages))

    layout_text = subprocess.run(
        ["pdftotext", "-layout", str(args.pdf), "-"],
        check=True, capture_output=True, text=True,
    ).stdout
    layout_pages = layout_text.split("\f")

    for page_idx, page in enumerate(pages, start=1):
        starts = find_block_starts(page["words"], expected_next, x_min, x_max)
        if not starts:
            page_text = layout_pages[page_idx - 1] if page_idx - 1 < len(layout_pages) else ""
            if looks_tabular(page_text):
                warnings.append(
                    f"pagina {page_idx}: no se detectó ninguna Sección, pero "
                    "pdftotext -layout sugiere contenido tabular — revisar a mano, "
                    "puede haber una sección perdida o un hueco en la numeración SEC"
                )
            continue

        png_path = pages_dir / f"page-{page_idx:0{width}d}.png"
        img = Image.open(png_path)
        img_w, img_h = img.size
        grid_lines = detect_grid_lines(img)

        footer_anchor_pt = find_footer_anchor_pt(page["words"])
        footer_anchor_px = footer_anchor_pt * scale if footer_anchor_pt is not None else img_h

        for i, (numero, y_top_pt) in enumerate(starts):
            top_px = nearest_line_at_or_above(y_top_pt * scale, grid_lines, 0)
            if i + 1 < len(starts):
                next_y_px = starts[i + 1][1] * scale
                bottom_px = nearest_line_at_or_above(next_y_px, grid_lines, img_h)
            else:
                bottom_px = nearest_line_at_or_above(footer_anchor_px, grid_lines, img_h)

            if bottom_px <= top_px:
                continue

            crop = img.crop((0, top_px, img_w, bottom_px))
            crop_name = f"seccion-{numero:03d}.png"
            crop.save(crops_dir / crop_name)
            rows_out.append({
                "seccion": numero,
                "pagina": page_idx,
                "pagina_png": str((pages_dir / f"page-{page_idx:0{width}d}.png").relative_to(args.out_dir)),
                "crop_png": str((crops_dir / crop_name).relative_to(args.out_dir)),
                "bbox_px": [0, top_px, img_w, bottom_px],
            })
        expected_next = starts[-1][0] + 1

    out_json = args.out_dir / "rows.json"
    out_json.write_text(json.dumps(rows_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Gate: cruza contra el total impreso por el propio documento
    # ("NUM. TOTAL DE PARTIDAS: N"), cuando existe.
    total_m = re.search(r"NUM\.\s*TOTAL\s*DE\s*PARTIDAS:\s*(\d+)", layout_text)
    if total_m:
        total_impreso = int(total_m.group(1))
        if total_impreso != len(rows_out):
            warnings.append(
                f"el documento declara NUM. TOTAL DE PARTIDAS: {total_impreso}, pero "
                f"se detectaron {len(rows_out)} Secciones — posible hueco, duplicado, "
                "o falla de autodetección"
            )

    print(f"paginas: {n_pages}")
    print(f"columna SEC autodetectada en x=[{x_min:.1f}, {x_max:.1f}] pt, arranca en {start_value}")
    print(f"secciones detectadas: {len(rows_out)}")
    print(f"ultimo SEC visto: {rows_out[-1]['seccion'] if rows_out else 'ninguno'}")
    print(f"manifest: {out_json}")
    if warnings:
        print(f"ADVERTENCIAS ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
