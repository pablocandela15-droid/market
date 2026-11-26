"""Impuestos estimados de las ventas de la cartera.

Reglas generales del IRPF español para un residente fiscal (territorio común): la ganancia o
la pérdida de cada venta va a la base del ahorro y tributa por tramos. Es una estimación para
saber cuánto se queda Hacienda; no sustituye a un asesor fiscal ni al programa de la renta.

- Cada venta se calcula en euros: lo cobrado, al cambio del día de la venta, menos lo pagado,
  al cambio del día de la compra. Por eso el dólar también cuenta. Se usa el cambio oficial del
  Banco Central Europeo de cada día (se descarga y se guarda en estado/cambio_bce.json); si no
  se ha podido descargar nunca, el que anotó el bot.
- Si se ha comprado el mismo valor varias veces, se venden primero las acciones más antiguas.
- Las pérdidas restan de las ganancias del mismo año; lo que sobre, de las de los cuatro años
  siguientes.
- Regla de los dos meses: una pérdida no cuenta si se ha comprado el mismo valor en los dos
  meses anteriores o posteriores a la venta. Queda aplazada hasta que se vendan esas acciones.
  (La norma aplaza solo la parte que corresponde a las acciones recompradas; aquí se aplaza la
  pérdida entera, que es la lectura prudente.)
- No entran los dividendos (la cartera simulada no los cobra) ni las comisiones (Alpaca no cobra).

Los tramos están en config/reglas.json (impuestos.tramos_ahorro).
"""
from __future__ import annotations

import datetime as dt

from .base import ESTADO, SUFIJO, ErrorFuente, ahora_madrid, escribir_json, leer_json, leer_lineas, pedir, reglas

TRAMOS_2026 = [[6000, 19], [50000, 21], [200000, 23], [300000, 27], [None, 30]]
CAMBIO_BCE = ESTADO / "cambio_bce.json"
HORAS_ENTRE_DESCARGAS = 3
_INTENTADO = {"ya": False}        # una descarga fallida no se repite en la misma ejecución
ANIOS_PARA_COMPENSAR = 4


def tramos(r=None):
    return ((r or reglas()).get("impuestos") or {}).get("tramos_ahorro") or TRAMOS_2026


def desglose(base, escala=None):
    """Cómo se reparte una base del ahorro entre los tramos: [{desde, hasta, tipo_pct, base_eur, cuota_eur}]."""
    filas, previo = [], 0.0
    for hasta, tipo in escala or tramos():
        if base <= previo:
            break
        tope = base if hasta is None else min(base, hasta)
        filas.append({"desde": previo, "hasta": hasta, "tipo_pct": tipo, "base_eur": round(tope - previo, 2),
                      "cuota_eur": round((tope - previo) * tipo / 100, 2)})
        if hasta is None:
            break
        previo = hasta
    return filas


def cuota(base, escala=None):
    """Impuesto de una base del ahorro: cada tramo paga su tipo."""
    return round(sum(f["cuota_eur"] for f in desglose(max(base, 0.0), escala)), 2)


# ---------------------------------------------------------------- cambio euro/dólar

def cambios(c):
    """{fecha: dólares por euro} que el bot conoce: aportaciones y cierres diarios."""
    tabla = {a["fecha"]: a["eurusd"] for a in (c or {}).get("aportaciones", []) if a.get("eurusd")}
    tabla.update({v["fecha"]: v["eurusd"] for v in (c or {}).get("valor_diario", []) if v.get("eurusd")})
    return tabla


def cambios_oficiales(desde=None, descargar=True):
    """Cambio oficial del BCE guardado en este Mac, puesto al día si hace falta. {} si nunca se pudo descargar.
    En las pruebas (BOT_PRUEBA) no se descarga nada."""
    guardado = leer_json(CAMBIO_BCE, {}) or {}
    tabla = guardado.get("cambios") or {}
    ahora = ahora_madrid()
    try:
        reciente = (ahora - dt.datetime.fromisoformat(guardado["descargado"])).total_seconds() < HORAS_ENTRE_DESCARGAS * 3600
    except (KeyError, ValueError, TypeError):
        reciente = False
    falta_inicio = bool(desde) and bool(tabla) and min(tabla) > desde and guardado.get("desde", "9999") > desde
    if descargar and not SUFIJO and not _INTENTADO["ya"] and (not reciente or falta_inicio):
        _INTENTADO["ya"] = True
        inicio = min(x for x in (desde, guardado.get("desde"), (ahora.date() - dt.timedelta(days=10)).isoformat()) if x)
        nuevos = cambios_bce(inicio, ahora.date().isoformat())
        if nuevos:
            tabla = dict(tabla, **nuevos)
            escribir_json(CAMBIO_BCE, {"descargado": ahora.isoformat(timespec="seconds"), "desde": inicio, "cambios": tabla})
    return tabla


def tabla_de_cambios(c, ops=None):
    """(tabla, oficial): el cambio de cada día y si es el oficial del BCE. Sin el oficial, el del bot."""
    fechas = [o["fecha"] for o in (ops if ops is not None else operaciones())] + [a["fecha"] for a in (c or {}).get("aportaciones", [])]
    desde = (dt.date.fromisoformat(min(fechas)) - dt.timedelta(days=7)).isoformat() if fechas else None
    oficial = cambios_oficiales(desde)
    return (oficial, True) if oficial else (cambios(c), False)


def cambio_del(fecha, tabla):
    """El cambio de ese día; si no lo hay, el del día anterior más cercano (o el siguiente)."""
    if fecha in tabla:
        return tabla[fecha]
    antes = [f for f in tabla if f < fecha]
    if antes:
        return tabla[max(antes)]
    despues = [f for f in tabla if f > fecha]
    return tabla[min(despues)] if despues else None


def cambios_bce(desde, hasta):
    """Cambio oficial del Banco Central Europeo (dólares por euro) de cada día hábil entre dos fechas.
    Devuelve {} si no se puede descargar."""
    url = ("https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?startPeriod=%s&endPeriod=%s&format=csvdata"
           % (desde, hasta))
    try:
        lineas = pedir(url, espera=30).strip().splitlines()
        cab = lineas[0].split(",")
        f, v = cab.index("TIME_PERIOD"), cab.index("OBS_VALUE")
        return {x.split(",")[f]: float(x.split(",")[v]) for x in lineas[1:] if x.split(",")[v]}
    except (ErrorFuente, ValueError, IndexError):
        return {}


def _mas_meses(fecha, n):
    f = dt.date.fromisoformat(fecha)
    m = f.month - 1 + n
    anio, mes = f.year + m // 12, m % 12 + 1
    for dia in (f.day, 30, 29, 28):
        try:
            return dt.date(anio, mes, dia).isoformat()
        except ValueError:
            continue


# ---------------------------------------------------------------- ventas en euros

def ventas(ops, tabla, oficial=False):
    """Cada venta, emparejada con su compra y calculada en euros.

    ops: operaciones en orden (las de estado/operaciones.jsonl). tabla: {fecha: dólares por euro}.
    Si la tabla no es la oficial, una operación que trae su propio "eurusd" usa ese.
    """
    lotes, colas, out = [], {}, []
    for o in ops:
        cambio = (None if oficial else o.get("eurusd")) or cambio_del(o["fecha"], tabla) or o.get("eurusd")
        if not cambio:
            continue
        t = o["ticker"]
        if o["accion"] == "compra":
            lote = {"id": len(lotes), "ticker": t, "fecha": o["fecha"], "queda": o["cantidad"], "precio": o["precio"],
                    "eurusd": cambio, "agotado": None}
            lotes.append(lote)
            colas.setdefault(t, []).append(lote)
        elif o["accion"] == "venta":
            falta, coste_usd, coste_eur, usados = o["cantidad"], 0.0, 0.0, []
            cola = colas.get(t, [])
            while falta > 1e-9 and cola:
                lote = cola[0]
                q = min(falta, lote["queda"])
                coste_usd += q * lote["precio"]
                coste_eur += q * lote["precio"] / lote["eurusd"]
                usados.append(lote)
                lote["queda"] -= q
                falta -= q
                if lote["queda"] <= 1e-9:
                    lote["agotado"] = o["fecha"]
                    cola.pop(0)
            ingreso_usd = o["cantidad"] * o["precio"]
            out.append({
                "ticker": t, "cantidad": o["cantidad"], "fecha": o["fecha"], "hora": o.get("hora", ""),
                "fecha_compra": usados[0]["fecha"] if usados else None,
                "precio_compra": round(coste_usd / o["cantidad"], 4) if o["cantidad"] else None,
                "precio_venta": o["precio"], "eurusd_compra": usados[0]["eurusd"] if usados else None,
                "eurusd_venta": cambio, "coste_usd": round(coste_usd, 2), "ingreso_usd": round(ingreso_usd, 2),
                "resultado_usd": round(ingreso_usd - coste_usd, 2),
                "coste_eur": round(coste_eur, 2), "ingreso_eur": round(ingreso_usd / cambio, 2),
                "resultado_eur": round(ingreso_usd / cambio - coste_eur, 2),
                "motivo": o.get("motivo", ""), "sin_compra_conocida": falta > 1e-9,
                "aplazada": False, "fecha_fiscal": o["fecha"], "_lotes": [x["id"] for x in usados]})
    # Regla de los dos meses: una pérdida se aplaza si hay acciones del mismo valor compradas en los dos
    # meses anteriores (y que seguían en cartera) o posteriores a la venta.
    for v in out:
        if v["resultado_eur"] >= 0:
            continue
        desde, hasta = _mas_meses(v["fecha"], -2), _mas_meses(v["fecha"], 2)
        culpables = [x for x in lotes if x["ticker"] == v["ticker"] and x["id"] not in v["_lotes"]
                     and desde <= x["fecha"] <= hasta
                     and (x["fecha"] > v["fecha"] or x["agotado"] is None or x["agotado"] > v["fecha"])]
        if culpables:
            v["aplazada"] = True
            pendientes = [x for x in culpables if x["agotado"] is None]
            v["fecha_fiscal"] = None if pendientes else max(x["agotado"] for x in culpables)
    for v in out:
        del v["_lotes"]
    return out


# ---------------------------------------------------------------- resumen del año

def anual(vs, anio, escala=None):
    """Las cuentas de un año: ganancias, pérdidas, lo que se compensa de años anteriores y el impuesto."""
    escala = escala or tramos()
    anios = sorted({int(v["fecha_fiscal"][:4]) for v in vs if v["fecha_fiscal"]} | {anio})
    pendientes, cuentas = [], {}                # pendientes: [año de la pérdida, importe] aún sin compensar
    for a in range(anios[0], anio + 1):
        del_anio = [v for v in vs if v["fecha_fiscal"] and v["fecha_fiscal"][:4] == str(a)]
        gan = sum(v["resultado_eur"] for v in del_anio if v["resultado_eur"] > 0)
        per = -sum(v["resultado_eur"] for v in del_anio if v["resultado_eur"] < 0)
        neto = gan - per
        pendientes = [p for p in pendientes if a - p[0] <= ANIOS_PARA_COMPENSAR]
        compensado, resto = 0.0, max(neto, 0.0)
        for p in pendientes:                    # primero las pérdidas más antiguas
            usa = min(p[1], resto)
            p[1] -= usa
            resto -= usa
            compensado += usa
        pendientes = [p for p in pendientes if p[1] > 0.005]
        if neto < 0:
            pendientes.append([a, -neto])
        cuentas[a] = {"ventas": del_anio, "ganancias_eur": round(gan, 2), "perdidas_eur": round(per, 2),
                      "neto_eur": round(neto, 2), "compensado_de_anios_anteriores_eur": round(compensado, 2),
                      "base_eur": round(resto, 2), "impuesto_eur": cuota(resto, escala),
                      "perdidas_para_anios_siguientes_eur": round(sum(p[1] for p in pendientes), 2)}
    return cuentas[anio]


def cuando_se_paga(anio):
    return ("en la declaración de la renta de %d, que se presenta entre abril y junio de %d; se puede pagar "
            "en dos plazos, el 60 %% al presentarla y el 40 %% a primeros de noviembre" % (anio, anio + 1))


def operaciones():
    return leer_lineas(ESTADO / "operaciones.jsonl")


def estado(c, posiciones=None, eurusd=None, hoy=None, r=None, ops=None, tabla=None, oficial=False):
    """Cómo van los impuestos del año: lo ya vendido y lo que pasaría si se vendiera hoy lo que queda.

    posiciones: las filas de cartera.resumen (ticker, cantidad, entrada, precio, fecha_entrada).
    """
    hoy = hoy or ahora_madrid().date()
    escala = tramos(r)
    ops = ops if ops is not None else operaciones()
    if tabla is None:
        tabla, oficial = tabla_de_cambios(c, ops)
    vs = ventas(ops, tabla, oficial)
    a = anual(vs, hoy.year, escala)
    aplazadas = [v for v in vs if v["aplazada"] and not v["fecha_fiscal"]]
    out = {"anio": hoy.year, "ventas_del_anio": len(a["ventas"]),
           **{k: a[k] for k in ("ganancias_eur", "perdidas_eur", "neto_eur", "compensado_de_anios_anteriores_eur",
                                "base_eur", "impuesto_eur", "perdidas_para_anios_siguientes_eur")},
           "despues_de_impuestos_eur": round(a["neto_eur"] - a["impuesto_eur"], 2),
           "tipo_efectivo_pct": round(a["impuesto_eur"] / a["base_eur"] * 100, 1) if a["base_eur"] else 0.0,
           "perdidas_aplazadas_eur": round(-sum(v["resultado_eur"] for v in aplazadas), 2),
           "cambio_oficial_del_bce": bool(oficial), "se_paga": cuando_se_paga(hoy.year)}
    if posiciones is not None and eurusd:
        filas, total = [], 0.0
        colchon = a["perdidas_para_anios_siguientes_eur"]      # pérdidas que absorberían una ganancia de hoy
        for p in posiciones:
            compra = cambio_del(p["fecha_entrada"], tabla) or eurusd
            res = p["cantidad"] * p["precio"] / eurusd - p["cantidad"] * p["entrada"] / compra
            total += res
            filas.append({"ticker": p["ticker"], "resultado_eur": round(res, 2),
                          "impuesto_si_se_vende_eur": round(cuota(a["base_eur"] + max(res - colchon, 0), escala)
                                                            - a["impuesto_eur"], 2) if res > 0 else 0.0})
        out["sin_vender"] = {"resultado_eur": round(total, 2), "posiciones": filas,
                             "impuesto_si_se_vende_todo_hoy_eur": round(
                                 max(cuota(a["base_eur"] + total - colchon, escala) - a["impuesto_eur"], 0.0), 2)}
    return out


def periodo(c, desde, hasta, hoy=None, r=None, ops=None, tabla=None, oficial=False):
    """Las ventas entre dos fechas (incluidas) y el impuesto que añaden a la cuenta del año."""
    hoy = hoy or ahora_madrid().date()
    escala = tramos(r)
    ops = ops if ops is not None else operaciones()
    if tabla is None:
        tabla, oficial = tabla_de_cambios(c, ops)
    vs = ventas(ops, tabla, oficial)
    suyas = [v for v in vs if desde <= v["fecha"] <= hasta]
    con = anual(vs, hoy.year, escala)["impuesto_eur"]
    sin = anual([v for v in vs if not desde <= v["fecha"] <= hasta], hoy.year, escala)["impuesto_eur"]
    res = round(sum(v["resultado_eur"] for v in suyas), 2)
    return {"ventas": suyas, "resultado_eur": res, "impuesto_eur": round(max(con - sin, 0.0), 2),
            "ahorro_de_impuestos_eur": round(max(sin - con, 0.0), 2),
            "despues_de_impuestos_eur": round(res - max(con - sin, 0.0), 2)}
