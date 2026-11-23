"""Pruebas de las reglas de órdenes y de la vigilancia de stops. Sin red.

    python3 -m unittest discover -s tests -v
"""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

os.environ["BOT_PRUEBA"] = "1"

from bot import cartera, ordenes  # noqa: E402

REGLAS = {
    "riesgo": {"riesgo_por_operacion_pct": 1.0, "max_posiciones": 5, "max_posicion_pct": 25, "colchon_caja_pct": 5,
               "min_orden_usd": 5, "min_recompensa_riesgo": 1.5, "stop_min_pct": 2, "stop_max_pct": 15,
               "max_hueco_apertura_pct": 3, "acciones_fraccionadas": True},
    "valoracion": {"peg_maximo": 2.0, "permitir_sin_beneficios": True, "dias_sin_comprar_antes_de_resultados": 2},
}
BARATA = {"per": 15.0, "per_futuro": 12.0, "peg": 0.9, "resultados": None}


def res(valor=1000.0, caja=1000.0, posiciones=(), huecos=5):
    return {"valor_usd": valor, "caja_usd": caja, "posiciones": [{"ticker": t} for t in posiciones],
            "huecos_libres": huecos}


def orden(ticker="NVDA", entrada=100.0, stop=95.0, objetivo=110.0):
    return {"ticker": ticker, "accion": "comprar", "entrada": entrada, "stop": stop, "objetivo": objetivo}


class Tamano(unittest.TestCase):
    def test_arriesga_el_uno_por_ciento(self):
        cantidad, _ = ordenes.dimensionar(100.0, 95.0, res(), REGLAS)
        self.assertEqual(cantidad, 2)                     # 10 $ de riesgo / 5 $ por acción
        self.assertLessEqual(cantidad * 5.0, 10.0)

    def test_tope_del_25_por_ciento(self):
        cantidad, nota = ordenes.dimensionar(100.0, 98.0, res(), REGLAS)   # pediría 5 acciones = 500 $
        self.assertEqual(cantidad, 2)
        self.assertIn("25", nota)

    def test_fraccion_cuando_no_llega_a_una_accion(self):
        cantidad, _ = ordenes.dimensionar(400.0, 370.0, res(), REGLAS)
        self.assertAlmostEqual(cantidad, 0.3333, places=4)

    def test_sin_caja_no_compra(self):
        cantidad, _ = ordenes.dimensionar(100.0, 95.0, res(caja=40.0), REGLAS)
        self.assertEqual(cantidad, 0)


class Validacion(unittest.TestCase):
    def v(self, o, r=None, val=BARATA, precio=None):
        return ordenes.validar(o, r or res(), val, precio, REGLAS)

    def test_orden_correcta(self):
        veredicto, _, cantidad = self.v(orden())
        self.assertEqual((veredicto, cantidad), ("APROBADA", 2))

    def test_bloqueos(self):
        casos = {
            "fuera del universo": orden("ZZZZ"),
            "ya está en cartera": orden(),
            "stop demasiado cerca": orden(stop=99.0, objetivo=103.0),
            "stop demasiado lejos": orden(stop=80.0, objetivo=140.0),
            "gana": orden(objetivo=104.0),
            "stop debe quedar": orden(stop=101.0),
        }
        for texto, o in casos.items():
            r = res(posiciones=("NVDA",)) if texto == "ya está en cartera" else res()
            veredicto, motivos, _ = self.v(o, r)
            self.assertEqual(veredicto, "BLOQUEADA", texto)
            self.assertTrue(any(texto in m for m in motivos), "%s -> %s" % (texto, motivos))

    def test_peg_caro_y_sin_datos(self):
        self.assertEqual(self.v(orden(), val=dict(BARATA, peg=2.5))[0], "BLOQUEADA")
        self.assertEqual(self.v(orden(), val=None)[0], "BLOQUEADA")

    def test_cartera_llena(self):
        self.assertEqual(self.v(orden(), res(huecos=0))[0], "BLOQUEADA")

    def test_resultados_inminentes(self):
        manana = (dt.date.today() + dt.timedelta(days=1)).strftime("%b %d") + " AMC"
        self.assertEqual(self.v(orden(), val=dict(BARATA, resultados=manana))[0], "BLOQUEADA")

    def test_apertura_precio_escapado_o_en_stop(self):
        self.assertEqual(self.v(orden(), precio=104.0)[0], "BLOQUEADA")
        self.assertEqual(self.v(orden(), precio=94.0)[0], "BLOQUEADA")
        self.assertEqual(self.v(orden(), precio=101.0)[0], "APROBADA")

    def test_vender_solo_lo_que_hay(self):
        o = {"ticker": "NVDA", "accion": "vender"}
        self.assertEqual(self.v(o, res(posiciones=("NVDA",)))[0], "APROBADA")
        self.assertEqual(self.v(o)[0], "BLOQUEADA")


class Stops(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cartera.OPERACIONES = Path(self.tmp.name) / "operaciones.jsonl"
        self.ayer = (dt.date.today() - dt.timedelta(days=3)).isoformat()
        self.c = {"caja_usd": 0.0, "posiciones": {"NVDA": {
            "cantidad": 2, "precio_entrada": 100.0, "fecha_entrada": self.ayer, "stop": 95.0, "objetivo": 110.0,
            "riesgo_usd": 10.0, "motivo": "", "via": "en seco", "stop_en_broker": False, "revisado_hasta": None}}}

    def tearDown(self):
        self.tmp.cleanup()

    def velas(self, *dias):
        """dias: (desplazamiento desde la entrada, apertura, máximo, mínimo, cierre)"""
        base = dt.datetime.fromisoformat(self.ayer).replace(tzinfo=dt.timezone.utc)
        t = [int((base + dt.timedelta(days=d[0], hours=14)).timestamp()) for d in dias]
        return {"NVDA": {"t": t, "o": [d[1] for d in dias], "h": [d[2] for d in dias],
                         "l": [d[3] for d in dias], "c": [d[4] for d in dias]}}

    def test_dia_de_entrada_ignora_el_minimo(self):
        self.assertEqual(cartera.revisar_stops(self.c, self.velas((0, 100, 101, 90, 99))), [])

    def test_stop_normal(self):
        v = cartera.revisar_stops(self.c, self.velas((0, 100, 101, 99, 100), (1, 99, 100, 94, 96)))
        self.assertEqual((v[0]["precio"], v[0]["motivo"]), (95.0, "stop"))
        self.assertEqual(v[0]["resultado_usd"], -10.0)
        self.assertEqual(self.c["caja_usd"], 190.0)

    def test_hueco_bajo_el_stop_vende_a_la_apertura(self):
        v = cartera.revisar_stops(self.c, self.velas((0, 100, 101, 99, 100), (1, 90, 92, 88, 91)))
        self.assertEqual(v[0]["precio"], 90.0)
        self.assertIn("hueco", v[0]["motivo"])

    def test_objetivo(self):
        v = cartera.revisar_stops(self.c, self.velas((0, 100, 101, 99, 100), (1, 101, 111, 100, 108)))
        self.assertEqual((v[0]["precio"], v[0]["resultado_usd"]), (110.0, 20.0))

    def test_stop_y_objetivo_el_mismo_dia_gana_el_stop(self):
        v = cartera.revisar_stops(self.c, self.velas((0, 100, 101, 99, 100), (1, 100, 112, 94, 105)))
        self.assertEqual(v[0]["motivo"], "stop")

    def test_no_vende_dos_veces(self):
        velas = self.velas((0, 100, 101, 99, 100), (1, 99, 100, 94, 96))
        cartera.revisar_stops(self.c, velas)
        self.assertEqual(cartera.revisar_stops(self.c, velas), [])


if __name__ == "__main__":
    unittest.main()
