"""Puente con las herramientas de day-trader-skill (clonado en ../repos/).

Se ejecutan como programas aparte, sin tocar su código, y se lee su salida JSON.
Ninguna escribe nada: se llaman con --no-write o --dry-run.
"""
from __future__ import annotations

import json
import subprocess
import sys

from .base import DTS, ErrorFuente, claves


def _correr(args, espera=180):
    if not DTS.exists():
        raise ErrorFuente("no encuentro day-trader-skill en %s" % DTS)
    try:
        r = subprocess.run([sys.executable] + [str(a) for a in args], capture_output=True,
                           text=True, timeout=espera, cwd=str(DTS))
    except subprocess.TimeoutExpired:
        raise ErrorFuente("%s tardó demasiado" % args[0]) from None
    texto = r.stdout.strip()
    inicios = [i for i in (texto.find("{"), texto.find("[")) if i >= 0]
    if not inicios:
        raise ErrorFuente("%s no devolvió JSON: %s" % (args[0], (r.stderr or texto)[:200]))
    ini = min(inicios)
    try:
        return json.loads(texto[ini:])
    except ValueError:
        raise ErrorFuente("%s devolvió JSON ilegible" % args[0]) from None


def escanear(simbolos):
    """Configuraciones técnicas de varios días. Devuelve {ticker: [configuraciones]}."""
    doc = _correr([DTS / "trader-scanner" / "scan.py", "daily", "--symbols", ",".join(simbolos),
                   "--json", "--no-write", "--quiet", "--min-volume", "200000"])
    return {h["symbol"]: h.get("setups", []) for h in doc.get("hits", [])}, doc.get("as_of")


def noticias(tickers, horas=24):
    """Titulares de Yahoo y, si hay identificación para la SEC, hechos relevantes (8-K)."""
    ua = claves().get("SEC_USER_AGENT")
    args = [DTS / "trader-news" / "news.py", "--tickers", *tickers, "--hours", horas,
            "--dry-run", "--all", "--json"]
    args += ["--user-agent", ua] if ua else ["--no-sec"]
    doc = _correr(args)
    items = []
    for it in doc.get("items", []):
        items.append({"ticker": it.get("ticker"), "titular": it.get("title"),
                      "resumen": (it.get("summary") or "")[:240], "hora": it.get("time"),
                      "tipo": it.get("kind"), "fuente": it.get("source"), "enlace": it.get("link"),
                      "etiquetas": it.get("tags") or []})
    return {"movimientos": doc.get("moves", []), "titulares": items, "con_sec": bool(ua)}


def resultados(tickers, trimestres=5):
    """Trimestres, crecimiento y beneficio por acción desde la SEC. Exige identificación."""
    ua = claves().get("SEC_USER_AGENT")
    if not ua:
        return None
    doc = _correr([DTS / "trader-analyst" / "facts.py", *tickers, "--quarters", trimestres,
                   "--no-filings", "--user-agent", ua, "--json"])
    campos = ("fiscal", "end", "revenue", "net_income", "eps_diluted", "gross_margin", "operating_margin",
              "net_margin", "revenue_yoy", "net_income_yoy", "eps_yoy", "filed")
    out = {}
    for item in doc if isinstance(doc, list) else [doc]:
        res = item.get("result") or {}
        if not res.get("rows"):
            continue
        out[item.get("ticker")] = {
            "empresa": res.get("entity"),
            "trimestres": [{k: f.get(k) for k in campos} for f in res["rows"][:3]],
            "ultimos_12_meses": res.get("ttm"),
            "cambios": (item.get("changes") or [])[:5]}
    return out
