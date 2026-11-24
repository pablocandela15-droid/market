"""Fuentes con clave gratuita: Alpha Vantage y Finnhub.

Alpha Vantage (plan gratuito): calendario de resultados de todo el mercado y
el historial de sorpresas de cada empresa. Admite 25 consultas al día y una
por segundo, así que todo se guarda en estado/cache/ y se pide lo justo. Su
sentimiento de noticias y la ficha de empresa son de pago.

Finnhub: recomendaciones de analistas y operaciones de directivos. Solo se usa
si la clave es válida.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import time

from .base import RAIZ, ErrorFuente, ahora_madrid, claves, escribir_json, leer_json, pedir

CACHE = RAIZ / "estado" / "cache"
TOPE_AV_DIA = 20


# ---------------------------------------------------------------- Alpha Vantage

def _av(errores, **parametros):
    """Una consulta a Alpha Vantage, respetando su ritmo y su tope diario. Devuelve texto o None."""
    clave = claves().get("ALPHAVANTAGE_API_KEY")
    if not clave:
        return None
    hoy = ahora_madrid().date().isoformat()
    uso = leer_json(CACHE / "av-uso.json", {})
    if uso.get("fecha") != hoy:
        uso = {"fecha": hoy, "consultas": 0}
    if uso["consultas"] >= TOPE_AV_DIA:
        return None
    uso["consultas"] += 1
    escribir_json(CACHE / "av-uso.json", uso)
    time.sleep(1.5)
    q = "&".join("%s=%s" % kv for kv in parametros.items())
    try:
        texto = pedir("https://www.alphavantage.co/query?%s&apikey=%s" % (q, clave), reintentos=0, espera=40)
    except ErrorFuente as e:
        errores.append("Alpha Vantage: %s" % str(e).replace(clave, "***")[:90])
        return None
    if texto.lstrip().startswith("{") and ('"Information"' in texto[:60] or '"Note"' in texto[:60]):
        errores.append("Alpha Vantage: sin acceso o tope alcanzado (%s)" % parametros.get("function"))
        return None
    return texto


def calendario_resultados(errores):
    """{ticker: fecha} de las presentaciones de resultados de los próximos tres meses. Una consulta al día."""
    hoy = ahora_madrid().date().isoformat()
    guardado = leer_json(CACHE / "av-calendario.json", {})
    if guardado.get("fecha") == hoy:
        return guardado["fechas"]
    texto = _av(errores, function="EARNINGS_CALENDAR", horizon="3month")
    if not texto:
        return guardado.get("fechas", {})
    fechas = {}
    for fila in csv.DictReader(io.StringIO(texto)):
        if fila.get("symbol") and fila.get("reportDate"):
            fechas.setdefault(fila["symbol"].upper(), fila["reportDate"])
    if len(fechas) < 50:
        return guardado.get("fechas", {})
    escribir_json(CACHE / "av-calendario.json", {"fecha": hoy, "fechas": fechas})
    return fechas


def sorpresas(ticker, errores):
    """Los últimos cuatro trimestres: beneficio por acción real frente al esperado. Se guarda 20 días."""
    todo = leer_json(CACHE / "av-resultados.json", {})
    g = todo.get(ticker)
    if g and (dt.date.today() - dt.date.fromisoformat(g["guardado"])).days < 20:
        return g["trimestres"]
    texto = _av(errores, function="EARNINGS", symbol=ticker.replace(".", "-"))
    if not texto:
        return (g or {}).get("trimestres")
    try:
        filas = json.loads(texto).get("quarterlyEarnings") or []
    except ValueError:
        return (g or {}).get("trimestres")
    trimestres = [{"trimestre": f.get("fiscalDateEnding"), "publicado": f.get("reportedDate"), "bpa": f.get("reportedEPS"),
                   "bpa_esperado": f.get("estimatedEPS"), "sorpresa_pct": f.get("surprisePercentage")} for f in filas[:4]]
    todo[ticker] = {"guardado": dt.date.today().isoformat(), "trimestres": trimestres}
    escribir_json(CACHE / "av-resultados.json", todo)
    return trimestres


# ---------------------------------------------------------------- Finnhub

def _clave_finnhub(errores):
    """La clave de Finnhub, solo si tiene su forma (letras minúsculas y números, sin prefijos).
    Una clave de otro servicio pegada aquí por error no se envía a Finnhub."""
    clave = (claves().get("FINNHUB_API_KEY") or "").strip()
    if not clave:
        return None
    if not re.fullmatch(r"[a-z0-9]{16,48}", clave):
        if not any("Finnhub" in x for x in errores):
            errores.append("Finnhub: lo guardado en FINNHUB_API_KEY no tiene forma de clave de Finnhub y no se usa")
        return None
    return clave


def _finnhub(ruta, errores, **parametros):
    clave = _clave_finnhub(errores)
    if not clave:
        return "clave_no_valida" if (claves().get("FINNHUB_API_KEY") or "").strip() else None
    q = "&".join("%s=%s" % kv for kv in parametros.items())
    try:
        time.sleep(1.1)
        return json.loads(pedir("https://finnhub.io/api/v1%s?%s&token=%s" % (ruta, q, clave), reintentos=0, espera=30))
    except (ErrorFuente, ValueError) as e:
        texto = str(e).replace(clave, "***")
        if "401" in texto or "Invalid API key" in texto:
            if not any("Finnhub" in x for x in errores):
                errores.append("Finnhub: la clave guardada no es válida")
            return "clave_no_valida"
        errores.append("Finnhub %s: %s" % (ruta, texto[:80]))
        return None


def analistas(tickers, errores):
    """Recomendaciones de los analistas de cada valor (comprar, mantener, vender). Se guarda tres días."""
    todo, out = leer_json(CACHE / "finnhub-analistas.json", {}), {}
    for t in tickers:
        g = todo.get(t)
        if g and (dt.date.today() - dt.date.fromisoformat(g["guardado"])).days < 3:
            out[t] = g["dato"]
            continue
        r = _finnhub("/stock/recommendation", errores, symbol=t)
        if r == "clave_no_valida":
            break
        if isinstance(r, list) and r:
            u = r[0]
            dato = {"periodo": u.get("period"), "comprar": (u.get("strongBuy") or 0) + (u.get("buy") or 0),
                    "mantener": u.get("hold") or 0, "vender": (u.get("sell") or 0) + (u.get("strongSell") or 0)}
            todo[t] = {"guardado": dt.date.today().isoformat(), "dato": dato}
            out[t] = dato
    if out:
        escribir_json(CACHE / "finnhub-analistas.json", todo)
    return out or None


def directivos(tickers, errores):
    """Compras y ventas de directivos de los últimos 90 días, resumidas por valor."""
    desde = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    out = {}
    for t in tickers:
        r = _finnhub("/stock/insider-transactions", errores, symbol=t, **{"from": desde})
        if r == "clave_no_valida":
            break
        filas = (r or {}).get("data") if isinstance(r, dict) else None
        if filas:
            compras = sum(1 for f in filas if (f.get("change") or 0) > 0 and f.get("transactionCode") == "P")
            ventas = sum(1 for f in filas if (f.get("change") or 0) < 0 and f.get("transactionCode") == "S")
            out[t] = {"compras_en_mercado": compras, "ventas_en_mercado": ventas, "dias": 90}
    return out or None
