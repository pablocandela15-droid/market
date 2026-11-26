"""Informe semanal: qué ha pasado, qué salió bien y mal, y qué aprende la cartera de sus errores.

    python3 -m bot.tarea semanal             # la última semana terminada (los sábados se lanza sola, tras el informe de la mañana)
    python3 -m bot.tarea semanal --forzar    # la semana en curso, aunque no haya terminado
    python3 -m bot.tarea semanal --sin-modelo   # solo cifras, gráficas e impuestos; no toca las lecciones

Cómo aprende: cada cierre deja hasta tres lecciones sueltas (estado/lecciones.jsonl). Una vez por
semana un modelo repasa lo planeado, lo hecho y lo que pasó después, separa el error de la mala suerte
y deja una lista corta de lecciones vigentes (estado/lecciones_vigentes.json). El estratega de la mañana
y la revisión de la apertura reciben esa lista cada día; la semana siguiente se comprueba si se cumplió
y cada lección se mantiene, se corrige o se retira.
"""
from __future__ import annotations

import datetime as dt

from . import cartera, gasto, graficas, impuestos, modelo
from . import informe as inf
from . import tarea as T
from .base import (ESTADO, RAIZ, SUFIJO, ahora_madrid, carpeta_del_dia, carpeta_informes, escribir_json, leer_json,
                   leer_lineas, reglas)

VIGENTES = ESTADO / "lecciones_vigentes.json"
MAX_LECCIONES = 8
S = {"type": "string"}
ESQ_SEMANAL = T._obj(
    resumen=S, mercado=S,
    aciertos=T._lista(T._obj(que=S, por_que=S)),
    errores=T._lista(T._obj(que=S, causa=T._op("análisis", "reglas", "ejecución", "mala suerte"), por_que=S, como_evitarlo=S)),
    lecciones=T._lista(T._obj(texto=S, origen=S, estado=T._op("nueva", "se mantiene", "corregida"))),
    retiradas=T._lista(T._obj(texto=S, motivo=S)),
    semana_que_viene=T._lista(S))


# ---------------------------------------------------------------- lecciones

def vigentes():
    return leer_json(VIGENTES, {}) or {}


def lecciones_para_hoy():
    """Lo que reciben el estratega y la apertura: las lecciones vigentes (ya contrastadas en la revisión
    semanal) y las que han dejado los cierres desde entonces (aún sin contrastar)."""
    v = vigentes()
    desde = v.get("actualizado", "")
    sueltas = [x for x in leer_lineas(ESTADO / "lecciones.jsonl") if x["fecha"] >= desde]
    return {"lecciones_vigentes": [{"leccion": x["texto"], "desde": x["desde"]} for x in v.get("lecciones", [])],
            "lecciones_recientes": [x["leccion"] for x in sueltas][-6:]}


def guardar_lecciones(resp, hoy, lunes, domingo):
    """Guarda la lista que deja la revisión. Si viene vacía, se conserva la anterior."""
    previo = vigentes()
    nuevas = [x for x in (resp.get("lecciones") or []) if (x.get("texto") or "").strip()][:MAX_LECCIONES]
    if not nuevas:
        return previo.get("lecciones", [])
    antiguas = {x["texto"]: x for x in previo.get("lecciones", [])}
    lista = [{"texto": x["texto"].strip(), "origen": (x.get("origen") or "").strip(), "estado": x.get("estado") or "nueva",
              "desde": antiguas.get(x["texto"].strip(), {}).get("desde", hoy)} for x in nuevas]
    retiradas = previo.get("retiradas", []) + [dict(x, fecha=hoy) for x in (resp.get("retiradas") or []) if x.get("texto")]
    escribir_json(VIGENTES, {"actualizado": hoy, "semana": [lunes, domingo], "lecciones": lista, "retiradas": retiradas[-20:]})
    return lista


# ---------------------------------------------------------------- datos de la semana

def semana(hoy, en_curso=False):
    """Lunes y domingo de la semana que se revisa: la que acaba (sábado y domingo) o la anterior;
    con `en_curso`, la de hoy."""
    ref = hoy if en_curso or hoy.weekday() >= 5 else hoy - dt.timedelta(days=7)
    lunes = ref - dt.timedelta(days=ref.weekday())
    return lunes.isoformat(), (lunes + dt.timedelta(days=6)).isoformat()


def _carpeta(fecha):
    return RAIZ / ("datos" + SUFIJO) / fecha


def _dias(lunes, domingo):
    """Lo planeado, lo hecho en la apertura y el balance de cada día de la semana que tenga datos."""
    out = []
    dia = dt.date.fromisoformat(lunes)
    while dia.isoformat() <= domingo:
        f = dia.isoformat()
        plan, ap, ci = (leer_json(_carpeta(f) / n, {}) or {} for n in ("plan.json", "apertura.json", "cierre.json"))
        if plan or ap or ci:
            fila = {"fecha": f, "dia": T.DIAS[dia.weekday()]}
            if plan:
                fila["plan"] = {
                    "postura": plan.get("tono"), "panorama": plan.get("panorama"), "riesgos": plan.get("riesgos"),
                    "ordenes": [{k: o.get(k) for k in ("ticker", "accion", "entrada", "stop", "objetivo", "conviccion",
                                                       "veredicto", "motivos", "por_que", "que_esperamos")}
                                for o in plan.get("ordenes", [])],
                    "posiciones": [{k: p.get(k) for k in ("ticker", "decision", "comentario")} for p in plan.get("posiciones", [])],
                    "vigilados": [v.get("ticker") for v in plan.get("vigilados", [])]}
            if ap:
                fila["apertura"] = {"comentario": ap.get("comentario"), "sin_compras": ap.get("sin_compras"),
                                    "cambios": [{k: x.get(k) for k in ("ticker", "que", "previsto", "entrada_prevista",
                                                                       "precio", "por_que")} for x in ap.get("cambios", [])]}
            if ci:
                res = ci.get("cartera") or {}
                fila["cierre"] = {k: ci.get(k) for k in ("resumen", "aciertos", "fallos", "lecciones")}
                fila["cierre"].update({"cartera_dia_pct": res.get("resultado_dia_pct"), "sp500_mismo_dinero_dia_pct": res.get("referencia_dia_pct"),
                                       "valor_usd": res.get("valor_usd"), "caja_usd": res.get("caja_usd"),
                                       "invertido_pct": res.get("invertido_pct")})
            out.append(fila)
        dia += dt.timedelta(days=1)
    return out


def _ultimo_cierre(hasta):
    """La cartera valorada en el último cierre guardado hasta esa fecha: (fecha, resumen, índices)."""
    for carpeta in sorted((RAIZ / ("datos" + SUFIJO)).glob("*/cierre.json"), reverse=True):
        fecha = carpeta.parent.name
        if fecha <= hasta:
            res = (leer_json(carpeta, {}) or {}).get("cartera")
            if res:
                indices = (leer_json(carpeta.parent / "dossier-cierre.json", {}) or {}).get("indices") or {}
                return fecha, res, indices
    return None, None, {}


def cuentas(c, lunes, domingo):
    """La semana en números: de dónde partía, dónde acaba y lo mismo para el S&P 500 con el mismo dinero."""
    vd = c.get("valor_diario", [])
    antes = [v for v in vd if v["fecha"] < lunes]
    dentro = [v for v in vd if lunes <= v["fecha"] <= domingo]
    aportado = sum(a["usd"] for a in c["aportaciones"] if lunes <= a["fecha"] <= domingo)
    if antes:
        base, base_ref = antes[-1]["valor_usd"] + aportado, antes[-1]["referencia_usd"] + aportado
    else:
        base = base_ref = sum(a["usd"] for a in c["aportaciones"] if a["fecha"] <= domingo)
    fin = dentro[-1] if dentro else None

    def pct(a, b):
        return round((a / b - 1) * 100, 2) if a is not None and b else None
    return {"inicio_usd": round(base, 2), "aportado_en_la_semana_usd": round(aportado, 2),
            "final_usd": fin["valor_usd"] if fin else None,
            "resultado_usd": round(fin["valor_usd"] - base, 2) if fin else None,
            "resultado_pct": pct(fin["valor_usd"], base) if fin else None,
            "referencia_final_usd": fin["referencia_usd"] if fin else None,
            "referencia_pct": pct(fin["referencia_usd"], base_ref) if fin else None,
            "diferencia_usd": round((fin["valor_usd"] - base) - (fin["referencia_usd"] - base_ref), 2) if fin else None,
            "dias": dentro}


def _corto(fecha):
    f = dt.date.fromisoformat(fecha)
    return "%d %s" % (f.day, T.MESES[f.month - 1][:3])


# ---------------------------------------------------------------- el PDF

def _graficas(c, res, ops, domingo):
    vd = [v for v in c.get("valor_diario", []) if v["fecha"] <= domingo]
    evol = graficas.evolucion([_corto(v["fecha"]) for v in vd],
                              [("Cartera", [v["valor_usd"] for v in vd]),
                               ("Mismo dinero en el S&P 500", [v["referencia_usd"] for v in vd])]) if vd else ""
    filas = [(o["ticker"], "vendida el %s" % _corto(o["fecha"]), o.get("resultado_usd")) for o in ops if o["accion"] == "venta"]
    filas += [(p["ticker"], "en cartera", p["resultado_usd"]) for p in res["posiciones"]]
    barras = graficas.resultados(filas)
    partes = [(p["ticker"], p["valor_usd"]) for p in res["posiciones"]][:5] + [("Caja", res["caja_usd"])]
    return ('<div class="dos"><div>%s</div><div>%s</div></div>%s' % (
        graficas.figura("Cartera frente al mismo dinero en el S&P 500, en dólares", evol,
                        "Desde el primer día. Los dos reciben las mismas aportaciones en las mismas fechas.") if evol
        else '<p class="gris">Aún no hay cierres guardados para dibujar la evolución.</p>',
        graficas.figura("Qué ha ganado o perdido cada valor", barras,
                        "Lo vendido esta semana, con su resultado final, y lo que sigue en cartera, a precios del último cierre.") if barras
        else '<p class="gris">Sin ventas ni posiciones abiertas.</p>',
        graficas.figura("Dónde está el dinero", graficas.reparto(partes), "La parte gris es dinero sin invertir.")))


def _retrospectiva(resp, lecciones):
    def parrafos(items, plantilla):
        return "".join(plantilla(x) for x in items) or '<p class="gris">Nada que destacar.</p>'
    aciertos = parrafos(resp.get("aciertos") or [], lambda x: "<p><b>%s</b><br>%s</p>" % (inf.e(x["que"]), inf.e(x["por_que"])))
    errores = parrafos(resp.get("errores") or [], lambda x: "<p><b>%s</b> <span class='cat'>%s</span><br><b>Por qué pasó:</b> %s<br>"
                       "<b>Cómo se evita:</b> %s</p>" % (inf.e(x["que"]), inf.e(x["causa"]), inf.e(x["por_que"]), inf.e(x["como_evitarlo"])))
    etiquetas = {"nueva": "Nueva", "se mantiene": "Se mantiene", "corregida": "Corregida"}
    lista = "".join("<li><b>%s.</b> %s%s</li>" % (
        etiquetas.get(x.get("estado"), "Vigente"), inf.e(x["texto"]),
        " <span class='gris'>De dónde sale: %s</span>" % inf.e(x["origen"]) if x.get("origen") else "") for x in lecciones)
    retiradas = "".join("<li><b>Retirada.</b> %s <span class='gris'>Motivo: %s</span></li>" % (inf.e(x["texto"]), inf.e(x["motivo"]))
                        for x in resp.get("retiradas") or [])
    return ('<h2>Lo que salió bien</h2><div class="columnas">%s</div><h2>Errores de la semana</h2><div class="columnas">%s</div>'
            "<h2>Lo que el bot ha aprendido</h2>%s<p class='gris'>Estas lecciones las recibe cada mañana quien decide las compras "
            "y cada tarde quien revisa la apertura. La semana que viene se comprueba si se cumplieron.</p>"
            "<h2>La semana que viene</h2>%s" % (
                aciertos, errores,
                "<ul>%s%s</ul>" % (lista, retiradas) if lista or retiradas else '<p class="gris">Todavía no hay lecciones.</p>',
                inf.lista(resp.get("semana_que_viene"), "Sin cambios previstos.")))


def _pdf(lunes, domingo, c, res, cu, ops, imp, per, resp, lecciones, costes, avisos, chips):
    hoy = ahora_madrid()
    a, b = dt.date.fromisoformat(lunes), dt.date.fromisoformat(domingo)
    cuerpo = [inf.cifras([
        ("Valor de la cartera", "%s <small>(%s)</small>" % (inf.dinero(res["valor_usd"]), inf.dinero(res["valor_eur"], "€"))),
        ("Resultado de la semana", "%s <small>%s</small>" % (inf.dinero(cu["resultado_usd"], "$", True), inf.pct(cu["resultado_pct"], 2))),
        ("El mismo dinero en el S&P 500", inf.pct(cu["referencia_pct"], 2)),
        ("Caja sin invertir", "%s <small>%s&nbsp;%% invertido</small>" % (inf.dinero(res["caja_usd"]), inf.num(res["invertido_pct"], 0)))])]
    if cu["aportado_en_la_semana_usd"]:
        cuerpo.append("<p class='gris'>Esta semana entraron %s de aportación; no cuentan como ganancia.</p>" % inf.dinero(cu["aportado_en_la_semana_usd"]))
    cuerpo.append("<h2>Qué ha pasado esta semana</h2><p>%s</p>%s" % (
        inf.e(resp["resumen"]), "<p>%s</p>" % inf.e(resp["mercado"]) if resp.get("mercado") else ""))
    cuerpo.append(_graficas(c, res, ops, domingo))
    cuerpo.append("<h2>Ganancias e impuestos</h2>" + inf.bloque_impuestos(imp, per, "esta semana"))
    cuerpo.append(_retrospectiva(resp, lecciones))
    porfecha = {}
    for o in ops:
        porfecha.setdefault(o["fecha"], []).append("%s %s" % (o["accion"], o["ticker"]))
    previo = None
    filas = []
    for v in c.get("valor_diario", []):
        if lunes <= v["fecha"] <= domingo:
            filas.append([inf.e(_corto(v["fecha"])), inf.dinero(v["valor_usd"]), inf.dinero(v["valor_eur"], "€"),
                          inf.dinero(v["referencia_usd"]), inf.dinero(v["valor_usd"] - v["referencia_usd"], "$", True),
                          inf.e(", ".join(porfecha.get(v["fecha"], [])) or "sin operaciones")])
        previo = v
    cuerpo.append("<h2>Día a día</h2>" + inf.tabla(
        [("Cierre del", 0), ("Cartera", 1), ("En euros", 1), ("Mismo dinero en el S&P 500", 1), ("Diferencia", 1), ("Operaciones", 0)],
        filas, "Esta semana no hay cierres guardados."))
    cuerpo.append("<h2>Operaciones de la semana</h2>" + inf.bloque_operaciones(
        [dict(o, hora="%s %s" % (_corto(o["fecha"]), o["hora"])) for o in ops], "Esta semana no ha habido operaciones."))
    cuerpo.append("<h2>Posiciones al último cierre</h2>" + inf.bloque_posiciones(res))
    titulo = "Informe semanal"
    sub = "semana del %d%s al %d de %s de %d · hecho el %s" % (
        a.day, "" if a.month == b.month else " de " + T.MESES[a.month - 1], b.day, T.MESES[b.month - 1], b.year, T._fecha_larga(hoy))
    return inf.componer(titulo, sub, chips, "".join(cuerpo), T._pie(avisos), carpeta_informes() / "0710-semanal.pdf", paginas_max=3)


# ---------------------------------------------------------------- la tarea

def ejecutar(a):
    hoy = ahora_madrid().date()
    lunes, domingo = semana(hoy, getattr(a, "forzar", False))
    r = reglas()
    c = cartera.cargar()
    if not c:
        return "semanal: todavía no hay cartera"
    avisos = []
    # La cartera de ahora (lo que hay de verdad), valorada con los precios del último cierre guardado.
    fecha_cierre, cierre, indices = _ultimo_cierre(domingo)
    precios = {p["ticker"]: p["precio"] for p in (cierre or {}).get("posiciones", [])}
    eurusd = (cierre or {}).get("eurusd") or c["aportaciones"][-1].get("eurusd") or 1.0
    vd = [v for v in c.get("valor_diario", []) if v["fecha"] <= domingo]
    unidades = c["referencia"]["unidades"]
    ref = vd[-1]["referencia_usd"] / unidades if vd and unidades else 1.0
    res = cartera.resumen(c, precios, eurusd, ref)
    if not cierre:
        avisos.append("no hay ningún cierre guardado: las posiciones van a su precio de compra")
    elif set(precios) != set(c["posiciones"]):
        avisos.append("la cartera ha cambiado desde el cierre del %s: lo comprado después va a su precio de compra" % _corto(fecha_cierre))
    ops = [o for o in cartera.operaciones(lunes) if o["fecha"] <= domingo]
    cu = cuentas(c, lunes, domingo)
    imp = res["impuestos"]
    per = impuestos.periodo(c, lunes, domingo, r=r)
    dias = _dias(lunes, domingo)
    previas = vigentes()
    resp, costes = None, []
    via = None if getattr(a, "sin_modelo", False) else modelo.autenticacion()
    if via and (via == "plan" or gasto.cabe("semanal")):
        mf = (leer_json(ESTADO / "mirofish" / "ultima.json", {}) or {})
        datos = {
            "semana": {"lunes": lunes, "domingo": domingo, "revisada_el": hoy.isoformat(),
                       "terminada": hoy.isoformat() > domingo or hoy.weekday() >= 5},
            "resultado": {k: cu[k] for k in cu if k != "dias"}, "cierres_diarios": cu["dias"], "dias": dias,
            "operaciones_de_la_semana": ops, "marcador": cartera.estadisticas(),
            "cartera_al_ultimo_cierre": {k: res.get(k) for k in ("valor_usd", "valor_eur", "caja_usd", "invertido_pct",
                                                                 "resultado_total_usd", "ventaja_sobre_referencia_usd", "posiciones")},
            "indices_al_ultimo_cierre": {n: {k: q.get(k) for k in ("precio", "var_1d", "var_5d", "var_1m")}
                                         for n, q in indices.items() if n in ("S&P 500", "Nasdaq 100", "Russell 2000", "VIX")},
            "impuestos": {k: imp[k] for k in imp if k != "sin_vender"},
            "lecciones_vigentes": previas.get("lecciones", []),
            "lecciones_de_los_cierres": [x for x in leer_lineas(ESTADO / "lecciones.jsonl") if lunes <= x["fecha"] <= domingo],
            "notas_del_dueno": leer_json(RAIZ / "config" / "notas_del_dueno.json", []) or [],
            "cambios_de_reglas": leer_json(RAIZ / "config" / "historial_de_reglas.json", []) or [],
            "reglas": {"riesgo": r["riesgo"], "valoracion": r["valoracion"]},
            "simulacion_semanal": {"fecha": mf.get("fecha"), **{k: (mf.get("resumen") or {}).get(k) for k in ("tono", "mercado", "sectores")}}
            if mf else None}
        resp, info = modelo.preguntar("semanal", "semanal", r["modelos"].get("semanal", r["modelos"]["estratega"]), datos,
                                      ESQ_SEMANAL, nivel="semanal", esfuerzo="high", espera=720, carpeta=carpeta_del_dia("datos"),
                                      tope_usd=r["credito_api"]["tope_por_llamada_usd"].get("semanal", 1.5))
        costes.append(info)
        if not resp:
            avisos.append("la revisión del modelo falló (%s): el informe trae solo las cifras" % info.get("error", ""))
    elif via:
        avisos.append("no queda crédito para la revisión semanal: el informe trae solo las cifras")
    if resp:
        lecciones = guardar_lecciones(resp, hoy.isoformat(), lunes, domingo)
    else:
        resp = {"resumen": "Sin revisión del modelo: este informe trae las cifras, las gráficas y los impuestos de la semana.",
                "mercado": "", "aciertos": [], "errores": [], "retiradas": [], "semana_que_viene": []}
        lecciones = [dict(x, estado="se mantiene") for x in previas.get("lecciones", [])]
    escribir_json(carpeta_del_dia("datos") / "semanal.json", {
        "semana": [lunes, domingo], "resultado": {k: cu[k] for k in cu if k != "dias"}, "impuestos": imp,
        "impuestos_de_la_semana": per, "revision": resp, "lecciones": lecciones, "llamadas": costes})
    from . import dossier
    chips = T._chips({"modo": "alpaca (simulación)" if dossier._hay_alpaca() else "en seco (sin claves de Alpaca)",
                      "credito_api": gasto.presupuesto(), "via": via}, costes)
    ruta, paginas, escala = _pdf(lunes, domingo, c, res, cu, ops, imp, per, resp, lecciones, costes, avisos, chips)
    return "semanal: semana del %s al %s, %s %% (S&P 500 %s %%), %d lecciones vigentes, coste %.2f $ -> %s (%s pág., escala %s)" % (
        lunes, domingo, cu["resultado_pct"], cu["referencia_pct"], len(lecciones),
        sum(i.get("coste_usd", 0) for i in costes), ruta, paginas, escala)
