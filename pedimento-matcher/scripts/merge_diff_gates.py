#!/usr/bin/env python3
"""
Paso 9 (pre-render) — merge de diff/diff_vN.json + gates/gates_vN.json en la
lista de hallazgos FINALES (Partida x Campo con Dice != Debe decir DEFINITIVO).

Para cada hallazgo real de diff_vN.json (excluye 'descripcion' -- formato_distinto
siempre, no es una discrepancia real, ver Paso 7):
- si gates_vN.json trae una resolucion para esa partida+campo, se aplica su
  'debe_decir_final' (y su fuente) en vez del 'debe_decir' de Paso 7.
- si tras aplicar el gate el nuevo 'debe_decir' coincide con 'Dice' (la Proforma
  ya tenia razon, el problema era Previo/Factura discrepando entre si, no la
  Proforma), el hallazgo se cae de la lista final -- ya no es una discrepancia
  -- pero queda su rastro en 'resueltos_sin_discrepancia' para auditoria.
- si no hay resolucion de gate para ese campo, el hallazgo de diff ya es final
  tal cual (se copia con 'resuelto_por_gate': null).

No decide nada nuevo -- es merge puro de dos JSON ya existentes. No relee
imagenes ni PDFs.

Uso:
    python3 merge_diff_gates.py <directorio>

Lee:
    diff/diff_vN.json
    gates/gates_vN.json
    facturas/invoice_vN/invoice_vN.json   (solo para reconstruir evidencia de
                                            Factura cuando un gate cambia la
                                            fuente de Previo a Factura)

Escribe:
    hallazgos/hallazgos_finales_vN.json
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diff_partidas_secciones import campo_iguales  # noqa: E402


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


def find_latest_flat(dir_glob, filename_tmpl):
    """Como find_latest pero para archivos sueltos en un directorio plano
    (p. ej. gates/gates_v1.json, diff/diff_v1.json), no en subcarpetas _vN."""
    candidates = []
    for f in glob.glob(dir_glob):
        base = os.path.splitext(os.path.basename(f))[0]
        m = re.match(filename_tmpl + r"_v(\d+)$", base)
        if m:
            candidates.append((int(m.group(1)), f))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def normaliza_fuente(fuente_final_raw: str, fuente_original: str) -> str:
    raw = (fuente_final_raw or "").lower()
    if raw.startswith("previo_o_factura"):
        return fuente_original
    if raw.startswith("previo"):
        return "previo"
    if raw.startswith("factura"):
        return "factura"
    return fuente_original


def main() -> None:
    if len(sys.argv) < 2:
        print("uso: merge_diff_gates.py <directorio>", file=sys.stderr)
        sys.exit(1)
    base_dir = sys.argv[1]

    diff_path = find_latest_flat(os.path.join(base_dir, "diff", "diff_v*.json"), "diff")
    if not diff_path:
        print("ERROR: no se encontro diff/diff_vN.json -- corre Paso 7 primero.", file=sys.stderr)
        sys.exit(1)

    gates_path = find_latest_flat(os.path.join(base_dir, "gates", "gates_v*.json"), "gates")
    if not gates_path:
        print(
            "ERROR: no se encontro gates/gates_vN.json -- corre Paso 8 "
            "(ronda de AskUserQuestion + apply_gates.py) primero.",
            file=sys.stderr,
        )
        sys.exit(1)

    invoice_path = find_latest(
        os.path.join(base_dir, "facturas", "invoice_v*"), "{base}.json"
    )
    if not invoice_path:
        print("ERROR: no se encontro facturas/invoice_vN/invoice_vN.json.", file=sys.stderr)
        sys.exit(1)

    diff_data = json.load(open(diff_path, encoding="utf-8"))
    gates_data = json.load(open(gates_path, encoding="utf-8"))
    invoice_data = json.load(open(invoice_path, encoding="utf-8"))

    invoice_by_partida = {p["partida"]: p for p in invoice_data["partidas"]}

    resoluciones_by_key = {}
    for r in gates_data.get("resoluciones_aplicadas", []):
        campo = r.get("campo")
        if not campo:
            continue  # p.ej. resolucion tipo "matching", sin campo propio
        resoluciones_by_key[(r["partida"], campo)] = r

    hallazgos_finales = []
    resueltos_sin_discrepancia = []

    for par in diff_data["pares"]:
        partida, seccion = par["partida"], par["seccion"]
        for h in par["hallazgos"]:
            campo = h["campo"]
            if campo == "descripcion":
                continue  # formato_distinto siempre -- no es discrepancia real, ver Paso 7

            resolucion = resoluciones_by_key.get((partida, campo))
            entry = dict(h)
            entry["partida"] = partida
            entry["seccion"] = seccion
            entry["resuelto_por_gate"] = None
            entry["marcado_sospechoso"] = False

            if resolucion is not None:
                debe_decir_final = resolucion["debe_decir_final"]
                fuente_final = normaliza_fuente(resolucion.get("fuente_final", ""), h["fuente_debe_decir"])

                if campo_iguales(campo, h["dice"], debe_decir_final):
                    resueltos_sin_discrepancia.append({
                        "partida": partida,
                        "seccion": seccion,
                        "campo": campo,
                        "dice": h["dice"],
                        "debe_decir_paso7": h["debe_decir"],
                        "debe_decir_gate": debe_decir_final,
                        "gate_tipo": resolucion["tipo"],
                        "gate_patron": resolucion.get("patron"),
                        "motivo": resolucion.get("motivo"),
                    })
                    continue  # ya no es hallazgo -- la Proforma tenia razon

                nueva_evidencia = entry["evidencia"]
                if fuente_final != h["fuente_debe_decir"]:
                    if fuente_final == "factura":
                        factura_rec = invoice_by_partida.get(partida, {})
                        nueva_evidencia = {
                            "factura_crop": {
                                "crop": factura_rec.get("crop"),
                                "pagina_pdf": factura_rec.get("pagina_pdf"),
                            }
                        }
                    elif fuente_final == "previo":
                        nueva_evidencia = {"previo_fotos": h["evidencia"].get("previo_fotos", [])}

                entry["debe_decir"] = debe_decir_final
                entry["fuente_debe_decir"] = fuente_final
                entry["evidencia"] = nueva_evidencia
                entry["resuelto_por_gate"] = {
                    "tipo": resolucion["tipo"],
                    "patron": resolucion.get("patron"),
                    "motivo": resolucion.get("motivo"),
                    "debe_decir_paso7": h["debe_decir"],
                }
                if resolucion["tipo"] == "hallazgo_sospechoso":
                    entry["marcado_sospechoso"] = True
                    entry["valor_proforma_marcado_sospechoso"] = resolucion.get(
                        "valor_proforma_marcado_sospechoso"
                    )

            hallazgos_finales.append(entry)

    por_campo = {}
    for h in hallazgos_finales:
        por_campo[h["campo"]] = por_campo.get(h["campo"], 0) + 1

    out = {
        "version": "hallazgos_finales_v1",
        "fuente_diff": os.path.relpath(diff_path, base_dir),
        "fuente_gates": os.path.relpath(gates_path, base_dir),
        "hallazgos_finales": hallazgos_finales,
        "resueltos_sin_discrepancia": resueltos_sin_discrepancia,
        "resumen": {
            "total_hallazgos_finales": len(hallazgos_finales),
            "total_resueltos_sin_discrepancia_por_gate": len(resueltos_sin_discrepancia),
            "por_campo": por_campo,
            "marcados_sospechosos": sum(1 for h in hallazgos_finales if h["marcado_sospechoso"]),
        },
    }

    out_dir = os.path.join(base_dir, "hallazgos")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "hallazgos_finales_v1.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"hallazgos finales: {len(hallazgos_finales)}")
    print(f"resueltos sin discrepancia (gate confirmo que Proforma tenia razon): {len(resueltos_sin_discrepancia)}")
    print(f"por campo: {por_campo}")
    print(f"marcados sospechosos: {out['resumen']['marcados_sospechosos']}")
    print(f"escrito: {out_path}")


if __name__ == "__main__":
    main()
