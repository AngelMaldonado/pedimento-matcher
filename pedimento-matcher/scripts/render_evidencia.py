#!/usr/bin/env python3
"""
Paso 9 — Render de evidencia visual por hallazgo final.

Para cada hallazgo de hallazgos/hallazgos_finales_vN.json arma un collage:
- Panel PROFORMA (siempre): el recorte de la Sección con un recuadro real
  dibujado sobre la posición exacta de 'Dice', localizada por bbox de PALABRA
  (pdftotext -bbox) -- nunca una posición adivinada. Si 'Dice' es
  'sin_evidencia' o la palabra no se pudo ubicar con certeza, se usa el
  recorte plano sin recuadro (mejor honesto que una flecha falsa).
- Panel de evidencia de 'Debe decir':
    - fuente Factura: mismo mecanismo de bbox de palabra sobre el recorte de
      Partida correspondiente.
    - fuente Previo: NO hay bbox posible (Paso 4 fue lectura de visión pura,
      sin coordenadas) -- se incluye la foto completa (o fotos) de Previo que
      respaldan el campo, SIN dibujar nada encima, etiquetada como evidencia
      física.
    - fuente ninguno (ni Previo ni Factura respaldan el campo, p.ej. la
      mayoría de fraccion_arancelaria): sin segundo panel, solo la nota.

No decide nada nuevo -- es render puro sobre datos ya extraídos/mergeados.

Uso:
    python3 render_evidencia.py <directorio>

Lee:
    hallazgos/hallazgos_finales_vN.json
    proformas/proforma_vN/{proforma_vN.json,rows.json,proforma_vN.pdf,pages/*}
    facturas/invoice_vN/{invoice_vN.json,rows.json,invoice_vN.pdf,pages/*}
    previo/merged.json

Escribe:
    evidencia/vN/images/*.png
    evidencia/vN/index_vN.json
"""
import glob
import json
import os
import re
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bbox_common import locate_text_bbox_px, words_by_page  # noqa: E402

TARGET_WIDTH = 1300
PREVIO_PHOTO_HEIGHT = 700
FONT = ImageFont.load_default()


def scale_for(page_img, page_words_entry):
    """Pixeles-por-punto-PDF real de esta pagina: ancho de la imagen ya
    renderizada (Paso 3/5, a cualquier dpi) entre el ancho de pagina en
    puntos que reporta `pdftotext -bbox`. Nunca asume un dpi fijo -- si
    Paso 3/5 corre con --dpi distinto de 200, esto sigue dando el recuadro
    en la posicion correcta."""
    return page_img.width / page_words_entry["width_pt"]


def find_latest(pattern_dir_glob, filename_tmpl):
    candidates = []
    for d in glob.glob(pattern_dir_glob):
        base = os.path.basename(d.rstrip("/"))
        m = re.match(r".*_v(\d+)$", base)
        if not m:
            continue
        v = int(m.group(1))
        f = os.path.join(d, filename_tmpl.format(base=base, v=v))
        if os.path.isfile(f):
            candidates.append((v, f))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def find_latest_flat(dir_glob, prefix):
    candidates = []
    for f in glob.glob(dir_glob):
        base = os.path.splitext(os.path.basename(f))[0]
        m = re.match(prefix + r"_v(\d+)$", base)
        if m:
            candidates.append((int(m.group(1)), f))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def has_evidencia(v):
    if v is None:
        return False
    if isinstance(v, str) and v.strip().lower() in ("", "sin_evidencia"):
        return False
    return True


def resize_to_width(im, width):
    if im.width == width:
        return im
    h = max(1, round(im.height * width / im.width))
    return im.resize((width, h), Image.LANCZOS)


def fold_ascii(text: str) -> str:
    """PIL ImageFont.load_default() es un bitmap font sin glyphs acentuados --
    renderiza 'Sección' como basura. Se pliega a ASCII solo para el TEXTO
    VISIBLE de los labels del collage; los datos (JSON) nunca se tocan."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def wrap_text(text, font, max_width):
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font.getlength(trial) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def caption_bar(text, width, bg=(30, 30, 30), fg=(255, 255, 255), pad=8):
    lines = wrap_text(fold_ascii(text), FONT, width - 2 * pad)
    line_h = FONT.getbbox("Ag")[3] + 6
    h = line_h * len(lines) + 2 * pad
    img = Image.new("RGB", (width, h), bg)
    draw = ImageDraw.Draw(img)
    y = pad
    for line in lines:
        draw.text((pad, y), line, font=FONT, fill=fg)
        y += line_h
    return img


def vstack(images, bg=(255, 255, 255), gap=4):
    width = max(im.width for im in images)
    total_h = sum(im.height for im in images) + gap * (len(images) - 1)
    canvas = Image.new("RGB", (width, total_h), bg)
    y = 0
    for im in images:
        canvas.paste(im, ((width - im.width) // 2, y))
        y += im.height + gap
    return canvas


def hstack(images, bg=(255, 255, 255), gap=6):
    total_w = sum(im.width for im in images) + gap * (len(images) - 1)
    height = max(im.height for im in images)
    canvas = Image.new("RGB", (total_w, height), bg)
    x = 0
    for im in images:
        canvas.paste(im, (x, (height - im.height) // 2))
        x += im.width + gap
    return canvas


class PageCache:
    def __init__(self):
        self._cache = {}

    def get(self, path):
        if path not in self._cache:
            self._cache[path] = Image.open(path).convert("RGB")
        return self._cache[path]


def crop_with_optional_box(page_img, row_bbox_px, target_bbox_px, pad=6):
    if target_bbox_px is not None:
        annotated = page_img.copy()
        draw = ImageDraw.Draw(annotated)
        x0, y0, x1, y1 = target_bbox_px
        draw.rectangle(
            [x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=(220, 0, 0), width=4
        )
        source = annotated
    else:
        source = page_img
    return source.crop(tuple(row_bbox_px)), target_bbox_px is not None


def main() -> None:
    if len(sys.argv) < 2:
        print("uso: render_evidencia.py <directorio>", file=sys.stderr)
        sys.exit(1)
    base_dir = sys.argv[1]

    hallazgos_path = find_latest_flat(
        os.path.join(base_dir, "hallazgos", "hallazgos_finales_v*.json"), "hallazgos_finales"
    )
    if not hallazgos_path:
        print(
            "ERROR: no se encontro hallazgos/hallazgos_finales_vN.json -- "
            "corre merge_diff_gates.py primero.",
            file=sys.stderr,
        )
        sys.exit(1)

    proforma_json_path = find_latest(os.path.join(base_dir, "proformas", "proforma_v*"), "{base}.json")
    proforma_rows_path = find_latest(os.path.join(base_dir, "proformas", "proforma_v*"), "rows.json")
    proforma_pdf_path = find_latest(os.path.join(base_dir, "proformas", "proforma_v*"), "{base}.pdf")
    invoice_json_path = find_latest(os.path.join(base_dir, "facturas", "invoice_v*"), "{base}.json")
    invoice_rows_path = find_latest(os.path.join(base_dir, "facturas", "invoice_v*"), "rows.json")
    invoice_pdf_path = find_latest(os.path.join(base_dir, "facturas", "invoice_v*"), "{base}.pdf")
    if not all([proforma_json_path, proforma_rows_path, proforma_pdf_path,
                invoice_json_path, invoice_rows_path, invoice_pdf_path]):
        print("ERROR: falta Proforma o Factura parseada (Paso 3/5).", file=sys.stderr)
        sys.exit(1)

    merged_path = os.path.join(base_dir, "previo", "merged.json")
    if not os.path.isfile(merged_path):
        print("ERROR: no se encontro previo/merged.json -- corre Paso 2 primero.", file=sys.stderr)
        sys.exit(1)

    hallazgos_data = json.load(open(hallazgos_path, encoding="utf-8"))
    proforma_secciones = {s["seccion"]: s for s in json.load(open(proforma_json_path, encoding="utf-8"))["secciones"]}
    proforma_rows = {r["seccion"]: r for r in json.load(open(proforma_rows_path, encoding="utf-8"))}
    invoice_rows = {r["partida"]: r for r in json.load(open(invoice_rows_path, encoding="utf-8"))}
    merged = json.load(open(merged_path, encoding="utf-8"))["partidas"]

    proforma_dir = os.path.dirname(proforma_json_path)
    invoice_dir = os.path.dirname(invoice_json_path)

    print("extrayendo bbox de palabra de Proforma (pdftotext -bbox)...")
    proforma_words = words_by_page(__import__("pathlib").Path(proforma_pdf_path))
    print("extrayendo bbox de palabra de Factura (pdftotext -bbox)...")
    invoice_words = words_by_page(__import__("pathlib").Path(invoice_pdf_path))

    pages = PageCache()
    out_dir = os.path.join(base_dir, "evidencia", "v1")
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

    cache_por_partida = {}
    index = []
    stats = {"total": 0, "con_recuadro_real": 0, "foto_plana_sin_recuadro": 0, "sin_segundo_panel": 0}

    for h in hallazgos_data["hallazgos_finales"]:
        partida, seccion, campo = h["partida"], h["seccion"], h["campo"]
        dice, debe_decir, fuente = h["dice"], h["debe_decir"], h["fuente_debe_decir"]

        # --- Panel Proforma ("Dice") ---
        prow = proforma_rows[seccion]
        page_img = pages.get(os.path.join(proforma_dir, prow["pagina_png"]))
        target_box = None
        if has_evidencia(dice):
            page_entry = proforma_words[prow["pagina"] - 1]
            target_box = locate_text_bbox_px(
                page_entry["words"], prow["bbox_px"][1], prow["bbox_px"][3],
                scale_for(page_img, page_entry), str(dice)
            )
        proforma_crop, tuvo_recuadro_proforma = crop_with_optional_box(page_img, prow["bbox_px"], target_box)
        proforma_crop = resize_to_width(proforma_crop, TARGET_WIDTH)
        proforma_panel = vstack([
            caption_bar(f"PROFORMA -- Sección {seccion} -- Dice: {dice}", TARGET_WIDTH),
            proforma_crop,
        ])

        # --- Panel de "Debe decir" ---
        segundo_panel = None
        segundo_key = None
        recuadro_real = tuvo_recuadro_proforma

        if fuente == "factura":
            irow = invoice_rows[partida]
            fpage_img = pages.get(os.path.join(invoice_dir, irow["pagina_png"]))
            fbox = None
            if has_evidencia(debe_decir):
                fpage_entry = invoice_words[irow["pagina"] - 1]
                fbox = locate_text_bbox_px(
                    fpage_entry["words"], irow["bbox_px"][1], irow["bbox_px"][3],
                    scale_for(fpage_img, fpage_entry), str(debe_decir)
                )
            factura_crop, tuvo_recuadro_factura = crop_with_optional_box(fpage_img, irow["bbox_px"], fbox)
            factura_crop = resize_to_width(factura_crop, TARGET_WIDTH)
            segundo_panel = vstack([
                caption_bar(f"FACTURA -- Partida {partida} -- Debe decir: {debe_decir}", TARGET_WIDTH),
                factura_crop,
            ])
            recuadro_real = recuadro_real or tuvo_recuadro_factura
            segundo_key = fbox

        elif fuente == "previo":
            fotos_nombres = h["evidencia"].get("previo_fotos", [])
            carpeta = merged.get(f"P{partida}", {}).get("path")
            fotos_imgs = []
            for nombre in fotos_nombres:
                ruta = os.path.join(base_dir, carpeta, nombre) if carpeta else None
                if ruta and os.path.isfile(ruta):
                    im = Image.open(ruta).convert("RGB")
                    h_scale = PREVIO_PHOTO_HEIGHT / im.height
                    im = im.resize((round(im.width * h_scale), PREVIO_PHOTO_HEIGHT), Image.LANCZOS)
                    fotos_imgs.append(im)
            if fotos_imgs:
                fotos_row = hstack(fotos_imgs)
                fotos_row = resize_to_width(fotos_row, TARGET_WIDTH) if fotos_row.width > TARGET_WIDTH else fotos_row
                segundo_panel = vstack([
                    caption_bar(
                        f"PREVIO -- Evidencia física, ver foto completa -- Debe decir: {debe_decir} "
                        f"(sin recuadro: Paso 4 no genera coordenadas de píxel)",
                        max(fotos_row.width, TARGET_WIDTH),
                        bg=(90, 60, 0),
                    ),
                    fotos_row,
                ])
                stats["foto_plana_sin_recuadro"] += 1
            segundo_key = tuple(fotos_nombres)
        else:
            stats["sin_segundo_panel"] += 1

        titulo = f"Partida {partida} / Sección {seccion} -- Campo: {campo}"
        if h.get("marcado_sospechoso"):
            titulo += "  [VALOR DE PROFORMA MARCADO SOSPECHOSO]"
        elif h.get("resuelto_por_gate"):
            titulo += "  [confirmado/resuelto en Paso 8]"
        titulo_bar = caption_bar(titulo, TARGET_WIDTH, bg=(0, 0, 0), fg=(255, 215, 0))

        paneles = [titulo_bar, proforma_panel]
        if segundo_panel is not None:
            paneles.append(segundo_panel)
        else:
            paneles.append(caption_bar(
                f"Debe decir: {debe_decir} -- ninguna fuente (Previo ni Factura) respalda este campo",
                TARGET_WIDTH, bg=(60, 60, 60),
            ))
        collage = vstack(paneles)

        dedup_key = (seccion, dice, target_box, fuente, debe_decir, segundo_key)
        partida_cache = cache_por_partida.setdefault(partida, {})
        if dedup_key in partida_cache:
            rel_path = partida_cache[dedup_key]
        else:
            fname = f"p{partida:02d}_{campo}.png"
            fpath = os.path.join(img_dir, fname)
            collage.save(fpath)
            rel_path = os.path.relpath(fpath, base_dir)
            partida_cache[dedup_key] = rel_path

        if recuadro_real:
            stats["con_recuadro_real"] += 1
        stats["total"] += 1

        index.append({
            "partida": partida,
            "seccion": seccion,
            "campo": campo,
            "dice": dice,
            "debe_decir": debe_decir,
            "fuente_debe_decir": fuente,
            "marcado_sospechoso": h.get("marcado_sospechoso", False),
            "resuelto_por_gate": h.get("resuelto_por_gate"),
            "tuvo_recuadro_proforma": tuvo_recuadro_proforma,
            "imagen": rel_path,
        })

    out = {
        "version": "evidencia_v1",
        "fuente_hallazgos": os.path.relpath(hallazgos_path, base_dir),
        "hallazgos": index,
        "resumen": stats,
    }
    idx_path = os.path.join(out_dir, "index_v1.json")
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"collages generados (archivos unicos en disco): {len(set(v for c in cache_por_partida.values() for v in c.values()))}")
    print(f"hallazgos indexados: {stats['total']}")
    print(f"con recuadro real (Factura o Proforma): {stats['con_recuadro_real']}")
    print(f"con foto plana sin recuadro (Previo): {stats['foto_plana_sin_recuadro']}")
    print(f"sin segundo panel (ninguna fuente): {stats['sin_segundo_panel']}")
    print(f"indice: {idx_path}")


if __name__ == "__main__":
    main()
