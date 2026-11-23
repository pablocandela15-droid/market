"""Base común: rutas, claves, reglas, peticiones web y calendario del mercado.

Solo biblioteca estándar de Python, igual que day-trader-skill.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent          # carpeta market/
PROYECTO = RAIZ.parent                                 # carpeta bot-bolsa/
DTS = PROYECTO / "repos" / "day-trader-skill"          # herramientas de datos
# Con BOT_PRUEBA=1 todo se escribe en carpetas "-prueba" y la cartera real no se toca.
SUFIJO = "-prueba" if os.environ.get("BOT_PRUEBA") else ""
ESTADO = RAIZ / ("estado" + SUFIJO)
MADRID = ZoneInfo("Europe/Madrid")
NY = ZoneInfo("America/New_York")

UA_NAVEGADOR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
# Yahoo rechaza las cabeceras de navegador sin sesión y acepta las de un cliente declarado.
UA_CLIENTE = "Mozilla/5.0 (compatible; bot-bolsa/0.1)"


class ErrorFuente(Exception):
    """Una fuente de datos no respondió o cambió de formato."""


# ---------------------------------------------------------------- archivos

def leer_json(ruta, defecto=None):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return defecto


def escribir_json(ruta, datos):
    """Escritura atómica: nunca deja un archivo a medias si la tarea se corta."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, ruta)


def anexar_linea(ruta, datos):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(datos, ensure_ascii=False) + "\n")


def leer_lineas(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------- claves y reglas

def claves():
    """Lee market/.env. Las variables de entorno ya definidas tienen prioridad."""
    valores = {}
    ruta = RAIZ / ".env"
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            valores[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FRED_API_KEY",
              "ANTHROPIC_API_KEY", "SEC_USER_AGENT"):
        if os.environ.get(k):
            valores[k] = os.environ[k]
    return {k: v for k, v in valores.items() if v}


def reglas():
    return leer_json(RAIZ / "config" / "reglas.json")


def universo():
    return leer_json(RAIZ / "config" / "universo.json")


def valores_del_universo():
    """{ticker: sector}, sin repetidos y en el orden del archivo."""
    out = {}
    for sector, lista in universo()["sectores"].items():
        for t in lista:
            out.setdefault(t, sector)
    return out


# ---------------------------------------------------------------- peticiones web

def pedir(url, cabeceras=None, espera=25, reintentos=2, pausa=2.0, datos=None, metodo=None):
    """GET (o POST si hay datos). Devuelve texto. Lanza ErrorFuente si falla."""
    cab = {"User-Agent": UA_NAVEGADOR, "Accept": "*/*"}
    cab.update(cabeceras or {})
    ultimo = "sin respuesta"
    for intento in range(reintentos + 1):
        try:
            req = urllib.request.Request(url, headers=cab, data=datos, method=metodo)
            with urllib.request.urlopen(req, timeout=espera) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            ultimo = "HTTP %d" % e.code
            cuerpo = ""
            try:
                cuerpo = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            finally:
                e.close()
            if e.code in (429, 500, 502, 503, 504) and intento < reintentos:
                time.sleep(pausa * (intento + 1))
                continue
            raise ErrorFuente("%s en %s %s" % (ultimo, url.split("?")[0], cuerpo)) from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            ultimo = str(getattr(e, "reason", e))
            if intento < reintentos:
                time.sleep(pausa)
                continue
    raise ErrorFuente("%s en %s" % (ultimo, url.split("?")[0]))


# ---------------------------------------------------------------- calendario

# Festivos y cierres anticipados de la bolsa de Nueva York (cierre a las 13:00 de allí).
FESTIVOS = {
    "2026-11-26": "Acción de Gracias", "2026-12-25": "Navidad",
    "2027-01-01": "Año Nuevo", "2027-01-18": "Martin Luther King",
    "2027-02-15": "Día de los Presidentes", "2027-03-26": "Viernes Santo",
    "2027-05-31": "Memorial Day", "2027-06-18": "Juneteenth",
    "2027-07-05": "Día de la Independencia", "2027-09-06": "Día del Trabajo",
    "2027-11-25": "Acción de Gracias", "2027-12-24": "Navidad",
}
CIERRE_ANTICIPADO = {"2026-11-27", "2026-12-24", "2027-11-26"}


def ahora_madrid():
    return dt.datetime.now(MADRID)


def sesion(fecha):
    """Horario de la sesión de ese día (fecha de Nueva York) o None si no hay."""
    clave = fecha.isoformat()
    if fecha.weekday() >= 5 or clave in FESTIVOS:
        return None
    abre = dt.datetime.combine(fecha, dt.time(9, 30), NY)
    cierra = dt.datetime.combine(fecha, dt.time(13, 0) if clave in CIERRE_ANTICIPADO else dt.time(16, 0), NY)
    return {"abre": abre, "cierra": cierra, "anticipado": clave in CIERRE_ANTICIPADO}


def estado_mercado(ahora=None):
    """Resumen del día de mercado, con las horas ya pasadas a Madrid."""
    ahora = ahora or ahora_madrid()
    hoy = ahora.astimezone(NY).date()
    s = sesion(hoy)
    sig = hoy + dt.timedelta(days=1)
    while sesion(sig) is None:
        sig += dt.timedelta(days=1)
    ant = hoy - dt.timedelta(days=1)
    while sesion(ant) is None:
        ant -= dt.timedelta(days=1)
    out = {
        "fecha": hoy.isoformat(),
        "dia_semana": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][hoy.weekday()],
        "abre_hoy": s is not None,
        "motivo_cierre": None,
        "proxima_sesion": sig.isoformat(),
        "sesion_anterior": ant.isoformat(),
    }
    if s is None:
        out["motivo_cierre"] = FESTIVOS.get(hoy.isoformat(), "fin de semana")
        out["abierto_ahora"] = False
        return out
    out.update({
        "abre_madrid": s["abre"].astimezone(MADRID).strftime("%H:%M"),
        "cierra_madrid": s["cierra"].astimezone(MADRID).strftime("%H:%M"),
        "cierre_anticipado": s["anticipado"],
        "abierto_ahora": s["abre"] <= ahora < s["cierra"],
        "ya_cerro": ahora >= s["cierra"],
    })
    return out


MESES_CARPETA = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
                 "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def carpeta_informes(fecha=None):
    """Carpeta de los PDF del día: Informes/<Mes>/<día>/.

    Por defecto cuelga de la carpeta del proyecto, fuera del repositorio; se
    cambia en config/reglas.json (informes.carpeta, relativa a market/).
    """
    fecha = fecha or ahora_madrid().date()
    base = (RAIZ / (reglas().get("informes") or {}).get("carpeta", "../Informes")).resolve()
    if SUFIJO:
        base = base.with_name(base.name + SUFIJO)
    p = base / MESES_CARPETA[fecha.month - 1] / ("%02d" % fecha.day)
    p.mkdir(parents=True, exist_ok=True)
    return p


def carpeta_del_dia(nombre, fecha=None):
    """datos/AAAA-MM-DD, informes/AAAA-MM-DD… con la fecha de Madrid."""
    fecha = fecha or ahora_madrid().date().isoformat()
    p = RAIZ / (nombre + SUFIJO) / fecha
    p.mkdir(parents=True, exist_ok=True)
    return p
