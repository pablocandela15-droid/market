"""Recogida de datos de cada tarea. No gasta tokens: todo son scripts.

El resultado se guarda en datos/AAAA-MM-DD/dossier-<tarea>.json y es lo único
que leen los modelos, además de lo que busquen en la web.
"""
from __future__ import annotations

from . import cartera, dts, fuentes, gasto, yahoo
from .base import (ErrorFuente, ahora_madrid, carpeta_del_dia, escribir_json, estado_mercado,
                   leer_json, reglas, universo, valores_del_universo)

BUENAS = ("breakout_20d", "bull_flag", "reclaim_ma20", "oversold_bounce")
NOMBRE_CONFIG = {"breakout_20d": "ruptura de máximos de 20 días", "bull_flag": "pausa tras subida",
                 "reclaim_ma20": "recupera la media de 20 días", "oversold": "sobrevendido",
                 "oversold_bounce": "rebote desde sobreventa"}


def _cotizaciones(mapa, errores):
    out = {}
    for nombre, simbolo in mapa.items():
        try:
            out[nombre] = yahoo.cotizacion(simbolo)
        except ErrorFuente as e:
            errores.append("%s: %s" % (nombre, e))
    return out


def tabla_universo(errores, solo=None):
    """Una fila por valor con precio, tendencia y configuración técnica. Devuelve
    también las velas, que hacen falta para vigilar stops. Con `solo`, se
    limita a esos valores (apertura y cierre no necesitan el universo entero)."""
    valores = valores_del_universo()
    if solo is not None:
        valores = {t: valores.get(t, "otros") for t in solo}
    if not valores:
        return {}, {}
    try:
        configs, _ = dts.escanear(list(valores))
    except ErrorFuente as e:
        configs = {}
        errores.append("escáner: %s" % e)
    filas, velas = {}, {}
    for t, sector in valores.items():
        try:
            s = yahoo.serie(t)
        except ErrorFuente as e:
            errores.append("%s: %s" % (t, e))
            continue
        velas[t] = s
        f = yahoo.ficha(s)
        f.update({"sector": sector, "configuraciones": [NOMBRE_CONFIG.get(x, x) for x in configs.get(t, [])],
                  "_claves": configs.get(t, [])})
        filas[t] = f
    return filas, velas


def _puntos(f):
    p = 3.0 * sum(1 for x in f["_claves"] if x in BUENAS) + (1.0 if "oversold" in f["_claves"] else 0.0)
    p += max(min((f.get("var_1m") or 0) / 10, 2), -2)
    p += 0.5 if (f.get("volumen_vs_media") or 0) > 1.5 else 0.0
    return p


def elegir_candidatos(filas, fijos, maximo):
    """Valores cuya valoración se consulta hoy: cartera y prioritarios siempre,
    y el resto por señal técnica, con un mínimo de tres por sector."""
    elegidos = [t for t in fijos if t in filas]
    por_sector = {}
    for t, f in sorted(filas.items(), key=lambda kv: -_puntos(kv[1])):
        por_sector.setdefault(f["sector"], []).append(t)
    for lista in por_sector.values():
        for t in lista[:3]:
            if t not in elegidos and len(elegidos) < maximo:
                elegidos.append(t)
    for t, _ in sorted(filas.items(), key=lambda kv: -_puntos(kv[1])):
        if t not in elegidos and len(elegidos) < maximo:
            elegidos.append(t)
    return elegidos


def valoraciones(tickers, errores, carpeta=None):
    """Fichas de Finviz, guardadas por día para no repetir peticiones entre tareas."""
    carpeta = carpeta or carpeta_del_dia("datos")
    guardadas = leer_json(carpeta / "finviz.json", {})
    limite = reglas()["limites_fuentes"]["finviz_max_valores_dia"] + 6
    for t in tickers:
        if t in guardadas:
            continue
        if len(guardadas) >= limite:
            errores.append("Finviz: tope diario alcanzado, %s sin valoración" % t)
            continue
        try:
            guardadas[t] = fuentes.finviz(t)
        except ErrorFuente as e:
            errores.append("Finviz %s: %s" % (t, e))
    escribir_json(carpeta / "finviz.json", guardadas)
    return {t: guardadas[t] for t in tickers if t in guardadas}


def construir(tarea):
    """Dossier completo de la tarea. Devuelve (dossier, velas, cartera)."""
    errores = []
    ahora = ahora_madrid()
    u, r = universo(), reglas()
    mercado = _cotizaciones(u["mercado"], errores)
    eurusd = (mercado.get("Euro/dólar") or {}).get("precio") or 1.0
    ref = (mercado.get("S&P 500") or {}).get("precio")
    c, nueva = cartera.crear_si_falta(eurusd, ref)
    aporto = False if nueva else cartera.aportar_si_toca(c, eurusd, ref)
    en_cartera = list(c["posiciones"])
    fijos = en_cartera + [t for t in u["prioritarios"] if t not in en_cartera]
    plan, del_plan = {}, []
    if tarea != "manana":
        plan = leer_json(carpeta_del_dia("datos") / "plan.json", {})
        del_plan = [o["ticker"].upper() for o in plan.get("ordenes", []) if o.get("ticker")]
    filas, velas = tabla_universo(errores, None if tarea == "manana" else list(dict.fromkeys(fijos + del_plan)))
    precios = {t: f["precio"] for t, f in filas.items()}
    d = {
        "tarea": tarea, "generado": ahora.strftime("%Y-%m-%d %H:%M"), "hora_madrid": ahora.strftime("%H:%M"),
        "mercado_hoy": estado_mercado(ahora), "indices": mercado,
        "modo": "alpaca (simulación)" if _hay_alpaca() else "en seco (sin claves de Alpaca)",
        "reglas": {"riesgo": r["riesgo"], "valoracion": r["valoracion"], "operativa": r["operativa"]},
        "prioritarios": u["prioritarios"],
        "nota_datos": ("Cada precio lleva 'fecha_dato', la sesión a la que pertenece. 'var_1d' compara ese precio "
                       "con el cierre de la sesión anterior. Si 'mercado_hoy.abierto_ahora' es verdadero y la fecha "
                       "es la de hoy, es el precio de la sesión en curso, no un cierre."),
    }
    if tarea == "manana":
        d["materias_primas"] = _cotizaciones(u["materias_primas"], errores)
        d["sectores"] = {s: _cotizaciones({x: x for x in lista}, errores)
                         for s, lista in u["fondos_de_contexto"].items() if s != "_nota"}
        cand = elegir_candidatos(filas, fijos, r["limites_fuentes"]["finviz_max_valores_dia"])
        val = valoraciones(cand, errores)
        try:
            tabla = fuentes.damodaran()
            d["media_sector"] = {s: fuentes.media_del_sector(s, tabla) for s in u["sectores"]}
        except ErrorFuente as e:
            errores.append("Damodaran: %s" % e)
        d["candidatos"] = cand
        d["macro"] = fuentes.fred()
        if d["macro"] is None:
            errores.append("FRED: falta la clave gratuita; el bloque macro sale solo con datos de mercado")
        try:        # trimestres y crecimiento desde la SEC; solo si hay identificación en .env
            d["resultados_sec"] = dts.resultados(list(dict.fromkeys(fijos + cand))[:6])
        except ErrorFuente as e:
            errores.append("SEC: %s" % e)
        horas, con_noticias = 20, cand[:r["limites_fuentes"]["noticias_max_valores"]]
        if ahora.weekday() == 6:      # domingo: grandes inversores
            d["grandes_inversores"] = {}
            for t in fijos + [x for x in cand if x not in fijos]:
                if len(d["grandes_inversores"]) >= r["limites_fuentes"]["dataroma_max_valores"]:
                    break
                try:
                    d["grandes_inversores"][t] = fuentes.dataroma(t)
                except ErrorFuente as e:
                    errores.append("Dataroma %s: %s" % (t, e))
            horas = 60
    else:
        val = valoraciones([t for t in dict.fromkeys(fijos + del_plan) if t in filas], errores)
        horas, con_noticias = 9, list(dict.fromkeys(fijos + del_plan))
        d["plan_de_la_manana"] = plan
        if d["mercado_hoy"]["abre_hoy"]:
            d["sesion_de_hoy"] = {}
            for t in dict.fromkeys(en_cartera + del_plan):
                try:
                    d["sesion_de_hoy"][t] = yahoo.intradia(t)
                    precios[t] = d["sesion_de_hoy"][t]["ultimo"]
                except ErrorFuente as e:
                    errores.append("sesión de %s: %s" % (t, e))
    try:
        d["noticias"] = dts.noticias(con_noticias, horas) if con_noticias else {"titulares": [], "movimientos": []}
        d["noticias"]["titulares"] = d["noticias"]["titulares"][:40]
    except ErrorFuente as e:
        errores.append("noticias: %s" % e)
        d["noticias"] = {"titulares": [], "movimientos": []}
    for f in filas.values():
        f.pop("_claves", None)
    d["valoracion"] = val
    d["universo"] = filas
    d["cartera"] = cartera.resumen(c, precios, eurusd, ref)
    d["cartera"]["aportacion_hoy"] = "capital inicial" if nueva else ("aportación mensual" if aporto else None)
    d["marcador"] = cartera.estadisticas()
    d["operaciones_recientes"] = cartera.operaciones()[-10:]
    d["credito_api"] = gasto.presupuesto()
    d["avisos_de_datos"] = errores
    escribir_json(carpeta_del_dia("datos") / ("dossier-%s.json" % tarea), d)
    return d, velas, c


def _hay_alpaca():
    from .base import claves
    k = claves()
    return bool(k.get("ALPACA_API_KEY") and k.get("ALPACA_SECRET_KEY"))
