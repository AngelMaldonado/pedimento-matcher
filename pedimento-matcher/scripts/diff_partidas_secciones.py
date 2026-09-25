#!/usr/bin/env python3
"""
Paso 7 — Diff centralizado (jerarquía de verdad + checklist de campos).

Para cada par Partida<->Seccion de matching/match_vN.json, calcula 'Debe
decir' con la jerarquia: Previo (foto) > Factura (texto) > sin_evidencia, y lo
compara contra 'Dice' (el valor actual en la Proforma). SOLO empareja
valores ya extraidos -- no relee imagenes, no decide gates (eso es Paso 8).

Casos especiales (nunca se resuelven solos, quedan needs_gate=true):
- Previo y Factura tienen AMBOS evidencia real mismo campo pero DISCREPAN
  entre si -> 'contradiccion_previo_factura' (posible error de Factura, no
  de Proforma -- tipo de hallazgo distinto del checklist normal).
- Previo trae un array de candidatos en conflicto (ambiguedad fisica real,
  Paso 4) -> no se usa como Debe decir con confianza ciega: si Factura tiene
  valor claro se usa esa, si no, sin_evidencia; se deja nota.

Uso:
    python3 diff_partidas_secciones.py <directorio>

Lee:
    facturas/invoice_vN/invoice_vN.json
    previo/fotos_vN/previo_vN.json
    proformas/proforma_vN/proforma_vN.json
    matching/match_vN.json

Escribe:
    diff/diff_vN.json
"""
import glob
import json
import os
import re
import sys

CHECKLIST = [
    "np",
    "marca",
    "modelo",
    "codigo_producto",
    "lote",
    "numero_serie",
    "pais_origen",
    "cantidad",
    "descripcion",
    "fraccion_arancelaria",
]

COUNTRY_ALIASES = {
    "CN": "CHINA", "CHN": "CHINA", "CHINA": "CHINA", "PRC": "CHINA",
    "PH": "PHILIPPINES", "PHL": "PHILIPPINES", "PHILIPPINES": "PHILIPPINES",
    "JP": "JAPAN", "JPN": "JAPAN", "JAPAN": "JAPAN", "JAPON": "JAPAN",
    "KR": "KOREA", "KOR": "KOREA", "KOREA": "KOREA", "SOUTH KOREA": "KOREA", "COREA": "KOREA",
    "BR": "BRAZIL", "BRA": "BRAZIL", "BRAZIL": "BRAZIL", "BRASIL": "BRAZIL",
    "TW": "TAIWAN", "TWN": "TAIWAN", "TAIWAN": "TAIWAN",
    "US": "USA", "USA": "USA", "UNITED STATES": "USA",
}


def find_latest(pattern_dir_glob, filename_tmpl):
    """filename_tmpl recibe {base} (nombre de carpeta, ej. 'invoice_v1') y {v} (numero de version, ej. '1')."""
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


def has_evidencia(v):
    """True si v es un valor escalar con evidencia real (no None, no sin_evidencia, no array)."""
    if v is None or isinstance(v, list):
        return False
    if isinstance(v, str) and v.strip().lower() == "sin_evidencia":
        return False
    if isinstance(v, str) and v.strip() == "":
        return False
    return True


def norm_text(v, campo=None):
    if not has_evidencia(v):
        return None
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"\s+", " ", str(v).strip()).upper()
    if campo == "np":
        # anotaciones cosmeticas del extractor ("MPN:", "(MPN)", "(P/N)") no son
        # parte del valor -- quitarlas antes de comparar, para no marcar como
        # contradiccion algo que en realidad es el mismo codigo con etiqueta distinta.
        s = re.sub(r"^(MPN|P/N|PN)\s*[:\-]\s*", "", s)
        s = re.sub(r"\s*\((MPN|P/N|PN)\)\s*$", "", s)
        s = s.strip()
        return s
    if campo == "pais_origen":
        # busca un alias de pais conocido como palabra dentro del string
        # (para no fallar por texto extra tipo "CN (COO:CN)" o "China (Made in China)")
        tokens = re.findall(r"[A-Z]+", s)
        for tok in tokens:
            if tok in COUNTRY_ALIASES:
                return COUNTRY_ALIASES[tok]
        for alias, canon in sorted(COUNTRY_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if alias in s:
                return canon
        return s
    return s


def cantidad_values(v):
    if v is None:
        return set()
    if isinstance(v, str):
        return set()
    if isinstance(v, (int, float)):
        return {v}
    if isinstance(v, list):
        out = set()
        for c in v:
            if isinstance(c, dict) and isinstance(c.get("valor"), (int, float)):
                out.add(c["valor"])
        return out
    return set()


def campo_iguales(campo, a_raw, b_raw):
    """Compara dos valores YA resueltos a escalar (no arrays) del mismo campo."""
    if campo == "cantidad":
        pa, pb = cantidad_values(a_raw), cantidad_values(b_raw)
        if not pa or not pb:
            return (not pa) and (not pb)
        return bool(pa & pb)
    na, nb = norm_text(a_raw, campo), norm_text(b_raw, campo)
    return na == nb


def resolver_campo(campo, previo_val, factura_val, seccion_val, previo_fotos_respaldo, factura_crop_info):
    """Devuelve (hallazgo_o_None, contradiccion_o_None)."""
    previo_es_conflicto = isinstance(previo_val, list)
    previo_real = has_evidencia(previo_val) and not previo_es_conflicto
    factura_real = has_evidencia(factura_val)

    contradiccion = None
    # 'descripcion' se excluye de este chequeo: Previo (texto crudo de etiqueta) y
    # Factura (descripcion catalogada en ingles) SIEMPRE difieren en formato por
    # diseno (ver Paso 3/5) -- no es una discrepancia real de dato, es la misma
    # info en dos redacciones distintas. Igual se sigue comparando como hallazgo
    # normal de checklist mas abajo (con formato_distinto=True).
    if campo != "descripcion" and previo_real and factura_real and not campo_iguales(campo, previo_val, factura_val):
        contradiccion = {
            "campo": campo,
            "valor_previo": previo_val,
            "valor_factura": factura_val,
            "evidencia_previo": previo_fotos_respaldo.get(campo, []),
            "evidencia_factura": factura_crop_info,
            "needs_gate": True,
            "motivo": (
                f"Previo y Factura tienen evidencia real para '{campo}' pero discrepan "
                f"({previo_val!r} vs {factura_val!r}) -- posible error de Factura, no de "
                "Proforma; no se resuelve solo."
            ),
        }

    # --- Debe decir, jerarquia Previo > Factura > sin_evidencia ---
    previo_ambiguo = False
    previo_candidatos = None
    if previo_es_conflicto:
        previo_ambiguo = True
        previo_candidatos = previo_val
        if factura_real:
            debe_decir, fuente = factura_val, "factura"
        else:
            debe_decir, fuente = "sin_evidencia", "ninguno"
    elif previo_real:
        debe_decir, fuente = previo_val, "previo"
    elif factura_real:
        debe_decir, fuente = factura_val, "factura"
    else:
        debe_decir, fuente = "sin_evidencia", "ninguno"

    dice = seccion_val if has_evidencia(seccion_val) else "sin_evidencia"

    dice_normalizado_vacio = not has_evidencia(seccion_val)
    debe_decir_vacio = fuente == "ninguno"
    if dice_normalizado_vacio and debe_decir_vacio:
        iguales = True
    else:
        iguales = campo_iguales(campo, dice, debe_decir)

    hallazgo = None
    if not iguales:
        evidencia = {}
        if fuente == "previo":
            evidencia["previo_fotos"] = previo_fotos_respaldo.get(campo, [])
        elif fuente == "factura":
            evidencia["factura_crop"] = factura_crop_info
        hallazgo = {
            "campo": campo,
            "dice": dice,
            "debe_decir": debe_decir,
            "fuente_debe_decir": fuente,
            "evidencia": evidencia,
            "previo_ambiguo": previo_ambiguo,
            "previo_candidatos": previo_candidatos,
            "formato_distinto": campo == "descripcion",
        }

    return hallazgo, contradiccion


def clasificar_alcance(previo_rec, partida_rec):
    if previo_rec is not None and previo_rec.get("categoria_evidencia") != "ninguna":
        for campo in CHECKLIST:
            if has_evidencia(previo_rec.get(campo)) or isinstance(previo_rec.get(campo), list):
                return "verificado_con_foto"
    if partida_rec is not None:
        for campo in CHECKLIST:
            if has_evidencia(partida_rec.get(campo)):
                return "solo_texto"
    return "sin_evidencia_todavia"


def diff_par(partida_rec, seccion_rec, previo_rec, par_matching):
    previo_fotos = (previo_rec or {}).get("fotos_respaldo", {})
    factura_crop = {"crop": partida_rec.get("crop"), "pagina_pdf": partida_rec.get("pagina_pdf")}

    hallazgos, contradicciones = [], []
    for campo in CHECKLIST:
        previo_val = (previo_rec or {}).get(campo, "sin_evidencia")
        factura_val = partida_rec.get(campo, "sin_evidencia")
        seccion_val = seccion_rec.get(campo, "sin_evidencia")
        h, c = resolver_campo(campo, previo_val, factura_val, seccion_val, previo_fotos, factura_crop)
        if h:
            hallazgos.append(h)
        if c:
            contradicciones.append(c)

    return {
        "partida": partida_rec["partida"],
        "seccion": seccion_rec["seccion"],
        "needs_gate_matching": par_matching.get("needs_gate", False),
        "motivo_gate_matching": par_matching.get("motivo_gate"),
        "categoria_alcance": clasificar_alcance(previo_rec, partida_rec),
        "hallazgos": hallazgos,
        "contradicciones_previo_factura": contradicciones,
        "pink_elephants": [],
    }


def diff_sin_match(item, tipo):
    if tipo == "partida":
        return {
            "partida": item["partida"],
            "seccion": None,
            "needs_gate_matching": True,
            "motivo_gate_matching": item.get("motivo"),
            "categoria_alcance": None,
            "hallazgos": [
                {
                    "campo": None,
                    "dice": None,
                    "debe_decir": "Falta esta partida en la proforma",
                    "fuente_debe_decir": "matching",
                    "evidencia": {},
                    "previo_ambiguo": False,
                    "previo_candidatos": None,
                    "formato_distinto": False,
                }
            ],
            "contradicciones_previo_factura": [],
            "pink_elephants": [],
        }
    return {
        "partida": None,
        "seccion": item["seccion"],
        "needs_gate_matching": True,
        "motivo_gate_matching": item.get("motivo"),
        "categoria_alcance": None,
        "hallazgos": [
            {
                "campo": None,
                "dice": None,
                "debe_decir": "Seccion sin partida correspondiente en la factura",
                "fuente_debe_decir": "matching",
                "evidencia": {},
                "previo_ambiguo": False,
                "previo_candidatos": None,
                "formato_distinto": False,
            }
        ],
        "contradicciones_previo_factura": [],
        "pink_elephants": [],
    }


DISTRIBUTOR_CODE_RE = re.compile(r"^[A-Z]?\d{3,4}[-\s]?[A-Z]?[-\s]?[A-Z0-9]{2,3}[-\s]?\d{3}$|^C\d{5,7}$")


def detectar_patrones_sistemicos(resultados):
    """Agrupa hallazgos/contradicciones que se repiten con la misma forma en
    muchas partidas -- para que no queden enterrados como N items sueltos
    dentro de la lista plana de pares. No reemplaza los hallazgos
    individuales (siguen ahi), solo los agrupa para lectura humana.

    Cada patron trae `partidas_afectadas` en forma estandar -- {partida,
    valor_actual, fuente_actual, valor_propuesto, fuente_propuesto} -- para
    que Paso 8 (gate_definitions.construir_preguntas) pueda armar la
    pregunta y su resolucion de forma generica, sin conocer el detalle de
    cada tipo de patron. `campo` (el campo del checklist afectado) y
    `recomienda_propuesto` (si la opcion "usar_propuesto" es la
    recomendada) tambien son parte de ese contrato.
    """
    patrones = []

    pais_afectadas = [
        {
            "partida": p["partida"],
            "valor_actual": h["debe_decir"],
            "fuente_actual": h["fuente_debe_decir"],
            "valor_propuesto": h["dice"],
            "fuente_propuesto": "proforma",
        }
        for p in resultados
        for h in p["hallazgos"]
        if h["campo"] == "pais_origen" and h["dice"] == "CHN"
    ]
    if pais_afectadas:
        patrones.append(
            {
                "tipo": "pais_origen_siempre_CHN_en_proforma",
                "campo": "pais_origen",
                "detalle": (
                    "La Proforma declara pais_origen='CHN' en (casi) todas las Secciones, "
                    "pero para estas partidas Previo y/o Factura tienen evidencia real de un "
                    "pais distinto -- posible declaracion incorrecta de origen a nivel Proforma, "
                    "no un error aislado por partida."
                ),
                "partidas_afectadas": pais_afectadas,
                "recomienda_propuesto": False,
                "needs_gate": True,
            }
        )

    np_distribuidor = [
        {
            "partida": p["partida"],
            "valor_actual": c["valor_previo"],
            "fuente_actual": "previo",
            "valor_propuesto": c["valor_factura"],
            "fuente_propuesto": "factura",
        }
        for p in resultados
        for c in p["contradicciones_previo_factura"]
        if c["campo"] == "np" and DISTRIBUTOR_CODE_RE.match(str(c["valor_previo"]).upper().strip())
    ]
    if np_distribuidor:
        patrones.append(
            {
                "tipo": "np_previo_parece_codigo_distribuidor_no_mpn_fabricante",
                "campo": "np",
                "detalle": (
                    "En estas partidas, el campo 'np' de Previo tiene forma de codigo interno "
                    "de distribuidor/almacen (ej. LCSC 'C123456' o codigos tipo '3434-A-C04-001'), "
                    "distinto del MPN de fabricante que usa Factura -- probablemente el "
                    "subagente de Paso 4 eligio el codigo mas prominente de la etiqueta en vez del "
                    "MPN real (que puede estar tambien visible, en letra mas chica). No se "
                    "resuelve aqui cual usar -- es candidato fuerte para revisar en Paso 8, "
                    "posiblemente en bloque dado el volumen."
                ),
                "partidas_afectadas": np_distribuidor,
                "recomienda_propuesto": True,
                "needs_gate": True,
            }
        )

    return patrones


def main():
    directorio = sys.argv[1] if len(sys.argv) > 1 else "."

    invoice_path = find_latest(os.path.join(directorio, "facturas", "invoice_v*"), "{base}.json")
    previo_path = find_latest(os.path.join(directorio, "previo", "fotos_v*"), "previo_v{v}.json")
    proforma_path = find_latest(os.path.join(directorio, "proformas", "proforma_v*"), "{base}.json")
    match_path = find_latest(os.path.join(directorio, "matching", "match_v*"), "{base}.json")
    if match_path is None:
        # matching/match_v1.json (sin carpeta versionada propia)
        cands = sorted(glob.glob(os.path.join(directorio, "matching", "match_v*.json")))
        match_path = cands[-1] if cands else None

    if not invoice_path:
        sys.exit("No se encontro invoice_vN.json -- corre Paso 3 primero.")
    if not previo_path:
        sys.exit("No se encontro previo_vN.json -- corre Paso 4 primero.")
    if not proforma_path:
        sys.exit("No se encontro proforma_vN.json -- corre Paso 5 primero.")
    if not match_path:
        sys.exit("No se encontro match_vN.json -- corre Paso 6 primero.")

    invoice = json.load(open(invoice_path))
    previo = json.load(open(previo_path))
    proforma = json.load(open(proforma_path))
    matching = json.load(open(match_path))

    partidas_by_id = {p["partida"]: p for p in invoice["partidas"]}
    previo_by_id = {p["partida"]: p for p in previo["partidas"]}
    secciones_by_id = {s["seccion"]: s for s in proforma["secciones"]}

    resultados = []
    for par in matching["pares"]:
        partida_rec = partidas_by_id[par["partida"]]
        seccion_rec = secciones_by_id[par["seccion"]]
        previo_rec = previo_by_id.get(par["partida"])
        resultados.append(diff_par(partida_rec, seccion_rec, previo_rec, par))

    for item in matching.get("partidas_sin_match", []):
        resultados.append(diff_sin_match(item, "partida"))
    for item in matching.get("secciones_sin_match", []):
        resultados.append(diff_sin_match(item, "seccion"))

    patrones_sistemicos = detectar_patrones_sistemicos(resultados)

    total_hallazgos = sum(len(r["hallazgos"]) for r in resultados)
    total_hallazgos_reales = sum(
        len([h for h in r["hallazgos"] if not h.get("formato_distinto")]) for r in resultados
    )
    total_contradicciones = sum(len(r["contradicciones_previo_factura"]) for r in resultados)
    dist_alcance = {}
    for r in resultados:
        k = r["categoria_alcance"] or "sin_match"
        dist_alcance[k] = dist_alcance.get(k, 0) + 1

    salida = {
        "version": "diff_v1",
        "fuente_invoice": invoice.get("version"),
        "fuente_previo": previo.get("version"),
        "fuente_proforma": proforma.get("version"),
        "fuente_matching": matching.get("version"),
        "checklist_campos": CHECKLIST,
        "pares": resultados,
        "patrones_sistemicos": patrones_sistemicos,
        "resumen": {
            "total_pares": len(resultados),
            "total_hallazgos": total_hallazgos,
            "total_hallazgos_reales_excl_descripcion": total_hallazgos_reales,
            "total_contradicciones_previo_factura": total_contradicciones,
            "distribucion_alcance": dist_alcance,
            "total_patrones_sistemicos": len(patrones_sistemicos),
        },
    }

    out_dir = os.path.join(directorio, "diff")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "diff_v1.json")
    json.dump(salida, open(out_path, "w"), indent=2, ensure_ascii=False)

    print(f"Escrito: {out_path}")
    print(json.dumps(salida["resumen"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
