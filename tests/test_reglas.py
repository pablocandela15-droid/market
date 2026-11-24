"""Pruebas de las reglas de órdenes y de la vigilancia de stops. Sin red.

    python3 -m unittest discover -s tests -v
"""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

os.environ["BOT_PRUEBA"] = "1"

from bot import cartera, gasto, mirofish, ordenes, tarea  # noqa: E402
from bot import informe as inf  # noqa: E402

# Universo fijo para las pruebas: así no dependen de descargar la lista del S&P 500.
ordenes.valores_del_universo = lambda: {"NVDA": "Tecnología", "JPM": "Finanzas"}

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

    def test_resultados_segun_el_calendario(self):
        hoy = dt.date.today()
        cerca = (hoy + dt.timedelta(days=1)).isoformat()
        lejos = (hoy + dt.timedelta(days=30)).isoformat()
        ayer = (hoy - dt.timedelta(days=1)).isoformat()
        self.assertEqual(self.v(orden(), val=dict(BARATA, fecha_de_resultados=cerca))[0], "BLOQUEADA")
        self.assertEqual(self.v(orden(), val=dict(BARATA, fecha_de_resultados=lejos))[0], "APROBADA")
        self.assertEqual(self.v(orden(), val=dict(BARATA, fecha_de_resultados=ayer))[0], "APROBADA")

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


class Informe(unittest.TestCase):
    """El informe no recorta textos y la sección del mundo agrupa por temas."""

    def test_el_mundo_hoy(self):
        largo = "Frase larga que antes se habría cortado con puntos suspensivos. " * 6
        m = {"resumen": "El día en dos frases.", "noticias": [
            {"categoria": "Tecnología", "titular": "Una empresa presenta un chip", "que_ha_pasado": largo,
             "por_que_importa": "Cambia el mercado.", "fuente": "Medio, 24 nov"},
            {"categoria": "Política y geopolítica", "titular": "Dos países firman un acuerdo", "que_ha_pasado": "Pasó ayer.",
             "por_que_importa": "Baja la tensión.", "fuente": "Agencia"},
            {"categoria": "Tecnología", "titular": "Otra noticia de tecnología", "que_ha_pasado": "Pasó hoy.",
             "por_que_importa": "Importa.", "fuente": "Medio"}]}
        h = tarea._el_mundo_hoy(m)
        self.assertIn(inf.e(largo.strip()), h)                       # el texto entra entero
        self.assertNotIn("…", h)
        self.assertEqual(h.count("class='cat'"), 2)                  # un rótulo por tema, no por noticia
        self.assertLess(h.index("Dos países"), h.index("Una empresa"))    # política va antes que tecnología
        self.assertEqual(tarea._el_mundo_hoy(None), "")
        self.assertEqual(tarea._el_mundo_hoy({"noticias": []}), "")

    def test_las_tablas_no_recortan(self):
        motivo = "Motivo muy largo que tiene que salir completo en la tabla del informe. " * 4
        h = inf.bloque_operaciones([{"hora": "15:45", "ticker": "NVDA", "accion": "compra", "cantidad": 2, "precio": 100.0,
                                     "importe_usd": 200.0, "motivo": motivo}])
        self.assertIn(inf.e(motivo), h)
        self.assertFalse(hasattr(inf, "corta"))



class NivelDeLaManana(unittest.TestCase):
    """Reparto del crédito entre las mañanas: tope sin mercado y nivel alto para los lunes."""
    C = {"alto": 3.0, "medio": 1.2, "bajo": 0.8}

    def n(self, dinero, dias, mercado=True, lunes=False, lunes_despues=0):
        return gasto.elegir_nivel(dinero, dias, mercado, lunes, lunes_despues, self.C)[0]

    def test_con_credito_de_sobra_todo_va_en_alto(self):
        self.assertEqual(self.n(100.0, 10, lunes_despues=1), "alto")

    def test_sin_mercado_nunca_pasa_de_medio(self):
        self.assertEqual(self.n(100.0, 10, mercado=False), "medio")

    def test_alto_solo_si_los_demas_dias_siguen_teniendo_analisis(self):
        # 10 mañanas: una en alto y nueve en medio cuestan 13,8
        self.assertEqual(self.n(13.8, 10), "alto")
        self.assertEqual(self.n(13.7, 10), "medio")

    def test_el_alto_se_guarda_para_los_lunes(self):
        # Sobra para un día en alto, pero queda un lunes por delante: hoy va en medio y el lunes en alto.
        self.assertEqual(self.n(14.0, 10, lunes_despues=1), "medio")
        self.assertEqual(self.n(14.0, 10, lunes=True, lunes_despues=1), "alto")
        self.assertEqual(self.n(16.0, 10, lunes_despues=1), "alto")       # sobra para hoy y para el lunes

    def test_va_bajando_de_nivel_y_al_final_sin_modelo(self):
        self.assertEqual(self.n(9.0, 10), "medio")       # 1,2 + 9 × 0,8 = 8,4
        self.assertEqual(self.n(8.0, 10), "bajo")
        self.assertEqual(self.n(0.5, 10), "sin_modelo")



class ExcepcionDeUnDia(unittest.TestCase):
    """Los ajustes de MiroFish de "solo_el" valen su fecha y ninguna otra."""

    def test_solo_ese_dia(self):
        original = mirofish.reglas
        mirofish.reglas = lambda: {"mirofish": {"modelo": "claude-haiku-5-5", "tope_usd": 9.0, "max_rondas": 120,
                                                "solo_el": {"_nota": "x", "2026-11-29": {"modelo": "claude-sonnet-5-5",
                                                                                         "tope_usd": 32.0, "max_rondas": 48}}}}
        try:
            domingo, siguiente = mirofish._cfg(dt.date(2026, 11, 29)), mirofish._cfg(dt.date(2026, 12, 6))
        finally:
            mirofish.reglas = original
        self.assertEqual((domingo["modelo"], domingo["tope_usd"], domingo["max_rondas"]), ("claude-sonnet-5-5", 32.0, 48))
        self.assertEqual((siguiente["modelo"], siguiente["tope_usd"], siguiente["max_rondas"]), ("claude-haiku-5-5", 9.0, 120))
        self.assertNotIn("solo_el", domingo)
