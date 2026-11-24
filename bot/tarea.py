"""Las tres tareas del bot.

    python3 -m bot.tarea manana      # 7:00  informe y plan del día
    python3 -m bot.tarea apertura    # 15:45 ejecuta el plan en la cuenta simulada
    python3 -m bot.tarea cierre      # 22:10 balance del día

Opciones: --sin-modelo (solo datos y reglas), --forzar (ignora el horario; para
pruebas), --con-plan (usa la sesión del plan en vez de la clave; para pruebas).
Con BOT_PRUEBA=1 delante, todo se escribe en carpetas "-prueba".
"""
from __future__ import annotations

import argparse
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import alpaca, cartera, dossier, gasto, modelo, ordenes
from . import informe as inf
from .base import (ESTADO, RAIZ, SUFIJO, ErrorFuente, ahora_madrid, anexar_linea, carpeta_del_dia,
                   carpeta_informes, escribir_json, leer_json, leer_lineas, reglas)

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# ---------------------------------------------------------------- esquemas de respuesta
S, N = {"type": "string"}, {"type": "number"}


def _obj(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _lista(x):
    return {"type": "array", "items": x}


def _op(*valores):
    return {"type": "string", "enum": list(valores)}


ESQ_INVESTIGADOR = _obj(
    resumen=S,
    hallazgos=_lista(_obj(titulo=S, detalle=S, tickers=_lista(S), sesgo=_op("positivo", "negativo", "neutro"), fuente=S)),
    candidatos=_lista(_obj(ticker=S, motivo=S, riesgos=S)),
    alertas=_lista(S))
TEMAS_MUNDO = ["Política y geopolítica", "Economía", "Tecnología", "Empresas y mercados", "España y Europa",
               "Energía y clima", "Ciencia y salud", "Otros"]
ESQ_MUNDO = _obj(
    resumen=S,
    noticias=_lista(_obj(categoria=_op(*TEMAS_MUNDO), titular=S, que_ha_pasado=S, por_que_importa=S, fuente=S)))
ESQ_ESTRATEGA = _obj(
    panorama=S, tono=_op("riesgo", "neutral", "defensivo"),
    prioritario=_obj(ticker=S, lectura=S, niveles=S, accion=_op("comprar", "mantener", "esperar", "vender")),
    ordenes=_lista(_obj(ticker=S, accion=_op("comprar", "vender"), entrada=N, stop=N, objetivo=N, motivo=S,
                        por_que=S, que_esperamos=S, cuando_saldriamos=S, conviccion=_op("alta", "media", "baja"))),
    posiciones=_lista(_obj(ticker=S, decision=_op("mantener", "vender"), nuevo_stop=N, nuevo_objetivo=N, comentario=S,
                           por_que=S, que_esperamos=S, cuando_saldriamos=S)),
    vigilados=_lista(_obj(ticker=S, por_que=S)),
    noticias_clave=_lista(_obj(titular=S, impacto=S)),
    agenda=_lista(S), riesgos=_lista(S))
ESQ_APERTURA = _obj(
    decisiones=_lista(_obj(ticker=S, decision=_op("ejecutar", "descartar"), motivo=S)),
    stops=_lista(_obj(ticker=S, nuevo_stop=N, motivo=S)),
    ventas=_lista(_obj(ticker=S, motivo=S)),
    comentario=S)
ESQ_CIERRE = _obj(resumen=S, aciertos=_lista(S), fallos=_lista(S), lecciones=_lista(S), avisos_manana=_lista(S))


# ---------------------------------------------------------------- utilidades

def _fecha_larga(a):
    return "%s %d de %s de %d · %s" % (DIAS[a.weekday()], a.day, MESES[a.month - 1], a.year, a.strftime("%H:%M"))


def _api(avisos):
    try:
        return alpaca.cliente()
    except ErrorFuente as e:
        avisos.append(str(e))
        return None


def _precios(d):
    p = {t: f["precio"] for t, f in d["universo"].items()}
    p.update({t: s["ultimo"] for t, s in (d.get("sesion_de_hoy") or {}).items()})
    return p


def _resumen(c, d):
    return cartera.resumen(c, _precios(d), d["cartera"]["eurusd"], d["indices"]["S&P 500"]["precio"])


def _vigilar(c, velas, api, avisos):
    """Pone la cartera al día: lo ejecutado en Alpaca y los stops que vigila el bot."""
    hechos = []
    if api:
        try:
            hechos += ordenes.sincronizar(c, api)
        except ErrorFuente as e:
            avisos.append("Alpaca: %s" % e)
    for v in cartera.revisar_stops(c, velas):
        hechos.append(v)
        if api and v["via"] == "alpaca":
            try:
                api.cerrar(v["ticker"])
            except ErrorFuente as e:
                avisos.append("Alpaca no cerró %s: %s" % (v["ticker"], e))
    return hechos


def _subir_stop(c, ticker, nuevo, precio, api, avisos):
    p = c["posiciones"].get(ticker)
    if not p or not nuevo or nuevo <= p["stop"] or (precio and nuevo >= precio):
        return False
    p["stop"] = round(float(nuevo), 2)
    if api and p.get("stop_en_broker") and p.get("orden_id"):
        try:
            api.mover_stop(p["orden_id"], p["stop"])
        except ErrorFuente as e:
            avisos.append("Alpaca no movió el stop de %s: %s" % (ticker, e))
    return True


def _subir_objetivo(c, ticker, nuevo, precio, api, avisos):
    """Aleja el objetivo de una posición para dejarla correr. Solo hacia arriba."""
    p = c["posiciones"].get(ticker)
    if not p or not nuevo or nuevo <= p["objetivo"] or (precio and nuevo <= precio):
        return False
    p["objetivo"] = round(float(nuevo), 2)
    if api and p.get("stop_en_broker") and p.get("orden_id"):
        try:
            api.mover_objetivo(p["orden_id"], p["objetivo"])
        except ErrorFuente as e:
            avisos.append("Alpaca no movió el objetivo de %s: %s" % (ticker, e))
    return True


def _chips(d, costes=None, nivel=None):
    cr = d["credito_api"]
    coste = sum(i.get("coste_usd", 0) for i in costes or [])
    con_plan = d.get("via") == "plan"
    return [d["modo"], "análisis: %s" % nivel.replace("_", " ") if nivel else None,
            ("%s: %s $" % ("equivalente de esta tarea" if con_plan else "coste de esta tarea", inf.num(coste))) if costes else None,
            "modelos con tu plan de Claude" if con_plan else
            "crédito del mes: %s de %s $" % (inf.num(cr["gastado_mes_usd"] + coste, 1), inf.num(cr["credito_usd"], 0))]


def _pie(avisos):
    extra = ""
    if avisos:
        extra = "Avisos de datos: " + "; ".join(inf.e(x) for x in avisos) + "<br>"
    return extra + inf.PIE


def _nota_cerrado(d, nombre, titulo):
    cuerpo = "<h2>Mercado cerrado</h2><p>Hoy la bolsa de EE. UU. no abre (%s). Próxima sesión: %s.</p>%s" % (
        inf.e(d["mercado_hoy"]["motivo_cierre"]), inf.e(d["mercado_hoy"]["proxima_sesion"]),
        inf.bloque_cartera(d["cartera"]))
    return inf.componer(titulo, _fecha_larga(ahora_madrid()), _chips(d), cuerpo, inf.PIE,
                        carpeta_informes() / nombre)


# ---------------------------------------------------------------- 7:00 mañana

def _fila_corta(f):
    return {k: f.get(k) for k in ("sector", "precio", "var_1d", "var_5d", "var_1m", "rsi14", "sobre_media50_pct",
                                  "bajo_maximo_anual_pct", "atr14", "volumen_vs_media", "configuraciones",
                                  "minimo_10d", "maximo_20d")}


def _detalle(d):
    """Filas completas de los valores que se miran hoy a fondo: candidatos, cartera y prioritarios."""
    u = d["universo"]
    interes = list(dict.fromkeys(d.get("candidatos", []) + [p["ticker"] for p in d["cartera"]["posiciones"]] + d["prioritarios"]))
    return {t: dict(_fila_corta(u[t]), tema=u[t].get("tema"), empresa=u[t].get("empresa")) for t in interes if t in u}


def _datos_investigador(papel, d):
    comun = {"fecha": d["generado"], "nota_datos": d["nota_datos"], "mercado_hoy": d["mercado_hoy"],
             "prioritarios": d["prioritarios"],
             "en_cartera": [p["ticker"] for p in d["cartera"]["posiciones"]], "candidatos": d.get("candidatos", []),
             "fuerza_de_sectores": d.get("fuerza_de_sectores")}
    if papel == "investigador-sectores":
        comun.update({"destacados_por_sector": d.get("destacados_por_sector"), "fondos_de_tema": d.get("fondos_de_tema"),
                      "valores_en_detalle": _detalle(d), "valoracion": d["valoracion"], "media_sector": d.get("media_sector"),
                      "sorpresas_de_resultados": d.get("sorpresas_de_resultados"), "analistas": d.get("analistas"),
                      "directivos": d.get("directivos")})
    elif papel == "investigador-noticias":
        comun.update({"noticias": d["noticias"], "valores_en_detalle": _detalle(d)})
    elif papel == "investigador-macro":
        comun.update({"indices": d["indices"], "macro": d.get("macro")})
    else:
        comun.update({"materias_primas": d.get("materias_primas"), "fondos_de_tema": d.get("fondos_de_tema"),
                      "destacados_por_sector": {s: v for s, v in (d.get("destacados_por_sector") or {}).items()
                                                if s in ("Energía", "Materiales", "Servicios públicos", "Industria",
                                                         "Renovables", "Nuclear y uranio", "Defensa")}})
    return comun


def _mundo(d, nivel=""):
    """El redactor de noticias del mundo: busca en la web lo más importante del día, de cualquier tema."""
    r = reglas()
    ahora = ahora_madrid()
    datos = {"ahora_en_madrid": ahora.strftime("%Y-%m-%d %H:%M") + " (" + DIAS[ahora.weekday()] + ")",
             "titulares_de_agencia": d.get("titulares_de_agencia") or [],
             "mercados_ayer": {k: {"ultimo": v.get("precio"), "dia_pct": v.get("var_1d")}
                               for k, v in (d.get("indices") or {}).items() if isinstance(v, dict)}}
    return modelo.preguntar("manana", "mundo", r["modelos"].get("mundo", r["modelos"]["investigador_medio"]), datos,
                            ESQ_MUNDO, nivel=nivel, web=True,
                            tope_usd=r["credito_api"]["tope_por_llamada_usd"].get("mundo", 0.5), esfuerzo="medium",
                            espera=720, carpeta=carpeta_del_dia("datos"))


def _investigar(d, nivel, a):
    r = reglas()
    papeles = ["investigador-noticias", "investigador-sectores", "investigador-macro", "investigador-materias"]
    if nivel == "bajo":
        papeles = papeles[:2]
    papeles.append("mundo")       # las noticias del mundo van siempre que haya modelo
    fable = nivel == "alto"
    mod = r["modelos"]["investigador_alto" if fable else "investigador_medio"]
    tope = r["credito_api"]["tope_por_llamada_usd"]["investigador_fable" if fable else "investigador_sonnet"]

    def uno(papel):
        if papel == "mundo":
            return papel, _mundo(d, nivel)
        return papel, modelo.preguntar("manana", papel, mod, _datos_investigador(papel, d), ESQ_INVESTIGADOR,
                                       nivel=nivel, web=True, tope_usd=tope, esfuerzo="medium", espera=720,
                                       carpeta=carpeta_del_dia("datos"))
    informes, costes = {}, []
    with ThreadPoolExecutor(max_workers=5) as grupo:
        for papel, (resp, info) in grupo.map(uno, papeles):
            costes.append(info)
            if resp:
                informes[papel.replace("investigador-", "")] = resp
    return informes, costes


def _estratega(d, informes, nivel, a):
    r = reglas()
    datos = {k: d.get(k) for k in ("generado", "nota_datos", "mercado_hoy", "mirofish", "indices", "materias_primas",
                                   "fuerza_de_sectores", "destacados_por_sector", "fondos_de_tema", "macro",
                                   "media_sector", "candidatos", "prioritarios", "valoracion", "reglas", "cartera",
                                   "marcador", "operaciones_recientes", "grandes_inversores", "avisos_de_ayer",
                                   "lecciones_recientes", "sorpresas_de_resultados", "analistas", "directivos")}
    datos["valores_en_detalle"] = _detalle(d)
    datos["universo_comprable"] = "Las %d empresas del S&P 500 y los valores de los temas." % len(d["universo"])
    datos["titulares"] = [{"ticker": n["ticker"], "titular": n["titular"], "hora": n["hora"]}
                          for n in d["noticias"]["titulares"][:25]]
    datos["investigadores"] = informes
    mod = r["modelos"]["estratega_bajo" if nivel == "bajo" else "estratega"]
    return modelo.preguntar("manana", "estratega", mod, datos, ESQ_ESTRATEGA, nivel=nivel, web=False,
                            tope_usd=r["credito_api"]["tope_por_llamada_usd"]["estratega"], esfuerzo="high",
                            espera=900, carpeta=carpeta_del_dia("datos"))


def _poner_veredictos(plan, d, avisos):
    """Pasa cada orden del plan por las reglas, en orden, descontando caja y huecos."""
    res = dict(d["cartera"])
    res["posiciones"] = list(res["posiciones"])
    for o in plan["ordenes"]:
        t = o["ticker"] = (o.get("ticker") or "").upper()
        val = d["valoracion"].get(t)
        if val is None and o.get("accion") == "comprar" and t in d["universo"]:
            val = dossier.valoraciones([t], avisos).get(t)
            if val:
                d["valoracion"][t] = val
        o["veredicto"], o["motivos"], cantidad = ordenes.validar(o, res, val)
        o["cantidad"] = cantidad or None
        if o["veredicto"] == "APROBADA" and o.get("accion") == "comprar":
            res["huecos_libres"] -= 1
            res["caja_usd"] = round(res["caja_usd"] - cantidad * float(o["entrada"]), 2)
            res["posiciones"].append({"ticker": t})


def _postura(tono):
    """El tono del plan, dicho como postura de la cartera."""
    return {"defensivo": "defensiva", "riesgo": "ofensiva", "neutral": "neutral"}.get((tono or "").lower(), tono or "sin análisis")


def _que_hacemos(d, plan):
    """Lo que se hace hoy con cada valor y por qué, en claro: compras, ventas, lo que se mantiene y el valor prioritario."""
    c, u = d["cartera"], d["universo"]
    pos = {p["ticker"]: p for p in c["posiciones"]}
    decisiones = {x["ticker"].upper(): x for x in plan.get("posiciones", [])}

    def parrafo(titulo, detalle, x, extra=""):
        lineas = ["<b>%s</b> <span class='gris'>%s</span>" % (titulo, detalle)]
        # Si la frase ya empieza por "Esperamos que…" o "Saldríamos si…", se resalta ese arranque
        # en vez de repetirlo como etiqueta.
        for etiqueta, clave, arranque in (("Por qué", "por_que", None), ("Qué esperamos", "que_esperamos", "Esperamos"),
                                          ("Saldríamos si", "cuando_saldriamos", "Saldríamos")):
            texto = (x.get(clave) or "").strip()
            if not texto:
                continue
            if arranque and texto.startswith(arranque + " "):
                lineas.append("<b>%s</b>%s" % (arranque, inf.e(texto[len(arranque):])))
            else:
                lineas.append("<b>%s:</b> %s" % (etiqueta, inf.e(texto)))
        return "<p>%s%s</p>" % ("<br>".join(lineas), extra)
    partes = []
    for o in plan["ordenes"]:
        t = o["ticker"]
        if o.get("accion") == "vender":
            p = pos.get(t, {})
            x = dict(decisiones.get(t, {}), **{k: v for k, v in o.items() if k in ("por_que", "que_esperamos", "cuando_saldriamos") and v})
            x.setdefault("por_que", o.get("motivo") or x.get("comentario"))
            partes.append(parrafo("%s · se vende" % inf.e(t), "comprada a %s $ · hoy %s $ (%s, %s)" % (
                inf.num(p.get("entrada")), inf.num(p.get("precio")), inf.pct(p.get("resultado_pct")),
                inf.dinero(p.get("resultado_usd"), "$", True)), x))
        elif o.get("veredicto") == "APROBADA":
            x = dict(o)
            x.setdefault("por_que", o.get("motivo"))
            n = o.get("cantidad") or 0
            partes.append(parrafo("%s · se compra" % inf.e(t), "%s · %s acciones, unos %s · entrada %s $ · stop %s $ · objetivo %s $%s" % (
                inf.e((u.get(t) or {}).get("empresa") or (u.get(t) or {}).get("sector") or ""),
                inf.num(n, 0 if n == int(n) else 4), inf.dinero(n * float(o["entrada"])), inf.num(o["entrada"]),
                inf.num(o["stop"]), inf.num(o["objetivo"]),
                (" · " + inf.e("; ".join(o["motivos"]))) if o.get("motivos") else ""), x))
        else:
            partes.append("<p><b>%s · no se compra:</b> %s</p>" % (inf.e(t), inf.e("; ".join(o.get("motivos") or []))))
    vendidas = {o["ticker"] for o in plan["ordenes"] if o.get("accion") == "vender"}
    for t, p in pos.items():
        if t in vendidas:
            continue
        x = dict(decisiones.get(t) or {})
        if not x.get("por_que"):
            x["por_que"] = x.get("comentario") or p.get("motivo_de_compra")
        partes.append(parrafo("%s · se mantiene" % inf.e(t), "comprada el %s a %s $ · hoy %s $ (%s, %s) · stop %s $ · objetivo %s $" % (
            inf.e(p["fecha_entrada"][8:] + "/" + p["fecha_entrada"][5:7]), inf.num(p["entrada"]), inf.num(p["precio"]),
            inf.pct(p["resultado_pct"]), inf.dinero(p["resultado_usd"], "$", True), inf.num(p["stop"]), inf.num(p["objetivo"])), x))
    pri = plan.get("prioritario") or {}
    t = (pri.get("ticker") or "").upper()
    if t and t not in pos and not any(o["ticker"] == t for o in plan["ordenes"]) and pri.get("lectura"):
        f = u.get(t) or {}
        partes.append("<p><b>%s · %s</b> <span class='gris'>en vigilancia · %s $, %s en el día y %s en el mes</span><br>%s%s</p>" % (
            inf.e(t), inf.e(pri.get("accion", "esperar")), inf.num(f.get("precio")), inf.pct(f.get("var_1d")), inf.pct(f.get("var_1m")),
            inf.e(pri["lectura"]), (" <span class='gris'>Niveles: %s</span>" % inf.e(pri["niveles"])) if pri.get("niveles") else ""))
    if not partes:
        return ""
    return "<h2>Qué hacemos hoy y por qué</h2>" + "".join(partes)


def _el_mundo_hoy(m):
    """La sección de noticias del mundo, por temas y a dos columnas. Vacía si no hubo redactor."""
    noticias = [n for n in (m or {}).get("noticias") or [] if n.get("titular")]
    if not noticias:
        return ""
    orden = {t: i for i, t in enumerate(TEMAS_MUNDO)}
    noticias.sort(key=lambda n: orden.get(n.get("categoria"), len(orden)))       # estable: respeta el orden dentro del tema
    partes, anterior = [], None
    for n in noticias:
        tema = n.get("categoria", "")
        # El nombre del tema va una vez, pegado a su primera noticia para que no quede suelto al pie de una columna.
        rotulo = "<span class='cat'>%s</span><br>" % inf.e(tema) if tema != anterior else ""
        anterior = tema
        partes.append("<p>%s<b>%s</b> %s <i>Por qué importa:</i> %s <span class='gris'>(%s)</span></p>" % (
            rotulo, inf.e(n["titular"].rstrip(".") + "."), inf.e(n.get("que_ha_pasado", "")),
            inf.e(n.get("por_que_importa", "")), inf.e(n.get("fuente", ""))))
    cabecera = "<h2>El mundo hoy</h2>"
    if m.get("resumen"):
        cabecera += "<p><b>%s</b></p>" % inf.e(m["resumen"])
    return cabecera + "<div class='columnas'>%s</div>" % "".join(partes)


def _pdf_manana(d, plan, nivel, costes, hechos, avisos):
    c, ind, u, val = d["cartera"], d["indices"], d["universo"], d["valoracion"]
    cuerpo = [inf.bloque_cartera(c)]
    if c.get("aportacion_hoy"):
        cuerpo.append("<p><b>Hoy entra en la cartera: %s.</b></p>" % inf.e(c["aportacion_hoy"]))
    if not d["mercado_hoy"]["abre_hoy"]:
        cuerpo.append("<p><b>Hoy no hay mercado (%s).</b> Próxima sesión: %s.</p>" % (
            inf.e(d["mercado_hoy"]["motivo_cierre"]), inf.e(d["mercado_hoy"]["proxima_sesion"])))
    elif d["mercado_hoy"].get("cierre_anticipado"):
        cuerpo.append("<p><b>Sesión corta:</b> hoy la bolsa cierra a las %s.</p>" % d["mercado_hoy"]["cierra_madrid"])
    cuerpo.append("<h2>Panorama · postura: %s</h2><p>%s</p>" % (inf.e(_postura(plan.get("tono"))), inf.e(plan["panorama"])))
    cuerpo.append(_que_hacemos(d, plan))
    mf = d.get("mirofish")
    if mf:
        favor = [x["sector"] for x in mf.get("sectores", []) if x["sesgo"] == "positivo"]
        contra = [x["sector"] for x in mf.get("sectores", []) if x["sesgo"] == "negativo"]
        cuerpo.append("<p class='gris'><b>Guía de la semana</b> (simulación del %s): postura %s. Sectores preferidos: %s. "
                      "A evitar: %s.</p>" % (inf.e(mf["fecha"][8:] + "/" + mf["fecha"][5:7]), inf.e(_postura(mf["tono"])),
                                             inf.e(", ".join(favor) or "ninguno"), inf.e(", ".join(contra) or "ninguno")))
    sectores = []
    for x in d.get("fuerza_de_sectores") or []:
        corto = inf.sector(x["nombre"]).replace(" ", "&nbsp;")
        nombre = ("%d.&nbsp;%s" % (x["puesto"], corto)) if x["tipo"] == "sector" else "<span class='gris'>tema&nbsp;·</span>&nbsp;%s" % corto
        sectores.append([nombre, inf.pct(x["dia_pct"]), inf.pct(x["semana_pct"]), inf.pct(x["mes_pct"]),
                         inf.pct(x["tres_meses_pct"]), "%s&nbsp;%%" % inf.num(x["sobre_media50_pct_de_valores"], 0)])
    todo = dict(ind)
    todo.update(d.get("materias_primas") or {})
    cuerpo.append('<div class="dos"><div><h2>Mercados</h2>%s</div><div><h2>Sectores, del más fuerte al más débil</h2>%s</div></div>' % (
        inf.bloque_indices(todo, ["S&P 500", "Nasdaq 100", "Russell 2000", "VIX", "Bono 10 años (%)", "Euro/dólar",
                                  "Euro Stoxx 50", "IBEX 35", "Nikkei 225", "Emergentes (EEM)", "Petróleo WTI", "Oro",
                                  "Cobre", "Bitcoin"][:15]),
        inf.tabla([("", 0), ("Día", 1), ("Sem.", 1), ("Mes", 1), ("3 m.", 1), ("Sobre media 50", 1)], sectores,
                  "Sin datos de sectores.")))
    if hechos:
        cuerpo.append("<h2>Movimientos desde el último informe</h2>" + inf.bloque_operaciones(hechos))
    vig = plan.get("vigilados") or [{"ticker": t, "por_que": ", ".join(u[t]["configuraciones"])} for t in d.get("candidatos", [])[:6]]
    medias = d.get("media_sector") or {}
    filas = []
    for v in vig:
        t = v["ticker"].upper()
        f, x = u.get(t), val.get(t, {})
        if not f:
            continue
        m = medias.get(f["sector"]) or {}
        filas.append([inf.e(t), inf.sector(f["sector"]), inf.num(f["precio"]), inf.pct(f["var_1d"]), inf.pct(f["var_1m"]),
                      inf.num(f["rsi14"], 0), inf.num(x.get("per"), 1), inf.num(x.get("per_futuro"), 1),
                      inf.num(x.get("peg"), 2), inf.num(m.get("per_futuro"), 1), inf.e(v.get("por_que", ""))])
    cuerpo.append("<h2>Valores vigilados</h2>" + inf.tabla(
        [("Valor", 0), ("Sector", 0), ("Precio", 1), ("Día", 1), ("Mes", 1), ("RSI", 1), ("P/E", 1), ("P/E fut.", 1),
         ("PEG", 1), ("P/E fut. sector", 1), ("Por qué", 0)], filas, "Sin valores vigilados hoy."))
    noticias = ["%s — %s" % (n["titular"], n["impacto"]) for n in plan.get("noticias_clave", [])] or \
               ["%s: %s" % (n["ticker"], n["titular"]) for n in d["noticias"]["titulares"][:4]]
    cuerpo.append('<div class="dos"><div><h2>Noticias que mueven la cartera</h2>%s</div><div><h2>Agenda y riesgos</h2>%s</div></div>' % (
        inf.lista(noticias, "Sin titulares."),
        inf.lista((plan.get("agenda") or []) + ["Riesgo: " + x for x in (plan.get("riesgos") or [])],
                  "Sin agenda (requiere el análisis del modelo).")))
    cuerpo.append(_el_mundo_hoy(plan.get("noticias_del_mundo")))
    return inf.componer("Informe de la mañana", _fecha_larga(ahora_madrid()), _chips(d, costes, nivel["nivel"]),
                        "".join(cuerpo), _pie(avisos), carpeta_informes() / "0700-manana.pdf", paginas_max=2)


def manana(a):
    previos = None
    if getattr(a, "solo_estratega", False):       # rehace la decisión y el informe con los datos y la investigación de hoy
        d = leer_json(carpeta_del_dia("datos") / "dossier-manana.json")
        if not d:
            raise ErrorFuente("no hay datos de la mañana de hoy: lanza antes la tarea completa")
        previos = leer_json(carpeta_del_dia("datos") / "investigadores.json", {})
        velas, c = {}, cartera.cargar()
        avisos = d["avisos_de_datos"]
        api, hechos = _api(avisos), []
        d["cartera"] = dict(_resumen(c, d), aportacion_hoy=None)
    else:
        d, velas, c = dossier.construir("manana")
        avisos = d["avisos_de_datos"]
        api = _api(avisos)
        hechos = _vigilar(c, velas, api, avisos)
        if hechos:
            d["cartera"] = _resumen(c, d)
    ayer = sorted((RAIZ / ("datos" + SUFIJO)).glob("*/cierre.json"))
    d["avisos_de_ayer"] = (leer_json(ayer[-1], {}).get("avisos_manana", []) if ayer else [])
    d["lecciones_recientes"] = [x["leccion"] for x in leer_lineas(ESTADO / "lecciones.jsonl")][-6:]
    nivel, plan, informes, costes = {"nivel": "sin_modelo"}, None, {}, []
    motivo = "se ha pedido sin modelo" if a.sin_modelo else "no hay forma de llamar a los modelos (falta la clave o el programa claude)"
    via = d["via"] = None if a.sin_modelo else modelo.autenticacion()
    if via:
        nivel = gasto.nivel_para_hoy() if via == "clave" else {"nivel": reglas()["autenticacion"]["nivel_con_plan"]}
        if getattr(a, "nivel", None):       # nivel fijado a mano al lanzar la tarea
            nivel = dict(nivel, nivel=a.nivel)
        motivo = "el crédito de API del mes no da para más"
        if nivel["nivel"] != "sin_modelo":
            informes, costes = (previos, []) if previos is not None else _investigar(d, nivel["nivel"], a)
            if costes and all(i.get("sin_saldo") for i in costes):
                motivo = "la organización de la clave de API no tiene saldo"
                nivel["nivel"] = "sin_modelo"
            else:
                plan, info = _estratega(d, informes, nivel["nivel"], a)
                costes.append(info)
                motivo = "el estratega no devolvió un plan válido (%s)" % info.get("error", "sin detalle")
    if not plan:
        plan = {"panorama": "Sin análisis del modelo: %s. Este informe trae solo los datos y no propone operaciones." % motivo,
                "tono": "sin análisis", "ordenes": [], "posiciones": []}
    _poner_veredictos(plan, d, avisos)
    precios = _precios(d)
    for p in plan.get("posiciones", []):
        t = p["ticker"].upper()
        if p.get("decision") == "vender" and t in c["posiciones"] and not any(o["ticker"] == t for o in plan["ordenes"]):
            plan["ordenes"].append({"ticker": t, "accion": "vender", "motivo": p.get("comentario", ""),
                                    "por_que": p.get("por_que"), "veredicto": "APROBADA", "motivos": []})
        else:
            if _subir_stop(c, t, p.get("nuevo_stop"), precios.get(t), api, avisos):
                hechos.append({"hora": ahora_madrid().strftime("%H:%M"), "ticker": t, "accion": "stop subido", "cantidad": 0,
                               "precio": p["nuevo_stop"], "importe_usd": 0, "motivo": p.get("comentario", "")})
            if _subir_objetivo(c, t, p.get("nuevo_objetivo"), precios.get(t), api, avisos):
                hechos.append({"hora": ahora_madrid().strftime("%H:%M"), "ticker": t, "accion": "objetivo subido", "cantidad": 0,
                               "precio": p["nuevo_objetivo"], "importe_usd": 0, "motivo": p.get("comentario", "")})
    plan["noticias_del_mundo"] = (informes or {}).get("mundo")
    plan.update({"fecha": ahora_madrid().date().isoformat(), "nivel": nivel["nivel"],
                 "coste_usd": round(sum(i.get("coste_usd", 0) for i in costes), 4), "llamadas": costes})
    carpeta = carpeta_del_dia("datos")
    escribir_json(carpeta / "plan.json", plan)
    escribir_json(carpeta / "investigadores.json", informes)
    cartera.guardar(c)
    ruta, paginas, escala = _pdf_manana(d, plan, nivel, costes, hechos, avisos)
    return "mañana: nivel %s, %d órdenes (%d aprobadas), coste %.2f $ -> %s (%s pág., escala %s)" % (
        nivel["nivel"], len(plan["ordenes"]), sum(1 for o in plan["ordenes"] if o["veredicto"] == "APROBADA"),
        plan["coste_usd"], ruta, paginas, escala)


# ---------------------------------------------------------------- 15:45 apertura

def apertura(a):
    d, velas, c = dossier.construir("apertura")
    avisos = d["avisos_de_datos"]
    if not d["mercado_hoy"]["abre_hoy"]:
        cartera.guardar(c)
        return "apertura: mercado cerrado -> %s" % _nota_cerrado(d, "1545-apertura.pdf", "Apertura")[0]
    api = _api(avisos)
    hechos = _vigilar(c, velas, api, avisos)
    plan = d.get("plan_de_la_manana") or {}
    aprobadas = [o for o in plan.get("ordenes", []) if o.get("veredicto") == "APROBADA"]
    sesion, precios = d.get("sesion_de_hoy") or {}, _precios(d)
    descartadas, costes, comentario = [], [], ""
    abierto = d["mercado_hoy"]["abierto_ahora"] or a.forzar
    decision = {o["ticker"]: ("ejecutar", "") for o in aprobadas}
    via = d["via"] = None if a.sin_modelo else modelo.autenticacion()
    if not abierto:
        avisos.append("la tarea se ha ejecutado con el mercado cerrado: no se lanza ninguna orden")
    elif (aprobadas or c["posiciones"]) and via and (via == "plan" or gasto.cabe("apertura")):
        r = reglas()
        datos = {k: d.get(k) for k in ("generado", "mercado_hoy", "indices", "plan_de_la_manana", "sesion_de_hoy",
                                       "noticias", "cartera", "valoracion", "mirofish")}
        resp, info = modelo.preguntar("apertura", "apertura", r["modelos"]["apertura"], datos, ESQ_APERTURA,
                                      nivel="apertura", tope_usd=r["credito_api"]["tope_por_llamada_usd"]["apertura"],
                                      esfuerzo="medium", espera=420, carpeta=carpeta_del_dia("datos"))
        costes.append(info)
        if resp:
            comentario = resp.get("comentario", "")
            for x in resp.get("decisiones", []):
                decision[x["ticker"].upper()] = (x["decision"], x.get("motivo", ""))
            for x in resp.get("stops", []):
                t = x["ticker"].upper()
                if _subir_stop(c, t, x.get("nuevo_stop"), precios.get(t), api, avisos):
                    hechos.append({"hora": ahora_madrid().strftime("%H:%M"), "ticker": t, "accion": "stop subido",
                                   "cantidad": 0, "precio": x["nuevo_stop"], "importe_usd": 0, "motivo": x.get("motivo", "")})
            for x in resp.get("ventas", []):
                if x["ticker"].upper() in c["posiciones"]:
                    aprobadas.append({"ticker": x["ticker"].upper(), "accion": "vender", "motivo": x.get("motivo", "")})
        else:
            avisos.append("la revisión del modelo falló (%s): se aplican solo las reglas" % info.get("error", ""))
    if abierto:
        for o in [x for x in aprobadas if x.get("accion") == "vender"] + [x for x in aprobadas if x.get("accion") != "vender"]:
            t = o["ticker"].upper()
            precio = (sesion.get(t) or {}).get("ultimo") or precios.get(t)
            try:
                if o.get("accion") == "vender":
                    if t in c["posiciones"] and precio:
                        hechos.append(ordenes.ejecutar_venta(c, t, precio, o.get("motivo") or "decisión del plan", api))
                    continue
                que, por_que = decision.get(t, ("ejecutar", ""))
                if que != "ejecutar":
                    descartadas.append([t, por_que or "descartada en la revisión de apertura"])
                    continue
                if not precio:
                    descartadas.append([t, "sin precio de la sesión"])
                    continue
                veredicto, motivos, cantidad = ordenes.validar(o, _resumen(c, d), d["valoracion"].get(t), precio)
                if veredicto != "APROBADA":
                    descartadas.append([t, "; ".join(motivos)])
                    continue
                hechos.append(ordenes.ejecutar_compra(c, o, cantidad, precio, api))
            except (ErrorFuente, ValueError) as e:
                descartadas.append([t, str(e)])
    cartera.guardar(c)
    res = _resumen(c, d)
    escribir_json(carpeta_del_dia("datos") / "apertura.json",
                  {"hechos": hechos, "descartadas": descartadas, "comentario": comentario, "llamadas": costes})
    cuerpo = [inf.bloque_cartera(res)]
    if comentario:
        cuerpo.append("<h2>Cómo ha abierto</h2><p>%s</p>" % inf.e(comentario))
    cuerpo.append("<h2>Mercado a las %s</h2>%s" % (d["hora_madrid"], inf.bloque_indices(
        d["indices"], ["S&P 500", "Nasdaq 100", "Russell 2000", "VIX", "Euro/dólar"])))
    cuerpo.append("<h2>Operaciones lanzadas</h2>" + inf.bloque_operaciones(
        hechos, "No se ha lanzado ninguna operación."))
    cuerpo.append("<h2>Órdenes del plan descartadas</h2>" + inf.tabla(
        [("Valor", 0), ("Motivo", 0)], [[inf.e(t), inf.e(m)] for t, m in descartadas], "Ninguna."))
    cuerpo.append("<h2>Posiciones tras la apertura</h2>" + inf.bloque_posiciones(res))
    ruta, paginas, escala = inf.componer("Apertura", _fecha_larga(ahora_madrid()), _chips(d, costes),
                                         "".join(cuerpo), _pie(avisos), carpeta_informes() / "1545-apertura.pdf")
    return "apertura: %d operaciones, %d descartadas -> %s (%s pág.)" % (len(hechos), len(descartadas), ruta, paginas)


# ---------------------------------------------------------------- 22:10 cierre

def _cuenta_del_dia(res, ops, velas, hoy):
    """La cuenta del día en claro: con cuánto empezaste, cuánto estuvo invertido
    y dónde, cuánto arriesgabas y con cuánto terminas."""
    eur = res["eurusd"]

    def usd_eur(x, signo=False):
        return [inf.dinero(x, "$", signo), inf.dinero(x / eur, "€", signo)]
    inicio = res["inicio_dia_usd"]
    invertido = riesgo = 0.0
    partes, filas = [], [["<b>Tenías al empezar el día</b>", "", *usd_eur(inicio), ""]]
    if res["aportado_hoy_usd"] and inicio != res["aportado_usd"]:      # el primer día todo es aportación
        filas.append(["de ellos, aportación de hoy", "", *usd_eur(res["aportado_hoy_usd"]), ""])
    for p in res["posiciones"]:
        s = velas.get(p["ticker"])
        comprada_hoy = p["fecha_entrada"] >= hoy
        previo = p["entrada"] if comprada_hoy or not s or len(s["c"]) < 2 else s["c"][-2]
        puesto = p["cantidad"] * previo                 # lo que valía al empezar el día, o lo pagado si se compró hoy
        en_riesgo = max(p["cantidad"] * (p["precio"] - p["stop"]), 0.0)
        invertido += puesto
        riesgo += en_riesgo
        partes.append("%s en %s" % (inf.dinero(puesto), inf.e(p["ticker"])))
        filas.append(["Invertido en %s%s" % (inf.e(p["ticker"]), " <small class='gris'>comprada hoy</small>" if comprada_hoy else ""),
                      "arriesgas %s hasta el stop" % inf.dinero(en_riesgo), *usd_eur(puesto),
                      inf.dinero(p["valor_usd"] - puesto, "$", True)])
    for o in ops:
        if o["accion"] == "venta":
            filas.append(["Vendida hoy: %s" % inf.e(o["ticker"]), inf.e(o.get("motivo", "")), *usd_eur(o["importe_usd"]),
                          "%s <small class='gris'>toda la operación</small>" % inf.dinero(o.get("resultado_usd"), "$", True)])
    filas.append(["En caja, sin invertir", "", *usd_eur(res["caja_usd"]), ""])
    filas.append(["<b>Terminas el día con</b>", "", *("<b>%s</b>" % x for x in usd_eur(res["valor_usd"])),
                  "<b>%s</b>" % inf.dinero(res["resultado_dia_usd"], "$", True)])
    if partes:
        frase = ("Empezaste el día con %s. Estuvieron invertidos %s (%s) y el resto, %s, en caja. Si hubieran saltado "
                 "todos los stops habrías perdido como mucho %s. Terminas con %s: %s (%s)." % (
                     inf.dinero(inicio), inf.dinero(invertido), ", ".join(partes), inf.dinero(res["caja_usd"]),
                     inf.dinero(riesgo), inf.dinero(res["valor_usd"]),
                     inf.dinero(res["resultado_dia_usd"], "$", True), inf.pct(res["resultado_dia_pct"], 2)))
    else:
        frase = "Empezaste el día con %s. No hubo nada invertido: todo estuvo en caja. Terminas con %s (%s)." % (
            inf.dinero(inicio), inf.dinero(res["valor_usd"]), inf.dinero(res["resultado_dia_usd"], "$", True))
    return "<h2>La cuenta del día</h2><p>%s</p>%s" % (frase, inf.tabla(
        [("", 0), ("", 0), ("Dólares", 1), ("Euros", 1), ("Resultado de hoy", 1)], filas))


def cierre(a):
    d, velas, c = dossier.construir("cierre")
    avisos = d["avisos_de_datos"]
    if not d["mercado_hoy"]["abre_hoy"]:
        cartera.guardar(c)
        return "cierre: mercado cerrado -> %s" % _nota_cerrado(d, "2210-cierre.pdf", "Cierre")[0]
    if not (d["mercado_hoy"].get("ya_cerro") or a.forzar):
        avisos.append("la bolsa aún no ha cerrado: los resultados son provisionales")
    api = _api(avisos)
    _vigilar(c, velas, api, avisos)
    res = _resumen(c, d)
    hoy = ahora_madrid().date().isoformat()
    ops = cartera.operaciones(hoy)
    resp, costes = None, []
    via = d["via"] = None if a.sin_modelo else modelo.autenticacion()
    if via and (via == "plan" or gasto.cabe("cierre")):
        r = reglas()
        datos = {k: d.get(k) for k in ("generado", "mercado_hoy", "indices", "plan_de_la_manana", "sesion_de_hoy",
                                       "noticias", "marcador", "mirofish")}
        datos.update({"cartera": res, "operaciones_de_hoy": ops,
                      "apertura": leer_json(carpeta_del_dia("datos") / "apertura.json", {})})
        resp, info = modelo.preguntar("cierre", "cierre", r["modelos"]["cierre"], datos, ESQ_CIERRE, nivel="cierre",
                                      tope_usd=r["credito_api"]["tope_por_llamada_usd"]["cierre"], esfuerzo="medium",
                                      espera=420, carpeta=carpeta_del_dia("datos"))
        costes.append(info)
        if not resp:
            avisos.append("el balance del modelo falló (%s)" % info.get("error", ""))
    resp = resp or {"resumen": "Sin balance del modelo: el informe trae solo las cifras.", "aciertos": [],
                    "fallos": [], "lecciones": [], "avisos_manana": []}
    for leccion in resp["lecciones"]:
        anexar_linea(ESTADO / "lecciones.jsonl", {"fecha": hoy, "leccion": leccion})
    cartera.registrar_valor(c, res)
    cartera.guardar(c)
    escribir_json(carpeta_del_dia("datos") / "cierre.json", dict(resp, cartera=res, llamadas=costes))
    diario = RAIZ / ("diario" + SUFIJO) / (hoy + ".md")
    diario.parent.mkdir(parents=True, exist_ok=True)
    diario.write_text("# %s\n\n%s\n\n- Cartera: %.2f $ (%.2f €). Día: %s %%. S&P 500: %s %%.\n\n## Operaciones\n\n%s\n\n"
                      "## Aciertos\n\n%s\n\n## Fallos\n\n%s\n\n## Lecciones\n\n%s\n\n## Avisos para mañana\n\n%s\n" % (
                          hoy, resp["resumen"], res["valor_usd"], res["valor_eur"], res["resultado_dia_pct"],
                          res["referencia_dia_pct"],
                          "\n".join("- %s %s %s × %.4g a %.2f $ (%s)" % (o["hora"], o["accion"], o["ticker"], o["cantidad"],
                                                                         o["precio"], o["motivo"]) for o in ops) or "- Ninguna.",
                          *("\n".join("- " + x for x in resp[k]) or "- Nada." for k in
                            ("aciertos", "fallos", "lecciones", "avisos_manana"))), encoding="utf-8")
    m = cartera.estadisticas()
    sp = d["indices"]["S&P 500"]
    cuerpo = [inf.cifras([
        ("Valor de la cartera", "%s <small>(%s)</small>" % (inf.dinero(res["valor_usd"]), inf.dinero(res["valor_eur"], "€"))),
        ("Resultado del día", "%s <small>%s</small>" % (inf.dinero(res["resultado_dia_usd"], "$", True), inf.pct(res["resultado_dia_pct"], 2))),
        ("S&P 500 hoy", inf.pct(sp["var_1d"], 2)),
        ("Total frente al S&P 500", inf.dinero(res["ventaja_sobre_referencia_usd"], "$", True))]),
        _cuenta_del_dia(res, ops, velas, hoy),
        "<h2>Balance</h2><p>%s</p>" % inf.e(resp["resumen"]),
        "<p>Semana: %s · Total: %s (%s) · Aportado: %s · Mismo dinero en el S&P 500: %s · Operaciones cerradas: %s%s</p>" % (
            inf.pct(res["resultado_semana_pct"], 2), inf.dinero(res["resultado_total_eur"], "€", True),
            inf.pct(res["resultado_total_pct"], 2), inf.dinero(res["aportado_eur"], "€"), inf.dinero(res["referencia_usd"]),
            m["cerradas"], (", %d %% con ganancia" % m["acierto_pct"]) if m["cerradas"] else ""),
        "<h2>Posiciones al cierre</h2>" + inf.bloque_posiciones(res),
        "<h2>Operaciones de hoy</h2>" + inf.bloque_operaciones(ops, "Hoy no ha habido operaciones."),
        '<div class="dos"><div><h2>Aciertos</h2>%s<h2>Fallos</h2>%s</div><div><h2>Lecciones</h2>%s<h2>Avisos para mañana</h2>%s</div></div>' % (
            inf.lista(resp["aciertos"]), inf.lista(resp["fallos"]), inf.lista(resp["lecciones"]),
            inf.lista(resp["avisos_manana"])),
        "<h2>Mercado al cierre</h2>" + inf.bloque_indices(d["indices"], ["S&P 500", "Nasdaq 100", "Russell 2000", "VIX", "Euro/dólar"])]
    ruta, paginas, escala = inf.componer("Cierre", _fecha_larga(ahora_madrid()), _chips(d, costes), "".join(cuerpo),
                                         _pie(avisos), carpeta_informes() / "2210-cierre.pdf")
    return "cierre: cartera %.2f $, día %s %% -> %s (%s pág.)" % (res["valor_usd"], res["resultado_dia_pct"], ruta, paginas)


# ---------------------------------------------------------------- entrada

def comprobar(a):
    """Comprobación sin efectos: que el entorno desde el que se lanza la tarea
    puede leer la configuración, escribir, encontrar los programas y llamar a un modelo."""
    import os
    from .base import claves
    lineas = ["carpeta del proyecto legible: %s" % (RAIZ / "config" / "reglas.json").exists(),
              "claves leídas de .env: %d" % len([v for v in claves().values() if v]),
              "herramientas de datos: %s" % (RAIZ.parent / "repos" / "day-trader-skill").exists(),
              "Chrome para los PDF: %s" % os.path.exists(inf.CHROME)]
    via = modelo.autenticacion()
    lineas.append("llamadas a modelos: %s" % (via or "no disponibles"))
    if via:
        resp, info = modelo.preguntar("comprobar", "cierre", modelo.SONDEO, {"generado": "comprobación del entorno"},
                                      ESQ_CIERRE, nivel="comprobar", tope_usd=0.05, esfuerzo="low", espera=120)
        lineas.append("respuesta del modelo: %s%s" % ("correcta" if info["ok"] else "FALLO", "" if info["ok"] else " (%s)" % info.get("error")))
    return "comprobación · " + " · ".join(lineas)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tareas del bot de bolsa (cartera simulada)")
    ap.add_argument("tarea", choices=["manana", "apertura", "cierre", "mirofish", "mirofish-sintesis", "comprobar"])
    ap.add_argument("--sin-modelo", action="store_true", help="solo datos y reglas, sin llamar a ningún modelo")
    ap.add_argument("--forzar", action="store_true", help="ignora el horario del mercado (para pruebas)")
    ap.add_argument("--nivel", choices=["alto", "medio", "bajo"], help="fija el nivel de análisis de la mañana")
    ap.add_argument("--solo-estratega", action="store_true",
                    help="mañana: rehace la decisión y el informe con los datos y la investigación ya hechos hoy")
    a = ap.parse_args(argv)
    print("[%s] inicio de %s%s" % (ahora_madrid().strftime("%Y-%m-%d %H:%M:%S"), a.tarea, " (PRUEBA)" if SUFIJO else ""), flush=True)
    try:
        from . import mirofish
        tareas = {"manana": manana, "apertura": apertura, "cierre": cierre, "comprobar": comprobar,
                  "mirofish": mirofish.ejecutar, "mirofish-sintesis": mirofish.resintetizar}
        print("[%s] %s" % (ahora_madrid().strftime("%H:%M:%S"), tareas[a.tarea](a)))
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
