"""La cartera simulada: contabilidad propia del bot, en dólares.

Aquí vive la verdad sobre el capital (1.000 € + 200 €/mes), las posiciones y
el resultado. Alpaca en simulación es solo el lugar donde se ejecutan las
órdenes; si no hay claves, las compras y ventas se anotan aquí al último precio.

La referencia es "haber metido el mismo dinero, el mismo día, en el S&P 500"
(índice de precios, sin dividendos).
"""
from __future__ import annotations

import datetime as dt

from .base import ESTADO, anexar_linea, ahora_madrid, escribir_json, leer_json, leer_lineas, reglas

ARCHIVO = ESTADO / "cartera.json"
OPERACIONES = ESTADO / "operaciones.jsonl"


def cargar():
    return leer_json(ARCHIVO)


def guardar(c):
    escribir_json(ARCHIVO, c)


def _aportar(c, eur, eurusd, ref_precio, concepto):
    usd = round(eur * eurusd, 2)
    c["caja_usd"] = round(c["caja_usd"] + usd, 2)
    c["aportaciones"].append({"fecha": ahora_madrid().date().isoformat(), "eur": eur, "usd": usd,
                              "eurusd": round(eurusd, 4), "concepto": concepto})
    c["referencia"]["unidades"] += usd / ref_precio


def crear_si_falta(eurusd, ref_precio):
    """Primera ejecución: entra el capital inicial, convertido a dólares al cambio del día."""
    c = cargar()
    if c:
        return c, False
    r = reglas()
    c = {"creada": ahora_madrid().date().isoformat(), "caja_usd": 0.0, "aportaciones": [],
         "posiciones": {}, "pendientes": {}, "referencia": {"simbolo": r["referencia"], "unidades": 0.0},
         "valor_diario": []}
    _aportar(c, r["capital"]["inicial_eur"], eurusd, ref_precio, "capital inicial")
    guardar(c)
    return c, True


def aportar_si_toca(c, eurusd, ref_precio):
    """Aportación mensual: una por mes natural, a partir del día fijado, desde el mes siguiente al inicial."""
    cap = reglas()["capital"]
    hoy = ahora_madrid().date()
    ultima = dt.date.fromisoformat(c["aportaciones"][-1]["fecha"])
    if (hoy.year, hoy.month) > (ultima.year, ultima.month) and hoy.day >= cap["dia_aportacion"]:
        _aportar(c, cap["aportacion_mensual_eur"], eurusd, ref_precio, "aportación mensual")
        return True
    return False


# ---------------------------------------------------------------- operaciones

def _anotar(ticker, accion, cantidad, precio, motivo, via, extra=None):
    ahora = ahora_madrid()
    fila = {"fecha": ahora.date().isoformat(), "hora": ahora.strftime("%H:%M"), "ticker": ticker,
            "accion": accion, "cantidad": round(cantidad, 6), "precio": round(precio, 4),
            "importe_usd": round(cantidad * precio, 2), "motivo": motivo, "via": via}
    fila.update(extra or {})
    anexar_linea(OPERACIONES, fila)
    return fila


def comprar(c, ticker, cantidad, precio, stop, objetivo, motivo, via, orden_id=None, stop_en_broker=False, tesis=None):
    importe = round(cantidad * precio, 2)
    if importe > c["caja_usd"] + 0.01:
        raise ValueError("no hay caja para comprar %s: %.2f $ > %.2f $" % (ticker, importe, c["caja_usd"]))
    c["caja_usd"] = round(c["caja_usd"] - importe, 2)
    c["posiciones"][ticker] = {
        "cantidad": round(cantidad, 6), "precio_entrada": round(precio, 4),
        "fecha_entrada": ahora_madrid().date().isoformat(), "stop": stop, "objetivo": objetivo,
        "riesgo_usd": round(cantidad * (precio - stop), 2), "motivo": motivo, "via": via,
        "orden_id": orden_id, "stop_en_broker": stop_en_broker, "revisado_hasta": None,
        "tesis": tesis or {}}       # por qué se compró, qué se esperaba y cuándo se saldría
    return _anotar(ticker, "compra", cantidad, precio, motivo, via)


def vender(c, ticker, precio, motivo, via=None):
    p = c["posiciones"].pop(ticker)
    importe = round(p["cantidad"] * precio, 2)
    c["caja_usd"] = round(c["caja_usd"] + importe, 2)
    res = round(p["cantidad"] * (precio - p["precio_entrada"]), 2)
    return _anotar(ticker, "venta", p["cantidad"], precio, motivo, via or p["via"], {
        "resultado_usd": res, "resultado_pct": round((precio / p["precio_entrada"] - 1) * 100, 2),
        "multiplo_riesgo": round(res / p["riesgo_usd"], 2) if p.get("riesgo_usd") else None,
        "dias": (ahora_madrid().date() - dt.date.fromisoformat(p["fecha_entrada"])).days})


def revisar_stops(c, velas):
    """Stops y objetivos que vigila el propio bot (posiciones sin stop en el bróker).

    `velas` es {ticker: serie diaria}. Día de la entrada: solo se mira el cierre,
    porque el mínimo del día incluye horas anteriores a la compra. Días
    siguientes: si abre por debajo del stop se vende a la apertura (hueco); si
    lo toca durante el día, al precio del stop. Si el mismo día toca stop y
    objetivo, se da por bueno el stop.
    """
    ventas = []
    for ticker in list(c["posiciones"]):
        p = c["posiciones"][ticker]
        s = velas.get(ticker)
        if p.get("stop_en_broker") or not s:
            continue
        entrada = p["fecha_entrada"]
        for i, ts in enumerate(s["t"]):
            dia = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
            if dia < entrada or (p.get("revisado_hasta") and dia <= p["revisado_hasta"]):
                continue
            o, h, l, cierre = s["o"][i], s["h"][i], s["l"][i], s["c"][i]
            precio = motivo = None
            if dia == entrada:
                if cierre <= p["stop"]:
                    precio, motivo = cierre, "stop (cierre del día de entrada)"
                elif cierre >= p["objetivo"]:
                    precio, motivo = cierre, "objetivo (cierre del día de entrada)"
            elif o <= p["stop"]:
                precio, motivo = o, "stop saltado por hueco de apertura"
            elif l <= p["stop"]:
                precio, motivo = p["stop"], "stop"
            elif o >= p["objetivo"]:
                precio, motivo = o, "objetivo superado en la apertura"
            elif h >= p["objetivo"]:
                precio, motivo = p["objetivo"], "objetivo"
            if precio is not None:
                ventas.append(vender(c, ticker, precio, motivo))
                break
            if i < len(s["t"]) - 1:          # la última vela puede ser la sesión en curso
                p["revisado_hasta"] = dia
    return ventas


# ---------------------------------------------------------------- valoración

def resumen(c, precios, eurusd, ref_precio):
    """Foto de la cartera con los precios dados ({ticker: precio})."""
    r = reglas()
    filas, valor_pos = [], 0.0
    for t, p in sorted(c["posiciones"].items()):
        precio = precios.get(t) or p["precio_entrada"]
        valor = p["cantidad"] * precio
        valor_pos += valor
        filas.append({
            "ticker": t, "cantidad": p["cantidad"], "entrada": p["precio_entrada"], "precio": round(precio, 2),
            "valor_usd": round(valor, 2), "resultado_usd": round(valor - p["cantidad"] * p["precio_entrada"], 2),
            "resultado_pct": round((precio / p["precio_entrada"] - 1) * 100, 2),
            "stop": p["stop"], "objetivo": p["objetivo"],
            "hasta_stop_pct": round((p["stop"] / precio - 1) * 100, 1),
            "hasta_objetivo_pct": round((p["objetivo"] / precio - 1) * 100, 1),
            "fecha_entrada": p["fecha_entrada"], "via": p["via"],
            "dias_en_cartera": (ahora_madrid().date() - dt.date.fromisoformat(p["fecha_entrada"])).days,
            "motivo_de_compra": p.get("motivo"), "tesis_de_compra": p.get("tesis") or {}})
    valor = c["caja_usd"] + valor_pos
    aportado_eur = sum(a["eur"] for a in c["aportaciones"])
    aportado_usd = sum(a["usd"] for a in c["aportaciones"])
    ref = c["referencia"]["unidades"] * ref_precio
    hoy = ahora_madrid().date().isoformat()
    previos = [v for v in c["valor_diario"] if v["fecha"] < hoy]
    lunes = (ahora_madrid().date() - dt.timedelta(days=ahora_madrid().weekday())).isoformat()
    antes_semana = [v for v in c["valor_diario"] if v["fecha"] < lunes]

    def base(anterior, desde):
        """Punto de partida de un periodo: el último cierre anterior más el dinero
        aportado después. Sin cierre anterior (primer día), todo lo aportado."""
        if not anterior:
            return {"valor_usd": aportado_usd, "referencia_usd": aportado_usd}
        nuevo = sum(a["usd"] for a in c["aportaciones"] if a["fecha"] >= desde)
        return {"valor_usd": anterior["valor_usd"] + nuevo, "referencia_usd": anterior["referencia_usd"] + nuevo}
    ayer = base(previos[-1] if previos else None, hoy)
    base_semana = base(antes_semana[-1] if antes_semana else None, lunes)
    aportado_hoy = sum(a["usd"] for a in c["aportaciones"] if a["fecha"] == hoy)

    def pct(a, b):
        return round((a / b - 1) * 100, 2) if b else None
    return {
        "valor_usd": round(valor, 2), "valor_eur": round(valor / eurusd, 2), "caja_usd": c["caja_usd"],
        "invertido_pct": round(valor_pos / valor * 100, 1) if valor else 0.0,
        "aportado_eur": aportado_eur, "aportado_usd": round(aportado_usd, 2),
        "resultado_total_usd": round(valor - aportado_usd, 2), "resultado_total_pct": pct(valor, aportado_usd),
        "resultado_total_eur": round(valor / eurusd - aportado_eur, 2),
        "inicio_dia_usd": round(ayer["valor_usd"], 2), "aportado_hoy_usd": round(aportado_hoy, 2),
        "resultado_dia_usd": round(valor - ayer["valor_usd"], 2) if ayer else None,
        "resultado_dia_pct": pct(valor, ayer["valor_usd"]) if ayer else None,
        "resultado_semana_pct": pct(valor, base_semana["valor_usd"]) if base_semana else None,
        "referencia_usd": round(ref, 2), "referencia_pct": pct(ref, aportado_usd),
        "ventaja_sobre_referencia_usd": round(valor - ref, 2),
        "referencia_dia_pct": pct(ref, ayer["referencia_usd"]) if ayer else None,
        "posiciones": filas, "huecos_libres": r["riesgo"]["max_posiciones"] - len(filas),
        "riesgo_por_operacion_usd": round(valor * r["riesgo"]["riesgo_por_operacion_pct"] / 100, 2),
        "eurusd": round(eurusd, 4),
    }


def registrar_valor(c, res):
    """Guarda el valor de cierre del día (una fila por fecha)."""
    hoy = ahora_madrid().date().isoformat()
    c["valor_diario"] = [v for v in c["valor_diario"] if v["fecha"] != hoy]
    c["valor_diario"].append({"fecha": hoy, "valor_usd": res["valor_usd"], "valor_eur": res["valor_eur"],
                              "referencia_usd": res["referencia_usd"], "eurusd": res["eurusd"]})


def operaciones(desde=None):
    ops = leer_lineas(OPERACIONES)
    return [o for o in ops if not desde or o["fecha"] >= desde]


def estadisticas():
    """Marcador de operaciones cerradas."""
    ventas = [o for o in leer_lineas(OPERACIONES) if o["accion"] == "venta"]
    if not ventas:
        return {"cerradas": 0}
    gan = [o for o in ventas if o["resultado_usd"] > 0]
    return {"cerradas": len(ventas), "ganadoras": len(gan),
            "acierto_pct": round(len(gan) / len(ventas) * 100),
            "resultado_usd": round(sum(o["resultado_usd"] for o in ventas), 2),
            "mejor": max(ventas, key=lambda o: o["resultado_usd"])["ticker"],
            "peor": min(ventas, key=lambda o: o["resultado_usd"])["ticker"]}
