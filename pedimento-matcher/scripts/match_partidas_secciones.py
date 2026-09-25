#!/usr/bin/env python3
"""
Paso 6 — Matching centralizado Partida (Factura) <-> Seccion (Proforma).

Determinístico primero sobre múltiples señales (NP, Marca, Modelo/Código
producto, Lote, Número de serie, Cantidad), con `factura_partida_citada` como
señal adicional de corroboración (nunca única fuente de verdad). Umbral de
confianza: acuerdo fuerte en múltiples campos -> automático; todo lo demás
(acuerdo parcial, señales contradictorias, sin match, posible N:N) -> needs_gate.

Este script NO decide nada por sí solo en los casos needs_gate — sólo los
marca con su motivo, para que el Paso 8 (gates agrupados, todavía no
implementado) los levante con AskUserQuestion.

Uso:
    python3 match_partidas_secciones.py <directorio>

Lee:
    facturas/invoice_vN/invoice_vN.json   (la versión más alta)
    proformas/proforma_vN/proforma_vN.json (la versión más alta)

Escribe:
    matching/match_vN.json
"""
import glob
import json
import os
import re
import sys

CAMPOS_SENAL = ["np", "marca", "modelo", "codigo_producto", "lote", "numero_serie", "cantidad"]

# NP es la señal más fuerte pero no un oráculo infalible (design doc): peso
# alto, no absoluto. Cantidad es la segunda señal más confiable en este
# dataset porque Factura casi no trae marca/modelo/lote/numero_serie.
PESOS = {
    "np": 3,
    "cantidad": 2,
    "marca": 1,
    "modelo": 1,
    "codigo_producto": 1,
    "lote": 1,
    "numero_serie": 1,
}
PESO_CITA_COINCIDE = 2

# Confianza alta automática requiere: NP coincide + cita coincide + cantidad
# coincide (acuerdo fuerte en múltiples campos, tal cual pide el diseño).
CAMPOS_REQUERIDOS_ALTA = {"np", "cantidad"}


def find_latest(pattern_dir_glob, filename_tmpl):
    """Encuentra la versión más alta de un artefacto vN dentro de un patrón de carpetas."""
    candidates = []
    for d in glob.glob(pattern_dir_glob):
        base = os.path.basename(d.rstrip("/"))
        m = re.match(r".*_v(\d+)$", base)
        if not m:
            continue
        v = int(m.group(1))
        f = os.path.join(d, filename_tmpl.format(base=base))
        if os.path.isfile(f):
            candidates.append((v, f))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def norm_text(v):
    """Normaliza un valor de texto para comparación: None/sin_evidencia -> None."""
    if v is None:
        return None
    if isinstance(v, str):
        if v.strip().lower() == "sin_evidencia":
            return None
        return re.sub(r"\s+", " ", v.strip()).upper()
    return v


def cantidad_valores(v):
    """Devuelve el set de valores numéricos candidatos para 'cantidad'.

    Soporta el shape de previo_v1.json (array de candidatos en conflicto,
    {"valor": ..., ...}) aunque en invoice_v1.json/proforma_v1.json de este
    piloto 'cantidad' siempre es un int limpio -- se maneja el caso general
    para cuando Paso 7 cruce contra Previo.
    """
    if v is None:
        return set()
    if isinstance(v, str):
        return set() if v.strip().lower() == "sin_evidencia" else set()
    if isinstance(v, (int, float)):
        return {v}
    if isinstance(v, list):
        out = set()
        for c in v:
            if isinstance(c, dict) and "valor" in c and isinstance(c["valor"], (int, float)):
                out.add(c["valor"])
        return out
    return set()


def comparar_campo(campo, pval, sval):
    """Devuelve ('match'|'mismatch'|'sin_evidencia', detalle)."""
    if campo == "cantidad":
        pset, sset = cantidad_valores(pval), cantidad_valores(sval)
        if not pset or not sset:
            return "sin_evidencia", None
        return ("match", None) if (pset & sset) else ("mismatch", (sorted(pset), sorted(sset)))
    pn, sn = norm_text(pval), norm_text(sval)
    if pn is None or sn is None:
        return "sin_evidencia", None
    return ("match", None) if pn == sn else ("mismatch", (pval, sval))


def score_par(partida, seccion, invoice_num):
    """Calcula score, señales coincidentes/discordantes y si la cita corrobora."""
    coincidentes, discordantes, detalle_discordancia = [], [], {}
    score = 0
    for campo in CAMPOS_SENAL:
        r, detalle = comparar_campo(campo, partida.get(campo), seccion.get(campo))
        if r == "match":
            score += PESOS[campo]
            coincidentes.append(campo)
        elif r == "mismatch":
            score -= PESOS[campo]
            discordantes.append(campo)
            detalle_discordancia[campo] = detalle

    cita = seccion.get("factura_partida_citada") or {}
    cita_coincide = (
        cita.get("partida_citada") == partida["partida"]
        and str(cita.get("factura", "")).strip() != ""  # cita presente
    )
    # La cita sólo corrobora score si además el nro de factura citado es el
    # mismo que estamos procesando -- con una sola versión de Factura en el
    # piloto esto es trivialmente cierto, pero se deja explícito para cuando
    # haya invoice_v2 y una Proforma vieja cite la v1.
    if cita_coincide and invoice_num and cita.get("factura") != invoice_num:
        cita_coincide = False
    if cita_coincide:
        score += PESO_CITA_COINCIDE

    return score, coincidentes, discordantes, detalle_discordancia, cita_coincide


def clasificar(coincidentes, discordantes, cita_coincide, cita_presente, empatado):
    """Determina confianza y, si aplica, needs_gate + motivo."""
    campos_alta = set(coincidentes) >= CAMPOS_REQUERIDOS_ALTA
    if empatado:
        return "baja", True, "ambiguo: más de un candidato con score máximo empatado"
    if discordantes:
        return (
            "baja",
            True,
            f"señales contradictorias: {', '.join(discordantes)} no coinciden pese al match elegido",
        )
    if campos_alta and (cita_coincide or not cita_presente):
        # Sin cita presente (no debería pasar, es gate de Paso 5) se acepta
        # igual si NP+cantidad coinciden limpio -- no todos los datasets
        # futuros van a traer 'factura_partida_citada' útil.
        return "alta", False, None
    if campos_alta and cita_presente and not cita_coincide:
        return (
            "media",
            True,
            "NP y cantidad coinciden pero la cita 'Factura X Partida Y' de la Sección "
            "apunta a otra Partida -- posible cita mal escrita en la Proforma, o Sección "
            "mal ubicada; no se resuelve solo",
        )
    if coincidentes:
        return "media", True, f"acuerdo parcial: sólo {', '.join(coincidentes)} coincide(n)"
    return "baja", True, "sin señales de campo coincidentes (evidencia insuficiente en ambos lados)"


def detectar_nn(partidas, secciones):
    """Candidatos a split/merge N:N: mismo partida_citada en >1 Sección, o
    texto en observaciones_a_nivel_partida que mencione más de un número de
    Partida. Nunca se resuelve aquí -- sólo se reporta como candidato a gate.
    """
    hallazgos = []

    citas_por_partida = {}
    for s in secciones:
        cita = s.get("factura_partida_citada") or {}
        pc = cita.get("partida_citada")
        if pc is not None:
            citas_por_partida.setdefault(pc, []).append(s["seccion"])

    for pc, secs in citas_por_partida.items():
        if len(secs) > 1:
            partida = next((p for p in partidas if p["partida"] == pc), None)
            secciones_obj = [s for s in secciones if s["seccion"] in secs]
            suma_cantidad_secciones = sum(
                sorted(cantidad_valores(s.get("cantidad")))[0]
                for s in secciones_obj
                if cantidad_valores(s.get("cantidad"))
            )
            cuadra_suma = (
                partida is not None
                and cantidad_valores(partida.get("cantidad"))
                and suma_cantidad_secciones in cantidad_valores(partida.get("cantidad"))
            )
            hallazgos.append(
                {
                    "tipo": "posible_split_partida_en_secciones",
                    "partida": pc,
                    "secciones_citantes": secs,
                    "suma_cantidad_secciones": suma_cantidad_secciones,
                    "cantidad_partida": sorted(cantidad_valores(partida.get("cantidad"))) if partida else None,
                    "cuadra_por_suma": cuadra_suma,
                    "needs_gate": True,
                    "motivo": (
                        f"{len(secs)} Secciones citan la misma Partida {pc} -- posible split "
                        "de una Partida en varias Secciones; " + (
                            "la suma de cantidades de esas Secciones SÍ cuadra con la cantidad de la Partida (corrobora split real)."
                            if cuadra_suma
                            else "la suma de cantidades NO cuadra con la cantidad de la Partida (revisar antes de asumir split)."
                        )
                    ),
                }
            )

    re_partida_num = re.compile(r"partida\s+(\d+)", re.IGNORECASE)
    for s in secciones:
        obs = s.get("observaciones_a_nivel_partida")
        if obs:
            nums = set(int(n) for n in re_partida_num.findall(obs))
            if len(nums) > 1:
                hallazgos.append(
                    {
                        "tipo": "observacion_cita_multiples_partidas",
                        "seccion": s["seccion"],
                        "observacion": obs,
                        "partidas_mencionadas": sorted(nums),
                        "needs_gate": True,
                        "motivo": (
                            f"'observaciones_a_nivel_partida' de la Sección {s['seccion']} menciona "
                            f"más de una Partida ({sorted(nums)}) -- posible merge/split, no se aplica solo."
                        ),
                    }
                )

    return hallazgos


def match(invoice, proforma):
    partidas = invoice["partidas"]
    secciones = proforma["secciones"]
    invoice_num = None
    # el nombre de Factura tal cual lo cita la Proforma (no viene en invoice_v1.json
    # como campo explícito; se infiere de la cita más común entre las Secciones)
    citas = [
        (s.get("factura_partida_citada") or {}).get("factura")
        for s in secciones
        if (s.get("factura_partida_citada") or {}).get("factura")
    ]
    if citas:
        invoice_num = max(set(citas), key=citas.count)

    # matriz de scores sección -> [(score, partida, coincidentes, discordantes, detalle, cita_coincide)]
    filas = []
    for s in secciones:
        candidatos = []
        for p in partidas:
            score, coinc, discor, detalle, cita_ok = score_par(p, s, invoice_num)
            candidatos.append((score, p["partida"], coinc, discor, detalle, cita_ok))
        candidatos.sort(key=lambda t: t[0], reverse=True)
        filas.append((s, candidatos))

    pares = []
    secciones_sin_match = []
    partida_a_mejor_seccion = {}

    # primero: para cada Partida, cuál es su mejor Sección (para chequear reciprocidad)
    for p in partidas:
        mejor = None
        for s in secciones:
            score, coinc, discor, detalle, cita_ok = score_par(p, s, invoice_num)
            if mejor is None or score > mejor[0]:
                mejor = (score, s["seccion"])
        partida_a_mejor_seccion[p["partida"]] = mejor

    for s, candidatos in filas:
        top_score = candidatos[0][0]
        empatados = [c for c in candidatos if c[0] == top_score]
        elegido = candidatos[0]
        score, partida_num, coinc, discor, detalle, cita_ok = elegido
        empatado = len(empatados) > 1 and top_score > 0

        if top_score <= 0:
            secciones_sin_match.append(
                {
                    "seccion": s["seccion"],
                    "np": s.get("np"),
                    "needs_gate": True,
                    "motivo": "ningún candidato de Partida tiene score positivo (sin señales de acuerdo)",
                }
            )
            continue

        cita = s.get("factura_partida_citada") or {}
        cita_presente = bool(cita.get("partida_citada"))

        confianza, needs_gate, motivo = clasificar(coinc, discor, cita_ok, cita_presente, empatado)

        # reciprocidad: si la mejor Sección de esa Partida no es esta Sección, es señal de conflicto
        mejor_sec_de_partida = partida_a_mejor_seccion.get(partida_num)
        reciproco = mejor_sec_de_partida is not None and mejor_sec_de_partida[1] == s["seccion"]
        if not reciproco and not needs_gate:
            needs_gate = True
            confianza = "media"
            motivo = (
                f"Partida {partida_num} tiene como mejor candidata a la Sección "
                f"{mejor_sec_de_partida[1]} (no esta Sección {s['seccion']}) -- posible match cruzado, revisar"
            )

        pares.append(
            {
                "partida": partida_num,
                "seccion": s["seccion"],
                "metodo": "determinista",
                "score": score,
                "senales_coincidentes": coinc,
                "senales_discordantes": discor,
                "detalle_discordancias": detalle or {},
                "citacion": cita,
                "citacion_coincide": cita_ok,
                "reciproco": reciproco,
                "confianza": confianza,
                "needs_gate": needs_gate,
                "motivo_gate": motivo,
            }
        )

    partidas_matcheadas = {par["partida"] for par in pares}
    partidas_sin_match = [
        {
            "partida": p["partida"],
            "np": p.get("np"),
            "needs_gate": True,
            "motivo": "ninguna Sección la tomó como mejor candidato",
        }
        for p in partidas
        if p["partida"] not in partidas_matcheadas
    ]

    posibles_nn = detectar_nn(partidas, secciones)

    automaticos = sum(1 for par in pares if not par["needs_gate"])
    needs_gate_count = sum(1 for par in pares if par["needs_gate"])

    return {
        "version": "match_v1",
        "fuente_invoice": invoice.get("version"),
        "fuente_proforma": proforma.get("version"),
        "total_partidas": len(partidas),
        "total_secciones": len(secciones),
        "pares": pares,
        "partidas_sin_match": partidas_sin_match,
        "secciones_sin_match": secciones_sin_match,
        "posibles_nn": posibles_nn,
        "resumen": {
            "pares_automaticos": automaticos,
            "pares_needs_gate": needs_gate_count,
            "partidas_sin_match": len(partidas_sin_match),
            "secciones_sin_match": len(secciones_sin_match),
            "posibles_nn": len(posibles_nn),
        },
    }


def main():
    directorio = sys.argv[1] if len(sys.argv) > 1 else "."
    invoice_path = find_latest(os.path.join(directorio, "facturas", "invoice_v*"), "{base}.json")
    proforma_path = find_latest(os.path.join(directorio, "proformas", "proforma_v*"), "{base}.json")
    if not invoice_path:
        sys.exit("No se encontró facturas/invoice_vN/invoice_vN.json -- corre Paso 3 primero.")
    if not proforma_path:
        sys.exit("No se encontró proformas/proforma_vN/proforma_vN.json -- corre Paso 5 primero.")

    invoice = json.load(open(invoice_path))
    proforma = json.load(open(proforma_path))

    resultado = match(invoice, proforma)

    out_dir = os.path.join(directorio, "matching")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "match_v1.json")
    with open(out_path, "w") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    r = resultado["resumen"]
    print(f"Escrito: {out_path}")
    print(f"Pares automaticos: {r['pares_automaticos']}")
    print(f"Pares needs_gate: {r['pares_needs_gate']}")
    print(f"Partidas sin match: {r['partidas_sin_match']}")
    print(f"Secciones sin match: {r['secciones_sin_match']}")
    print(f"Posibles N:N: {r['posibles_nn']}")


if __name__ == "__main__":
    main()
