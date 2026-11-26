"""Gráficas de los informes: SVG escrito a mano, sin bibliotecas, para imprimirse en el PDF.

Criterios (los mismos en todas):
- Cada gráfica hace un solo trabajo: una línea para la evolución, barras desde el cero para ganar o
  perder, una barra partida para el reparto de un todo.
- Un solo eje por gráfica. Rejilla y ejes finos y grises; lo único que llama la atención son los datos.
- El texto va en tinta de texto, nunca en el color de la serie: el color lo lleva la marca de al lado.
  Ganar o perder nunca se dice solo con el color: el importe lleva su signo.
- Los colores de las series salen de una paleta comprobada para daltónicos, siempre en el mismo orden.
  Tres de ellos tienen poco contraste sobre blanco: por eso cada parte lleva su cifra escrita y los
  mismos datos van también en una tabla del informe.
- El PDF no tiene ratón: lo que en pantalla iría al pasar el cursor va en la leyenda o en la tabla.
"""
from __future__ import annotations

import html
import math

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
GANA, PIERDE = "#0ca30c", "#d03b3b"
NEUTRO, REJILLA, EJE = "#c3c2b7", "#e1e0d9", "#c3c2b7"
TINTA, TINTA2, TENUE = "#1b1f24", "#57606a", "#898781"
FUENTE = '-apple-system, "Helvetica Neue", Arial, sans-serif'
ANCHO_LETRA = 0.56          # anchura media de un carácter respecto al tamaño de letra (para saber si un texto cabe)


def _e(x):
    return html.escape(str(x))


def _n(x, dec=0):
    x = round(x, dec) + 0.0
    return ("{:,.%df}" % dec).format(x).replace(",", " ").replace(".", ",").replace(" ", ".")


def _dinero(x, unidad="$", dec=2, signo=False):
    return "%s%s %s" % ("+" if signo and round(x, dec) > 0 else "", _n(x, dec).replace("-", "−"), unidad)


def _ancho(texto, cuerpo):
    return len(texto) * cuerpo * ANCHO_LETRA


def _svg(ancho, alto, dentro, descripcion):
    return ('<svg viewBox="0 0 %d %d" width="100%%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="%s" '
            'style="display:block;font-family:%s">%s</svg>' % (ancho, alto, _e(descripcion), _e(FUENTE), "".join(dentro)))


def _texto(x, y, texto, cuerpo=10, color=TINTA, ancla="start", peso=None):
    return '<text x="%.1f" y="%.1f" font-size="%s" fill="%s" text-anchor="%s"%s>%s</text>' % (
        x, y, cuerpo, color, ancla, ' font-weight="%s"' % peso if peso else "", _e(texto))


def _marcas(lo, hi, n=4):
    """Valores redondos para el eje, entre lo y hi."""
    if hi <= lo:
        hi = lo + 1
    bruto = (hi - lo) / n
    base = 10 ** math.floor(math.log10(bruto))
    paso = next(m * base for m in (1, 2, 2.5, 5, 10) if m * base >= bruto)
    v = math.ceil(lo / paso) * paso
    out = []
    while v <= hi + paso * 1e-6:
        out.append(round(v, 10))
        v += paso
    return out, paso


def figura(titulo, svg, nota=""):
    return '<div class="graf"><div class="tit">%s</div>%s%s</div>' % (
        _e(titulo), svg, '<div class="nota">%s</div>' % _e(nota) if nota else "")


# ---------------------------------------------------------------- evolución (líneas)

def evolucion(etiquetas, series, ancho=352, alto=176, unidad="$"):
    """Líneas de varias series sobre las mismas fechas.

    etiquetas: textos del eje horizontal, uno por punto. series: [(nombre, [valores])], como mucho 6.
    La leyenda lleva el último valor de cada serie, así que no hace falta rotular los extremos (que,
    cuando las líneas acaban juntas, se pisarían).
    """
    series = [(n, list(v)) for n, v in series if v][:len(SERIES)]
    if not series or not etiquetas:
        return ""
    todos = [x for _, v in series for x in v]
    lo, hi = min(todos), max(todos)
    margen = (hi - lo) * 0.12 or abs(hi) * 0.01 or 1
    marcas, paso = _marcas(lo - margen, hi + margen)
    lo, hi = min(marcas[0], lo - margen), max(marcas[-1], hi + margen)
    dec = 0 if paso >= 1 else 2
    izq = max(_ancho(_n(m, dec), 9) for m in marcas) + 10
    x0, x1, y0, y1 = izq, ancho - 10, 26, alto - 20
    n = len(etiquetas)

    def px(i):
        return (x0 + x1) / 2 if n == 1 else x0 + (x1 - x0) * i / (n - 1)

    def py(v):
        return y1 - (y1 - y0) * (v - lo) / (hi - lo)
    out = []
    x = 0.0
    for i, (nombre, v) in enumerate(series):               # leyenda: marca de color, nombre y último valor
        rotulo = "%s  %s" % (nombre, _dinero(v[-1], unidad))
        out.append('<line x1="%.1f" y1="9" x2="%.1f" y2="9" stroke="%s" stroke-width="2" stroke-linecap="round"/>' % (x, x + 12, SERIES[i]))
        out.append(_texto(x + 16, 12.5, rotulo, 9.5, TINTA2))
        x += 16 + _ancho(rotulo, 9.5) + 14
    for m in marcas:
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>' % (x0, py(m), x1, py(m), REJILLA))
        out.append(_texto(x0 - 6, py(m) + 3.2, _n(m, dec), 9, TENUE, "end"))
    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>' % (x0, y1, x1, y1, EJE))
    salto = max(1, math.ceil(n / 6))
    visibles = sorted(set(range(0, n, salto)) | {n - 1})
    if len(visibles) > 1 and visibles[-1] - visibles[-2] < salto / 2:
        visibles.pop(-2)
    for i in visibles:
        ancla = "start" if i == 0 and n > 1 else "end" if i == n - 1 and n > 1 else "middle"
        out.append(_texto(px(i), alto - 6, etiquetas[i], 9, TENUE, ancla))
    for i, (nombre, v) in enumerate(series):
        puntos = " ".join("%.1f,%.1f" % (px(j), py(y)) for j, y in enumerate(v))
        if len(v) > 1:
            out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round" '
                       'stroke-linecap="round"/>' % (puntos, SERIES[i]))
    for i, (nombre, v) in enumerate(series):               # los puntos finales, encima de todas las líneas
        out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="#ffffff" stroke-width="2"/>' % (
            px(len(v) - 1), py(v[-1]), SERIES[i]))
    return _svg(ancho, alto, out, "Evolución de " + ", ".join(n for n, _ in series))


# ---------------------------------------------------------------- ganar o perder (barras desde el cero)

def resultados(filas, ancho=352, unidad="$"):
    """Barras horizontales desde el cero: a la derecha lo que gana, a la izquierda lo que pierde.

    filas: [(nombre, detalle, valor)]. El importe va escrito con su signo en la punta de cada barra.
    """
    filas = [f for f in filas if f[2] is not None]
    if not filas:
        return ""
    alto_fila, grosor = 22, 12
    alto = len(filas) * alto_fila + 6
    col_nombre = max(_ancho(f[0], 10) for f in filas) * 1.18 + 7            # la negrita ocupa más
    col = col_nombre + max(_ancho(f[1], 8.5) if f[1] else 0 for f in filas) + 10
    rotulos = [_dinero(f[2], unidad, signo=True) for f in filas]
    hueco = max(_ancho(r, 9.5) for r in rotulos) + 8
    maxpos, maxneg = max([f[2] for f in filas] + [0]), -min([f[2] for f in filas] + [0])
    x0, x1 = col + (hueco if maxneg else 4), ancho - (hueco if maxpos else 4)
    total = (maxpos + maxneg) or 1
    cero = x0 + (x1 - x0) * maxneg / total
    escala = (x1 - x0) / total
    out = ['<line x1="%.1f" y1="2" x2="%.1f" y2="%d" stroke="%s" stroke-width="1"/>' % (cero, cero, alto - 2, EJE)]
    for i, ((nombre, detalle, v), rotulo) in enumerate(zip(filas, rotulos)):
        cy = 3 + i * alto_fila + alto_fila / 2
        out.append(_texto(0, cy + 3.5, nombre, 10, TINTA, peso=600))
        if detalle:
            out.append(_texto(col_nombre, cy + 3.5, detalle, 8.5, TENUE))
        largo = abs(v) * escala
        y = cy - grosor / 2
        if largo >= 0.5:
            r = min(4, largo)
            if v >= 0:      # recta en el cero, redondeada en la punta
                camino = "M%.1f,%.1f h%.1f a%.1f,%.1f 0 0 1 %.1f,%.1f v%.1f a%.1f,%.1f 0 0 1 %.1f,%.1f h%.1f z" % (
                    cero, y, largo - r, r, r, r, r, grosor - 2 * r, r, r, -r, r, -(largo - r))
            else:
                camino = "M%.1f,%.1f h%.1f a%.1f,%.1f 0 0 0 %.1f,%.1f v%.1f a%.1f,%.1f 0 0 0 %.1f,%.1f h%.1f z" % (
                    cero, y, -(largo - r), r, r, -r, r, grosor - 2 * r, r, r, r, r, largo - r)
            out.append('<path d="%s" fill="%s"/>' % (camino, GANA if v >= 0 else PIERDE))
        if v >= 0:
            out.append(_texto(cero + largo + 5, cy + 3.3, rotulo, 9.5, TINTA))
        else:
            out.append(_texto(cero - largo - 5, cy + 3.3, rotulo, 9.5, TINTA, "end"))
    return _svg(ancho, alto, out, "Resultado de cada valor")


# ---------------------------------------------------------------- reparto de un todo (barra partida)

def reparto(partes, ancho=720, unidad="$", neutras=("Caja",)):
    """Una barra partida: cómo se reparte un total. partes: [(nombre, valor)], como mucho 6 con valor.

    Cada trozo lleva su porcentaje dentro si cabe; la leyenda da el nombre y el importe de cada uno
    (y el porcentaje, cuando no cabía en el trozo). Las partes de `neutras` van en gris.
    """
    partes = [(n, v) for n, v in partes if v and v > 0]
    total = sum(v for _, v in partes)
    if not partes or total <= 0:
        return ""
    alto_barra, sep = 28, 2
    util = ancho - sep * (len(partes) - 1)
    out, x, leyenda, k = [], 0.0, [], 0
    for nombre, v in partes:
        if nombre in neutras:
            color = NEUTRO
        else:
            color, k = SERIES[k % len(SERIES)], k + 1
        w = util * v / total
        pct = "%s %%" % _n(v / total * 100, 0)
        cabe = _ancho(pct, 10) + 12 <= w
        out.append('<rect x="%.1f" y="0" width="%.1f" height="%d" rx="4" fill="%s"/>' % (x, max(w, 1), alto_barra, color))
        if cabe:
            claro = color in (NEUTRO, "#eda100")
            out.append(_texto(x + 6, alto_barra / 2 + 3.6, pct, 10, "#0b0b0b" if claro else "#ffffff", peso=600))
        leyenda.append((color, "%s  %s%s" % (nombre, _dinero(v, unidad), "" if cabe else " (%s)" % pct)))
        x += w + sep
    lx, ly = 0.0, alto_barra + 16
    for color, rotulo in leyenda:
        w = 14 + _ancho(rotulo, 9.5) + 16
        if lx and lx + w > ancho:
            lx, ly = 0.0, ly + 15
        out.append('<rect x="%.1f" y="%.1f" width="9" height="9" rx="2" fill="%s"/>' % (lx, ly - 8, color))
        out.append(_texto(lx + 14, ly, rotulo, 9.5, TINTA2))
        lx += w
    return _svg(ancho, int(ly + 6), out, "Reparto: " + ", ".join(n for n, _ in partes))
