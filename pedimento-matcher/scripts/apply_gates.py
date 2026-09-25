#!/usr/bin/env python3
"""
Paso 8 — Ronda de gates agrupada: aplica las respuestas humanas (recolectadas
vía AskUserQuestion, agrupadas por patrón donde aplica, en bloques de hasta 4
preguntas por llamada) sobre matching/match_vN.json + diff/diff_vN.json, y
escribe el "Debe decir" DEFINITIVO que Paso 9/10 debe usar — no el diff crudo
de Paso 7, donde todavía había ambigüedad sin resolver.

Este script no decide nada por sí solo: las respuestas son las que dio el
humano real vía `AskUserQuestion`, escritas apenas se obtienen en
`gates/respuestas_vN.json` (mismo esquema de versión que el resto del
pipeline) -- nunca hardcodeadas en el script. Correrlo de nuevo con el mismo
`respuestas_vN.json` es idempotente; una corrida futura con gates distintos
(dataset nuevo) necesita su propio `gates/respuestas_vN.json`.

Uso:
    python3 apply_gates.py <directorio>

Lee:
    matching/match_vN.json
    diff/diff_vN.json
    gates/respuestas_vN.json   (la version mas alta -- si no existe, falla con
                                 mensaje claro en vez de inventar valores)

Escribe:
    gates/gates_vN.json
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_definitions import construir_preguntas, etiqueta_opcion, etiquetas_opciones  # noqa: E402


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


def gate_no_implementado(clave, valor):
    sys.exit(
        f"gates/respuestas_vN.json trae '{clave}' = '{valor}', pero apply_gates.py no tiene "
        "una rama de resolucion implementada para esa respuesta en esta corrida -- las unicas "
        "ramas codificadas son las que el humano efectivamente eligio la vez que se corrio "
        "AskUserQuestion. Si el humano cambio de opinion, agrega el caso nuevo al script antes "
        "de re-correrlo; no se aplica ninguna resolucion a ciegas."
    )


def main():
    directorio = sys.argv[1] if len(sys.argv) > 1 else "."

    match_cands = sorted(glob.glob(os.path.join(directorio, "matching", "match_v*.json")))
    diff_cands = sorted(glob.glob(os.path.join(directorio, "diff", "diff_v*.json")))
    respuestas_path = find_latest(os.path.join(directorio, "gates", "respuestas_v*"), "{base}.json")
    if respuestas_path is None:
        # gates/respuestas_v1.json no vive en su propia carpeta versionada como
        # invoice_vN/proforma_vN -- es un archivo suelto gates/respuestas_vN.json
        cands = sorted(glob.glob(os.path.join(directorio, "gates", "respuestas_v*.json")))
        respuestas_path = cands[-1] if cands else None
    if not match_cands:
        sys.exit("No se encontro matching/match_vN.json -- corre Paso 6 primero.")
    if not diff_cands:
        sys.exit("No se encontro diff/diff_vN.json -- corre Paso 7 primero.")
    if not respuestas_path:
        sys.exit(
            "No se encontro gates/respuestas_vN.json -- corre primero la ronda de "
            "AskUserQuestion (ver Paso 8 del SKILL.md) y guarda las respuestas ahi antes de "
            "correr este script. No se inventan respuestas por defecto."
        )
    match_path, diff_path = match_cands[-1], diff_cands[-1]

    matching = json.load(open(match_path))
    diff = json.load(open(diff_path))
    respuestas_doc = json.load(open(respuestas_path))
    respuestas = respuestas_doc["respuestas"]
    by_partida = {p["partida"]: p for p in diff["pares"] if p["partida"] is not None}

    preguntas = {p["clave"]: p for p in construir_preguntas(matching, diff)}

    def validar(clave):
        preg = preguntas[clave]
        resp = respuestas.get(clave)
        if resp not in preg["opciones"]:
            gate_no_implementado(clave, resp)
        return resp

    def registrar_gate(preg, resp, patron=None):
        entry = {"tipo": preg["tipo"]}
        if preg["tipo"] == "patron_sistemico":
            entry["patron"] = patron
            entry["partidas_afectadas"] = preg["alcance"]["partidas_afectadas"]
        else:
            entry["alcance"] = preg["alcance"]
        entry["pregunta"] = preg["pregunta"]
        entry["opciones"] = etiquetas_opciones(preg)
        entry["respuesta"] = etiqueta_opcion(preg, resp)
        gates.append(entry)

    gates = []
    resoluciones = []

    # --- Gate 1: matching Partida 37 <-> Seccion 37 (confianza media) ---
    par37 = next(p for p in matching["pares"] if p["needs_gate"])
    preg = preguntas["matching_seccion_37"]
    resp = validar("matching_seccion_37")
    registrar_gate(preg, resp)
    resoluciones.append(
        {
            "tipo": "matching",
            "partida": par37["partida"],
            "seccion": par37["seccion"],
            "confirmado": resp == "confirmar",
        }
    )

    # --- Gate 2: patron np-distribuidor ---
    pat_np = next(p for p in diff["patrones_sistemicos"] if p["tipo"] == "np_previo_parece_codigo_distribuidor_no_mpn_fabricante")
    preg = preguntas["patron_np_distribuidor"]
    resp = validar("patron_np_distribuidor")
    registrar_gate(preg, resp, patron=pat_np["tipo"])
    if resp == "usar_factura":
        for a in pat_np["partidas_afectadas"]:
            resoluciones.append(
                {
                    "tipo": "patron_sistemico",
                    "patron": pat_np["tipo"],
                    "partida": a["partida"],
                    "campo": "np",
                    "debe_decir_anterior": a["np_previo"],
                    "debe_decir_final": a["np_factura"],
                    "fuente_final": "factura",
                    "motivo": "Previo probablemente capturo codigo de distribuidor, no el MPN.",
                }
            )

    # --- Gate 3: patron pais_origen ---
    pat_pais = next(p for p in diff["patrones_sistemicos"] if p["tipo"] == "pais_origen_siempre_CHN_en_proforma")
    preg = preguntas["patron_pais_origen"]
    resp = validar("patron_pais_origen")
    registrar_gate(preg, resp, patron=pat_pais["tipo"])
    if resp == "usar_previo_factura":
        for a in pat_pais["partidas_afectadas"]:
            resoluciones.append(
                {
                    "tipo": "patron_sistemico",
                    "patron": pat_pais["tipo"],
                    "partida": a["partida"],
                    "campo": "pais_origen",
                    "debe_decir_final": a["debe_decir"],
                    "fuente_final": "previo_o_factura (ya resuelto en Paso 7, gate solo confirma)",
                    "motivo": "Se confirma el pais real en vez de aceptar el CHN constante de Proforma.",
                }
            )

    # --- Gate 4: individual np P32 ---
    p32 = by_partida[32]
    c32 = next(c for c in p32["contradicciones_previo_factura"] if c["campo"] == "np")
    preg = preguntas["individual_np_p32"]
    resp = validar("individual_np_p32")
    registrar_gate(preg, resp)
    if resp == "usar_factura":
        resoluciones.append(
            {
                "tipo": "contradiccion_individual",
                "partida": 32,
                "campo": "np",
                "debe_decir_anterior": c32["valor_previo"],
                "debe_decir_final": c32["valor_factura"],
                "fuente_final": "factura",
                "motivo": "Texto nativo de PDF (Factura) mas confiable que lectura de etiqueta fisica para 1 caracter ambiguo.",
            }
        )

    # --- Gate 5: individual np P43 ---
    p43 = by_partida[43]
    c43 = next(c for c in p43["contradicciones_previo_factura"] if c["campo"] == "np")
    preg = preguntas["individual_np_p43"]
    resp = validar("individual_np_p43")
    registrar_gate(preg, resp)
    if resp == "usar_factura":
        resoluciones.append(
            {
                "tipo": "contradiccion_individual",
                "partida": 43,
                "campo": "np",
                "debe_decir_anterior": c43["valor_previo"],
                "debe_decir_final": c43["valor_factura"],
                "fuente_final": "factura",
                "motivo": "'6S0M' no tiene forma de identificador valido, probable extraccion fallida de la foto de Previo.",
            }
        )

    # --- Gate 6: lote sospechoso P47 ---
    p47 = by_partida[47]
    h47 = next(h for h in p47["hallazgos"] if h["campo"] == "lote")
    preg = preguntas["hallazgo_lote_p47"]
    resp = validar("hallazgo_lote_p47")
    registrar_gate(preg, resp)
    if resp == "usar_previo_marcar_proforma_sospechoso":
        resoluciones.append(
            {
                "tipo": "hallazgo_sospechoso",
                "partida": 47,
                "campo": "lote",
                "debe_decir_final": h47["debe_decir"],
                "fuente_final": "previo (ya resuelto en Paso 7, gate solo confirma)",
                "valor_proforma_marcado_sospechoso": h47["dice"],
                "motivo": "El valor de Proforma no tiene formato reconocible; se usa Previo y se deja constancia para revisar la extraccion de la Proforma.",
            }
        )

    salida = {
        "version": "gates_v1",
        "fuente_matching": matching.get("version"),
        "fuente_diff": diff.get("version"),
        "gates": gates,
        "resoluciones_aplicadas": resoluciones,
        "resumen": {
            "total_preguntas": len(gates),
            "preguntas_agrupadas_por_patron": sum(1 for g in gates if g["tipo"] == "patron_sistemico"),
            "preguntas_individuales": sum(1 for g in gates if g["tipo"] != "patron_sistemico"),
            "total_partidas_afectadas_por_resoluciones": len(resoluciones),
            "todas_recomendadas_aceptadas": all("(Recomendado)" in g["respuesta"] for g in gates),
        },
    }

    out_dir = os.path.join(directorio, "gates")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "gates_v1.json")
    json.dump(salida, open(out_path, "w"), indent=2, ensure_ascii=False)

    print(f"Escrito: {out_path}")
    print(json.dumps(salida["resumen"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
