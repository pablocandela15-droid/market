"""Control del crédito mensual de la API de Claude.

Cada llamada a un modelo anota su coste real. Antes de la tarea de las 7:00 se
mira lo que queda del crédito y los días que faltan, y se elige el nivel de
análisis de hoy: así se aprovecha el crédito sin pasarse.

El crédito no da para el nivel alto todos los días, de modo que se reparte:
los días sin mercado van como mucho en un nivel intermedio y el nivel alto se
gasta en los días de mercado, empezando por los lunes, que es cuando se
estrena la simulación semanal. Ver `elegir_nivel`.
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
    por_ejecucion, repeticiones = {}, {}
    for g in leer_lineas(ARCHIVO):
        marca = g["nivel"] if g["tarea"] == "manana" else g["tarea"]
        if marca == clave:
            k = (g["fecha"], g["tarea"])
            por_ejecucion[k] = por_ejecucion.get(k, 0.0) + g["coste_usd"]
            # Si la tarea de la mañana se repitió ese día, el total se reparte entre las
            # ejecuciones completas (cada una llama una vez al investigador de noticias).
            if g.get("papel") == "investigador-noticias":
                repeticiones[k] = repeticiones.get(k, 0) + 1
    ultimas = [v / max(1, repeticiones.get(k, 1)) for k, v in por_ejecucion.items()][-5:]
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
    # La simulación semanal de MiroFish (domingos): se aparta su tope para cada domingo que quede,
    # contando las excepciones de un solo día ("solo_el").
    mf = r.get("mirofish") or {}
    fijo += sum(((mf.get("solo_el") or {}).get(d.isoformat()) or {}).get("tope_usd", mf.get("tope_usd", 0))
                for d in dias if d.weekday() == 6) if mf else 0
    libre = cred["usd_mes"] - cred["reserva_usd"] - gastado
    return {"credito_usd": cred["usd_mes"], "reserva_usd": cred["reserva_usd"], "gastado_mes_usd": gastado,
            "proxima_renovacion": renovacion.isoformat(),
            "dias_restantes": len(dias), "dias_de_mercado_restantes": dias_mercado,
            "apartado_apertura_y_cierre_usd": round(fijo, 2),
            "para_las_mananas_usd": round(max(libre - fijo, 0), 2),
            "disponible_hoy_para_la_manana_usd": round(max(libre - fijo, 0) / len(dias), 2),
            "hoy_hay_mercado": bool(sesion(hoy)),
            "lunes_de_mercado_despues": sum(1 for d in dias[1:] if d.weekday() == 0 and sesion(d)),
            "libre_usd": round(libre, 2)}


def elegir_nivel(para_mananas, dias_restantes, hoy_hay_mercado, hoy_es_lunes, lunes_despues, costes,
                 tope_sin_mercado="medio"):
    """Nivel de la mañana de hoy y el motivo. No lee nada: solo hace la cuenta.

    para_mananas: lo que queda para todas las mañanas del ciclo, la de hoy incluida.
    costes: lo que cuesta una mañana en cada nivel.

    Reglas, en orden:
    - Un día sin mercado va como mucho en `tope_sin_mercado`.
    - Un nivel cabe hoy si, después de pagarlo, queda para hacer todas las demás
      mañanas en el nivel inmediatamente inferior. Así un nivel caro se usa algunos
      días sin dejar a los demás sin análisis.
    - El nivel alto se guarda primero para los lunes que quedan: un día que no es
      lunes solo lo usa si además sobra para ponerlo en todos ellos.
    """
    resto = max(dias_restantes - 1, 0)
    niveles = list(NIVELES)
    motivo = ""
    if not hoy_hay_mercado and tope_sin_mercado in niveles:
        niveles = niveles[niveles.index(tope_sin_mercado):]
        motivo = "hoy no hay mercado: nivel %s como máximo" % tope_sin_mercado
    for nivel in niveles:
        i = NIVELES.index(nivel)
        inferior = costes[NIVELES[i + 1]] if i + 1 < len(NIVELES) else 0.0
        sobra = para_mananas - costes[nivel] - resto * inferior
        if sobra < 0:
            continue
        if nivel == "alto" and not hoy_es_lunes and sobra < lunes_despues * (costes["alto"] - costes["medio"]):
            motivo = "el nivel alto se guarda para los lunes que quedan"
            continue
        return nivel, motivo or "cabe en el crédito que queda"
    return "sin_modelo", "el crédito de API del mes no da para más"


def nivel_para_hoy(hoy=None):
    """Nivel de análisis de las 7:00 de hoy: alto, medio, bajo o sin_modelo."""
    hoy = hoy or ahora_madrid().date()
    r = reglas()
    p = presupuesto(hoy, r)
    tipicos = {n: coste_tipico(n, r) for n in NIVELES}
    nivel, motivo = elegir_nivel(p["para_las_mananas_usd"], p["dias_restantes"], p["hoy_hay_mercado"],
                                 hoy.weekday() == 0, p["lunes_de_mercado_despues"],
                                 {n: c for n, (c, _) in tipicos.items()},
                                 r["credito_api"].get("nivel_maximo_sin_mercado", "medio"))
    coste, medido = tipicos.get(nivel, (0.0, False))
    p.update({"nivel": nivel, "motivo_del_nivel": motivo, "coste_previsto_usd": coste, "coste_medido": medido})
    return p


def cabe(tarea):
    """¿Queda crédito para la llamada de apertura o de cierre?"""
    p = presupuesto()
    return p["libre_usd"] >= coste_tipico(tarea)[0]
