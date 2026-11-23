"""Precios desde el punto de datos público de Yahoo Finance (no oficial, gratuito).

Mismo método que day-trader-skill: query2 primero, query1 si falla, y una
pausa corta entre peticiones.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.parse

from .base import ErrorFuente, UA_CLIENTE, pedir

SERVIDORES = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
PAUSA = 0.25


def serie(simbolo, rango="1y", intervalo="1d"):
    """Velas de un símbolo: listas t, o, h, l, c, v sin huecos, más los metadatos."""
    ruta = "/v8/finance/chart/%s?range=%s&interval=%s" % (
        urllib.parse.quote(simbolo.upper().replace(".", "-")), rango, intervalo)
    ultimo = None
    for srv in SERVIDORES:
        try:
            doc = json.loads(pedir(srv + ruta, {"User-Agent": UA_CLIENTE}, reintentos=1, pausa=4))
            break
        except (ErrorFuente, ValueError) as e:
            ultimo = e
    else:
        raise ErrorFuente("Yahoo no dio datos de %s (%s)" % (simbolo, ultimo))
    res = (doc.get("chart") or {}).get("result")
    if not res:
        raise ErrorFuente("Yahoo no conoce %s" % simbolo)
    res = res[0]
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    filas = []
    for i, ts in enumerate(t):
        c = (q.get("close") or [None] * len(t))[i]
        if c is None:
            continue
        filas.append((ts, (q.get("open") or [c] * len(t))[i] or c, (q.get("high") or [c] * len(t))[i] or c,
                      (q.get("low") or [c] * len(t))[i] or c, c, (q.get("volume") or [0] * len(t))[i] or 0))
    if not filas:
        raise ErrorFuente("Yahoo devolvió %s sin velas" % simbolo)
    time.sleep(PAUSA)
    cols = list(zip(*filas))
    return {"simbolo": simbolo, "t": list(cols[0]), "o": list(cols[1]), "h": list(cols[2]),
            "l": list(cols[3]), "c": list(cols[4]), "v": list(cols[5]), "meta": res.get("meta") or {}}


def _var(c, n):
    return round((c[-1] / c[-1 - n] - 1) * 100, 2) if len(c) > n and c[-1 - n] else None


def _fecha(s):
    """Fecha de la última vela: dice de qué sesión es el último precio."""
    return datetime.datetime.fromtimestamp(s["t"][-1], datetime.timezone.utc).date().isoformat()


def _media(x, n):
    return sum(x[-n:]) / n if len(x) >= n else None


def rsi(c, n=14):
    if len(c) <= n:
        return None
    sub = baj = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        sub += max(d, 0)
        baj += max(-d, 0)
    sub, baj = sub / n, baj / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        sub = (sub * (n - 1) + max(d, 0)) / n
        baj = (baj * (n - 1) + max(-d, 0)) / n
    return 100.0 if baj == 0 else round(100 - 100 / (1 + sub / baj), 1)


def atr(s, n=14):
    """Rango medio diario (Wilder): cuánto se mueve el valor en un día normal."""
    h, l, c = s["h"], s["l"], s["c"]
    if len(c) <= n:
        return None
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def ficha(s):
    """Indicadores de un valor a partir de sus velas diarias."""
    c, v = s["c"], s["v"]
    precio = c[-1]
    m20, m50 = _media(c, 20), _media(c, 50)
    a = atr(s)
    maximo = max(s["h"][-252:])
    vol20 = _media(v[:-1], 20) if len(v) > 20 else None
    return {
        "precio": round(precio, 2), "fecha_dato": _fecha(s),
        "var_1d": _var(c, 1), "var_5d": _var(c, 5), "var_1m": _var(c, 21), "var_3m": _var(c, 63),
        "rsi14": rsi(c),
        "sobre_media20_pct": round((precio / m20 - 1) * 100, 1) if m20 else None,
        "sobre_media50_pct": round((precio / m50 - 1) * 100, 1) if m50 else None,
        "bajo_maximo_anual_pct": round((precio / maximo - 1) * 100, 1) if maximo else None,
        "atr14": round(a, 2) if a else None,
        "atr14_pct": round(a / precio * 100, 1) if a else None,
        "volumen_vs_media": round(v[-1] / vol20, 2) if vol20 else None,
        "minimo_10d": round(min(s["l"][-10:]), 2),
        "maximo_20d": round(max(s["h"][-20:]), 2),
    }


def cotizacion(simbolo):
    """Precio, variación diaria y a 5 y 21 sesiones de un índice, divisa o materia prima."""
    s = serie(simbolo, "3mo", "1d")
    c = s["c"]
    return {"precio": round(c[-1], 4 if c[-1] < 10 else 2), "fecha_dato": _fecha(s), "var_1d": _var(c, 1),
            "var_5d": _var(c, 5), "var_1m": _var(c, 21)}


def intradia(simbolo):
    """La sesión de hoy en velas de 5 minutos: apertura, último, máximo, mínimo y hueco."""
    s = serie(simbolo, "1d", "5m")
    previo = s["meta"].get("chartPreviousClose") or s["meta"].get("previousClose")
    out = {"apertura": round(s["o"][0], 2), "ultimo": round(s["c"][-1], 2),
           "maximo": round(max(s["h"]), 2), "minimo": round(min(s["l"]), 2),
           "cierre_anterior": round(previo, 2) if previo else None, "velas": len(s["c"]),
           "hora_ultima_vela": s["t"][-1]}
    if previo:
        out["hueco_pct"] = round((s["o"][0] / previo - 1) * 100, 2)
        out["var_dia_pct"] = round((s["c"][-1] / previo - 1) * 100, 2)
    return out
