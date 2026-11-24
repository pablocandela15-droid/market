"""El universo de valores: el S&P 500 completo, con su sector, más los temas de config/universo.json.

La lista del índice se toma de la tabla pública de Wikipedia y se guarda 30
días. Si un día no se puede descargar, se usa la última guardada.
"""
from __future__ import annotations

import datetime as dt
import html
import re

from .base import RAIZ, ErrorFuente, escribir_json, leer_json, pedir, universo

CACHE = RAIZ / "estado" / "cache" / "sp500.json"
SECTOR_ES = {
    "Information Technology": "Tecnología", "Health Care": "Salud", "Financials": "Finanzas",
    "Consumer Discretionary": "Consumo discrecional", "Communication Services": "Comunicación",
    "Industrials": "Industria", "Consumer Staples": "Consumo básico", "Energy": "Energía",
    "Utilities": "Servicios públicos", "Real Estate": "Inmobiliario", "Materials": "Materiales"}
_MEMO = {}


def _texto(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _descargar():
    h = pedir("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
              {"User-Agent": "bot-bolsa/0.1 (uso personal)"}, espera=40)
    i = h.find('id="constituents"')
    if i < 0:
        raise ErrorFuente("la tabla del S&P 500 ha cambiado de formato")
    out = {}
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", h[i:h.find("</table>", i)], re.S)[1:]:
        c = [_texto(x) for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", fila, re.S)]
        if len(c) >= 4 and c[0] and c[2] in SECTOR_ES:
            out[c[0].upper()] = {"empresa": c[1], "sector": SECTOR_ES[c[2]], "industria": c[3]}
    if len(out) < 400:
        raise ErrorFuente("la tabla del S&P 500 ha devuelto solo %d empresas" % len(out))
    return out


def sp500():
    """{ticker: {empresa, sector, industria}} de las empresas del índice."""
    if "sp500" in _MEMO:
        return _MEMO["sp500"]
    guardado = leer_json(CACHE)
    reciente = guardado and (dt.date.today() - dt.date.fromisoformat(guardado["descargado"])).days < 30
    if not reciente:
        try:
            guardado = {"descargado": dt.date.today().isoformat(), "empresas": _descargar()}
            escribir_json(CACHE, guardado)
        except ErrorFuente:
            if not guardado:
                raise
    _MEMO["sp500"] = guardado["empresas"]
    return _MEMO["sp500"]


def temas():
    """{ticker: tema} de los valores agrupados a mano en config/universo.json."""
    out = {}
    for tema, lista in universo().get("temas", {}).items():
        for t in lista:
            out.setdefault(t.upper(), tema)
    return out


def valores():
    """{ticker: sector}. Sector del índice; para lo que no está en él, su tema."""
    if "valores" not in _MEMO:
        out = {t: v["sector"] for t, v in sp500().items()}
        for t, tema in temas().items():
            out.setdefault(t, tema)
        _MEMO["valores"] = out
    return _MEMO["valores"]


def nombre(ticker):
    return (sp500().get(ticker) or {}).get("empresa")
