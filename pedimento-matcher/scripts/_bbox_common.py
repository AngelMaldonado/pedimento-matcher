"""Utilidades compartidas de geometria de palabra (pdftotext -bbox) para Paso 9.

Copia deliberada del workaround de metadata ya escrito en prepare_invoice_pages.py
/ prepare_proforma_pages.py (Paso 3/5) -- mismo bug de poppler 26.04.0 (SIGABRT
si /Keywords viene vacio), mismo fix. Se centraliza aca porque Paso 9 necesita
bbox a nivel de PALABRA (no solo de fila) tanto sobre la Factura como sobre la
Proforma, y no tiene sentido triplicar la funcion por tercera vez.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import pypdf


def patch_metadata_copy(pdf_path: Path) -> Path:
    reader = pypdf.PdfReader(str(pdf_path))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.add_metadata({"/Keywords": "x", "/Subject": "x"})
    tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
    with open(tmp, "wb") as f:
        writer.write(f)
    return tmp


def word_rows(bbox_html_path: Path) -> list[dict]:
    """Por pagina (1-indexed en la lista), lista de palabras con bbox en puntos PDF."""
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


def words_by_page(pdf_path: Path) -> list[dict]:
    """Bbox de palabra (puntos PDF) por pagina, para el PDF dado. Aplica el
    workaround de metadata; el PDF original nunca se toca."""
    patched = patch_metadata_copy(pdf_path)
    try:
        bbox_html = Path(tempfile.mkstemp(suffix=".html")[1])
        subprocess.run(["pdftotext", "-bbox", str(patched), str(bbox_html)], check=True)
        return word_rows(bbox_html)
    finally:
        patched.unlink(missing_ok=True)


def _strip_annotations(s: str) -> str:
    """Mismas anotaciones cosmeticas que diff_partidas_secciones.norm_text ignora
    para 'np' (MPN:/P-N:/(MPN)) -- se quitan solo para BUSCAR el texto en la
    pagina, nunca para lo que se muestra en el label."""
    s = re.sub(r"^(MPN|P/N|PN)\s*[:\-]\s*", "", s, flags=re.I)
    s = re.sub(r"\s*\((MPN|P/N|PN)\)\s*$", "", s, flags=re.I)
    return s.strip()


def locate_text_bbox_px(
    words: list[dict], y_min_px: float, y_max_px: float, scale: float, target: str
) -> tuple[float, float, float, float] | None:
    """Busca `target` entre las palabras de una pagina restringidas a la banda
    vertical [y_min_px, y_max_px] (px, ya multiplicado por `scale`), agrupando
    palabras por linea y probando substring match (con y sin espacios, con y
    sin anotaciones cosmeticas). Devuelve bbox en PIXELES (xmin,ymin,xmax,ymax)
    de la union de palabras que cubren el match, o None si no se pudo ubicar
    con certeza -- nunca inventa una posicion.
    """
    target = (target or "").strip()
    if not target:
        return None

    band = [
        w for w in words
        if y_min_px <= ((w["yMin"] + w["yMax"]) / 2.0) * scale <= y_max_px
        and w["text"].strip()
    ]
    if not band:
        return None
    band.sort(key=lambda w: (round(w["yMin"] / 2.0), w["xMin"]))

    lines: list[list[dict]] = []
    for w in band:
        if lines and abs(w["yMin"] - lines[-1][-1]["yMin"]) <= 2.0:
            lines[-1].append(w)
        else:
            lines.append([w])

    candidates = [target, _strip_annotations(target)]

    for cand in candidates:
        cand_upper = cand.upper()
        for line in lines:
            line = sorted(line, key=lambda w: w["xMin"])
            spaced = ""
            spans = []  # (start_char, end_char, word)
            for w in line:
                start = len(spaced)
                spaced += w["text"]
                spans.append((start, len(spaced), w))
                spaced += " "
            idx = spaced.upper().find(cand_upper)
            if idx >= 0:
                end = idx + len(cand_upper)
                hit = [w for (s, e, w) in spans if s < end and e > idx]
                if hit:
                    return (
                        min(w["xMin"] for w in hit) * scale,
                        min(w["yMin"] for w in hit) * scale,
                        max(w["xMax"] for w in hit) * scale,
                        max(w["yMax"] for w in hit) * scale,
                    )

            nospace_map = []
            nospace = ""
            for w in line:
                start = len(nospace)
                nospace += w["text"]
                nospace_map.append((start, len(nospace), w))
            idx2 = nospace.upper().find(cand_upper.replace(" ", ""))
            if idx2 >= 0:
                end2 = idx2 + len(cand_upper.replace(" ", ""))
                hit2 = [w for (s, e, w) in nospace_map if s < end2 and e > idx2]
                if hit2:
                    return (
                        min(w["xMin"] for w in hit2) * scale,
                        min(w["yMin"] for w in hit2) * scale,
                        max(w["xMax"] for w in hit2) * scale,
                        max(w["yMax"] for w in hit2) * scale,
                    )
    return None
