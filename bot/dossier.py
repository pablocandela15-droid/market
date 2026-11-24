"""Recogida de datos de cada tarea. No gasta tokens: todo son scripts.

El resultado se guarda en datos/AAAA-MM-DD/dossier-<tarea>.json y es lo único
que leen los modelos, además de lo que busquen en la web.

Por la mañana se mira el mercado entero: las 500 empresas del S&P 500 y los
valores de los temas, la fuerza de cada sector y los mercados de fuera. Los
datos caros (valoración, noticias, serie completa) se piden solo para los
candidatos que salen de ese barrido.
"""
from __future__ import annotations

from . import cartera, dts, extra, fuentes, gasto, yahoo
from . import universo as uni
from .base import (ErrorFuente, ahora_madrid, carpeta_del_dia, claves, escribir_json, estado_mercado,
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


def _mediana(valores):
    v = sorted(x for x in valores if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return round(v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2, 2)


# ---------------------------------------------------------------- barrido del mercado

def barrido(errores):
    """Una fila por valor de todo el universo, calculada con cierres diarios."""
    valores, temas = valores_del_universo(), uni.temas()
    try:
        configs, _ = dts.escanear(list(valores))
    except ErrorFuente as e:
        configs = {}
        errores.append("escáner: %s" % e)
    precios = yahoo.cierres(list(valores), errores=errores)
    filas = {}
    for t, sector in valores.items():
        if t not in precios:
            continue
        f = yahoo.ficha_de_cierres(precios[t]["c"], precios[t]["fecha"])
        f.update({"sector": sector, "tema": temas.get(t), "empresa": uni.nombre(t),
                  "configuraciones": [NOMBRE_CONFIG.get(x, x) for x in configs.get(t, [])],
                  "_claves": configs.get(t, [])})
        filas[t] = f
    faltan = len(valores) - len(filas)
    if faltan:
        errores.append("sin precios de %d de %d valores" % (faltan, len(valores)))
    return filas


def series_completas(tickers, filas, errores):
    """Velas con máximos y mínimos de los valores que importan hoy; afina su ficha."""
    velas = {}
    for t in tickers:
        try:
            s = yahoo.serie(t)
        except ErrorFuente as e:
            errores.append("%s: %s" % (t, e))
            continue
        velas[t] = s
        previa = filas.get(t, {})
        f = yahoo.ficha(s)
        for k in ("sector", "tema", "empresa", "configuraciones", "_claves"):
            f[k] = previa.get(k, [] if k in ("configuraciones", "_claves") else None)
        f["sector"] = f["sector"] or valores_del_universo().get(t, "otros")
        filas[t] = f
    return velas


def fuerza_de_sectores(filas, fondos):
    """Clasificación de sectores y temas: cuál lidera y cuál se queda atrás.

    Para cada uno, la mediana de sus valores (día, semana, mes, tres meses) y la
    amplitud: qué porcentaje de sus valores cotiza por encima de su media de 50 días.
    """
    grupos = {}
    del_indice = set(uni.SECTOR_ES.values())
    for f in filas.values():
        if f["sector"] in del_indice:
            grupos.setdefault(("sector", f["sector"]), []).append(f)
        if f.get("tema"):
            grupos.setdefault(("tema", f["tema"]), []).append(f)
    out = []
    for (tipo, nombre), fs in grupos.items():
        if len(fs) < 4:
            continue
        sobre50 = [f["sobre_media50_pct"] for f in fs if f.get("sobre_media50_pct") is not None]
        fila = {"nombre": nombre, "tipo": tipo, "valores": len(fs),
                "dia_pct": _mediana(f["var_1d"] for f in fs), "semana_pct": _mediana(f["var_5d"] for f in fs),
                "mes_pct": _mediana(f["var_1m"] for f in fs), "tres_meses_pct": _mediana(f["var_3m"] for f in fs),
                "sobre_media50_pct_de_valores": round(sum(1 for x in sobre50 if x > 0) / len(sobre50) * 100) if sobre50 else None,
                "fondo": fondos.get(nombre)}
        fila["nota"] = round((fila["mes_pct"] or 0) + 0.5 * (fila["tres_meses_pct"] or 0)
                             + ((fila["sobre_media50_pct_de_valores"] or 50) - 50) / 10, 2)
        out.append(fila)
    out.sort(key=lambda x: -x["nota"])
    puesto = 0
    for fila in out:
        if fila["tipo"] == "sector":
            puesto += 1
            fila["puesto"] = puesto
    return out


def destacados(filas, n=3):
    """Los valores que más suben y más bajan en la semana dentro de cada sector."""
    por = {}
    for t, f in filas.items():
        if f.get("var_5d") is not None:
            por.setdefault(f["sector"], []).append((f["var_5d"], t))
    out = {}
    for sector, lista in por.items():
        lista.sort(reverse=True)
        out[sector] = {"suben": ["%s %+.1f %%" % (t, v) for v, t in lista[:n]],
                       "bajan": ["%s %+.1f %%" % (t, v) for v, t in lista[-n:][::-1]]}
    return out


def _puntos(f, puesto_sector, favor=(), contra=()):
    p = 3.0 * sum(1 for x in f["_claves"] if x in BUENAS) + (1.0 if "oversold" in f["_claves"] else 0.0)
    p += max(min((f.get("var_1m") or 0) / 10, 2), -2)
    p += 0.5 if (f.get("sobre_media50_pct") or 0) > 0 else 0.0
    p += {1: 1.5, 2: 1.5, 3: 1.0, 4: 0.5, 5: 0.5}.get(puesto_sector, 0.0)      # premia a los sectores que lideran
    grupos = {f["sector"], f.get("tema")}
    if grupos & set(favor):          # la simulación de MiroFish manda en qué sectores se busca
        p += 2.5
    if grupos & set(contra):
        p -= 2.5
    return p


def elegir_candidatos(filas, fijos, fuerza, maximo, favor=(), contra=()):
    """Valores que se miran hoy a fondo: cartera, prioritarios y favoritos de MiroFish
    siempre; el resto por señal técnica, fuerza de su sector y lo que pida la
    simulación. Al menos uno por sector; más hueco para los sectores favorecidos."""
    puesto = {x["nombre"]: x.get("puesto", 99) for x in fuerza if x["tipo"] == "sector"}
    orden = sorted(filas, key=lambda t: -_puntos(filas[t], puesto.get(filas[t]["sector"], 99), favor, contra))
    elegidos = [t for t in fijos if t in filas]
    por_sector = {}
    for t in elegidos:
        por_sector[filas[t]["sector"]] = por_sector.get(filas[t]["sector"], 0) + 1
    for sector in {f["sector"] for f in filas.values()}:            # uno por sector, el mejor
        if not por_sector.get(sector) and len(elegidos) < maximo:
            mejor = next((t for t in orden if filas[t]["sector"] == sector and t not in elegidos), None)
            if mejor:
                elegidos.append(mejor)
                por_sector[sector] = 1
    for t in orden:
        if len(elegidos) >= maximo:
            break
        s = filas[t]["sector"]
        tope = 8 if s in favor else 2 if s in contra else 4
        if t not in elegidos and por_sector.get(s, 0) < tope:
            elegidos.append(t)
            por_sector[s] = por_sector.get(s, 0) + 1
    return elegidos


def valoraciones(tickers, errores, carpeta=None):
    """Fichas de Finviz, guardadas por día para no repetir peticiones entre tareas."""
    carpeta = carpeta or carpeta_del_dia("datos")
    guardadas = leer_json(carpeta / "finviz.json", {})
    limite = reglas()["limites_fuentes"]["finviz_max_valores_dia"] + 8
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


# ---------------------------------------------------------------- dossier

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
        fondos_sector = _cotizaciones(u["fondos_de_sector"], errores)
        d["fondos_de_tema"] = _cotizaciones(u["fondos_de_tema"], errores)
        filas = barrido(errores)
        d["fuerza_de_sectores"] = fuerza_de_sectores(filas, fondos_sector)
        d["destacados_por_sector"] = destacados(filas)
        # La simulación de MiroFish orienta dónde se mira: sus valores entran siempre y sus sectores pesan más.
        mf = _mirofish(errores) or {}
        favor = {x["sector"] for x in mf.get("sectores", []) if x.get("sesgo") == "positivo"}
        contra = {x["sector"] for x in mf.get("sectores", []) if x.get("sesgo") == "negativo"}
        suyos = [v["ticker"].upper() for v in mf.get("valores", []) if v.get("sesgo") != "negativo"]
        suyos += [str(x[0]).upper() for x in ((mf.get("encuesta") or {}).get("valores") or [])]
        fijos += [t for t in dict.fromkeys(suyos) if t in filas and t not in fijos][:8]
        cand = elegir_candidatos(filas, fijos, d["fuerza_de_sectores"], r["limites_fuentes"]["finviz_max_valores_dia"],
                                 favor, contra)
        velas = series_completas(list(dict.fromkeys(fijos + cand)), filas, errores)
        val = valoraciones(cand, errores)
        try:
            tabla = fuentes.damodaran()
            d["media_sector"] = {s: fuentes.media_del_sector(s, tabla) for s in {f["sector"] for f in filas.values()}}
        except ErrorFuente as e:
            errores.append("Damodaran: %s" % e)
        d["candidatos"] = cand
        d["macro"] = fuentes.fred()
        if d["macro"] is None:
            errores.append("FRED: falta la clave gratuita; el bloque macro sale solo con datos de mercado")
        # Resultados: cuándo presenta cada valor y si suele batir lo esperado (Alpha Vantage).
        interes = list(dict.fromkeys(fijos + cand))
        hoy = ahora.date()
        fechas = extra.calendario_resultados(errores)
        for tk in interes:
            if tk in fechas and tk in val:
                val[tk]["fecha_de_resultados"] = fechas[tk]
                val[tk]["dias_a_resultados"] = (hoy.fromisoformat(fechas[tk]) - hoy).days
        d["titulares_de_agencia"] = extra.noticias_generales(errores)
        d["sorpresas_de_resultados"] = {tk: s for tk in interes[:8] for s in [extra.sorpresas(tk, errores)] if s}
        d["analistas"] = extra.analistas(interes[:12], errores)
        d["directivos"] = extra.directivos(interes[:8], errores) if d["analistas"] else None
        try:        # trimestres y crecimiento desde la SEC; solo si hay identificación en .env
            d["resultados_sec"] = dts.resultados(list(dict.fromkeys(fijos + cand))[:8])
        except ErrorFuente as e:
            errores.append("SEC: %s" % e)
        horas, con_noticias = 20, list(dict.fromkeys(fijos + cand))[:r["limites_fuentes"]["noticias_max_valores"]]
        if ahora.weekday() == 6:      # domingo: grandes inversores
            d["grandes_inversores"] = {}
            for t in list(dict.fromkeys(fijos + cand))[:r["limites_fuentes"]["dataroma_max_valores"]]:
                try:
                    d["grandes_inversores"][t] = fuentes.dataroma(t)
                except ErrorFuente as e:
                    errores.append("Dataroma %s: %s" % (t, e))
            horas = 60
    else:
        plan = leer_json(carpeta_del_dia("datos") / "plan.json", {})
        del_plan = [o["ticker"].upper() for o in plan.get("ordenes", []) if o.get("ticker")]
        interes = list(dict.fromkeys(fijos + del_plan))
        filas = {}
        velas = series_completas(interes, filas, errores)
        val = valoraciones([t for t in interes if t in filas], errores)
        horas, con_noticias = 9, interes
        d["plan_de_la_manana"] = plan
    precios = {t: f["precio"] for t, f in filas.items()}
    if tarea != "manana" and d["mercado_hoy"]["abre_hoy"]:
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
    d["mirofish"] = _mirofish(errores)
    d["avisos_de_datos"] = errores
    escribir_json(carpeta_del_dia("datos") / ("dossier-%s.json" % tarea), d)
    return d, velas, c


def _mirofish(errores):
    """Síntesis de la simulación semanal de MiroFish, si hay una reciente."""
    try:
        from . import mirofish
        return mirofish.para_el_analisis()
    except Exception as e:      # un fallo aquí no debe tumbar la tarea
        errores.append("MiroFish: %s" % str(e)[:120])
        return None


def _hay_alpaca():
    k = claves()
    return bool(k.get("ALPACA_API_KEY") and k.get("ALPACA_SECRET_KEY"))
