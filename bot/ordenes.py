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
    """Cuántas acciones comprar para arriesgar la parte de la cartera que fijan las reglas
    (riesgo_por_operacion_pct), sin pasar del tamaño máximo por posición ni de la caja libre.

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


def _n(x, dec=2):
    """Número con coma decimal, para los textos que lee el dueño de la cartera."""
    return ("%.*f" % (dec, x)).replace(".", ",")


def validar(orden, res, valoracion, precio_actual=None, r=None, previsto=True):
    """Devuelve (veredicto, motivos, cantidad). veredicto: 'APROBADA' o 'BLOQUEADA'.

    orden: {ticker, accion, entrada, stop, objetivo}
    res: resumen de la cartera. valoracion: ficha de Finviz del valor (o None).
    precio_actual: en la apertura, el precio en ese momento.
    previsto: falso en una compra que no venía en el plan de la mañana; se le exige la proporción completa
    entre ganancia y riesgo, porque se propone ya con el precio de ahora.
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
    escapado = False
    if precio_actual:
        if precio_actual > entrada * (1 + rr["max_hueco_apertura_pct"] / 100):
            escapado = True
            motivos.append("cotiza a %s $, un %s %% por encima de los %s $ a los que se pensaba comprar; el plan "
                           "admite como mucho un %s %%, porque comprar más caro deja menos ganancia para el mismo riesgo"
                           % (_n(precio_actual), _n((precio_actual / entrada - 1) * 100, 1), _n(entrada),
                              ("%g" % rr["max_hueco_apertura_pct"]).replace(".", ",")))
        if precio_actual <= stop:
            return "BLOQUEADA", motivos + ["cotiza a %s $, ya en el nivel de venta de protección (%s $) o por debajo: la caída "
                                           "que el plan quería evitar ya ha ocurrido" % (_n(precio_actual), _n(stop))], 0
        entrada = precio_actual
        if not stop < entrada < objetivo:
            return "BLOQUEADA", motivos + ["cotiza a %s $, ya en el objetivo del plan (%s $) o por encima: no queda recorrido "
                                           "que ganar" % (_n(precio_actual), _n(objetivo))], 0
    dist = (entrada - stop) / entrada * 100
    if dist < rr["stop_min_pct"]:
        motivos.append("stop demasiado cerca (%.1f %%)" % dist)
    if dist > rr["stop_max_pct"]:
        motivos.append("stop demasiado lejos (%.1f %%)" % dist)
    rb = (objetivo - entrada) / (entrada - stop)
    # Por la mañana se exige la proporción completa. En la apertura, con el precio real, se admite algo
    # menos (min_recompensa_riesgo_apertura): que el valor abra un poco más caro no tira el plan.
    en_apertura = rr.get("min_recompensa_riesgo_apertura", rr["min_recompensa_riesgo"])
    minimo = en_apertura if precio_actual and previsto else rr["min_recompensa_riesgo"]
    if rb < minimo and escapado:
        pass        # ya está dicho: se ha encarecido más de lo admitido
    elif rb < minimo:
        if precio_actual:
            motivos.append("cotiza a %s $ y se pensaba comprar a %s $. A ese precio se arriesgan %s $ por acción hasta el stop "
                           "(%s $) para ganar %s $ hasta el objetivo (%s $): %s veces lo arriesgado, y se exige al menos %s"
                           % (_n(precio_actual), _n(float(orden["entrada"])), _n(entrada - stop), _n(stop), _n(objetivo - entrada),
                              _n(objetivo), _n(rb, 1), _n(minimo, 1)))
        else:
            motivos.append("ganaría %s por cada 1 que arriesga; el mínimo es %s" % (_n(rb, 1), _n(minimo, 1)))
    elif not precio_actual:
        # Margen justo: si abre solo un 1 % más cara, la proporción ya no llega a lo que se exige en la apertura.
        caro = entrada * 1.01
        if (objetivo - caro) / (caro - stop) < en_apertura:
            avisos.append("margen justo: si abre un 1 %% por encima de %s $, ya no ganará %s veces lo que arriesga y no se comprará"
                          % (_n(entrada), _n(en_apertura, 1)))
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
        hoy = ahora_madrid().date()
        fecha = valoracion.get("fecha_de_resultados")    # fecha confirmada del calendario, si la hay
        try:
            dias = (hoy.fromisoformat(fecha) - hoy).days if fecha else None
        except ValueError:
            dias = None
        if dias is None:
            dias = dias_hasta_resultados(valoracion.get("resultados"), hoy)
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
    tesis = {k: orden[k] for k in ("por_que", "que_esperamos", "cuando_saldriamos") if orden.get(k)}
    if api is None:
        return cartera.comprar(c, t, cantidad, precio, stop, objetivo, motivo, "en seco", tesis=tesis)
    ref = "bot-%s-%s" % (ahora_madrid().strftime("%Y%m%d-%H%M"), t)
    enviada, con_salidas = api.comprar(t, cantidad, stop, objetivo, ref)
    hecha = api.esperar_ejecucion(enviada["id"])
    if hecha.get("status") != "filled":
        c.setdefault("pendientes", {})[t] = {"orden_id": enviada["id"], "stop": stop, "objetivo": objetivo,
                                             "motivo": motivo, "stop_en_broker": con_salidas, "tesis": tesis}
        raise ErrorFuente("Alpaca aún no ha ejecutado la compra de %s (estado: %s); queda pendiente"
                          % (t, hecha.get("status")))
    return cartera.comprar(c, t, float(hecha["filled_qty"]), float(hecha["filled_avg_price"]), stop, objetivo,
                           motivo, "alpaca", enviada["id"], con_salidas, tesis=tesis)


def _anotar_venta_real(c, ticker, orden, motivo, previsto=None):
    """Anota una venta al precio al que Alpaca la ha ejecutado. Si se esperaba otro precio (el del stop,
    el del objetivo o la última cotización), guarda también ese y la diferencia en dólares."""
    real = float(orden["filled_avg_price"])
    extra = {"orden_venta_id": orden.get("id")}
    if previsto:
        extra["precio_previsto"] = round(float(previsto), 4)
        extra["deslizamiento_usd"] = round((real - float(previsto)) * c["posiciones"][ticker]["cantidad"], 2)
    return cartera.vender(c, ticker, real, motivo, extra=extra)


def ejecutar_venta(c, ticker, precio, motivo, api=None):
    """Vende una posición entera. `precio` es el previsto: el del stop, el del objetivo o la última cotización.

    Sin bróker (modo en seco) se anota a ese precio. Con Alpaca se anota el precio al que el bróker ejecuta
    de verdad, que puede ser peor: un stop que el bot descubre tarde se vende a lo que cotice en ese momento.
    Si Alpaca no la ejecuta al instante (bolsa cerrada), la posición sigue en cartera marcada como venta
    pendiente y se anota cuando se ejecute (`sincronizar`)."""
    p = c["posiciones"][ticker]
    if p["via"] != "alpaca":
        return cartera.vender(c, ticker, precio, motivo)
    if api is None:       # la posición está en Alpaca: sin conexión no se anota una venta que no ha ocurrido
        raise ErrorFuente("no hay conexión con Alpaca: la venta de %s no se ha lanzado y la posición sigue en cartera" % ticker)
    enviada = api.cerrar(ticker) or {}
    if not enviada.get("id"):
        raise ErrorFuente("Alpaca no ha devuelto la orden de venta de %s; la posición sigue en cartera" % ticker)
    hecha = api.esperar_ejecucion(enviada["id"])
    if hecha.get("status") != "filled":
        p["venta_pendiente"] = {"orden_id": enviada["id"], "motivo": motivo, "precio_previsto": precio,
                                "desde": ahora_madrid().strftime("%Y-%m-%d %H:%M")}
        raise ErrorFuente("Alpaca aún no ha ejecutado la venta de %s (estado: %s); queda pendiente y se anotará "
                          "al precio real cuando se ejecute" % (ticker, hecha.get("status")))
    return _anotar_venta_real(c, ticker, hecha, motivo, precio)


def descuadres(c, api):
    """Diferencias entre la cartera del bot y la cuenta de Alpaca, en frases. Lista vacía si cuadran."""
    en_alpaca = {p["symbol"]: float(p["qty"]) for p in api.posiciones() or []}
    out = []
    for t, p in sorted(c["posiciones"].items()):
        if p["via"] != "alpaca":
            continue
        q = en_alpaca.pop(t, None)
        if p.get("venta_pendiente"):
            out.append("la venta de %s está enviada a Alpaca y pendiente de ejecutarse" % t)
        elif q is None:
            out.append("%s figura en la cartera del bot y no está en Alpaca" % t)
        elif abs(q - p["cantidad"]) > 0.0001:
            out.append("%s: el bot anota %s acciones y Alpaca tiene %s" % (t, _n(p["cantidad"], 4), _n(q, 4)))
    for t, q in sorted(en_alpaca.items()):
        out.append("%s está en Alpaca (%s acciones) y no en la cartera del bot" % (t, _n(q, 4)))
    return out


def sincronizar(c, api):
    """Pone al día la cartera con lo ocurrido en Alpaca: compras pendientes ya
    ejecutadas, ventas pendientes y salidas (stop u objetivo) que el bróker haya cruzado."""
    hechos = []
    for t in list(c["posiciones"]):
        pen = c["posiciones"][t].get("venta_pendiente")
        if not pen:
            continue
        o = api.orden(pen["orden_id"])
        if o.get("status") == "filled":
            hechos.append(_anotar_venta_real(c, t, o, pen["motivo"], pen.get("precio_previsto")))
        elif o.get("status") in ("canceled", "rejected", "expired"):
            del c["posiciones"][t]["venta_pendiente"]       # no se vendió: la vigilancia volverá a intentarlo
    for t in list(c.get("pendientes", {})):
        pen = c["pendientes"][t]
        o = api.orden(pen["orden_id"])
        if o.get("status") == "filled":
            hechos.append(cartera.comprar(c, t, float(o["filled_qty"]), float(o["filled_avg_price"]),
                                          pen["stop"], pen["objetivo"], pen["motivo"], "alpaca",
                                          pen["orden_id"], pen["stop_en_broker"], tesis=pen.get("tesis")))
            del c["pendientes"][t]
        elif o.get("status") in ("canceled", "rejected", "expired"):
            del c["pendientes"][t]
    for t in list(c["posiciones"]):
        p = c["posiciones"][t]
        if p.get("venta_pendiente") or not (p["via"] == "alpaca" and p.get("stop_en_broker") and p.get("orden_id")):
            continue
        for pata in api.orden(p["orden_id"]).get("legs") or []:
            if pata.get("status") == "filled":
                motivo = "stop" if pata.get("type") in ("stop", "stop_limit") else "objetivo"
                hechos.append(cartera.vender(c, t, float(pata["filled_avg_price"]), motivo + " (ejecutado por Alpaca)"))
                break
    return hechos
