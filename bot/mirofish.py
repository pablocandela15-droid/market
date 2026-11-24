"""Simulación semanal con MiroFish (repos/MiroFish), manejada por el bot.

MiroFish crea un mundo de agentes a partir de un documento de partida y simula
cómo debaten en un foro. Aquí se automatiza el ciclo completo:

  1. Reparto: un modelo busca qué se está diciendo del mercado y describe
     unos 30 participantes (inversores, gestores, analistas, bajistas, medios).
  2. Documento de partida: el mercado entero (índices, sectores, noticias) y ese reparto.
  3. MiroFish: grafo de conocimiento, un agente por participante y varios días simulados.
  4. Encuesta: se pregunta a cada agente qué espera y qué sectores compraría.
  5. Informe de MiroFish y síntesis final, que es lo que usa el estratega.

    python3 -m bot.tarea mirofish        # ciclo completo (domingos por la tarde)

Cada predicción se anota y se comprueba después con precios reales.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import gasto, modelo, yahoo
from . import informe as inf
from .base import (ESTADO, PROYECTO, RAIZ, SUFIJO, ErrorFuente, ahora_madrid, anexar_linea, carpeta_del_dia,
                   carpeta_informes, escribir_json, leer_json, leer_lineas, reglas, valores_del_universo)

REPO = PROYECTO / "repos" / "MiroFish"
IMAGEN, CONTENEDOR = "mirofish-local:latest", "mirofish-bot"
API = "http://127.0.0.1:5001"
CARPETA = ESTADO / "mirofish"
ULTIMA, PREDICCIONES, ACIERTOS = CARPETA / "ultima.json", CARPETA / "predicciones.jsonl", CARPETA / "aciertos.jsonl"
INDICES = {"^GSPC": "S&P 500", "^NDX": "Nasdaq 100", "^RUT": "Russell 2000", "^VIX": "VIX"}
# Tipos de entidad que MiroFish no debe convertir en agentes: son temas de conversación, no participantes.
# Se mira la última palabra del nombre: "FundManager" opina, "SectorFund" no.
NO_OPINAN = {"company", "corporation", "corp", "fund", "etf", "index", "commodity", "sector", "market", "instrument",
             "product", "asset", "security", "stock", "currency", "event", "policy", "technology", "indicator",
             "benchmark", "organization", "institution", "agency", "government", "regulator",
             "person"}       # "Person" son las personas reales citadas en las noticias: no se las hace hablar


def _opina(tipo):
    palabras = re.findall(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?![a-z])", tipo)
    return bool(palabras) and palabras[-1].lower() not in NO_OPINAN
TIPOS = ["inversor particular", "gestor de fondos", "analista", "operador de corto plazo", "vendedor en corto",
         "economista", "periodista de mercados", "asesor patrimonial"]
SESGO = {"type": "string", "enum": ["positivo", "negativo", "neutro"]}
GRADO = {"type": "string", "enum": ["alta", "media", "baja"]}
S = {"type": "string"}


def _obj(**p):
    return {"type": "object", "properties": p, "required": list(p), "additionalProperties": False}


def _lista(x):
    return {"type": "array", "items": x}


ESQ_PARTICIPANTES = _obj(participantes=_lista(_obj(
    nombre=S, tipo={"type": "string", "enum": TIPOS}, perfil=S,
    postura={"type": "string", "enum": ["alcista", "bajista", "neutral"]}, tesis=S,
    sectores_que_prefiere=_lista(S), sectores_que_evita=_lista(S))))
ESQ_SINTESIS = _obj(
    tesis=S, tono={"type": "string", "enum": ["riesgo", "neutral", "defensivo"]},
    mercado=_obj(direccion={"type": "string", "enum": ["sube", "baja", "lateral"]}, conviccion=GRADO, motivo=S),
    sectores=_lista(_obj(sector=S, sesgo=SESGO, conviccion=GRADO, motivo=S)),
    valores=_lista(_obj(ticker=S, sesgo=SESGO, motivo=S)),
    predicciones=_lista(_obj(simbolo=S, direccion={"type": "string", "enum": ["sube", "baja"]}, motivo=S)),
    debate=_lista(S), fiabilidad=S)


def _log(texto):
    print("[%s] mirofish: %s" % (ahora_madrid().strftime("%H:%M:%S"), texto), flush=True)


def _cfg(fecha=None):
    base = {"modelo": "claude-haiku-5-5", "modelo_informe": "claude-sonnet-5-5", "modelo_sintesis": "claude-fable-5-2",
            "modelo_participantes": "claude-sonnet-5-5", "tope_usd": 6.0, "max_rondas": 72, "plataforma": "reddit",
            "encuesta": True, "puerto_puente": 8765, "espera_max_minutos": 150, "borrar_grafo_al_terminar": True,
            "tamano_trozo": 1600, "llamadas_a_la_vez": 6, "parte_del_debate": 0.5,
            "dias_de_validez": 8, "plazo_sesiones": 5}
    propio = reglas().get("mirofish") or {}
    base.update({k: v for k, v in propio.items() if not k.startswith("_") and k != "solo_el"})
    # Excepciones de un solo día ("solo_el": {"2026-11-29": {...}}): valen esa fecha y caducan solas.
    base.update((propio.get("solo_el") or {}).get((fecha or ahora_madrid().date()).isoformat()) or {})
    return base


def _env_mirofish():
    out = {}
    ruta = REPO / ".env"
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------- Docker y puente

def _docker_exe():
    for c in (shutil.which("docker"), os.path.expanduser("~/.docker/bin/docker"), "/usr/local/bin/docker",
              "/Applications/Docker.app/Contents/Resources/bin/docker"):
        if c and os.path.exists(c):
            return c
    raise ErrorFuente("no encuentro Docker en este Mac")


def _docker(*args, espera=120):
    return subprocess.run([_docker_exe(), *args], capture_output=True, text=True, timeout=espera)


def arrancar_docker():
    if _docker("info", espera=30).returncode == 0:
        return
    _log("Docker no está en marcha: lo arranco")
    subprocess.run(["open", "-g", "-a", "Docker"], capture_output=True)
    fin = time.time() + 240
    while time.time() < fin:
        time.sleep(5)
        if _docker("info", espera=30).returncode == 0:
            return
    raise ErrorFuente("Docker no ha arrancado en 4 minutos")


def _get(url, espera=10):
    with urllib.request.urlopen(url, timeout=espera) as r:
        return json.loads(r.read())


def arrancar_puente(cfg, token, registro):
    proc = subprocess.Popen([sys.executable, "-m", "bot.puente", "--puerto", str(cfg["puerto_puente"]), "--token", token,
                             "--tope-usd", str(cfg["tope_usd"]), "--registro", str(registro),
                             "--a-la-vez", str(cfg.get("llamadas_a_la_vez", 6)),
                             "--tokens-por-minuto", str(int(cfg.get("tokens_por_minuto", 0)))],
                            cwd=str(RAIZ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            _get("http://127.0.0.1:%d/salud" % cfg["puerto_puente"], 3)
            return proc
        except (OSError, ValueError):
            time.sleep(0.5)
    proc.terminate()
    raise ErrorFuente("el puente local no ha arrancado (¿puerto %d ocupado?)" % cfg["puerto_puente"])


def _modelo_del_puente(cfg, token, nombre):
    """Fija el modelo al que el puente manda todas las peticiones (None: el que pida MiroFish)."""
    req = urllib.request.Request("http://127.0.0.1:%d/modo" % cfg["puerto_puente"],
                                 data=json.dumps({"modelo": nombre}).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def guion_twitter_ligero(cfg):
    """Copia del guion de MiroFish para la red tipo Twitter, con un recomendador ligero.

    El original elige las publicaciones que ve cada agente con "twhin-bert", un modelo de más de
    1 GB que se descarga y se carga en memoria: en un Mac de 8 GB tumba el contenedor. Aquí se
    cambia por el recomendador que diga la configuración ("random" no carga ningún modelo;
    "twitter" carga uno pequeño). No se toca el repositorio de MiroFish: la copia se guarda en
    estado/ y se monta sobre el guion del contenedor. Devuelve None si se pide el original."""
    tipo = cfg.get("recomendador_twitter", "random")
    if tipo == "twhin-bert":
        return None
    if tipo not in ("random", "twitter"):
        raise ErrorFuente("recomendador_twitter debe ser random, twitter o twhin-bert")
    texto = (REPO / "backend" / "scripts" / "run_twitter_simulation.py").read_text(encoding="utf-8")
    viejo, principal = "platform=oasis.DefaultPlatformType.TWITTER,", 'if __name__ == "__main__":'
    if texto.count(viejo) != 1 or texto.count(principal) != 1:
        raise ErrorFuente("el guion de MiroFish para la red tipo Twitter ha cambiado y no se puede aligerar")
    ve = int(cfg.get("twitter_publicaciones_por_agente", 5))
    ayuda = (
        "def _plataforma_ligera(db_path):\n"
        "    # Añadido por bot-bolsa: misma red, con un recomendador que cabe en poca memoria.\n"
        "    from oasis.social_platform.channel import Channel\n"
        "    from oasis.social_platform.platform import Platform\n"
        "    return Platform(db_path=db_path, channel=Channel(), recsys_type=%r,\n"
        "                    refresh_rec_post_count=%d, max_rec_post_len=%d, following_post_count=3)\n\n\n" % (tipo, ve, ve))
    texto = texto.replace(viejo, "platform=_plataforma_ligera(db_path),").replace(principal, ayuda + principal)
    destino = CARPETA / "run_twitter_ligero.py"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    return destino


def arrancar_contenedor(cfg=None):
    if _docker("image", "inspect", IMAGEN).returncode != 0:
        raise ErrorFuente("falta la imagen %s: constrúyela con `docker build -t %s %s`" % (IMAGEN, IMAGEN, REPO))
    _docker("rm", "-f", CONTENEDOR)
    (REPO / "backend" / "uploads").mkdir(parents=True, exist_ok=True)
    montajes = ["-v", "%s:/app/backend/uploads" % (REPO / "backend" / "uploads")]
    if cfg and cfg.get("plataforma") == "twitter":
        ligero = guion_twitter_ligero(cfg)
        if ligero:
            montajes += ["-v", "%s:/app/backend/scripts/run_twitter_simulation.py:ro" % ligero]
    r = _docker("run", "-d", "--name", CONTENEDOR, "--env-file", str(REPO / ".env"),
                "-p", "127.0.0.1:5001:5001", "-p", "127.0.0.1:3000:3000", *montajes, IMAGEN)
    if r.returncode != 0:
        raise ErrorFuente("no se pudo arrancar MiroFish: %s" % r.stderr.strip()[:300])
    fin = time.time() + 240
    while time.time() < fin:
        try:
            _get(API + "/health", 5)
            return
        except (OSError, ValueError):
            time.sleep(4)
    raise ErrorFuente("MiroFish no responde: %s" % _docker("logs", "--tail", "15", CONTENEDOR).stdout[-600:])


def parar(proc_puente):
    try:
        _docker("rm", "-f", CONTENEDOR)
    except (ErrorFuente, subprocess.SubprocessError, OSError):
        pass
    if proc_puente and proc_puente.poll() is None:
        proc_puente.terminate()


# ---------------------------------------------------------------- API de MiroFish

def _api(metodo, ruta, cuerpo=None, multiparte=None, espera=180):
    cab, datos = {"Accept-Language": "es"}, None
    if multiparte:
        datos, cab["Content-Type"] = multiparte
    elif cuerpo is not None:
        datos, cab["Content-Type"] = json.dumps(cuerpo).encode(), "application/json"
    req = urllib.request.Request(API + ruta, data=datos, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=espera) as r:
            doc = json.loads(r.read())
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", "replace")
        try:
            texto = json.loads(texto).get("error") or texto
        except ValueError:
            pass
        raise ErrorFuente("MiroFish %s -> HTTP %d: %s" % (ruta, e.code, str(texto)[:300])) from None
    except (urllib.error.URLError, OSError) as e:
        raise ErrorFuente("MiroFish %s -> %s" % (ruta, e)) from None
    if isinstance(doc, dict) and doc.get("success") is False:
        raise ErrorFuente("MiroFish %s -> %s" % (ruta, str(doc.get("error"))[:300]))
    return doc.get("data", doc) if isinstance(doc, dict) else doc


def _multiparte(campos, nombre_archivo, contenido):
    limite = "----botbolsa" + secrets.token_hex(12)
    partes = [("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (limite, k, v)).encode()
              for k, v in campos.items()]
    partes.append(("--%s\r\nContent-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\n"
                   "Content-Type: text/markdown\r\n\r\n" % (limite, nombre_archivo)).encode() + contenido.encode() + b"\r\n")
    partes.append(("--%s--\r\n" % limite).encode())
    return b"".join(partes), "multipart/form-data; boundary=" + limite


def _esperar(que, consultar, hecho, fallo, limite, pausa=10, vigilar=None):
    """Consulta hasta que termina. Devuelve el último estado; lanza ErrorFuente si falla o se agota el tiempo."""
    ultimo = ""
    while time.time() < limite:
        d = consultar()
        estado = str(d.get("status") or d.get("runner_status") or "")
        avance = d.get("progress", d.get("progress_percent", d.get("current_round", "")))
        aviso = "%s %s" % (estado, avance)
        if aviso != ultimo:              # una línea por cambio de estado o de avance, no por consulta
            _log("%s: %s" % (que, aviso))
            ultimo = aviso
        if estado in fallo:
            raise ErrorFuente("%s falló: %s" % (que, str(d.get("error") or d.get("message"))[:300]))
        if estado in hecho or d.get("already_prepared"):
            return d
        if vigilar and vigilar():
            return d
        time.sleep(pausa)
    raise ErrorFuente("%s no terminó a tiempo" % que)


# ---------------------------------------------------------------- documento de partida

def _ultimo(nombre):
    """El archivo más reciente con ese nombre dentro de datos/AAAA-MM-DD/."""
    rutas = sorted((RAIZ / ("datos" + SUFIJO)).glob("*/" + nombre))
    return leer_json(rutas[-1]) if rutas else None


def _linea(nombre, q):
    return "- %s: %s (día %+.1f %%, 5 días %+.1f %%, mes %+.1f %%)" % (
        nombre, q["precio"], q.get("var_1d") or 0, q.get("var_5d") or 0, q.get("var_1m") or 0)


def mercado_al_dia(d):
    """Rehace con datos de este momento la foto del mercado entero: índices, sectores y destacados.
    Así la simulación no depende de a qué hora se hizo el último informe de la mañana."""
    from . import dossier
    from .base import universo
    errores, u = [], universo()
    d["indices"] = dossier._cotizaciones(u["mercado"], errores) or d.get("indices")
    d["materias_primas"] = dossier._cotizaciones(u["materias_primas"], errores) or d.get("materias_primas")
    filas = dossier.barrido(errores)
    if filas:
        d["fuerza_de_sectores"] = dossier.fuerza_de_sectores(filas, dossier._cotizaciones(u["fondos_de_sector"], errores))
        d["destacados_por_sector"] = dossier.destacados(filas)
    d["generado"] = ahora_madrid().strftime("%Y-%m-%d %H:%M")
    return d


def reparto(d, plan, inv, carpeta, cfg):
    """Los participantes de la simulación, descritos por un modelo que mira qué se dice ahora del mercado."""
    guardado = leer_json(carpeta / "participantes.json")
    if guardado:
        return guardado, None
    datos = {"fecha": d["generado"], "indices": d["indices"], "fuerza_de_sectores": d.get("fuerza_de_sectores"),
             "materias_primas": d.get("materias_primas"), "macro": d.get("macro"), "lectura_del_dia": plan.get("panorama"),
             "analistas": {k: {"resumen": v.get("resumen"), "hallazgos": [h["titulo"] for h in v.get("hallazgos", [])]}
                           for k, v in inv.items()},
             "titulares": [n["titular"] for n in d["noticias"]["titulares"][:25]]}
    resp, info = modelo.preguntar("mirofish", "mirofish-participantes", cfg["modelo_participantes"], datos,
                                  ESQ_PARTICIPANTES, nivel="mirofish", web=True, tope_usd=0.8, esfuerzo="medium",
                                  espera=900, carpeta=carpeta)
    lista = (resp or {}).get("participantes") or []
    if len(lista) < 12:
        raise ErrorFuente("no se pudo preparar el reparto de la simulación: %s" % info.get("error", "pocos participantes"))
    escribir_json(carpeta / "participantes.json", lista)
    return lista, info


def semilla(participantes=None, d=None):
    """Documento de partida y pregunta de la simulación, con el mercado entero y el reparto."""
    plan, inv = _ultimo("plan.json") or {}, _ultimo("investigadores.json") or {}
    d = d or _ultimo("dossier-manana.json")
    if not d:
        raise ErrorFuente("no hay ningún informe de la mañana del que partir")
    c = d["cartera"]
    en_cartera = [p["ticker"] for p in c["posiciones"]]
    t = ["# El debate sobre la bolsa de EE. UU. a %s" % d["generado"], ""]
    if participantes:
        t += ["## Quién participa en el debate", "",
              "Estas son las personas que opinan e invierten. Cada una publica y comenta con su propia voz."]
        for p in participantes:
            t += ["", "### %s (%s)" % (p["nombre"], p["tipo"]), p["perfil"],
                  "Postura sobre la bolsa: %s. %s" % (p["postura"], p["tesis"]),
                  "Sectores que prefiere: %s. Sectores que evita: %s." % (
                      ", ".join(p["sectores_que_prefiere"]) or "ninguno en especial",
                      ", ".join(p["sectores_que_evita"]) or "ninguno en especial")]
        t += ["", "## De qué se habla: la situación del mercado", ""]
    t += ["### Mercados", *[_linea(n, q) for n, q in d["indices"].items()], "",
          "### Sectores, del más fuerte al más débil"]
    for x in d.get("fuerza_de_sectores") or []:
        t.append("- %s (%s): día %+.1f %%, semana %+.1f %%, mes %+.1f %%, tres meses %+.1f %%; %s %% de sus valores sobre su media de 50 días" % (
            x["nombre"], x["tipo"], x["dia_pct"] or 0, x["semana_pct"] or 0, x["mes_pct"] or 0, x["tres_meses_pct"] or 0,
            x["sobre_media50_pct_de_valores"]))
    t += ["", "### Lo que más sube y más baja esta semana en cada sector"]
    for sector, m in (d.get("destacados_por_sector") or {}).items():
        t.append("- %s: suben %s; bajan %s" % (sector, ", ".join(m["suben"]), ", ".join(m["bajan"])))
    t += ["", "### Materias primas", *[_linea(n, q) for n, q in (d.get("materias_primas") or {}).items()], ""]
    if d.get("macro"):
        t += ["### Economía", *["- %s: %s (%s)" % (v["nombre"], v.get("valor"), v.get("fecha")) for v in d["macro"].values() if "valor" in v], ""]
    if plan.get("panorama"):
        t += ["### Lectura del día", plan["panorama"], ""]
    for nombre, informe in inv.items():
        t += ["### Análisis de %s" % nombre, informe.get("resumen", "")]
        t += ["- %s: %s (%s)" % (h["titulo"], h["detalle"], h["fuente"]) for h in informe.get("hallazgos", [])]
        t += [""]
    t += ["### Titulares recientes"] + ["- %s" % n["titular"] for n in d["noticias"]["titulares"][:30]] + [""]
    pregunta = (
        "Simula el debate de la próxima semana de bolsa entre inversores particulares, gestores de fondos, analistas, "
        "vendedores en corto, economistas y periodistas ante esta situación del mercado de EE. UU. Quiero saber: "
        "1) si el movimiento reciente del mercado continúa o se da la vuelta; 2) hacia qué sectores va el dinero y de "
        "cuáles sale, entre los once sectores del mercado y los temas de inteligencia artificial, renovables, nuclear y "
        "defensa; 3) qué valores concretos generan más convicción%s; 4) qué argumentos hacen cambiar de opinión a los "
        "participantes y qué noticias o temores podrían dar la vuelta al ánimo." % (
            (", incluidos " + ", ".join(en_cartera)) if en_cartera else ""))
    return "\n".join(t), pregunta, d


CONTEXTO = (
    "Los participantes de esta simulación son las personas descritas en el apartado 'Quién participa en el debate': "
    "inversores particulares, gestores de fondos, analistas, operadores, vendedores en corto, economistas, periodistas "
    "y asesores. Define los tipos de entidad para ellos. Las empresas cotizadas, los índices, los fondos, los sectores y "
    "las materias primas son temas de conversación, no participantes: no deben publicar ni comentar. Tampoco las "
    "personas reales que aparecen en las noticias. Todos los participantes escriben en español.")

PREGUNTA_ENCUESTA = (
    "Encuesta final sobre la bolsa de EE. UU. Responde SOLO con un objeto JSON, sin texto antes ni después, con estas "
    "claves: \"mercado\" (\"sube\", \"baja\" o \"lateral\": qué crees que hará en los próximos cinco días), "
    "\"sectores_a_favor\" (hasta 2 sectores en los que pondrías dinero ahora), \"sectores_en_contra\" (hasta 2 que "
    "evitarías), \"valores\" (hasta 2 símbolos de acciones que comprarías) y \"motivo\" (una frase con tu razón principal "
    "y, si has cambiado de opinión durante el debate, qué te hizo cambiar). Usa para los sectores estos nombres: "
    "Tecnología, Salud, Finanzas, Consumo discrecional, Comunicación, Industria, Consumo básico, Energía, Servicios "
    "públicos, Inmobiliario, Materiales, Inteligencia artificial y chips, Renovables, Nuclear y uranio, Defensa.")
SECTORES = ["Tecnología", "Salud", "Finanzas", "Consumo discrecional", "Comunicación", "Industria", "Consumo básico",
            "Energía", "Servicios públicos", "Inmobiliario", "Materiales", "Inteligencia artificial y chips",
            "Renovables", "Nuclear y uranio", "Defensa"]


def _sin_tildes(s):
    return s.lower().translate(str.maketrans("áéíóúü", "aeiouu"))


ALIAS = [   # de más específico a más general
    ("inteligencia artificial", "Inteligencia artificial y chips"), ("chips", "Inteligencia artificial y chips"),
    ("semiconduct", "Inteligencia artificial y chips"), ("renovable", "Renovables"), ("renewable", "Renovables"),
    ("solar", "Renovables"), ("nuclear", "Nuclear y uranio"), ("uranio", "Nuclear y uranio"), ("uranium", "Nuclear y uranio"),
    ("defens", "Defensa"), ("aeroespacial", "Defensa"), ("utilit", "Servicios públicos"), ("servicios publicos", "Servicios públicos"),
    ("electricas", "Servicios públicos"), ("consumo basico", "Consumo básico"), ("staples", "Consumo básico"),
    ("consumo defensivo", "Consumo básico"), ("consumo discrecional", "Consumo discrecional"), ("discretionary", "Consumo discrecional"),
    ("consumo ciclico", "Consumo discrecional"), ("comunicacion", "Comunicación"), ("communication", "Comunicación"),
    ("telecom", "Comunicación"), ("inmobiliari", "Inmobiliario"), ("real estate", "Inmobiliario"), ("material", "Materiales"),
    ("mineria", "Materiales"), ("industri", "Industria"), ("salud", "Salud"), ("health", "Salud"), ("farma", "Salud"),
    ("biotec", "Salud"), ("financ", "Finanzas"), ("banc", "Finanzas"), ("bank", "Finanzas"), ("segur", "Finanzas"),
    ("energ", "Energía"), ("petrol", "Energía"), ("oil", "Energía"), ("tecnolog", "Tecnología"), ("tech", "Tecnología"),
    ("software", "Tecnología")]


def _sector(texto):
    """Lleva lo que escribe un agente al nombre de sector del bot, si se reconoce."""
    x = _sin_tildes(str(texto)).strip()
    if x in ("ia", "ia y chips"):
        return "Inteligencia artificial y chips"
    for clave, sector in ALIAS:
        if clave in x:
            return sector
    return None


def contar_encuesta(resultados):
    """Recuento de la encuesta: qué espera cada agente y qué sectores y valores cita."""
    universo = set(valores_del_universo())
    mercado, favor, contra, valores, motivos, validas = (collections.Counter(), collections.Counter(),
                                                         collections.Counter(), collections.Counter(), [], 0)
    for r in resultados.values():
        texto = str(r.get("response") or "")
        try:
            j = json.loads(texto[texto.find("{"):texto.rfind("}") + 1])
        except ValueError:
            continue
        if not isinstance(j, dict):
            continue
        validas += 1
        m = _sin_tildes(str(j.get("mercado") or ""))
        mercado["sube" if "sub" in m else "baja" if "baj" in m else "lateral"] += 1
        for s in {_sector(x) for x in (j.get("sectores_a_favor") or [])[:2]} - {None}:
            favor[s] += 1
        for s in {_sector(x) for x in (j.get("sectores_en_contra") or [])[:2]} - {None}:
            contra[s] += 1
        for v in (j.get("valores") or [])[:2]:
            v = str(v).upper().strip().lstrip("$")
            if v in universo:
                valores[v] += 1
        if j.get("motivo"):
            motivos.append(str(j["motivo"])[:280])
    saldo = sorted(SECTORES, key=lambda s: -(favor[s] - contra[s]))
    return {"respuestas": validas, "agentes": len(resultados), "mercado": dict(mercado),
            "sectores": [{"sector": s, "a_favor": favor[s], "en_contra": contra[s], "saldo": favor[s] - contra[s]} for s in saldo],
            "valores": valores.most_common(12), "motivos": motivos}


def rondas_hechas(sim_id):
    """Cuántos pasos de debate completó la simulación, según el registro de MiroFish."""
    ruta = REPO / "backend" / "uploads" / "simulations" / sim_id / "log" / "oasis.env.log"
    try:
        return ruta.read_text(encoding="utf-8", errors="replace").count("performed all actions")
    except OSError:
        return 0


# Las dos redes guardan lo mismo en la misma base de datos. En la de tipo Reddit se debate con
# comentarios; en la de tipo Twitter, citando publicaciones (quote_content) o reenviándolas.
SQL_DICHOS = ("select user_id, coalesce(quote_content, content) from post "
              "where original_post_id is null or quote_content is not null "
              "union all select user_id, content from comment")
SQL_PUBLICACIONES = ("select content from post where original_post_id is null "
                     "order by num_likes + num_shares desc, post_id desc limit %d")
SQL_RESPUESTAS = ("select texto from (select content as texto, num_likes as apoyo, comment_id as orden from comment "
                  "union all select quote_content, num_likes + num_shares, post_id from post where quote_content is not null) "
                  "order by apoyo desc, orden desc limit %d")
RED = {"reddit": "un foro", "twitter": "una red social de mensajes cortos"}


def _perfiles(base):
    """Las fichas de los agentes. MiroFish las genera para las dos redes; la de tipo Reddit es la más completa."""
    perfiles = leer_json(base / "reddit_profiles.json", [])
    if perfiles:
        return perfiles
    try:
        with open(base / "twitter_profiles.csv", encoding="utf-8", newline="") as f:
            return [{"user_id": x.get("user_id"), "name": x.get("name"), "bio": x.get("description"),
                     "persona": x.get("user_char")} for x in csv.DictReader(f)]
    except OSError:
        return []


def encuesta_propia(sim_id, plataforma, cfg):
    """La encuesta final, hecha por el bot.

    MiroFish solo deja entrevistar a los agentes si el debate termina por sí
    solo. Si se cortó (por gasto o por tiempo), aquí se pregunta a cada agente
    lo mismo, dándole su personalidad y todo lo que escribió durante el debate.
    """
    base = REPO / "backend" / "uploads" / "simulations" / sim_id
    perfiles = _perfiles(base)
    tipos = {str(x.get("agent_id")): x.get("entity_type") for x in (leer_json(base / "simulation_config.json", {}).get("agent_configs") or [])}
    ruta_db = base / ("%s_simulation.db" % plataforma)
    if not perfiles or not ruta_db.exists():
        return {}
    c = sqlite3.connect(str(ruta_db))
    dichos = collections.defaultdict(list)
    try:
        for uid, texto in c.execute(SQL_DICHOS):
            if texto:
                dichos[int(uid)].append(texto)
        foro = [x[0][:350] for x in c.execute(SQL_PUBLICACIONES % 8)]
    finally:
        c.close()
    from .base import claves
    from .puente import DESTINO, PRECIOS
    clave = claves()["ANTHROPIC_API_KEY"]

    def uno(p):
        uid = int(p["user_id"])
        propio = "\n".join("- " + x[:450] for x in dichos.get(uid, [])[-8:]) or "(no llegaste a escribir)"
        # Margen amplio: el modelo razona antes de contestar y ese razonamiento también consume salida.
        cuerpo = {"model": cfg["modelo"], "max_tokens": 4000, "messages": [
            {"role": "system", "content": "Eres %s. Profesión: %s.\n%s\n%s\nResponde en español, en primera persona y fiel a tu personaje." % (
                p.get("name"), p.get("profession", ""), str(p.get("bio", ""))[:700], str(p.get("persona", ""))[:1800])},
            {"role": "user", "content": "Has participado estos días en %s sobre la bolsa de EE. UU.\n\nLo que tú escribiste:\n%s\n\n"
                                        "Lo más compartido allí:\n%s\n\n%s" % (
                                            RED.get(plataforma, "un foro"), propio, "\n".join("- " + x for x in foro), PREGUNTA_ENCUESTA)}]}
        req = urllib.request.Request(DESTINO, data=json.dumps(cuerpo).encode(),
                                     headers={"Authorization": "Bearer " + clave, "Content-Type": "application/json"})
        for intento in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    doc = json.loads(r.read())
                return uid, doc["choices"][0]["message"]["content"] or "", doc.get("usage") or {}
            except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
                time.sleep(3 * (intento + 1))
        return uid, "", {}
    from concurrent.futures import ThreadPoolExecutor
    opinan = [p for p in perfiles if _opina(tipos.get(str(p.get("user_id")), "Participante"))]
    resultados, ent, sal = {}, 0, 0
    with ThreadPoolExecutor(max_workers=6) as grupo:
        for uid, texto, uso in grupo.map(uno, opinan):
            resultados["agente_%d" % uid] = {"agent_id": uid, "response": texto}
            ent += uso.get("prompt_tokens") or 0
            sal += uso.get("completion_tokens") or 0
    pe, ps = PRECIOS.get(cfg["modelo"], PRECIOS["claude-sonnet-5-5"])
    gasto.anotar("mirofish", "encuesta", cfg["modelo"], "", (ent * pe + sal * ps) / 1e6, {"entrada": ent, "salida": sal}, True, "clave")
    return resultados


def _lo_mas_valorado(sim_id, plataforma):
    """Publicaciones y respuestas (comentarios o citas) con más apoyo del debate, leídas de la base de datos de la simulación."""
    ruta = REPO / "backend" / "uploads" / "simulations" / sim_id / ("%s_simulation.db" % plataforma)
    if not ruta.exists():
        return [], [], {}
    c = sqlite3.connect(str(ruta))
    try:
        uno = lambda sql: c.execute(sql).fetchone()[0]      # noqa: E731
        pubs = [x[0][:500] for x in c.execute(SQL_PUBLICACIONES % 14)]
        coms = [x[0][:350] for x in c.execute(SQL_RESPUESTAS % 24) if x[0]]
        cifras = {"user": uno("select count(*) from user"),
                  "post": uno("select count(*) from post where original_post_id is null"),
                  # respuestas: comentarios (red tipo Reddit) más citas (red tipo Twitter)
                  "comment": uno("select count(*) from comment") + uno("select count(*) from post where quote_content is not null"),
                  "reenvios": uno("select count(*) from post where original_post_id is not null and quote_content is null"),
                  "like": uno('select count(*) from "like"'), "red": plataforma}
        cifras["activos"] = uno("select count(distinct user_id) from trace where action in "
                                "('create_post','create_comment','like_post','like_comment','quote_post','repost')")
    except sqlite3.Error:
        return [], [], {}
    finally:
        c.close()
    return pubs, coms, cifras


# ---------------------------------------------------------------- aciertos

def _cierres(simbolo):
    s = yahoo.serie(simbolo, "6mo", "1d")
    return [(dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat(), c) for ts, c in zip(s["t"], s["c"])]


def puntuar():
    """Comprueba con precios reales las predicciones cuyo plazo ya ha pasado."""
    hechas = {(a["fecha"], a["simbolo"]) for a in leer_lineas(ACIERTOS)}
    hoy = ahora_madrid().date().isoformat()
    for p in leer_lineas(PREDICCIONES):
        if (p["fecha"], p["simbolo"]) in hechas:
            continue
        try:
            serie = [x for x in _cierres(p["simbolo"]) if x[0] < hoy]
        except ErrorFuente:
            continue
        antes = [x for x in serie if x[0] <= p["fecha"]]
        despues = [x for x in serie if x[0] > p["fecha"]]
        if not antes or len(despues) < p["plazo_sesiones"]:
            continue
        base, fin = antes[-1][1], despues[p["plazo_sesiones"] - 1][1]
        var = round((fin / base - 1) * 100, 2)
        anexar_linea(ACIERTOS, dict(p, variacion_pct=var, fecha_fin=despues[p["plazo_sesiones"] - 1][0],
                                    acierto=(var > 0) == (p["direccion"] == "sube")))
        hechas.add((p["fecha"], p["simbolo"]))


def historial():
    a = leer_lineas(ACIERTOS)
    ok = sum(1 for x in a if x["acierto"])
    pendientes = len({(p["fecha"], p["simbolo"]) for p in leer_lineas(PREDICCIONES)}) - len(a)
    return {"evaluadas": len(a), "acertadas": ok, "acierto_pct": round(ok / len(a) * 100) if a else None,
            "pendientes": max(pendientes, 0)}


def para_el_analisis():
    """Lo que el estratega recibe de MiroFish. None si no hay simulación reciente."""
    u = leer_json(ULTIMA)
    if not u:
        return None
    dias = (ahora_madrid().date() - dt.date.fromisoformat(u["fecha"])).days
    if dias > _cfg()["dias_de_validez"]:
        return None
    h = historial()
    enc = u.get("encuesta") or {}
    return dict(u["resumen"], fecha=u["fecha"], antiguedad_dias=dias, historial_de_aciertos=h,
                encuesta={k: enc.get(k) for k in ("respuestas", "agentes", "mercado", "sectores", "valores")},
                actividad=u.get("actividad"),
                como_usarlo=("Es la guía principal para decidir en qué sectores buscar compras y cuáles evitar, y la "
                             "postura general de la cartera. " + (
                                 "Todavía no hay predicciones evaluadas con precios reales." if not h["evaluadas"] else
                                 "Lleva %d aciertos de %d predicciones evaluadas." % (h["acertadas"], h["evaluadas"]))))


# ---------------------------------------------------------------- ciclo completo

def ejecutar(a=None):
    cfg = _cfg()
    env = _env_mirofish()
    if not env.get("ZEP_API_KEY"):
        raise ErrorFuente("falta la clave de Zep Cloud en repos/MiroFish/.env (línea ZEP_API_KEY=)")
    if not env.get("LLM_API_KEY"):
        raise ErrorFuente("falta la contraseña local LLM_API_KEY en repos/MiroFish/.env")
    if modelo.autenticacion() != "clave":
        raise ErrorFuente("MiroFish necesita la clave de API de Claude con saldo")
    # Si alguien corta la tarea, que igualmente pare el contenedor y el puente.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    puntuar()
    carpeta = carpeta_del_dia("datos") / "mirofish"
    carpeta.mkdir(parents=True, exist_ok=True)
    # Lo ya hecho hoy se anota para poder reanudar sin repetir pasos (ni gastar otra vez).
    ruta_prog = carpeta / "progreso.json"
    prog = leer_json(ruta_prog, {})
    if prog.get("terminada"):
        prog = {}
        for viejo in ("participantes.json", "encuesta.json", "informe.md"):
            (carpeta / viejo).unlink(missing_ok=True)

    def anotar(**cambios):
        prog.update(cambios)
        escribir_json(ruta_prog, prog)
    d = _ultimo("dossier-manana.json")
    if not d:
        raise ErrorFuente("no hay ningún informe de la mañana del que partir")
    _log("mirando el mercado entero")
    d = mercado_al_dia(d)
    plan, inv = _ultimo("plan.json") or {}, _ultimo("investigadores.json") or {}
    _log("paso 1/7: preparando el reparto de participantes")
    participantes, info_reparto = reparto(d, plan, inv, carpeta, cfg)
    texto, pregunta, d = semilla(participantes, d)
    (carpeta / "semilla.md").write_text(texto, encoding="utf-8")
    inicio, limite = time.time(), time.time() + cfg["espera_max_minutos"] * 60
    hoy = ahora_madrid().date().isoformat()
    puente = None
    consumo, encuesta, md = {}, leer_json(carpeta / "encuesta.json", {}), ""

    def gastado():
        try:
            return _get("http://127.0.0.1:%d/salud" % cfg["puerto_puente"], 5)
        except (OSError, ValueError):
            return {}
    try:
        arrancar_docker()
        puente = arrancar_puente(cfg, env["LLM_API_KEY"], carpeta / "consumo.json")
        _log("arrancando el contenedor")
        arrancar_contenedor(cfg)
        if not prog.get("proyecto"):
            _log("paso 2/7: analizando el documento de partida (%d participantes, %d caracteres)" % (len(participantes), len(texto)))
            p = _api("POST", "/api/graph/ontology/generate", espera=900, multiparte=_multiparte(
                {"simulation_requirement": pregunta, "project_name": "Bolsa %s" % hoy, "additional_context": CONTEXTO},
                "debate-%s.md" % hoy, texto))
            tipos = [x.get("name") for x in (p.get("ontology") or {}).get("entity_types", []) if x.get("name")]
            anotar(proyecto=p["project_id"], tipos=tipos, tipos_agente=[x for x in tipos if _opina(x)])
        proyecto = prog["proyecto"]
        _log("tipos de entidad: %s | serán agentes: %s" % (", ".join(prog.get("tipos") or []), ", ".join(prog.get("tipos_agente") or []) or "todos"))
        if not prog.get("grafo"):
            _log("paso 3/7: construyendo el grafo de conocimiento")
            # Trozos grandes: Zep tarda por número de trozos y MiroFish solo le espera diez minutos.
            t = _api("POST", "/api/graph/build", {"project_id": proyecto, "chunk_size": cfg["tamano_trozo"],
                                                  "chunk_overlap": 100, "force": True})
            _esperar("grafo", lambda: _api("GET", "/api/graph/task/%s" % t["task_id"]), {"completed"}, {"failed"}, limite)
            anotar(grafo=_api("GET", "/api/graph/project/%s" % proyecto).get("graph_id"))
        grafo = prog["grafo"]
        if not prog.get("sim"):
            anotar(sim=_api("POST", "/api/simulation/create", {
                "project_id": proyecto, "graph_id": grafo, "enable_twitter": True, "enable_reddit": True})["simulation_id"])
        sim = prog["sim"]
        # MiroFish pide siempre el modelo de su .env. Si la configuración quiere otro para escribir las
        # fichas de los agentes o para el debate, el puente lo cambia al vuelo.
        de_mirofish = env.get("LLM_MODEL_NAME")
        if not prog.get("preparada"):
            _modelo_del_puente(cfg, env["LLM_API_KEY"], cfg.get("modelo_perfiles") if cfg.get("modelo_perfiles") != de_mirofish else None)
            _log("paso 4/7: creando los agentes (con %s)" % (cfg.get("modelo_perfiles") or de_mirofish))
            cuerpo = {"simulation_id": sim}
            if prog.get("tipos_agente"):
                cuerpo["entity_types"] = prog["tipos_agente"]
            pr = _api("POST", "/api/simulation/prepare", cuerpo, espera=900)
            if not pr.get("already_prepared"):
                _esperar("agentes", lambda: _api("POST", "/api/simulation/prepare/status",
                                                 {"task_id": pr.get("task_id"), "simulation_id": sim}),
                         {"completed", "ready"}, {"failed"}, limite)
            anotar(preparada=True)
        if not prog.get("simulada"):
            rondas = rondas_que_caben(cfg)
            anotar(plataforma=cfg["plataforma"])
            _modelo_del_puente(cfg, env["LLM_API_KEY"], cfg["modelo"] if cfg["modelo"] != de_mirofish else None)
            _log("paso 5/7: simulación en %s (%d rondas, agentes con %s)%s" % (
                cfg["plataforma"], rondas, cfg["modelo"], ", reanudada" if prog.get("intentos") else ""))
            antes = gastado().get("coste_usd") or 0.0
            _api("POST", "/api/simulation/start", {"simulation_id": sim, "platform": cfg["plataforma"],
                                                   "max_rounds": rondas, "enable_graph_memory_update": False,
                                                   "force": bool(prog.get("intentos"))}, espera=900)
            anotar(intentos=prog.get("intentos", 0) + 1)

            def cerca_del_tope():      # el resto del tope queda para la encuesta y el informe
                return (gastado().get("coste_usd") or 0) >= cfg["tope_usd"] * cfg["parte_del_debate"]

            def rondas_terminadas():
                # Con una sola red, MiroFish no marca la simulación como terminada: se queda
                # "en marcha" esperando órdenes. La señal fiable es que el entorno pasa a "vivo".
                try:
                    return bool(_api("POST", "/api/simulation/env-status", {"simulation_id": sim}, espera=20).get("env_alive"))
                except ErrorFuente:
                    return False
            est = _esperar("simulación", lambda: _api("GET", "/api/simulation/%s/run-status" % sim),
                           {"completed", "stopped"}, {"failed"}, limite - 1500, pausa=15,
                           vigilar=lambda: cerca_del_tope() or rondas_terminadas())
            if cfg["encuesta"] and est.get("runner_status") not in ("stopped", "failed") and rondas_terminadas():
                _log("paso 6/7: encuesta a los agentes")
                try:
                    r = _api("POST", "/api/simulation/interview/all", {
                        "simulation_id": sim, "prompt": PREGUNTA_ENCUESTA, "platform": cfg["plataforma"], "timeout": 600}, espera=700)
                    encuesta = contar_encuesta(((r.get("result") or {}).get("results")) or {})
                    escribir_json(carpeta / "encuesta.json", encuesta)
                    _log("encuesta: %d respuestas válidas de %d agentes" % (encuesta["respuestas"], encuesta["agentes"]))
                except ErrorFuente as e:
                    _log("la encuesta no se pudo hacer: %s" % e)
            if est.get("runner_status") not in ("completed", "stopped"):
                _api("POST", "/api/simulation/stop", {"simulation_id": sim})
                _esperar("cierre de la simulación", lambda: _api("GET", "/api/simulation/%s/run-status" % sim),
                         {"completed", "stopped"}, {"failed"}, time.time() + 120, pausa=5)
            anotar(simulada=True, coste_debate=round((gastado().get("coste_usd") or 0.0) - antes, 3))
        if cfg["encuesta"] and not encuesta.get("respuestas"):
            _log("paso 6/7: encuesta a los agentes, hecha por el bot")
            encuesta = contar_encuesta(encuesta_propia(sim, cfg["plataforma"], cfg))
            escribir_json(carpeta / "encuesta.json", encuesta)
            _log("encuesta: %d respuestas válidas de %d agentes" % (encuesta["respuestas"], encuesta["agentes"]))
        _log("paso 7/7: informe de MiroFish (con %s) y síntesis" % cfg["modelo_informe"])
        _modelo_del_puente(cfg, env["LLM_API_KEY"], cfg["modelo_informe"])
        rep = _api("POST", "/api/report/generate", {"simulation_id": sim, "force_regenerate": prog.get("intentos", 1) > 1}, espera=900)
        if rep.get("task_id"):
            _esperar("informe", lambda: _api("POST", "/api/report/generate/status",
                                             {"task_id": rep.get("task_id"), "simulation_id": sim}),
                     {"completed"}, {"failed"}, limite + 900, pausa=15)
        md = _api("GET", "/api/report/by-simulation/%s" % sim).get("markdown_content") or ""
        if len(md) < 200:
            raise ErrorFuente("MiroFish devolvió un informe vacío")
        (carpeta / "informe.md").write_text(md, encoding="utf-8")
        anotar(terminada=True)
        consumo = gastado()
        if cfg["borrar_grafo_al_terminar"] and grafo:
            try:
                _api("DELETE", "/api/graph/delete/%s" % grafo)
            except ErrorFuente as e:
                _log("no se pudo borrar el grafo de Zep: %s" % e)
    finally:
        consumo = consumo or gastado()
        if consumo.get("llamadas"):
            gasto.anotar("mirofish", "simulacion", cfg["modelo"], "", consumo.get("coste_usd", 0.0),
                         {"entrada": consumo.get("tokens_entrada"), "salida": consumo.get("tokens_salida")},
                         ok=(carpeta / "informe.md").exists(), via="clave")
        parar(puente)
    return _cerrar(carpeta, prog, cfg, d, pregunta, len(participantes), md, encuesta, consumo,
                   (info_reparto or {}).get("coste_usd", 0.0), round((time.time() - inicio) / 60), hoy)


def rondas_que_caben(cfg):
    """Rondas de debate que caben en la parte del tope reservada al debate.

    Cada agente relee todo lo que lleva dicho y visto, así que el coste crece con el cuadrado de
    las rondas: el doble de rondas cuesta unas cuatro veces más. Con lo que costó la última
    simulación en la misma red se calcula cuántas caben, con un margen. Sin ese dato se pide el
    máximo: el corte por gasto para el debate a tiempo de todos modos."""
    u = leer_json(ULTIMA, {})
    hechas, coste = u.get("rondas"), (u.get("costes") or {}).get("debate_usd")
    if hechas and coste and (u.get("plataforma") or "reddit") == cfg["plataforma"]:
        caben = hechas * (cfg["tope_usd"] * cfg["parte_del_debate"] / coste) ** 0.5
        return max(8, min(cfg["max_rondas"], int(caben * 0.9)))
    return cfg["max_rondas"]


def _cerrar(carpeta, prog, cfg, d, pregunta, n_participantes, md, encuesta, consumo, coste_reparto, minutos, hoy):
    """Síntesis final, predicciones anotadas, resumen para el estratega y PDF."""
    cfg = dict(cfg, plataforma=prog.get("plataforma") or cfg["plataforma"])
    pubs, coms, actividad = _lo_mas_valorado(prog["sim"], cfg["plataforma"])
    actividad["rondas"] = rondas_hechas(prog["sim"])
    _log("síntesis con %s" % cfg["modelo_sintesis"])
    resumen, info = modelo.preguntar(
        "mirofish", "mirofish-resumen", cfg["modelo_sintesis"],
        {"fecha": hoy, "pregunta": pregunta, "universo": "Las empresas del S&P 500 y los valores de los temas del bot.",
         "indices_validos": INDICES, "en_cartera": [p["ticker"] for p in d["cartera"]["posiciones"]],
         "encuesta": {k: encuesta.get(k) for k in ("respuestas", "agentes", "mercado", "sectores", "valores")},
         "respuestas_de_la_encuesta": (encuesta.get("motivos") or [])[:40], "actividad": actividad,
         "publicaciones": pubs, "comentarios": coms, "fuerza_de_sectores": d.get("fuerza_de_sectores"),
         "informe_de_mirofish": md[:60000]},
        ESQ_SINTESIS, nivel="mirofish", tope_usd=1.5, esfuerzo="high", espera=900, carpeta=carpeta)
    if not resumen:
        raise ErrorFuente("no se pudo hacer la síntesis de MiroFish: %s" % info.get("error"))
    validos = set(valores_del_universo()) | set(INDICES)
    resumen["predicciones"] = [p for p in resumen["predicciones"] if p["simbolo"].upper() in validos][:8]
    resumen["valores"] = [v for v in resumen["valores"] if v["ticker"].upper() in validos][:10]
    ya = {(p["fecha"], p["simbolo"]) for p in leer_lineas(PREDICCIONES)}
    for p in resumen["predicciones"]:
        if (hoy, p["simbolo"].upper()) not in ya:
            anexar_linea(PREDICCIONES, {"fecha": hoy, "simbolo": p["simbolo"].upper(), "direccion": p["direccion"],
                                        "plazo_sesiones": cfg["plazo_sesiones"], "motivo": p["motivo"]})
    costes = {"simulacion_e_informe_usd": consumo.get("coste_usd", 0.0), "debate_usd": prog.get("coste_debate"),
              "reparto_usd": coste_reparto, "sintesis_usd": info.get("coste_usd", 0.0)}
    costes["total_usd"] = round(costes["simulacion_e_informe_usd"] + costes["reparto_usd"] + costes["sintesis_usd"], 2)
    escribir_json(ULTIMA, {"fecha": hoy, "plataforma": cfg["plataforma"], "resumen": resumen, "encuesta": encuesta, "actividad": actividad,
                           "rondas": actividad["rondas"], "participantes": n_participantes, "consumo": consumo,
                           "costes": costes, "minutos": minutos, "informe": str(carpeta / "informe.md")})
    ruta = _pdf(resumen, encuesta, actividad, consumo, costes, minutos, hoy, n_participantes)
    return "mirofish: %d agentes activos, %d rondas, %d llamadas, %.2f $, %d min -> %s" % (
        actividad.get("activos", 0), actividad["rondas"], consumo.get("llamadas", 0), costes["total_usd"], minutos, ruta)


def resintetizar(a=None):
    """Rehace la encuesta (si falta) y la síntesis con lo ya simulado hoy, sin volver a simular."""
    cfg = _cfg()
    carpeta = carpeta_del_dia("datos") / "mirofish"
    prog = leer_json(carpeta / "progreso.json", {})
    if not prog.get("sim") or not (carpeta / "informe.md").exists():
        raise ErrorFuente("no hay una simulación de hoy con informe que resumir")
    participantes = leer_json(carpeta / "participantes.json", [])
    _log("mirando el mercado entero")
    d = mercado_al_dia(_ultimo("dossier-manana.json"))
    _, pregunta, d = semilla(participantes, d)
    encuesta = leer_json(carpeta / "encuesta.json", {})
    if cfg["encuesta"] and not encuesta.get("respuestas"):
        _log("encuesta a los agentes, hecha por el bot")
        encuesta = contar_encuesta(encuesta_propia(prog["sim"], prog.get("plataforma") or "reddit", cfg))
        escribir_json(carpeta / "encuesta.json", encuesta)
        _log("encuesta: %d respuestas válidas de %d agentes" % (encuesta["respuestas"], encuesta["agentes"]))
    previo = leer_json(ULTIMA, {})
    return _cerrar(carpeta, prog, cfg, d, pregunta, len(participantes), (carpeta / "informe.md").read_text(encoding="utf-8"),
                   encuesta, leer_json(carpeta / "consumo.json", {}), (previo.get("costes") or {}).get("reparto_usd", 0.0),
                   previo.get("minutos", 0), ahora_madrid().date().isoformat())


def _pdf(r, enc, act, consumo, costes, minutos, hoy, n_participantes):
    h = historial()
    votos = {x["sector"]: x for x in enc.get("sectores") or []}
    m = enc.get("mercado") or {}
    n = enc.get("respuestas") or 0
    filas = []
    for x in r["sectores"]:
        v = votos.get(_sector(x["sector"]) or x["sector"], {})
        filas.append([inf.sector(x["sector"]).replace(" ", "&nbsp;"), inf.e(x["sesgo"]), inf.e(x["conviccion"]),
                      str(v.get("a_favor", "")), str(v.get("en_contra", "")), inf.e(x["motivo"])])
    cuerpo = [
        inf.cifras([("Postura que sale de la simulación", inf.e(r["tono"])),
                    ("El mercado, a cinco sesiones", "%s <small>convicción %s</small>" % (inf.e(r["mercado"]["direccion"]), inf.e(r["mercado"]["conviccion"]))),
                    ("Encuesta: sube / lateral / baja", ("%d / %d / %d" % (m.get("sube", 0), m.get("lateral", 0), m.get("baja", 0))) if n else "sin encuesta"),
                    ("Aciertos hasta hoy", ("%d de %d" % (h["acertadas"], h["evaluadas"])) if h["evaluadas"] else "sin evaluar")]),
        "<h2>Tesis</h2><p>%s</p><p class='gris'>%s</p>" % (inf.e(r["tesis"]), inf.e(r["mercado"]["motivo"])),
        "<h2>Sectores, del más favorecido al menos (votos de %d agentes)</h2>" % n +
        inf.tabla([("Sector", 0), ("Sesgo", 0), ("Convicción", 0), ("A favor", 1), ("En contra", 1), ("Motivo", 0)], filas),
        '<div class="dos"><div><h2>Valores</h2>%s</div><div><h2>Predicciones a %d sesiones</h2>%s</div></div>' % (
            inf.tabla([("Valor", 0), ("Sesgo", 0), ("Motivo", 0)],
                      [[inf.e(x["ticker"]), inf.e(x["sesgo"]), inf.e(x["motivo"])] for x in r["valores"]],
                      "La simulación no destaca valores concretos."), _cfg()["plazo_sesiones"],
            inf.tabla([("Qué", 0), ("Dirección", 0), ("Motivo", 0)],
                      [[inf.e(INDICES.get(x["simbolo"], x["simbolo"])), inf.e(x["direccion"]), inf.e(x["motivo"])] for x in r["predicciones"]],
                      "Sin predicciones comprobables.")),
        '<div class="dos"><div><h2>En qué no se ponen de acuerdo</h2>%s</div><div><h2>Cuánto fiarse</h2><p>%s</p></div></div>' % (
            inf.lista(r.get("debate")), inf.e(r["fiabilidad"])),
        "<p class='gris'>%d participantes descritos · %s agentes, %s activos · %s rondas en una red tipo %s · %s publicaciones y %s %s · "
        "%d minutos · %s llamadas al modelo · coste total %s.</p>" % (
            n_participantes, act.get("user", "?"), act.get("activos", "?"), act.get("rondas", "?"),
            "Twitter" if act.get("red") == "twitter" else "Reddit", act.get("post", "?"), act.get("comment", "?"),
            "citas" if act.get("red") == "twitter" else "comentarios",
            minutos, consumo.get("llamadas", 0), inf.dinero(costes["total_usd"]))]
    pie = ("MiroFish simula opiniones con agentes de inteligencia artificial: no predice precios. Sus predicciones se "
           "comprueban después con precios reales. " + inf.PIE)
    c = _cfg()
    ruta, _, _ = inf.componer("Simulación semanal (MiroFish)", "%s · %s" % (hoy, ahora_madrid().strftime("%H:%M")),
                              ["agentes: %s" % c["modelo"], "informe: %s" % c["modelo_informe"], "síntesis: %s" % c["modelo_sintesis"]],
                              "".join(cuerpo), pie, carpeta_informes() / "1700-mirofish.pdf")
    return ruta
