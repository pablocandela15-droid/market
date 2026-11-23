"""Reglas que aprueban o bloquean cada orden, tamaño de la posición y ejecución.

Las reglas son código, no opinión del modelo: una orden que no las cumple no
se ejecuta, diga lo que diga el análisis. Los límites están en config/reglas.json.
"""
from __future__ import annotations

import math

from . import cartera
from .base import ErrorFuente, ahora_madrid, reglas, valores_del_universo
from .fuentes import dias_hasta_resultados


def dimensionar(entrada, stop, res, r=None):
    """Cuántas acciones comprar para arriesgar el 1 % de la cartera.

    Devuelve (cantidad, motivo). cantidad 0 significa que no se puede abrir.
    """
    rr = (r or reglas())["riesgo"]
    riesgo_usd = res["valor_usd"] * rr["riesgo_por_operacion_pct"] / 100
    por_accion = entrada - stop
    if por_accion <= 0:
        return 0, "el stop no está por debajo de la entrada"
    cantidad = riesgo_usd / por_accion
    tope_posicion = res["valor_usd"] * rr["max_posicion_pct"] / 100
    caja_libre = res["caja_usd"] - res["valor_usd"] * rr["colchon_caja_pct"] / 100
    notas = []
    if cantidad * entrada > tope_posicion:
        cantidad = tope_posicion / entrada
        notas.append("limitada al %d %% de la cartera" % rr["max_posicion_pct"])
    if cantidad * entrada > caja_libre:
        cantidad = max(caja_libre, 0) / entrada
        notas.append("limitada por la caja disponible")
    if cantidad >= 1:
        cantidad = math.floor(cantidad)
    elif rr["acciones_fraccionadas"]:
        cantidad = math.floor(cantidad * 10000) / 10000
    else:
        return 0, "no alcanza para una acción entera"
    if cantidad * entrada < rr["min_orden_usd"]:
        return 0, "importe por debajo del mínimo (%s)" % ("; ".join(notas) or "riesgo muy pequeño")
    return cantidad, "; ".join(notas)


def validar(orden, res, valoracion, precio_actual=None, r=None):
    """Devuelve (veredicto, motivos, cantidad). veredicto: 'APROBADA' o 'BLOQUEADA'.

    orden: {ticker, accion, entrada, stop, objetivo}
    res: resumen de la cartera. valoracion: ficha de Finviz del valor (o None).
    precio_actual: en la apertura, el precio en ese momento.
    """
    r = r or reglas()
    rr, rv = r["riesgo"], r["valoracion"]
    t = (orden.get("ticker") or "").upper()
    en_cartera = {p["ticker"] for p in res["posiciones"]}
    if orden.get("accion") == "vender":
        return ("APROBADA", [], None) if t in en_cartera else ("BLOQUEADA", ["no está en cartera"], None)
    motivos, avisos = [], []
    if t not in valores_del_universo():
        motivos.append("fuera del universo de valores")
    if t in en_cartera:
        motivos.append("ya está en cartera")
    if res["huecos_libres"] <= 0:
        motivos.append("ya hay %d posiciones abiertas" % rr["max_posiciones"])
    try:
        entrada, stop, objetivo = float(orden["entrada"]), float(orden["stop"]), float(orden["objetivo"])
    except (KeyError, TypeError, ValueError):
        return "BLOQUEADA", motivos + ["faltan entrada, stop u objetivo"], 0
    if not stop < entrada < objetivo:
        return "BLOQUEADA", motivos + ["el stop debe quedar por debajo de la entrada y el objetivo por encima"], 0
    if precio_actual:
        if precio_actual > entrada * (1 + rr["max_hueco_apertura_pct"] / 100):
            motivos.append("el precio se ha escapado: %.2f frente a %.2f de entrada prevista" % (precio_actual, entrada))
        if precio_actual <= stop:
            motivos.append("el precio ya está en el stop o por debajo")
        entrada = precio_actual
        if not stop < entrada < objetivo:
            return "BLOQUEADA", motivos + ["con el precio actual, la operación ya no tiene sentido"], 0
    dist = (entrada - stop) / entrada * 100
    if dist < rr["stop_min_pct"]:
        motivos.append("stop demasiado cerca (%.1f %%)" % dist)
    if dist > rr["stop_max_pct"]:
        motivos.append("stop demasiado lejos (%.1f %%)" % dist)
    rb = (objetivo - entrada) / (entrada - stop)
    if rb < rr["min_recompensa_riesgo"]:
        motivos.append("gana %.1f por cada 1 que arriesga; el mínimo es %.1f" % (rb, rr["min_recompensa_riesgo"]))
    if not valoracion:
        motivos.append("sin datos de valoración")
    else:
        peg, per = valoracion.get("peg"), valoracion.get("per")
        if peg is not None and peg > rv["peg_maximo"]:
            motivos.append("PEG %.1f por encima de %.1f" % (peg, rv["peg_maximo"]))
        if peg is None and per is None:
            if rv["permitir_sin_beneficios"]:
                avisos.append("sin beneficios en los últimos 12 meses: no tiene P/E ni PEG")
            else:
                motivos.append("empresa sin beneficios")
        dias = dias_hasta_resultados(valoracion.get("resultados"), ahora_madrid().date())
        if dias is not None and 0 <= dias <= rv["dias_sin_comprar_antes_de_resultados"]:
            motivos.append("presenta resultados en %d día(s)" % dias)
    cantidad, nota = dimensionar(entrada, stop, res, r)
    if cantidad <= 0:
        motivos.append(nota)
    elif nota:
        avisos.append(nota)
    if motivos:
        return "BLOQUEADA", motivos, 0
    return "APROBADA", avisos, cantidad


def ejecutar_compra(c, orden, cantidad, precio, api=None):
    """Lanza una compra ya aprobada. Con Alpaca, contra la cuenta de simulación;
    sin claves, se anota en la cartera al precio actual (modo en seco)."""
    t = orden["ticker"].upper()
    stop, objetivo, motivo = float(orden["stop"]), float(orden["objetivo"]), orden.get("motivo", "")
    if api is None:
        return cartera.comprar(c, t, cantidad, precio, stop, objetivo, motivo, "en seco")
    ref = "bot-%s-%s" % (ahora_madrid().strftime("%Y%m%d-%H%M"), t)
    enviada, con_salidas = api.comprar(t, cantidad, stop, objetivo, ref)
    hecha = api.esperar_ejecucion(enviada["id"])
    if hecha.get("status") != "filled":
        c.setdefault("pendientes", {})[t] = {"orden_id": enviada["id"], "stop": stop, "objetivo": objetivo,
                                             "motivo": motivo, "stop_en_broker": con_salidas}
        raise ErrorFuente("Alpaca aún no ha ejecutado la compra de %s (estado: %s); queda pendiente"
                          % (t, hecha.get("status")))
    return cartera.comprar(c, t, float(hecha["filled_qty"]), float(hecha["filled_avg_price"]), stop, objetivo,
                           motivo, "alpaca", enviada["id"], con_salidas)


def ejecutar_venta(c, ticker, precio, motivo, api=None):
    p = c["posiciones"][ticker]
    if api is not None and p["via"] == "alpaca":
        api.cerrar(ticker)
    return cartera.vender(c, ticker, precio, motivo)


def sincronizar(c, api):
    """Pone al día la cartera con lo ocurrido en Alpaca: compras pendientes ya
    ejecutadas y salidas (stop u objetivo) que el bróker haya cruzado."""
    hechos = []
    for t in list(c.get("pendientes", {})):
        pen = c["pendientes"][t]
        o = api.orden(pen["orden_id"])
        if o.get("status") == "filled":
            hechos.append(cartera.comprar(c, t, float(o["filled_qty"]), float(o["filled_avg_price"]),
                                          pen["stop"], pen["objetivo"], pen["motivo"], "alpaca",
                                          pen["orden_id"], pen["stop_en_broker"]))
            del c["pendientes"][t]
        elif o.get("status") in ("canceled", "rejected", "expired"):
            del c["pendientes"][t]
    for t in list(c["posiciones"]):
        p = c["posiciones"][t]
        if not (p["via"] == "alpaca" and p.get("stop_en_broker") and p.get("orden_id")):
            continue
        for pata in api.orden(p["orden_id"]).get("legs") or []:
            if pata.get("status") == "filled":
                motivo = "stop" if pata.get("type") in ("stop", "stop_limit") else "objetivo"
                hechos.append(cartera.vender(c, t, float(pata["filled_avg_price"]), motivo + " (ejecutado por Alpaca)"))
                break
    return hechos
