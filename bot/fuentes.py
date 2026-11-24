"""Fuentes de valoración y contexto: Finviz, FRED, Damodaran y Dataroma.

Todas se leen con moderación: pocas páginas, con pausa, y lo que cambia poco
se guarda en estado/cache/ para no volver a pedirlo.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import time

from .base import RAIZ, ErrorFuente, claves, escribir_json, leer_json, pedir

CACHE = RAIZ / "estado" / "cache"


def _texto(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _numero(s):
    """'3025.27B' -> 3025270000000.0, '12.5%' -> 12.5, '-' -> None."""
    if s is None:
        return None
    s = s.strip().replace(",", "").replace("$", "")
    if s in ("", "-", "N/A"):
        return None
    mult = 1.0
    if s[-1:] in "KMBT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[s[-1]]
        s = s[:-1]
    s = s.rstrip("%")
    try:
        return float(s) * mult
    except ValueError:
        return None


# ---------------------------------------------------------------- Finviz

def finviz(ticker):
    """Ficha de valoración de un valor: P/E, P/E futuro, PEG y contexto."""
    h = pedir("https://finviz.com/quote.ashx?t=%s" % ticker.replace(".", "-"), espera=30)
    pares = re.findall(r'snapshot-td-label[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>', h, re.S)
    if len(pares) < 20:
        raise ErrorFuente("Finviz cambió el formato de la ficha de %s" % ticker)
    d = {_texto(a): _texto(b) for a, b in pares}
    sector = re.search(r'href="[^"]*f=sec_[^"]*"[^>]*>(.*?)</a>', h, re.S)
    industria = re.search(r'href="[^"]*f=ind_[^"]*"[^>]*>(.*?)</a>', h, re.S)
    time.sleep(1.0)
    return {
        "per": _numero(d.get("P/E")), "per_futuro": _numero(d.get("Forward P/E")),
        "peg": _numero(d.get("PEG")), "capitalizacion": _numero(d.get("Market Cap")),
        "crec_bpa_prox_ano_pct": _numero(d.get("EPS next Y")),
        "crec_ventas_trim_pct": _numero(d.get("Sales Q/Q")),
        "margen_neto_pct": _numero(d.get("Profit Margin")),
        "cortos_pct": _numero(d.get("Short Float")),
        "deuda_capital": _numero(d.get("Debt/Eq")),
        "resultados": d.get("Earnings"), "precio_objetivo_analistas": _numero(d.get("Target Price")),
        "sector": _texto(sector.group(1)) if sector else None,
        "industria": _texto(industria.group(1)) if industria else None,
    }


_MESES = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def dias_hasta_resultados(texto, hoy=None):
    """'Nov 25 AMC' -> días que faltan. None si no hay fecha o ya pasó hace tiempo."""
    m = re.match(r"([A-Z][a-z]{2}) (\d{1,2})", texto or "")
    if not m or m.group(1) not in _MESES:
        return None
    hoy = hoy or dt.date.today()
    for anio in (hoy.year, hoy.year + 1):
        try:
            f = dt.date(anio, _MESES[m.group(1)], int(m.group(2)))
        except ValueError:
            return None
        if -5 <= (f - hoy).days <= 200:
            return (f - hoy).days
    return None


# ---------------------------------------------------------------- FRED

SERIES_FRED = {
    "DFF": "Tipo oficial efectivo (%)", "DGS2": "Bono 2 años (%)", "DGS10": "Bono 10 años (%)",
    "T10Y2Y": "Curva 10a-2a (puntos)", "VIXCLS": "VIX", "BAMLH0A0HYM2": "Diferencial alto riesgo (%)",
    "CPIAUCSL": "IPC (índice)", "UNRATE": "Paro (%)", "DEXUSEU": "Dólares por euro",
    "DCOILWTICO": "Petróleo WTI ($)",
}


def fred():
    """Último dato y dato de hace un mes de cada serie. None si falta la clave."""
    clave = claves().get("FRED_API_KEY")
    if not clave:
        return None
    out = {}
    desde = (dt.date.today() - dt.timedelta(days=430)).isoformat()
    for serie, nombre in SERIES_FRED.items():
        url = ("https://api.stlouisfed.org/fred/series/observations?series_id=%s&api_key=%s"
               "&file_type=json&observation_start=%s&sort_order=desc&limit=400" % (serie, clave, desde))
        try:
            obs = [o for o in json.loads(pedir(url)).get("observations", []) if o.get("value") not in (".", None)]
        except (ErrorFuente, ValueError) as e:
            out[serie] = {"nombre": nombre, "error": str(e)[:80].replace(clave, "***")}
            continue
        if not obs:
            continue
        ult = obs[0]
        fila = {"nombre": nombre, "valor": float(ult["value"]), "fecha": ult["date"]}
        mensual = serie in ("CPIAUCSL", "UNRATE")
        atras = obs[12] if mensual and len(obs) > 12 else (obs[21] if not mensual and len(obs) > 21 else None)
        if atras:
            fila["hace_un_ano" if mensual else "hace_un_mes"] = float(atras["value"])
            if serie == "CPIAUCSL":
                fila["inflacion_anual_pct"] = round((fila["valor"] / float(atras["value"]) - 1) * 100, 2)
        out[serie] = fila
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------- Damodaran

def damodaran():
    """P/E y PEG medios por industria (datos de enero de cada año). Se guarda 30 días."""
    ruta = CACHE / "damodaran-pe.json"
    guardado = leer_json(ruta)
    if guardado and (dt.date.today() - dt.date.fromisoformat(guardado["descargado"])).days < 30:
        return guardado
    h = pedir("https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html", espera=40)
    filas = re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S | re.I)
    industrias = {}
    for f in filas[1:]:
        c = [_texto(x) for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", f, re.S | re.I)]
        if len(c) >= 10 and c[0]:
            industrias[c[0]] = {"empresas": _numero(c[1]), "per_actual": _numero(c[3]),
                                "per_ultimos_12m": _numero(c[4]), "per_futuro": _numero(c[5]),
                                "crecimiento_esperado_pct": _numero(c[8]), "peg": _numero(c[9])}
    if len(industrias) < 50:
        raise ErrorFuente("Damodaran cambió el formato de la tabla de P/E")
    doc = {"descargado": dt.date.today().isoformat(), "industrias": industrias}
    escribir_json(ruta, doc)
    return doc


# Industrias de Damodaran que sirven de vara de medir para cada sector y tema del universo.
INDUSTRIA_DE_SECTOR = {
    "Tecnología": ["Semiconductor", "Software (System & Application)", "Computers/Peripherals", "Computer Services"],
    "Salud": ["Drugs (Pharmaceutical)", "Drugs (Biotechnology)", "Healthcare Products", "Healthcare Support Services"],
    "Finanzas": ["Bank (Money Center)", "Banks (Regional)", "Insurance (General)", "Brokerage & Investment Banking",
                 "Financial Svcs. (Non-bank & Insurance)"],
    "Consumo discrecional": ["Retail (General)", "Retail (Special Lines)", "Auto & Truck", "Restaurant/Dining",
                             "Hotel/Gaming", "Homebuilding"],
    "Comunicación": ["Software (Internet)", "Entertainment", "Telecom. Services", "Advertising"],
    "Industria": ["Machinery", "Aerospace/Defense", "Transportation", "Electrical Equipment", "Engineering/Construction"],
    "Consumo básico": ["Food Processing", "Beverage (Soft)", "Household Products", "Retail (Grocery and Food)", "Tobacco"],
    "Energía": ["Oil/Gas (Integrated)", "Oil/Gas (Production and Exploration)", "Oilfield Svcs/Equip.", "Oil/Gas Distribution"],
    "Servicios públicos": ["Utility (General)", "Power", "Utility (Water)"],
    "Inmobiliario": ["R.E.I.T.", "Real Estate (Operations & Services)"],
    "Materiales": ["Chemical (Specialty)", "Chemical (Basic)", "Metals & Mining", "Steel", "Packaging & Container"],
    "Inteligencia artificial y chips": ["Semiconductor", "Semiconductor Equip"],
    "Renovables": ["Green & Renewable Energy", "Power"],
    "Nuclear y uranio": ["Power", "Utility (General)", "Metals & Mining"],
    "Defensa": ["Aerospace/Defense"],
}


def media_del_sector(sector, tabla=None):
    """Media de las industrias de referencia de un sector: P/E futuro y PEG."""
    tabla = tabla or damodaran()
    nombres = [i for i in INDUSTRIA_DE_SECTOR.get(sector, []) if i in tabla["industrias"]] or \
              [i for i in ("Total Market",) if i in tabla["industrias"]]
    filas = [tabla["industrias"][i] for i in nombres]
    if not filas:
        return None

    def med(k):
        v = [f[k] for f in filas if f.get(k)]
        return round(sum(v) / len(v), 1) if v else None
    return {"per_futuro": med("per_futuro"), "per_12m": med("per_ultimos_12m"), "peg": med("peg"),
            "industrias": nombres}


# ---------------------------------------------------------------- Dataroma

def dataroma(ticker):
    """Qué hacen los grandes inversores con un valor (declaraciones trimestrales 13F)."""
    h = pedir("https://www.dataroma.com/m/stock.php?sym=%s" % ticker.replace(".", "-"), espera=30)
    filas = [[_texto(x) for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", f, re.S | re.I)]
             for f in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S | re.I)]
    out = {"propietarios": None, "movimientos": []}
    for c in filas:
        if len(c) == 2 and c[0].startswith("Ownership count"):
            out["propietarios"] = _numero(c[1])
        elif len(c) >= 6 and c[0] == "≡" and c[3]:
            out["movimientos"].append({"gestor": c[1], "peso_cartera_pct": _numero(c[2]), "ultimo": c[3]})
    compras = sum(1 for m in out["movimientos"] if m["ultimo"].startswith(("Add", "Buy")))
    ventas = sum(1 for m in out["movimientos"] if m["ultimo"].startswith(("Reduce", "Sell")))
    out.update({"amplian": compras, "reducen": ventas, "movimientos": out["movimientos"][:6]})
    time.sleep(1.0)
    return out
