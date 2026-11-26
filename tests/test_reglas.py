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
# Perfil agresivo (26 nov de 2026): más riesgo por compra y más ganancia exigida. Las cifras de config/reglas.json
# que cambian respecto a las de arriba.
AGRESIVAS = {
    "riesgo": dict(REGLAS["riesgo"], riesgo_por_operacion_pct=1.5, max_posicion_pct=30, max_compras_dia=2,
                   min_recompensa_riesgo=2.0, min_recompensa_riesgo_apertura=1.5, max_hueco_apertura_pct=4),
    "valoracion": dict(REGLAS["valoracion"], peg_maximo=2.5),
}


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



class PorQueEnLaApertura(unittest.TestCase):
    """Cuando la apertura no hace lo previsto, o hace algo no previsto, el informe dice por qué."""

    def v(self, precio=None, **o):
        return ordenes.validar(orden(**o), res(), BARATA, precio, REGLAS)

    def test_precio_algo_mas_caro_ya_no_compensa(self):
        # entrada 100, stop 95, objetivo 108: gana 1,6 veces lo arriesgado; a 100,50 ya solo 1,36
        veredicto, motivos, _ = self.v(100.5, objetivo=108.0)
        self.assertEqual(veredicto, "BLOQUEADA")
        self.assertIn("se arriesgan 5,50 $ por acción", motivos[0])
        self.assertIn("para ganar 7,50 $", motivos[0])
        self.assertIn("exige al menos 1,5", motivos[0])

    def test_precio_escapado_y_precio_en_el_stop(self):
        self.assertIn("por encima de los 100,00 $", self.v(104.0)[1][0])
        self.assertEqual(len(self.v(104.0)[1]), 1)                       # un solo motivo, no dos que digan lo mismo
        self.assertIn("venta de protección", self.v(94.0)[1][0])

    def test_la_manana_avisa_si_el_margen_es_justo(self):
        veredicto, avisos, _ = self.v(objetivo=108.0)
        self.assertEqual(veredicto, "APROBADA")
        self.assertTrue(any("margen justo" in x for x in avisos))
        self.assertFalse(any("margen justo" in x for x in self.v(objetivo=112.0)[1]))

    def test_el_informe_distingue_lo_previsto_de_lo_no_previsto(self):
        h = tarea._lo_hecho_en_la_apertura([
            {"ticker": "AAA", "que": "no comprada", "previsto": True, "entrada_prevista": 10.0, "precio": 10.4, "stop": 9.5,
             "objetivo": 11.0, "por_que": "Se ha encarecido."},
            {"ticker": "BBB", "que": "comprada", "previsto": False, "cantidad": 3, "precio": 20.0, "importe_usd": 60.0, "stop": 19.0,
             "objetivo": 23.0, "por_que": "Ha superado su máximo.", "que_esperamos": "Esperamos que siga.", "cuando_saldriamos": "Saldríamos si cae."}],
            True, "")
        self.assertIn("AAA · no se ha comprado, aunque estaba en el plan", h)
        self.assertIn("Se ha encarecido.", h)
        self.assertIn("BBB · comprada, aunque no estaba en el plan de la mañana", h)
        self.assertIn("<b>Esperamos</b> que siga.", h)
        self.assertNotIn("No había ninguna compra prevista", h)

    def test_sin_compras_previstas_ni_hechas_tambien_se_explica(self):
        h = tarea._lo_hecho_en_la_apertura([], False, "el mercado abre débil y ningún valor vigilado ha dado señal")
        self.assertIn("No había ninguna compra prevista y no se ha hecho ninguna.", h)
        self.assertIn("El mercado abre débil y ningún valor vigilado ha dado señal.", h)


class PerfilAgresivo(unittest.TestCase):
    """Más riesgo por compra para ganar más: cada apuesta arriesga más y tiene que poder ganar el doble."""

    def v(self, precio=None, previsto=True, val=BARATA, **o):
        return ordenes.validar(orden(**o), res(), val, precio, AGRESIVAS, previsto=previsto)

    def test_arriesga_el_uno_y_medio_por_ciento(self):
        cantidad, _ = ordenes.dimensionar(100.0, 94.0, res(), AGRESIVAS)       # 15 $ de riesgo / 6 $ por acción = 2,5
        self.assertEqual(cantidad, 2)
        cantidad, nota = ordenes.dimensionar(100.0, 97.0, res(), AGRESIVAS)    # pediría 5 acciones = 500 $
        self.assertEqual(cantidad, 3)
        self.assertIn("30", nota)

    def test_la_manana_pide_ganar_el_doble_de_lo_arriesgado(self):
        veredicto, motivos, _ = self.v(objetivo=108.0)                         # 1,6 veces: antes valía
        self.assertEqual(veredicto, "BLOQUEADA")
        self.assertIn("el mínimo es 2,0", motivos[0])
        self.assertEqual(ordenes.validar(orden(objetivo=108.0), res(), BARATA, None, REGLAS)[0], "APROBADA")
        self.assertEqual(self.v(objetivo=112.0)[0], "APROBADA")                # 2,4 veces
        self.assertFalse(any("margen justo" in x for x in self.v(objetivo=112.0)[1]))
        # stop al 3 %: gana justo el doble, pero un 1 % más cara ya solo ganaría 1,25 veces
        veredicto, avisos, _ = self.v(stop=97.0, objetivo=106.0)
        self.assertEqual(veredicto, "APROBADA")
        self.assertTrue(any("margen justo" in x for x in avisos))

    def test_abrir_algo_mas_cara_no_tira_la_compra_prevista(self):
        plan = dict(objetivo=111.0)                                            # 2,2 veces a 100 $
        self.assertEqual(self.v(100.8, **plan)[0], "APROBADA")                 # a 100,80 $ gana 1,8 veces: basta con 1,5
        veredicto, motivos, _ = self.v(102.0, **plan)                          # a 102 $ ya solo 1,3 veces
        self.assertEqual(veredicto, "BLOQUEADA")
        self.assertIn("se exige al menos 1,5", motivos[0])

    def test_a_la_compra_no_prevista_se_le_exige_la_proporcion_completa(self):
        self.assertEqual(self.v(100.0, previsto=True, objetivo=108.0)[0], "APROBADA")     # 1,6 veces: vale si venía en el plan
        self.assertEqual(self.v(100.0, previsto=False, objetivo=108.0)[0], "BLOQUEADA")
        self.assertEqual(self.v(100.0, previsto=False, objetivo=111.0)[0], "APROBADA")    # 2,2 veces

    def test_admite_valoraciones_y_huecos_algo_mayores(self):
        cara = dict(BARATA, peg=2.3)
        self.assertEqual(self.v(val=cara, objetivo=112.0)[0], "APROBADA")
        self.assertEqual(ordenes.validar(orden(objetivo=112.0), res(), cara, None, REGLAS)[0], "BLOQUEADA")
        lejos = dict(objetivo=125.0)                                           # abre un 3,5 % por encima de lo previsto
        self.assertEqual(self.v(103.5, **lejos)[0], "APROBADA")
        self.assertEqual(ordenes.validar(orden(**lejos), res(), BARATA, 103.5, REGLAS)[0], "BLOQUEADA")

    def test_las_reglas_en_vigor_son_las_del_perfil_agresivo(self):
        r = ordenes.reglas()
        for k, v in AGRESIVAS["riesgo"].items():
            self.assertEqual(r["riesgo"][k], v, k)
        self.assertEqual(r["valoracion"]["peg_maximo"], 2.5)
        self.assertEqual(r["riesgo"]["max_posiciones"], 5)                     # lo que no cambia
        self.assertEqual(r["valoracion"]["dias_sin_comprar_antes_de_resultados"], 2)
        self.assertFalse(r["operativa"]["margen"] or r["operativa"]["cortos"] or r["operativa"]["opciones"])

    def test_los_guiones_no_empujan_a_invertir_todo(self):
        guiones = Path(tarea.__file__).resolve().parent.parent / "tareas" / "guiones"
        estratega = (guiones / "estratega.md").read_text(encoding="utf-8")
        apertura = (guiones / "apertura.md").read_text(encoding="utf-8")
        self.assertIn("No hay ninguna obligación de operar ni de estar invertido", estratega)
        self.assertIn("para ganar más dinero", estratega)
        self.assertNotIn("está para estar invertida", estratega)
        self.assertNotIn("convicción media basta", estratega)
        self.assertIn("No hay ninguna obligación de comprar", apertura)


class BrokerFalso:
    """Alpaca de mentira: vende al precio que se le diga, o deja la venta sin ejecutar."""

    def __init__(self, precio=None, estado="filled", posiciones=()):
        self.precio, self.estado, self._pos, self.cerradas = precio, estado, list(posiciones), []

    def cerrar(self, simbolo):
        self.cerradas.append(simbolo)
        return {"id": "venta-1"}

    def esperar_ejecucion(self, orden_id):
        return self.orden(orden_id)

    def orden(self, orden_id):
        return {"id": orden_id, "status": self.estado, "filled_avg_price": self.precio and str(self.precio)}

    def posiciones(self):
        return self._pos


class VentasAlPrecioReal(unittest.TestCase):
    """Con Alpaca, una venta se anota al precio al que el bróker la ejecuta, no al del stop. El 25 nov de 2026
    el bot anotó la venta de AMZN a 242,00 $ (su stop) y Alpaca la había ejecutado a 237,968 $."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cartera.OPERACIONES = Path(self.tmp.name) / "operaciones.jsonl"
        hace_dias = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        self.c = {"caja_usd": 0.0, "pendientes": {}, "posiciones": {"AMZN": {
            "cantidad": 0.6985, "precio_entrada": 257.63, "fecha_entrada": hace_dias, "stop": 242.0, "objetivo": 284.0,
            "riesgo_usd": 10.92, "motivo": "", "via": "alpaca", "orden_id": "compra-1", "stop_en_broker": False,
            "revisado_hasta": None}}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_se_anota_el_precio_de_alpaca_y_no_el_del_stop(self):
        v = ordenes.ejecutar_venta(self.c, "AMZN", 242.0, "stop", BrokerFalso(237.968))
        self.assertEqual((v["precio"], v["precio_previsto"]), (237.968, 242.0))
        self.assertEqual((v["resultado_usd"], v["deslizamiento_usd"], v["multiplo_riesgo"]), (-13.73, -2.82, -1.26))
        self.assertEqual(self.c["caja_usd"], 166.22)
        self.assertNotIn("AMZN", self.c["posiciones"])

    def test_con_la_bolsa_cerrada_queda_pendiente_y_se_anota_al_ejecutarse(self):
        cerrado = BrokerFalso(estado="accepted")
        with self.assertRaises(ordenes.ErrorFuente):
            ordenes.ejecutar_venta(self.c, "AMZN", 242.0, "stop", cerrado)
        self.assertIn("venta_pendiente", self.c["posiciones"]["AMZN"])       # sigue en cartera y sin anotar
        self.assertEqual(self.c["caja_usd"], 0.0)
        self.assertEqual(cartera.detectar_salidas(self.c, {"AMZN": {"t": [], "o": [], "h": [], "l": [], "c": []}}), [])
        self.assertEqual(ordenes.sincronizar(self.c, cerrado), [])           # aún sin ejecutar: nada cambia
        hechos = ordenes.sincronizar(self.c, BrokerFalso(239.5))             # al día siguiente Alpaca la ejecuta
        self.assertEqual((hechos[0]["precio"], hechos[0]["motivo"]), (239.5, "stop"))
        self.assertNotIn("AMZN", self.c["posiciones"])

    def test_si_alpaca_cancela_la_venta_se_vuelve_a_vigilar(self):
        with self.assertRaises(ordenes.ErrorFuente):
            ordenes.ejecutar_venta(self.c, "AMZN", 242.0, "stop", BrokerFalso(estado="accepted"))
        ordenes.sincronizar(self.c, BrokerFalso(estado="canceled"))
        self.assertNotIn("venta_pendiente", self.c["posiciones"]["AMZN"])

    def test_sin_conexion_no_se_anota_una_venta_que_no_ha_ocurrido(self):
        with self.assertRaises(ordenes.ErrorFuente):
            ordenes.ejecutar_venta(self.c, "AMZN", 242.0, "stop", None)
        self.assertIn("AMZN", self.c["posiciones"])

    def test_en_seco_se_anota_al_precio_previsto(self):
        self.c["posiciones"]["AMZN"]["via"] = "en seco"
        self.assertEqual(ordenes.ejecutar_venta(self.c, "AMZN", 242.0, "stop", None)["precio"], 242.0)

    def test_la_vigilancia_vende_en_alpaca_y_anota_el_precio_real(self):
        base = dt.datetime.fromisoformat(self.c["posiciones"]["AMZN"]["fecha_entrada"]).replace(tzinfo=dt.timezone.utc)
        t = [int((base + dt.timedelta(days=d, hours=14)).timestamp()) for d in (0, 1)]
        velas = {"AMZN": {"t": t, "o": [258, 250], "h": [260, 251], "l": [255, 236], "c": [257, 238]}}
        api, avisos = BrokerFalso(237.968), []
        hechos = tarea._vigilar(self.c, velas, api, avisos)
        self.assertEqual((api.cerradas, hechos[0]["precio"], hechos[0]["precio_previsto"]), (["AMZN"], 237.968, 242.0))
        self.assertEqual(avisos, [])

    def test_avisa_si_la_cartera_no_cuadra_con_alpaca(self):
        igual = BrokerFalso(posiciones=[{"symbol": "AMZN", "qty": "0.6985"}])
        self.assertEqual(ordenes.descuadres(self.c, igual), [])
        distinto = BrokerFalso(posiciones=[{"symbol": "AMZN", "qty": "0.5"}, {"symbol": "TSLA", "qty": "1"}])
        avisos = ordenes.descuadres(self.c, distinto)
        self.assertEqual(len(avisos), 2)
        self.assertIn("Alpaca tiene 0,5000", avisos[0])
        self.assertIn("TSLA", avisos[1])
        self.assertIn("no está en Alpaca", ordenes.descuadres(self.c, BrokerFalso())[0])
