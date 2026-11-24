"""Control del crédito mensual de la API de Claude.

Cada llamada a un modelo anota su coste real. Antes de la tarea de las 7:00 se
reparte lo que queda del crédito entre los días que quedan del mes y se elige
el nivel de análisis que cabe: así se aprovecha el crédito sin pasarse.
"""
from __future__ import annotations

import datetime as dt

from .base import ESTADO, ahora_madrid, anexar_linea, leer_lineas, reglas, sesion

ARCHIVO = ESTADO / "gasto_api.jsonl"
NIVELES = ("alto", "medio", "bajo")


def anotar(tarea, papel, modelo, nivel, coste_usd, uso=None, ok=True, via="clave"):
    """via: 'clave' (gasta crédito de API) o 'plan' (gasta uso del plan; el
    coste es solo el equivalente, para comparar)."""
    ahora = ahora_madrid()
    anexar_linea(ARCHIVO, {"fecha": ahora.date().isoformat(), "hora": ahora.strftime("%H:%M"),
                           "tarea": tarea, "papel": papel, "modelo": modelo, "nivel": nivel, "via": via,
                           "coste_usd": round(coste_usd or 0.0, 4), "uso": uso or {}, "ok": ok})


def periodo(hoy, r=None):
    """Ciclo del crédito que contiene a `hoy`: (primer día, día de la siguiente renovación)."""
    dia = min((r or reglas())["credito_api"].get("dia_renovacion", 1), 28)
    ini = hoy.replace(day=dia)
    if hoy.day < dia:
        ini = (hoy.replace(day=1) - dt.timedelta(days=1)).replace(day=dia)
    sig = (ini.replace(day=28) + dt.timedelta(days=4)).replace(day=dia)
    return ini, sig


def _del_mes(hoy):
    """Gasto del ciclo que cuenta contra el crédito: solo lo hecho con la clave."""
    ini, sig = periodo(hoy)
    return [g for g in leer_lineas(ARCHIVO)
            if ini.isoformat() <= g["fecha"] < sig.isoformat() and g.get("via", "clave") == "clave"]


def de_hoy():
    """Equivalente en dólares de todas las llamadas de hoy, con clave o con plan."""
    hoy = ahora_madrid().date().isoformat()
    return round(sum(g["coste_usd"] for g in leer_lineas(ARCHIVO) if g["fecha"] == hoy), 2)


def coste_tipico(clave, r=None):
    """Media real de las últimas 5 ejecuciones de ese nivel o tarea; si aún no
    hay ninguna, la estimación de config/reglas.json."""
    r = r or reglas()
    por_ejecucion = {}
    for g in leer_lineas(ARCHIVO):
        marca = g["nivel"] if g["tarea"] == "manana" else g["tarea"]
        if marca == clave:
            k = (g["fecha"], g["tarea"])
            por_ejecucion[k] = por_ejecucion.get(k, 0.0) + g["coste_usd"]
    ultimas = list(por_ejecucion.values())[-5:]
    if ultimas:
        return round(sum(ultimas) / len(ultimas), 3), True
    return r["credito_api"]["coste_estimado_usd"][clave], False


def presupuesto(hoy=None, r=None):
    r = r or reglas()
    hoy = hoy or ahora_madrid().date()
    cred = r["credito_api"]
    gastado = round(sum(g["coste_usd"] for g in _del_mes(hoy)), 2)
    _, renovacion = periodo(hoy, r)
    dias = [hoy + dt.timedelta(days=i) for i in range((renovacion - hoy).days)]
    dias_mercado = sum(1 for d in dias if sesion(d))
    fijo = dias_mercado * (coste_tipico("apertura", r)[0] + coste_tipico("cierre", r)[0])
    # La simulación semanal de MiroFish (domingos): se aparta su tope para cada domingo que quede.
    if r.get("mirofish"):
        fijo += sum(1 for d in dias if d.weekday() == 6) * r["mirofish"].get("tope_usd", 0)
    libre = cred["usd_mes"] - cred["reserva_usd"] - gastado
    return {"credito_usd": cred["usd_mes"], "reserva_usd": cred["reserva_usd"], "gastado_mes_usd": gastado,
            "proxima_renovacion": renovacion.isoformat(),
            "dias_restantes": len(dias), "dias_de_mercado_restantes": dias_mercado,
            "apartado_apertura_y_cierre_usd": round(fijo, 2),
            "disponible_hoy_para_la_manana_usd": round(max(libre - fijo, 0) / len(dias), 2),
            "libre_usd": round(libre, 2)}


def nivel_para_hoy():
    """Nivel de análisis de las 7:00 que cabe hoy: alto, medio, bajo o sin_modelo."""
    p = presupuesto()
    for nivel in NIVELES:
        coste, medido = coste_tipico(nivel)
        if coste <= p["disponible_hoy_para_la_manana_usd"]:
            p.update({"nivel": nivel, "coste_previsto_usd": coste, "coste_medido": medido})
            return p
    p.update({"nivel": "sin_modelo", "coste_previsto_usd": 0.0, "coste_medido": False})
    return p


def cabe(tarea):
    """¿Queda crédito para la llamada de apertura o de cierre?"""
    p = presupuesto()
    return p["libre_usd"] >= coste_tipico(tarea)[0]
