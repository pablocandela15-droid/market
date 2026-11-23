"""Informes en PDF de una página: se componen en HTML y Chrome los imprime.

Si el contenido no cabe en una hoja, se reduce la escala hasta que quepa.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import tempfile
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ESCALAS = (1.0, 0.93, 0.86, 0.8, 0.74, 0.68)

CSS = """
@page { size: A4; margin: 9mm 10mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-size: 8.4pt; color: #1b1f24;
       margin: 0; line-height: 1.32; zoom: %(escala)s; }
header { display: flex; justify-content: space-between; align-items: flex-end;
         border-bottom: 2px solid #1b1f24; padding-bottom: 4px; margin-bottom: 7px; }
h1 { font-size: 15pt; margin: 0; letter-spacing: -0.2px; }
.sub { color: #57606a; font-size: 8.4pt; }
.chips span { display: inline-block; border: 1px solid #c9d1d9; border-radius: 9px; padding: 1px 7px;
              margin-left: 4px; font-size: 7.4pt; color: #57606a; }
h2 { font-size: 8.6pt; text-transform: uppercase; letter-spacing: 0.5px; color: #57606a;
     margin: 8px 0 3px; border-bottom: 1px solid #d8dee4; padding-bottom: 1px; }
.dos { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
table { border-collapse: collapse; width: 100%%; }
th { text-align: left; font-weight: 600; color: #57606a; font-size: 7.4pt; border-bottom: 1px solid #c9d1d9;
     padding: 1.5px 4px; white-space: nowrap; }
td { padding: 1.6px 4px; border-bottom: 1px solid #eef1f4; vertical-align: top; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.pos { color: #116329; } .neg { color: #b42318; } .gris { color: #6e7781; }
.ok { color: #116329; font-weight: 600; } .no { color: #b42318; font-weight: 600; }
p { margin: 2px 0 3px; }
ul { margin: 2px 0 2px 14px; padding: 0; } li { margin: 0 0 1px; }
.cifras { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; margin: 2px 0 4px; }
.cifra { border: 1px solid #d8dee4; border-radius: 5px; padding: 3px 6px; }
.cifra b { display: block; font-size: 11pt; font-variant-numeric: tabular-nums; }
.cifra b small { font-size: 7.6pt; font-weight: 500; }
.cifra .et { font-size: 7.2pt; color: #57606a; }
footer { margin-top: 7px; border-top: 1px solid #d8dee4; padding-top: 3px; font-size: 6.9pt; color: #6e7781; }
"""


def e(x):
    return html.escape(str(x)) if x is not None else ""


def corta(texto, n):
    """Recorta en un espacio y añade puntos suspensivos; devuelve HTML ya escapado."""
    texto = str(texto or "")
    if len(texto) <= n:
        return e(texto)
    return e(texto[:n].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…")


def num(x, dec=2):
    if x is None:
        return '<span class="gris">–</span>'
    x = round(x, dec) + 0.0          # evita el "-0,00"
    return ("{:,.%df}" % dec).format(x).replace(",", " ").replace(".", ",").replace(" ", ".")


def pct(x, dec=1):
    if x is None:
        return '<span class="gris">–</span>'
    x = round(x, dec) + 0.0
    return '<span class="%s">%s%s&nbsp;%%</span>' % ("pos" if x > 0 else "neg" if x < 0 else "gris",
                                                    "+" if x > 0 else "", num(x, dec))


def dinero(x, simbolo="$", signo=False):
    if x is None:
        return '<span class="gris">–</span>'
    x = round(x, 2) + 0.0
    txt = "%s%s&nbsp;%s" % ("+" if signo and x > 0 else "", num(x), simbolo)
    return '<span class="%s">%s</span>' % ("pos" if x > 0 else "neg" if x < 0 else "gris", txt) if signo else txt


def tabla(cabeceras, filas, vacio="Sin datos."):
    """cabeceras: lista de (texto, es_numero). filas: listas de celdas ya en HTML."""
    if not filas:
        return '<p class="gris">%s</p>' % e(vacio)
    th = "".join('<th%s>%s</th>' % (' class="n"' if n else "", e(t)) for t, n in cabeceras)
    tr = "".join("<tr>%s</tr>" % "".join('<td%s>%s</td>' % (' class="n"' if cabeceras[i][1] else "", c)
                                         for i, c in enumerate(f)) for f in filas)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (th, tr)


def lista(items, vacio="Nada que destacar."):
    items = [i for i in (items or []) if i]
    if not items:
        return '<p class="gris">%s</p>' % e(vacio)
    return "<ul>%s</ul>" % "".join("<li>%s</li>" % e(i) for i in items)


def cifras(pares):
    return '<div class="cifras">%s</div>' % "".join(
        '<div class="cifra"><b>%s</b><div class="et">%s</div></div>' % (v, e(k)) for k, v in pares)


SECTOR = {"tecnologia": "Tecnología", "renovables": "Renovables", "nuclear": "Nuclear", "defensa": "Defensa"}


def sector(s):
    return e(SECTOR.get(s, s))


def _paginas(ruta):
    return len(re.findall(rb"/Type\s*/Page(?![s])", Path(ruta).read_bytes()))


def componer(titulo, subtitulo, chips, cuerpo, pie, ruta_pdf):
    """Escribe el HTML y el PDF. Devuelve (ruta, páginas, escala). Si no hay
    Chrome, deja el HTML y devuelve su ruta."""
    ruta_pdf = Path(ruta_pdf)
    ruta_pdf.parent.mkdir(parents=True, exist_ok=True)
    ruta_html = ruta_pdf.with_suffix(".html")
    plantilla = ('<!doctype html><html lang="es"><head><meta charset="utf-8"><title>%s</title><style>%s</style></head>'
                 '<body><header><div><h1>%s</h1><div class="sub">%s</div></div><div class="chips">%s</div></header>'
                 '%s<footer>%s</footer></body></html>')
    chips_html = "".join("<span>%s</span>" % e(c) for c in chips if c)
    if not os.path.exists(CHROME):
        ruta_html.write_text(plantilla % (e(titulo), CSS % {"escala": 1}, e(titulo), e(subtitulo),
                                          chips_html, cuerpo, pie), encoding="utf-8")
        return ruta_html, None, None
    paginas = escala = None
    borrador = ruta_pdf.with_name("." + ruta_pdf.name + ".tmp")   # el PDF definitivo aparece ya terminado
    with tempfile.TemporaryDirectory() as perfil:
        for escala in ESCALAS:
            ruta_html.write_text(plantilla % (e(titulo), CSS % {"escala": escala}, e(titulo), e(subtitulo),
                                              chips_html, cuerpo, pie), encoding="utf-8")
            subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            "--no-first-run", "--user-data-dir=" + perfil,
                            "--print-to-pdf=" + str(borrador), ruta_html.as_uri()],
                           capture_output=True, timeout=120)
            if not borrador.exists():
                return ruta_html, None, None
            paginas = _paginas(borrador)
            if paginas <= 1:
                break
    os.replace(borrador, ruta_pdf)
    ruta_html.unlink(missing_ok=True)
    return ruta_pdf, paginas, escala


# ---------------------------------------------------------------- bloques comunes

PIE = ("Simulación sin dinero real. Análisis automático con datos públicos (Yahoo, Finviz, FRED, SEC, Damodaran, "
       "Dataroma): no es asesoramiento financiero y puede contener errores.")


def bloque_indices(indices, nombres):
    filas = []
    for n in nombres:
        q = indices.get(n)
        if q:
            filas.append([e(n), num(q["precio"], 4 if q["precio"] < 2 else 2), pct(q["var_1d"]),
                          pct(q["var_5d"]), pct(q["var_1m"])])
    return tabla([("", 0), ("Último", 1), ("Día", 1), ("5 días", 1), ("Mes", 1)], filas)


def bloque_cartera(c):
    return cifras([
        ("Valor de la cartera", "%s <small>(%s)</small>" % (dinero(c["valor_usd"]), dinero(c["valor_eur"], "€"))),
        ("Resultado total", "%s <small>%s</small>" % (dinero(c["resultado_total_eur"], "€", True), pct(c["resultado_total_pct"]))),
        ("Frente al S&P 500", dinero(c["ventaja_sobre_referencia_usd"], "$", True)),
        ("Caja libre", "%s <small>%s&nbsp;%% invertido</small>" % (dinero(c["caja_usd"]), num(c["invertido_pct"], 0))),
    ])


def bloque_posiciones(c, vacio="Sin posiciones abiertas."):
    filas = [[e(p["ticker"]), num(p["cantidad"], 4 if p["cantidad"] != int(p["cantidad"]) else 0),
              num(p["entrada"]), num(p["precio"]), dinero(p["resultado_usd"], "$", True), pct(p["resultado_pct"]),
              "%s <small>(%s)</small>" % (num(p["stop"]), pct(p["hasta_stop_pct"])),
              "%s <small>(%s)</small>" % (num(p["objetivo"]), pct(p["hasta_objetivo_pct"]))]
             for p in c["posiciones"]]
    return tabla([("Valor", 0), ("Acciones", 1), ("Entrada", 1), ("Precio", 1), ("Resultado", 1), ("%", 1),
                  ("Stop", 1), ("Objetivo", 1)], filas, vacio)


def bloque_operaciones(ops, vacio="Sin operaciones."):
    filas = [[e(o["hora"]), e(o["ticker"]), e(o["accion"]), num(o["cantidad"], 4 if o["cantidad"] != int(o["cantidad"]) else 0),
              num(o["precio"]), dinero(o["importe_usd"]),
              dinero(o.get("resultado_usd"), "$", True) if o["accion"] == "venta" else "", corta(o.get("motivo", ""), 90)]
             for o in ops]
    return tabla([("Hora", 0), ("Valor", 0), ("Acción", 0), ("Acciones", 1), ("Precio", 1), ("Importe", 1),
                  ("Resultado", 1), ("Motivo", 0)], filas, vacio)


def bloque_plan(ordenes, vacio="Hoy no hay órdenes en el plan."):
    filas = []
    for o in ordenes:
        ok = o.get("veredicto") == "APROBADA"
        detalle = "; ".join(o.get("motivos") or []) or o.get("motivo", "")
        filas.append([e(o.get("ticker")), e(o.get("accion")), num(o.get("entrada")), num(o.get("stop")),
                      num(o.get("objetivo")),
                      num(o.get("cantidad"), 4 if o.get("cantidad") and o["cantidad"] != int(o["cantidad"]) else 0) if o.get("cantidad") else "",
                      dinero(o["cantidad"] * o["entrada"]) if o.get("cantidad") and o.get("entrada") else "",
                      '<span class="%s">%s</span>' % ("ok" if ok else "no", e(o.get("veredicto", ""))),
                      corta(detalle, 150)])
    return tabla([("Valor", 0), ("Acción", 0), ("Entrada", 1), ("Stop", 1), ("Objetivo", 1), ("Acciones", 1),
                  ("Importe", 1), ("Reglas", 0), ("Motivo", 0)], filas, vacio)
