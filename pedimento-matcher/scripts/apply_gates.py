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

    preguntas = construir_preguntas(matching, diff)

    def registrar_gate(preg, resp):
        entry = {"tipo": preg["tipo"]}
        if preg["tipo"] == "patron_sistemico":
            entry["patron"] = preg["patron_tipo"]
            entry["partidas_afectadas"] = preg["alcance"]["partidas_afectadas"]
        else:
            entry["alcance"] = preg["alcance"]
        entry["pregunta"] = preg["pregunta"]
        entry["opciones"] = etiquetas_opciones(preg)
        entry["respuesta"] = etiqueta_opcion(preg, resp)
        gates.append(entry)

    def resolver_matching(preg, resp):
        alcance = preg["alcance"]
        if preg["tipo"] == "matching_confianza_media":
            resoluciones.append(
                {
                    "tipo": "matching",
                    "partida": alcance["partida"],
                    "seccion": alcance["seccion"],
                    "confirmado": resp == "confirmar",
                }
            )
        # matching_sin_par no cambia ningun campo -- el hallazgo sintetico
        # "falta/sobra" de Paso 7 ya aparece siempre en hallazgos_finales;
        # la respuesta queda registrada en 'gates' para trazabilidad.

    def resolver_patron(preg, resp):
        if resp != "usar_propuesto" and resp != "usar_actual":
            return  # caso_por_caso: no se resuelve en bloque, queda como Paso 7 lo dejo
        usar_propuesto = resp == "usar_propuesto"
        for a in preg["_afectadas"]:
            resoluciones.append(
                {
                    "tipo": "patron_sistemico",
                    "patron": preg["patron_tipo"],
                    "partida": a["partida"],
                    "campo": preg["campo"],
                    "debe_decir_anterior": a["valor_actual"],
                    "debe_decir_final": a["valor_propuesto"] if usar_propuesto else a["valor_actual"],
                    "fuente_final": a["fuente_propuesto"] if usar_propuesto else a["fuente_actual"],
                    "motivo": preg["pregunta"],
                }
            )

    def resolver_contradiccion(preg, resp):
        if resp == "sin_resolver":
            return  # no se resuelve -- queda como Paso 7 lo dejo (previo por jerarquia)
        fuente = "previo" if resp == "usar_previo" else "factura"
        resoluciones.append(
            {
                "tipo": "contradiccion_individual",
                "partida": preg["alcance"]["partida"],
                "campo": preg["campo"],
                "debe_decir_final": preg["_valores"][resp],
                "fuente_final": fuente,
                "motivo": preg["pregunta"],
            }
        )

    RESOLVERES = {
        "matching_confianza_media": resolver_matching,
        "matching_sin_par": resolver_matching,
        "patron_sistemico": resolver_patron,
        "contradiccion_individual": resolver_contradiccion,
    }

    gates = []
    resoluciones = []

    for preg in preguntas:
        resp = respuestas.get(preg["clave"])
        if resp not in preg["opciones"]:
            gate_no_implementado(preg["clave"], resp)
        registrar_gate(preg, resp)
        RESOLVERES[preg["tipo"]](preg, resp)

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
