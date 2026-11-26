"""Informe para la declaración de la renta: todas las operaciones del año y la cuenta de impuestos.

    python3 -m bot.tarea renta               # el año en curso, hasta hoy
    python3 -m bot.tarea renta --anio 2026   # un año concreto

No llama a ningún modelo: son cuentas. Deja en Informes/Renta/<año>/ el PDF y dos hojas de cálculo
(CSV) con las operaciones y con las ventas, para pasarlas al programa de la renta o a un gestor.

Alpaca es un bróker de EE. UU.: no informa a Hacienda ni retiene nada, así que estas cuentas las tiene
que llevar quien declara. Se usa el cambio oficial del Banco Central Europeo de cada día, el mismo que en
los demás informes; si nunca se pudo descargar, el que guardó el bot.
"""
from __future__ import annotations

import csv
import datetime as dt

from . import cartera, impuestos
from . import informe as inf
from . import tarea as T
from .base import ahora_madrid, carpeta_informes, reglas


def _carpeta(anio):
    p = carpeta_informes().parent.parent / "Renta" / str(anio)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _f(fecha):
    return "%s/%s/%s" % (fecha[8:10], fecha[5:7], fecha[:4]) if fecha else ""


def _acciones(n):
    return inf.num(n, 0 if n == int(n) else 4)


def cuentas(anio, hoy=None, oficial=None):
    """Todo lo que lleva el informe, sin dibujarlo: operaciones y ventas en euros, y la cuenta del año."""
    hoy = hoy or ahora_madrid().date()
    c = cartera.cargar() or {}
    ops = impuestos.operaciones()
    if oficial is None:
        tabla, oficial = impuestos.tabla_de_cambios(c, ops)
    else:
        tabla = oficial or impuestos.cambios(c)
    escala = impuestos.tramos()
    vs = impuestos.ventas(ops, tabla, bool(oficial))
    a = impuestos.anual(vs, anio, escala)
    del_anio = []
    for o in ops:
        if o["fecha"][:4] != str(anio):
            continue
        cambio = (None if oficial else o.get("eurusd")) or impuestos.cambio_del(o["fecha"], tabla) or o.get("eurusd")
        del_anio.append(dict(o, cambio=cambio, importe_eur=round(o["importe_usd"] / cambio, 2) if cambio else None))
    aplazadas = [v for v in vs if v["aplazada"] and v["fecha"][:4] == str(anio)]
    return {"anio": anio, "hasta": min(hoy.isoformat(), "%d-12-31" % anio), "cambio_oficial": bool(oficial),
            "operaciones": del_anio, "ventas": [v for v in vs if v["fecha"][:4] == str(anio)], "cuenta": a,
            "tramos": impuestos.desglose(a["base_eur"], escala), "aplazadas": aplazadas,
            "posiciones": [dict(p, ticker=t, cambio=impuestos.cambio_del(p["fecha_entrada"], tabla))
                           for t, p in sorted((c.get("posiciones") or {}).items())] if anio == hoy.year else []}


def _csv(ruta, cabecera, filas):
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:      # con marca para que Excel lea las tildes
        w = csv.writer(f, delimiter=";")
        w.writerow(cabecera)
        w.writerows(filas)


def _dec(x, n=2):
    return "" if x is None else ("%.*f" % (n, x)).replace(".", ",")


def ejecutar(a):
    hoy = ahora_madrid().date()
    anio = getattr(a, "anio", None) or hoy.year
    q = cuentas(anio, hoy)
    cu, carpeta = q["cuenta"], _carpeta(anio)
    cerrado = hoy.year > anio
    cuerpo = ['<div class="aviso"><b>Esto es una cartera simulada.</b> Mientras no haya dinero real no hay nada que declarar: '
              "el informe enseña cómo quedaría la declaración si estas operaciones fueran de verdad. Es una estimación con las "
              "reglas generales del IRPF para un residente en España; no sustituye a un asesor fiscal ni al programa de la renta.</div>"]
    cuerpo.append(inf.cifras([
        ("Ganancias de %d" % anio, inf.dinero(cu["ganancias_eur"], "€")),
        ("Pérdidas de %d" % anio, inf.dinero(cu["perdidas_eur"], "€")),
        ("Resultado de las ventas", inf.dinero(cu["neto_eur"], "€", True)),
        ("Impuesto estimado", inf.dinero(cu["impuesto_eur"], "€"))]))
    if not cerrado:
        cuerpo.append("<p class='gris'>El año %d no ha terminado: son las cuentas hasta el %s. Lo que se venda hasta el 31 de "
                      "diciembre las cambiará.</p>" % (anio, _f(q["hasta"])))
    # La cuenta, paso a paso
    filas = [["Ganancias de las ventas con beneficio", inf.dinero(cu["ganancias_eur"], "€")],
             ["Pérdidas de las ventas con pérdida", inf.dinero(-cu["perdidas_eur"], "€", True)],
             ["<b>Resultado del año</b>", "<b>%s</b>" % inf.dinero(cu["neto_eur"], "€", True)]]
    if cu["compensado_de_anios_anteriores_eur"]:
        filas.append(["Pérdidas de años anteriores que se restan", inf.dinero(-cu["compensado_de_anios_anteriores_eur"], "€", True)])
    filas.append(["<b>Ganancia que paga impuesto</b> (base del ahorro)", "<b>%s</b>" % inf.dinero(cu["base_eur"], "€")])
    for t in q["tramos"]:
        tramo = "hasta %s €" % inf.num(t["hasta"], 0) if t["hasta"] else "de %s € en adelante" % inf.num(t["desde"], 0)
        filas.append(["&nbsp;&nbsp;Tramo %s, al %s&nbsp;%%: %s" % (tramo, inf.num(t["tipo_pct"], 0), inf.dinero(t["base_eur"], "€")),
                      inf.dinero(t["cuota_eur"], "€")])
    filas.append(["<b>Impuesto estimado</b>", "<b>%s</b>" % inf.dinero(cu["impuesto_eur"], "€")])
    filas.append(["<b>Te queda después de impuestos</b>", "<b>%s</b>" % inf.dinero(cu["neto_eur"] - cu["impuesto_eur"], "€", True)])
    if cu["perdidas_para_anios_siguientes_eur"]:
        filas.append(["Pérdidas que quedan para restar en los cuatro años siguientes", inf.dinero(cu["perdidas_para_anios_siguientes_eur"], "€")])
    if q["aplazadas"]:
        filas.append(["Pérdidas aplazadas por la regla de los dos meses (no entran en la cuenta)",
                      inf.dinero(-sum(v["resultado_eur"] for v in q["aplazadas"]), "€")])
    cuerpo.append("<h2>Cómo sale la cuenta</h2>" + inf.tabla([("", 0), ("Euros", 1)], filas))
    cuerpo.append("<h2>Cuándo y cómo se paga</h2><p>No se paga al vender. Las ventas de %d se declaran y se pagan %s. "
                  "Si el resultado del año es una pérdida no se paga nada, pero conviene declararla igualmente para poder restarla "
                  "de ganancias futuras.</p>" % (anio, inf.e(impuestos.cuando_se_paga(anio))))
    # Ventas: lo que se declara
    cuerpo.append("<h2>Ventas del año: lo que se declara, una a una</h2>" + inf.tabla(
        [("Valor", 0), ("Acciones", 1), ("Comprada", 0), ("Vendida", 0), ("Valor de compra", 1), ("Valor de venta", 1),
         ("Resultado", 1), ("Cambio compra / venta", 1), ("Nota", 0)],
        [[inf.e(v["ticker"]), _acciones(v["cantidad"]), _f(v["fecha_compra"]), _f(v["fecha"]), inf.dinero(v["coste_eur"], "€"),
          inf.dinero(v["ingreso_eur"], "€"), inf.dinero(v["resultado_eur"], "€", True),
          "%s / %s" % (inf.num(v["eurusd_compra"], 4), inf.num(v["eurusd_venta"], 4)),
          "pérdida aplazada (regla de los dos meses)" if v["aplazada"] else inf.e(v["motivo"])] for v in q["ventas"]],
        "En %d no se ha vendido nada: no hay ganancias ni pérdidas que declarar." % anio))
    cuerpo.append("<p class='gris'>En el programa de la renta van en ganancias y pérdidas patrimoniales, como transmisiones de "
                  "acciones admitidas a negociación: por cada venta, el valor de venta y el valor de compra en euros.</p>")
    # Todas las operaciones
    cuerpo.append("<h2>Todas las operaciones de %d</h2>" % anio + inf.tabla(
        [("Fecha", 0), ("Hora", 0), ("Valor", 0), ("Operación", 0), ("Acciones", 1), ("Precio", 1), ("Importe", 1),
         ("Cambio $/€", 1), ("En euros", 1), ("Resultado", 1)],
        [[_f(o["fecha"]), inf.e(o.get("hora", "")), inf.e(o["ticker"]), inf.e(o["accion"]), _acciones(o["cantidad"]),
          inf.dinero(o["precio"]), inf.dinero(o["importe_usd"]), inf.num(o["cambio"], 4), inf.dinero(o["importe_eur"], "€"),
          inf.dinero(o.get("resultado_usd"), "$", True) if o["accion"] == "venta" else ""] for o in q["operaciones"]],
        "En %d no ha habido operaciones." % anio))
    if q["posiciones"]:
        cuerpo.append("<h2>Lo que sigue en cartera: no se declara hasta venderlo</h2>" + inf.tabla(
            [("Valor", 0), ("Acciones", 1), ("Comprada", 0), ("Precio de compra", 1), ("Pagado", 1), ("Pagado en euros", 1)],
            [[inf.e(p["ticker"]), _acciones(p["cantidad"]), _f(p["fecha_entrada"]), inf.dinero(p["precio_entrada"]),
              inf.dinero(p["cantidad"] * p["precio_entrada"]),
              inf.dinero(p["cantidad"] * p["precio_entrada"] / p["cambio"], "€") if p["cambio"] else ""] for p in q["posiciones"]]))
    cuerpo.append("<h2>Qué tener en cuenta con un bróker extranjero</h2>" + inf.lista([
        "Alpaca es un bróker de Estados Unidos. No informa a la Hacienda española ni te retiene nada: las ventas las declaras tú, "
        "con estas cuentas.",
        "Cada compra y cada venta se pasan a euros con el cambio de su día. %s" % (
            "Aquí se ha usado el cambio oficial del Banco Central Europeo." if q["cambio_oficial"] else
            "No se ha podido descargar el cambio oficial del Banco Central Europeo: se ha usado el que guardó el bot, que puede "
            "diferir en algún céntimo."),
        "Estados Unidos no cobra impuesto por la ganancia de un no residente. Sí retiene parte de los dividendos (un 15 % si el "
        "bróker tiene tu formulario W-8BEN), que en España se declaran y se descuentan para no pagar dos veces. La simulación no "
        "cobra dividendos, así que aquí no aparecen.",
        "Si vendes con pérdida y vuelves a comprar el mismo valor en los dos meses de antes o de después, esa pérdida no se puede "
        "restar hasta que vendas las acciones recompradas.",
        "El modelo 720 (bienes en el extranjero) solo hace falta si lo que tienes fuera de España supera los 50.000 €.",
        "País Vasco y Navarra tienen su propia escala. Antes de presentar una declaración real, contrasta estas cuentas con un "
        "gestor o con el programa de Hacienda."]))
    ruta, paginas, escala = inf.componer(
        "Informe para la renta de %d" % anio, "operaciones del 1 de enero al %s · hecho el %s" % (_f(q["hasta"]), T._fecha_larga(ahora_madrid())),
        ["cartera simulada", "impuesto estimado: %s €" % inf.num(cu["impuesto_eur"])], "".join(cuerpo), inf.PIE,
        carpeta / ("renta-%d.pdf" % anio), escala_fija=1.0)
    _csv(carpeta / ("renta-%d-operaciones.csv" % anio),
         ["Fecha", "Hora", "Valor", "Operación", "Acciones", "Precio (USD)", "Importe (USD)", "Cambio USD por EUR", "Importe (EUR)",
          "Resultado (USD)", "Motivo"],
         [[o["fecha"], o.get("hora", ""), o["ticker"], o["accion"], _dec(o["cantidad"], 6), _dec(o["precio"], 4), _dec(o["importe_usd"]),
           _dec(o["cambio"], 4), _dec(o["importe_eur"]), _dec(o.get("resultado_usd")) if o["accion"] == "venta" else "",
           o.get("motivo", "")] for o in q["operaciones"]])
    _csv(carpeta / ("renta-%d-ventas.csv" % anio),
         ["Valor", "Acciones", "Fecha de compra", "Fecha de venta", "Valor de compra (EUR)", "Valor de venta (EUR)",
          "Resultado (EUR)", "Cambio en la compra", "Cambio en la venta", "Resultado (USD)", "Pérdida aplazada"],
         [[v["ticker"], _dec(v["cantidad"], 6), v["fecha_compra"] or "", v["fecha"], _dec(v["coste_eur"]), _dec(v["ingreso_eur"]),
           _dec(v["resultado_eur"]), _dec(v["eurusd_compra"], 4), _dec(v["eurusd_venta"], 4), _dec(v["resultado_usd"]),
           "sí" if v["aplazada"] else "no"] for v in q["ventas"]])
    return "renta %d: %d operaciones, %d ventas, resultado %.2f €, impuesto estimado %.2f € -> %s (%s pág.) y dos CSV en %s" % (
        anio, len(q["operaciones"]), len(q["ventas"]), cu["neto_eur"], cu["impuesto_eur"], ruta, paginas, carpeta)
