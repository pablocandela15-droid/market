"""Cliente mínimo de Alpaca, SOLO para la cuenta de simulación (paper trading).

Dos seguros para que este código no pueda tocar dinero real:
  1. La dirección es fija y es la de simulación. No hay opción para cambiarla.
  2. Se rechaza cualquier clave que no tenga el prefijo de las claves de simulación.
"""
from __future__ import annotations

import json
import time
import urllib.parse

from .base import ErrorFuente, claves, pedir

DIRECCION = "https://paper-api.alpaca.markets"      # simulación; no modificar
PREFIJO_SIMULACION = "PK"


class Alpaca:
    def __init__(self, clave, secreto):
        if not clave.startswith(PREFIJO_SIMULACION):
            raise ErrorFuente("La clave de Alpaca no es de simulación (debe empezar por PK). "
                              "Este bot no opera con claves reales.")
        self._cab = {"APCA-API-KEY-ID": clave, "APCA-API-SECRET-KEY": secreto,
                     "Content-Type": "application/json", "Accept": "application/json"}

    def _llamar(self, metodo, ruta, cuerpo=None, **parametros):
        url = DIRECCION + ruta
        if parametros:
            url += "?" + urllib.parse.urlencode({k: v for k, v in parametros.items() if v is not None})
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        texto = pedir(url, self._cab, datos=datos, metodo=metodo, reintentos=1)
        return json.loads(texto) if texto.strip() else None

    def cuenta(self):
        return self._llamar("GET", "/v2/account")

    def reloj(self):
        return self._llamar("GET", "/v2/clock")

    def posiciones(self):
        return self._llamar("GET", "/v2/positions")

    def orden(self, orden_id):
        return self._llamar("GET", "/v2/orders/%s" % orden_id, nested="true")

    def ordenes_abiertas(self, simbolo=None):
        return self._llamar("GET", "/v2/orders", status="open", symbols=simbolo, nested="true")

    def comprar(self, simbolo, cantidad, stop=None, objetivo=None, referencia=None):
        """Compra a mercado. Con acciones enteras y stop+objetivo va como orden
        con salida doble (el bróker vigila el stop). Con fracciones, Alpaca no
        admite esa orden: se compra sin más y el stop lo vigila el bot."""
        entera = float(cantidad) == int(cantidad)
        cuerpo = {"symbol": simbolo, "qty": str(int(cantidad) if entera else cantidad),
                  "side": "buy", "type": "market", "time_in_force": "gtc" if entera else "day"}
        if referencia:
            cuerpo["client_order_id"] = referencia
        con_salidas = bool(entera and stop and objetivo)
        if con_salidas:
            cuerpo.update({"order_class": "bracket", "take_profit": {"limit_price": "%.2f" % objetivo},
                           "stop_loss": {"stop_price": "%.2f" % stop}})
        return self._llamar("POST", "/v2/orders", cuerpo), con_salidas

    def esperar_ejecucion(self, orden_id, segundos=20):
        """Espera a que la compra a mercado se ejecute y devuelve la orden."""
        fin = time.time() + segundos
        o = self.orden(orden_id)
        while o.get("status") not in ("filled", "canceled", "rejected", "expired") and time.time() < fin:
            time.sleep(2)
            o = self.orden(orden_id)
        return o

    def mover_stop(self, orden_id, nuevo_stop):
        """Sube el stop de una compra con salida doble."""
        for pata in self.orden(orden_id).get("legs") or []:
            if pata.get("type") in ("stop", "stop_limit") and pata.get("status") not in ("filled", "canceled", "expired"):
                return self._llamar("PATCH", "/v2/orders/%s" % pata["id"], {"stop_price": "%.2f" % nuevo_stop})
        return None

    def mover_objetivo(self, orden_id, nuevo_objetivo):
        """Aleja el objetivo de una compra con salida doble."""
        for pata in self.orden(orden_id).get("legs") or []:
            if pata.get("type") == "limit" and pata.get("status") not in ("filled", "canceled", "expired"):
                return self._llamar("PATCH", "/v2/orders/%s" % pata["id"], {"limit_price": "%.2f" % nuevo_objetivo})
        return None

    def cerrar(self, simbolo):
        """Cancela las órdenes abiertas del valor y vende toda la posición a mercado."""
        for o in self.ordenes_abiertas(simbolo) or []:
            self._llamar("DELETE", "/v2/orders/%s" % o["id"])
        time.sleep(1)
        return self._llamar("DELETE", "/v2/positions/%s" % simbolo)


def cliente():
    """Devuelve el cliente si hay claves de simulación; si no, None (modo en seco)."""
    k = claves()
    if k.get("ALPACA_API_KEY") and k.get("ALPACA_SECRET_KEY"):
        return Alpaca(k["ALPACA_API_KEY"], k["ALPACA_SECRET_KEY"])
    return None
