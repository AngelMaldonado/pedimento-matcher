#!/usr/bin/env python3
"""
Definicion compartida de las preguntas de Paso 8 (gates), leida tanto por
`apply_gates.py` (aplica una respuesta ya dada) como por `gate_server.py`
(interfaz web para recolectar la respuesta). Una sola fuente de verdad para
el texto de la pregunta y las opciones -- si un lado y el otro construyeran
su propio texto por separado, eventualmente divergirian.

Completamente generico: recorre `matching/match_vN.json` +
`diff/diff_vN.json` y arma tantas preguntas como haga falta para ESTA
corrida -- 0, 1, 6 o 20. Nunca asume partidas/tipos de patron especificos
de un dataset -- eso fue un bug real (v0.0.3): `construir_preguntas`
llegó a estar atada a 6 preguntas de un piloto concreto y una corrida con
un diff distinto no podia usar la interfaz web en absoluto.

Fuentes de gate, las 3 unicas segun el diseno (ver SKILL.md Paso 8):
  1. Pares de matching con `needs_gate_matching` (baja confianza, o
     partida/seccion sin contraparte) -- una pregunta por par.
  2. `patrones_sistemicos` del diff con `needs_gate: true` -- una pregunta
     por patron, agrupando todas las partidas que comparte.
  3. `contradicciones_previo_factura` con `needs_gate: true` que NINGUN
     patron ya cubrio -- una pregunta por contradiccion suelta.
"""

CAMPO_LABELS = {
    "np": "Numero de parte (NP)",
    "marca": "Marca",
    "modelo": "Modelo",
    "codigo_producto": "Codigo de producto",
    "lote": "Lote",
    "numero_serie": "Numero de serie",
    "pais_origen": "Pais de origen",
    "cantidad": "Cantidad",
    "descripcion": "Descripcion",
    "fraccion_arancelaria": "Fraccion arancelaria",
}


def campo_label(campo):
    return CAMPO_LABELS.get(campo, campo or "campo")


def etiqueta_opcion(pregunta, opcion_clave):
    """Label de una opcion, con ' (Recomendado)' anexado si es la recomendada
    -- mismo texto que veia el humano en la ronda original de AskUserQuestion."""
    label = pregunta["opciones"][opcion_clave]
    if opcion_clave == pregunta["recomendada"]:
        label += " (Recomendado)"
    return label


def etiquetas_opciones(pregunta):
    return [etiqueta_opcion(pregunta, k) for k in pregunta["opciones"]]


def _pregunta_matching(par):
    partida, seccion = par.get("partida"), par.get("seccion")
    motivo = par.get("motivo_gate_matching") or "sin motivo registrado"

    if partida is not None and seccion is not None:
        return {
            "clave": f"matching_p{partida}_s{seccion}",
            "tipo": "matching_confianza_media",
            "alcance": {"partida": partida, "seccion": seccion},
            "pregunta": (
                f"Matching Partida {partida} ↔ Seccion {seccion} quedo con confianza "
                f"baja en el matching automatico ({motivo}). Confirmas que este par es correcto?"
            ),
            "opciones": {
                "confirmar": "Si, confirmar el par",
                "rechazar": "No, no es la partida/seccion correcta",
            },
            "recomendada": "confirmar",
            "evidencia": {"partida": partida, "seccion": seccion},
        }
    if partida is not None:
        return {
            "clave": f"matching_p{partida}_sin_seccion",
            "tipo": "matching_sin_par",
            "alcance": {"partida": partida},
            "pregunta": (
                f"Partida {partida} no tiene Seccion correspondiente en la Proforma "
                f"({motivo}). Como la tratamos?"
            ),
            "opciones": {
                "confirmar_faltante": "Confirmar que falta en la Proforma (requiere correccion)",
                "buscar_manual": "Buscar manualmente antes de decidir",
            },
            "recomendada": "confirmar_faltante",
            "evidencia": {"partida": partida},
        }
    return {
        "clave": f"matching_s{seccion}_sin_partida",
        "tipo": "matching_sin_par",
        "alcance": {"seccion": seccion},
        "pregunta": (
            f"Seccion {seccion} no tiene Partida correspondiente en la Factura "
            f"({motivo}). Como la tratamos?"
        ),
        "opciones": {
            "confirmar_sobrante": "Confirmar que sobra en la Proforma (requiere correccion)",
            "buscar_manual": "Buscar manualmente antes de decidir",
        },
        "recomendada": "confirmar_sobrante",
        "evidencia": {"seccion": seccion},
    }


def _pregunta_patron(pat):
    afectadas = pat["partidas_afectadas"]
    n = len(afectadas)
    campo = pat.get("campo")
    ejemplo = afectadas[0]
    recomienda_propuesto = pat.get("recomienda_propuesto", False)

    opciones = {
        "usar_propuesto": f"Usar '{ejemplo['valor_propuesto']}' (y equivalentes) para las {n} partidas",
        "usar_actual": f"Mantener '{ejemplo['valor_actual']}' (y equivalentes, valor ya vigente) para las {n}",
        "caso_por_caso": "Revisar caso por caso, no en bloque",
    }
    return {
        "clave": f"patron_{pat['tipo']}",
        "tipo": "patron_sistemico",
        "patron_tipo": pat["tipo"],
        "campo": campo,
        "alcance": {"partidas_afectadas": [a["partida"] for a in afectadas]},
        "pregunta": (
            f"{pat['detalle']} Afecta a {n} partidas ({campo_label(campo)}). "
            "Como lo resolvemos para todas?"
        ),
        "opciones": opciones,
        "recomendada": "usar_propuesto" if recomienda_propuesto else "usar_actual",
        "evidencia": {"partidas_afectadas": [a["partida"] for a in afectadas]},
        "detalle_tabla": [
            {
                "partida": a["partida"],
                "valor_actual": a["valor_actual"],
                "valor_propuesto": a["valor_propuesto"],
            }
            for a in afectadas
        ],
        "_afectadas": afectadas,
    }


def _pregunta_contradiccion(partida, c):
    campo = c["campo"]
    return {
        "clave": f"contradiccion_p{partida}_{campo}",
        "tipo": "contradiccion_individual",
        "campo": campo,
        "alcance": {"partida": partida},
        "pregunta": (
            f"{campo_label(campo)}: Previo '{c['valor_previo']}' vs Factura '{c['valor_factura']}'. "
            f"{c.get('motivo', '')} Cual usamos?"
        ),
        "opciones": {
            "usar_previo": f"Mantener Previo: {c['valor_previo']} (valor ya vigente por jerarquia)",
            "usar_factura": f"Usar Factura: {c['valor_factura']}",
            "sin_resolver": "Marcar sin resolver / requiere verificacion fisica",
        },
        "recomendada": "usar_previo",
        "evidencia": {"partida": partida},
        "_valores": {"usar_previo": c["valor_previo"], "usar_factura": c["valor_factura"]},
    }


def construir_preguntas(matching, diff):
    """Devuelve la lista de preguntas de Paso 8 para ESTA corrida, en el
    orden en que se presentan -- tantas como el matching/diff realmente
    produjeron, nunca un numero fijo. Cada pregunta trae:
      - clave: id estable, usado como key en respuestas_vN.json
      - tipo: mismo vocabulario que gates_vN.json
      - alcance: {"partida":, "seccion":} (uno u otro puede faltar) o
        {"partidas_afectadas": [...]}
      - pregunta: texto para humano
      - opciones: {opcion_clave: label} (dict ordenado)
      - recomendada: opcion_clave marcada como recomendada
      - evidencia: que partidas/secciones mostrar en la UI (para gate_server)
    """
    preguntas = []

    for par in diff.get("pares", []):
        if par.get("needs_gate_matching"):
            preguntas.append(_pregunta_matching(par))

    cubiertos_por_patron = set()
    for pat in diff.get("patrones_sistemicos", []):
        if not pat.get("needs_gate"):
            continue
        preg = _pregunta_patron(pat)
        preguntas.append(preg)
        for a in preg["_afectadas"]:
            cubiertos_por_patron.add((a["partida"], preg["campo"]))

    for par in diff.get("pares", []):
        partida = par.get("partida")
        for c in par.get("contradicciones_previo_factura", []):
            if not c.get("needs_gate"):
                continue
            if (partida, c["campo"]) in cubiertos_por_patron:
                continue
            preguntas.append(_pregunta_contradiccion(partida, c))

    return preguntas
