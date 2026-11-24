"""Puente local entre MiroFish y la API de Claude.

MiroFish habla el formato de OpenAI y envía parámetros que los modelos
actuales de Claude rechazan. Este servidor, que solo vive mientras dura una
simulación, recibe sus peticiones en este Mac y:

  - quita `temperature` y `top_p`, y convierte la petición de "responde en
    JSON" en una instrucción;
  - las reenvía a la API de Claude con la clave real, que MiroFish nunca ve:
    a MiroFish se le da una contraseña local de usar y tirar;
  - suma el coste de cada respuesta y corta al llegar al tope fijado.

    python3 -m bot.puente --puerto 8765 --token <local> --tope-usd 3 --registro <archivo.json>
"""
from __future__ import annotations

import argparse
import collections
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .base import claves, escribir_json

DESTINO = "https://api.anthropic.com/v1/chat/completions"
# Dólares por millón de tokens (entrada, salida). Lo desconocido se cuenta como Sonnet.
PRECIOS = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
           "claude-fable-5-2": (8.0, 40.0)}
QUITAR = ("temperature", "top_p", "presence_penalty", "frequency_penalty", "logit_bias", "seed", "n")
SOLO_JSON = "Responde únicamente con un objeto JSON válido, sin texto antes ni después y sin bloques de código."


class Ritmo:
    """Reparte las peticiones en el tiempo para no pasar del límite de tokens de entrada por minuto.

    La API rechaza (error 429) lo que exceda ese límite, y al final del debate cada petición
    lleva decenas de miles de tokens. En vez de chocar y reintentar, aquí se espera antes de
    enviar: tarda algo más y no se pierde ninguna llamada. Los tokens de cada petición se
    estiman por su tamaño, con una proporción que se corrige con el consumo real."""

    def __init__(self, por_minuto):
        self.limite = por_minuto
        self.cerrojo = threading.Lock()
        self.ventana = collections.deque()      # (momento, tokens estimados) del último minuto
        self.por_byte = 0.3

    def esperar(self, nbytes):
        """Bloquea hasta que la petición quepa en el minuto en curso. Devuelve los segundos esperados."""
        if not self.limite:
            return 0.0
        tokens = min(nbytes * self.por_byte, self.limite * 0.9)
        inicio = time.time()
        while True:
            with self.cerrojo:
                ahora = time.time()
                while self.ventana and ahora - self.ventana[0][0] > 60:
                    self.ventana.popleft()
                if not self.ventana or sum(t for _, t in self.ventana) + tokens <= self.limite:
                    self.ventana.append((ahora, tokens))
                    return ahora - inicio
                pausa = 60 - (ahora - self.ventana[0][0])
            time.sleep(min(max(pausa, 0.5), 5))

    def aprender(self, nbytes, tokens_reales):
        if nbytes and tokens_reales:
            with self.cerrojo:
                self.por_byte = 0.8 * self.por_byte + 0.2 * tokens_reales / nbytes


class Cuenta:
    def __init__(self, tope, registro, a_la_vez=6, tokens_por_minuto=0):
        self.tope, self.registro = tope, registro
        self.cerrojo = threading.Lock()
        # MiroFish lanza hasta 30 peticiones a la vez y la API las rechaza por exceso de ritmo.
        # Aquí hacen cola: pasan pocas a la vez, tarda algo más y no se pierde ninguna.
        self.turno = threading.BoundedSemaphore(max(1, a_la_vez))
        self.ritmo = Ritmo(tokens_por_minuto)
        self.modelo_forzado = None      # si se fija, todas las peticiones van a ese modelo (fase del informe)
        self.d = {"llamadas": 0, "errores": 0, "reintentos": 0, "frenadas_por_ritmo": 0, "a_la_vez": max(1, a_la_vez),
                  "tokens_por_minuto": tokens_por_minuto, "segundos_de_espera": 0.0,
                  "tokens_entrada": 0, "tokens_salida": 0, "coste_usd": 0.0,
                  "tope_usd": tope, "cortado_por_tope": False, "inicio": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.guardar()

    def guardar(self):
        if self.registro:
            escribir_json(self.registro, self.d)

    def agotada(self):
        return self.d["coste_usd"] >= self.tope

    def sumar(self, modelo, uso):
        ent, sal = uso.get("prompt_tokens") or 0, uso.get("completion_tokens") or 0
        pe, ps = PRECIOS.get(modelo, PRECIOS["claude-sonnet-5-5"])
        with self.cerrojo:
            self.d["llamadas"] += 1
            self.d["tokens_entrada"] += ent
            self.d["tokens_salida"] += sal
            self.d["coste_usd"] = round(self.d["coste_usd"] + (ent * pe + sal * ps) / 1e6, 6)
            if self.d["llamadas"] % 10 == 0:
                self.guardar()

    def espera(self, segundos):
        if segundos > 0.2:
            with self.cerrojo:
                self.d["segundos_de_espera"] = round(self.d["segundos_de_espera"] + segundos, 1)

    def reintento(self, por_ritmo=False):
        with self.cerrojo:
            self.d["reintentos"] += 1
            self.d["frenadas_por_ritmo"] += 1 if por_ritmo else 0

    def error(self, cortado=False, detalle=None):
        with self.cerrojo:
            self.d["errores"] += 1
            if detalle:        # los últimos errores, para poder diagnosticar después
                self.d["ultimos_errores"] = (self.d.get("ultimos_errores") or [])[-4:] + [str(detalle)[:220]]
            self.d["cortado_por_tope"] = self.d["cortado_por_tope"] or cortado
            self.guardar()


def adaptar(cuerpo):
    """Deja la petición en una forma que los modelos de Claude aceptan."""
    for k in QUITAR:
        cuerpo.pop(k, None)
    if "max_completion_tokens" in cuerpo and "max_tokens" not in cuerpo:
        cuerpo["max_tokens"] = cuerpo.pop("max_completion_tokens")
    rf = cuerpo.get("response_format")
    if isinstance(rf, dict) and rf.get("type") == "json_object":
        cuerpo.pop("response_format")
        mensajes = cuerpo.get("messages") or []
        if mensajes and mensajes[0].get("role") == "system" and isinstance(mensajes[0].get("content"), str):
            mensajes[0] = dict(mensajes[0], content=mensajes[0]["content"] + "\n\n" + SOLO_JSON)
        else:
            mensajes.insert(0, {"role": "system", "content": SOLO_JSON})
        cuerpo["messages"] = mensajes
    return cuerpo


def crear_manejador(token, clave, cuenta):
    class Manejador(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):          # sin ruido en la consola
            pass

        def _json(self, codigo, datos):
            cuerpo = json.dumps(datos).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _error(self, codigo, mensaje, tipo="invalid_request_error"):
            self._json(codigo, {"error": {"message": mensaje, "type": tipo, "code": tipo, "param": None}})

        def do_GET(self):
            if self.path.rstrip("/").endswith("/salud"):
                return self._json(200, dict(cuenta.d, ok=True))
            if self.path.rstrip("/").endswith("/models"):
                return self._json(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in PRECIOS]})
            self._error(404, "no existe")

        def do_POST(self):
            largo = int(self.headers.get("Content-Length") or 0)
            crudo = self.rfile.read(largo)
            if self.headers.get("Authorization", "") != "Bearer " + token:
                return self._error(401, "contraseña local incorrecta", "authentication_error")
            if self.path.rstrip("/").endswith("/modo"):      # el bot cambia de modelo entre fases
                try:
                    cuenta.modelo_forzado = json.loads(crudo or b"{}").get("modelo") or None
                except ValueError:
                    return self._error(400, "la petición no es JSON")
                return self._json(200, {"ok": True, "modelo": cuenta.modelo_forzado})
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self._error(404, "este puente solo atiende /chat/completions")
            if cuenta.agotada():
                cuenta.error(cortado=True)
                return self._error(400, "tope de gasto de la simulación alcanzado (%.2f $)" % cuenta.tope, "tope_de_gasto")
            try:
                cuerpo = adaptar(json.loads(crudo))
            except ValueError:
                return self._error(400, "la petición no es JSON")
            if cuenta.modelo_forzado:
                cuerpo["model"] = cuenta.modelo_forzado
            cuerpo.pop("stream", None)
            cuerpo.pop("stream_options", None)
            datos = json.dumps(cuerpo).encode()
            with cuenta.turno:
                return self._enviar(cuerpo, datos)

        def _enviar(self, cuerpo, datos):
            ultimo = 6
            for intento in range(ultimo + 1):
                cuenta.espera(cuenta.ritmo.esperar(len(datos)))
                req = urllib.request.Request(DESTINO, data=datos, headers={
                    "Authorization": "Bearer " + clave, "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=300) as r:
                        resp = json.loads(r.read())
                    cuenta.sumar(cuerpo.get("model", ""), resp.get("usage") or {})
                    cuenta.ritmo.aprender(len(datos), (resp.get("usage") or {}).get("prompt_tokens"))
                    return self._json(200, resp)
                except urllib.error.HTTPError as e:
                    texto = e.read().decode("utf-8", "replace").replace(clave, "***")
                    if e.code in (429, 500, 502, 503, 504, 529) and intento < ultimo:
                        cuenta.reintento(por_ritmo=e.code in (429, 529))
                        try:
                            pedido = float(e.headers.get("retry-after") or 0)
                        except ValueError:
                            pedido = 0.0
                        time.sleep(min(pedido or 3.0 * 2 ** intento, 60))     # 3, 6, 12, 24, 48, 60 s
                        continue
                    cuenta.error(detalle="HTTP %d: %s" % (e.code, texto))
                    try:
                        return self._json(e.code, json.loads(texto))
                    except ValueError:
                        return self._error(e.code, texto[:300])
                except (urllib.error.URLError, OSError, ValueError) as e:
                    if intento < 4:
                        cuenta.reintento()
                        time.sleep(2.0 * (intento + 1))
                        continue
                    cuenta.error(detalle="sin respuesta: %s" % str(e)[:150])
                    return self._error(502, "sin respuesta de la API de Claude: %s" % str(e)[:120], "api_error")
    return Manejador


def main(argv=None):
    ap = argparse.ArgumentParser(description="Puente local entre MiroFish y la API de Claude")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--token", required=True, help="contraseña local que usará MiroFish en lugar de la clave real")
    ap.add_argument("--tope-usd", type=float, default=3.0)
    ap.add_argument("--registro", help="archivo JSON donde se anota el consumo")
    ap.add_argument("--escucha", default="127.0.0.1")
    ap.add_argument("--a-la-vez", type=int, default=6, help="peticiones a la API en curso a la vez; el resto hace cola")
    ap.add_argument("--tokens-por-minuto", type=int, default=0,
                    help="tokens de entrada por minuto que no se quieren superar (0: sin límite propio)")
    a = ap.parse_args(argv)
    clave = claves().get("ANTHROPIC_API_KEY")
    if not clave:
        raise SystemExit("Falta ANTHROPIC_API_KEY en market/.env")
    cuenta = Cuenta(a.tope_usd, a.registro, a.a_la_vez, a.tokens_por_minuto)
    servidor = ThreadingHTTPServer((a.escucha, a.puerto), crear_manejador(a.token, clave, cuenta))
    servidor.daemon_threads = True
    print("puente escuchando en %s:%d (tope %.2f $, %d a la vez)" % (a.escucha, a.puerto, a.tope_usd, a.a_la_vez), flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cuenta.guardar()


if __name__ == "__main__":
    main()
