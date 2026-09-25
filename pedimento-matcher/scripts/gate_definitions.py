#!/usr/bin/env python3
"""
Definicion compartida de las preguntas de Paso 8 (gates), leida tanto por
`apply_gates.py` (aplica una respuesta ya dada) como por `gate_server.py`
(interfaz web para recolectar la respuesta). Una sola fuente de verdad para
el texto de la pregunta y las opciones -- si un lado y el otro construyeran
su propio texto por separado, eventualmente divergirian.

Esta corrida especifica: las 6 preguntas estan atadas a lo que el diff real
de este dataset piloto encontro (matching Partida 37, los 2 patrones
sistemicos, las 2 contradicciones individuales de np, el lote sospechoso de
P47). Un dataset nuevo con hallazgos distintos necesita su propio
`construir_preguntas` -- no se generaliza aqui a "cualquier gate posible"
porque eso todavia no esta diseñado (ver limitaciones conocidas en SKILL.md).
"""


def etiqueta_opcion(pregunta, opcion_clave):
    """Label de una opcion, con ' (Recomendado)' anexado si es la recomendada
    -- mismo texto que veia el humano en la ronda original de AskUserQuestion."""
    label = pregunta["opciones"][opcion_clave]
    if opcion_clave == pregunta["recomendada"]:
        label += " (Recomendado)"
    return label


def etiquetas_opciones(pregunta):
    return [etiqueta_opcion(pregunta, k) for k in pregunta["opciones"]]


def construir_preguntas(matching, diff):
    """Devuelve la lista de preguntas de Paso 8, en el orden en que se
    presentan. Cada pregunta trae:
      - clave: id estable, usado como key en respuestas_vN.json
      - tipo: mismo vocabulario que gates_vN.json
      - alcance: {"partida":, "seccion":} o {"partidas_afectadas": [...]}
      - pregunta: texto para humano
      - opciones: {opcion_clave: label} (dict ordenado)
      - recomendada: opcion_clave marcada como recomendada
      - evidencia: que partidas/secciones mostrar en la UI (para gate_server)
    """
    by_partida = {p["partida"]: p for p in diff["pares"] if p["partida"] is not None}
    preguntas = []

    # --- Gate 1: matching Partida 37 <-> Seccion 37 (confianza media) ---
    par37 = next(p for p in matching["pares"] if p["needs_gate"])
    preguntas.append(
        {
            "clave": "matching_seccion_37",
            "tipo": "matching_confianza_media",
            "alcance": {"partida": par37["partida"], "seccion": par37["seccion"]},
            "pregunta": (
                f"Matching Partida {par37['partida']} ↔ Seccion {par37['seccion']} quedo "
                f"confianza {par37['confianza']} en el matching automatico ({par37['motivo_gate']}). "
                "Confirmas que este par es correcto?"
            ),
            "opciones": {
                "confirmar": "Si, confirmar el par",
                "rechazar": "No, no es la partida/seccion correcta",
            },
            "recomendada": "confirmar",
            "evidencia": {"partida": par37["partida"], "seccion": par37["seccion"]},
        }
    )

    # --- Gate 2: patron np-distribuidor (partidas con np de Previo con forma de codigo interno) ---
    pat_np = next(
        p for p in diff["patrones_sistemicos"]
        if p["tipo"] == "np_previo_parece_codigo_distribuidor_no_mpn_fabricante"
    )
    n_np = len(pat_np["partidas_afectadas"])
    preguntas.append(
        {
            "clave": "patron_np_distribuidor",
            "tipo": "patron_sistemico",
            "alcance": {"partidas_afectadas": [a["partida"] for a in pat_np["partidas_afectadas"]]},
            "pregunta": (
                f"En {n_np} partidas el 'np' de Previo tiene forma de codigo interno de "
                "distribuidor/almacen, distinto del MPN de fabricante que usa Factura. "
                f"Usamos Factura como 'Debe decir' de np para estas {n_np}?"
            ),
            "opciones": {
                "usar_factura": "Si, usar el MPN de Factura",
                "usar_previo": "Mantener el codigo de Previo",
                "caso_por_caso": "Revisar caso por caso, no en bloque",
            },
            "recomendada": "usar_factura",
            "evidencia": {"partidas_afectadas": [a["partida"] for a in pat_np["partidas_afectadas"]]},
            "detalle_tabla": [
                {"partida": a["partida"], "np_previo": a["np_previo"], "np_factura": a["np_factura"]}
                for a in pat_np["partidas_afectadas"]
            ],
        }
    )

    # --- Gate 3: patron pais_origen (Proforma siempre CHN) ---
    pat_pais = next(p for p in diff["patrones_sistemicos"] if p["tipo"] == "pais_origen_siempre_CHN_en_proforma")
    n_pais = len(pat_pais["partidas_afectadas"])
    preguntas.append(
        {
            "clave": "patron_pais_origen",
            "tipo": "patron_sistemico",
            "alcance": {"partidas_afectadas": [a["partida"] for a in pat_pais["partidas_afectadas"]]},
            "pregunta": (
                f"La Proforma declara pais_origen='CHN' en todas las Secciones, pero en {n_pais} "
                "partidas Previo y/o Factura tienen evidencia real de otro pais. "
                "Que usamos como 'Debe decir'?"
            ),
            "opciones": {
                "usar_previo_factura": "Usar el pais real de Previo/Factura",
                "dejar_chn": "Dejar CHN en la Proforma",
                "caso_por_caso": "Revisar caso por caso, no en bloque",
            },
            "recomendada": "usar_previo_factura",
            "evidencia": {"partidas_afectadas": [a["partida"] for a in pat_pais["partidas_afectadas"]]},
            "detalle_tabla": [
                {"partida": a["partida"], "dice": a["dice"], "debe_decir": a["debe_decir"]}
                for a in pat_pais["partidas_afectadas"]
            ],
        }
    )

    # --- Gate 4: individual np P32 ---
    p32 = by_partida[32]
    c32 = next(c for c in p32["contradicciones_previo_factura"] if c["campo"] == "np")
    preguntas.append(
        {
            "clave": "individual_np_p32",
            "tipo": "contradiccion_individual",
            "alcance": {"partida": 32},
            "pregunta": (
                f"np Previo '{c32['valor_previo']}' vs Factura '{c32['valor_factura']}' -- 1 caracter "
                "de diferencia (J/9), compatible con confusion de lectura. Cual usamos?"
            ),
            "opciones": {
                "usar_factura": f"Usar Factura: {c32['valor_factura']}",
                "usar_previo": f"Usar Previo: {c32['valor_previo']}",
                "sin_resolver": "Marcar sin resolver / requiere verificacion fisica",
            },
            "recomendada": "usar_factura",
            "evidencia": {"partida": 32},
        }
    )

    # --- Gate 5: individual np P43 ---
    p43 = by_partida[43]
    c43 = next(c for c in p43["contradicciones_previo_factura"] if c["campo"] == "np")
    preguntas.append(
        {
            "clave": "individual_np_p43",
            "tipo": "contradiccion_individual",
            "alcance": {"partida": 43},
            "pregunta": (
                f"np Previo '{c43['valor_previo']}' -- forma atipica, no parece MPN ni codigo de "
                f"distribuidor valido. Factura dice '{c43['valor_factura']}'. Cual usamos?"
            ),
            "opciones": {
                "usar_factura": f"Usar Factura: {c43['valor_factura']}",
                "usar_previo": f"Usar Previo: {c43['valor_previo']}",
                "sin_resolver": "Marcar sin resolver / requiere verificacion fisica",
            },
            "recomendada": "usar_factura",
            "evidencia": {"partida": 43},
        }
    )

    # --- Gate 6: lote sospechoso P47 ---
    p47 = by_partida[47]
    h47 = next(h for h in p47["hallazgos"] if h["campo"] == "lote")
    preguntas.append(
        {
            "clave": "hallazgo_lote_p47",
            "tipo": "hallazgo_sospechoso",
            "alcance": {"partida": 47},
            "pregunta": (
                f"Proforma trae lote='{h47['dice']}', formato no visto en el resto del dataset. "
                f"Previo/Factura sugieren '{h47['debe_decir']}'. Como tratamos este caso?"
            ),
            "opciones": {
                "usar_previo_marcar_proforma_sospechoso": (
                    f"Usar '{h47['debe_decir']}' (Previo) y marcar el valor de Proforma como sospechoso"
                ),
                "investigar_mas": "Investigar mas antes de decidir (no cerrar el gate ahora)",
                "usar_proforma": "Usar el valor de Proforma tal cual",
            },
            "recomendada": "usar_previo_marcar_proforma_sospechoso",
            "evidencia": {"partida": 47},
        }
    )

    return preguntas
