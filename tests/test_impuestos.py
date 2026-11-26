"""Pruebas de los impuestos, las gráficas, el informe semanal y el de la renta. Sin red.

    python3 -m unittest discover -s tests -v
"""
import datetime as dt
import os
import unittest

os.environ["BOT_PRUEBA"] = "1"

from bot import graficas, impuestos, renta, semanal  # noqa: E402
from bot import informe as inf  # noqa: E402

TABLA = {"2026-01-10": 1.00, "2026-02-10": 1.25, "2026-06-01": 1.10}
HOY = dt.date(2026, 12, 1)


def op(fecha, ticker, accion, cantidad, precio, **extra):
    return dict({"fecha": fecha, "hora": "15:46", "ticker": ticker, "accion": accion, "cantidad": cantidad,
                 "precio": precio, "importe_usd": round(cantidad * precio, 2), "motivo": "prueba"}, **extra)


class Escala(unittest.TestCase):
    def test_tramos_de_2026(self):
        self.assertEqual(impuestos.cuota(100), 19.0)
        self.assertEqual(impuestos.cuota(6000), 1140.0)
        self.assertEqual(impuestos.cuota(10000), 1140.0 + 4000 * 0.21)
        self.assertEqual(impuestos.cuota(350000), 86880.0)          # 1.140 + 9.240 + 34.500 + 27.000 + 15.000
        self.assertEqual(impuestos.cuota(-50), 0.0)

    def test_el_desglose_suma_la_cuota(self):
        d = impuestos.desglose(60000)
        self.assertEqual([x["tipo_pct"] for x in d], [19, 21, 23])
        self.assertAlmostEqual(sum(x["cuota_eur"] for x in d), impuestos.cuota(60000))
        self.assertEqual(impuestos.desglose(0), [])


class VentasEnEuros(unittest.TestCase):
    def test_cada_pata_a_su_cambio(self):
        # compra 10 acciones a 100 $ con el euro a 1,00 (1.000 €) y vende a 110 $ con el euro a 1,25 (880 €):
        # gana 100 $ y, por la subida del euro, pierde 120 €
        v, = impuestos.ventas([op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 110.0)], TABLA)
        self.assertEqual((v["coste_eur"], v["ingreso_eur"], v["resultado_eur"], v["resultado_usd"]), (1000.0, 880.0, -120.0, 100.0))
        self.assertEqual((v["fecha_compra"], v["eurusd_compra"], v["eurusd_venta"]), ("2026-01-10", 1.0, 1.25))

    def test_sin_cambio_de_ese_dia_vale_el_anterior(self):
        self.assertEqual(impuestos.cambio_del("2026-01-20", TABLA), 1.00)
        self.assertEqual(impuestos.cambio_del("2026-01-01", TABLA), 1.00)      # antes del primero: el siguiente
        self.assertIsNone(impuestos.cambio_del("2026-01-01", {}))

    def test_con_la_tabla_oficial_no_se_usa_el_cambio_anotado_por_el_bot(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0, eurusd=2.0), op("2026-02-10", "AAA", "venta", 10, 100.0, eurusd=2.0)]
        self.assertEqual(impuestos.ventas(ops, TABLA)[0]["coste_eur"], 500.0)
        self.assertEqual(impuestos.ventas(ops, TABLA, oficial=True)[0]["coste_eur"], 1000.0)

    def test_se_venden_primero_las_mas_antiguas(self):
        ops = [op("2026-01-10", "AAA", "compra", 5, 100.0), op("2026-02-10", "AAA", "compra", 5, 200.0),
               op("2026-06-01", "AAA", "venta", 6, 150.0)]
        v, = impuestos.ventas(ops, {"2026-01-10": 1.0, "2026-02-10": 1.0, "2026-06-01": 1.0})
        self.assertEqual(v["coste_usd"], 5 * 100.0 + 1 * 200.0)
        self.assertEqual(v["fecha_compra"], "2026-01-10")


class ReglaDeLosDosMeses(unittest.TestCase):
    UNO = {"2026-01-10": 1.0}

    def test_recomprar_el_mismo_valor_aplaza_la_perdida(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 90.0),
               op("2026-03-01", "AAA", "compra", 10, 85.0)]
        v, = impuestos.ventas(ops, self.UNO)
        self.assertTrue(v["aplazada"])
        self.assertIsNone(v["fecha_fiscal"])
        self.assertEqual(impuestos.anual([v], 2026)["perdidas_eur"], 0.0)
        e = impuestos.estado({}, hoy=HOY, ops=ops, tabla=self.UNO)
        self.assertEqual((e["neto_eur"], e["perdidas_aplazadas_eur"]), (0.0, 100.0))

    def test_cuenta_cuando_se_venden_las_recompradas(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 90.0),
               op("2026-03-01", "AAA", "compra", 10, 85.0), op("2026-09-01", "AAA", "venta", 10, 95.0)]
        primera, segunda = impuestos.ventas(ops, self.UNO)
        self.assertEqual(primera["fecha_fiscal"], "2026-09-01")
        self.assertFalse(segunda["aplazada"])
        self.assertEqual(impuestos.anual([primera, segunda], 2026)["neto_eur"], 0.0)       # −100 + 100

    def test_comprar_otro_valor_o_pasados_dos_meses_no_aplaza(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 90.0),
               op("2026-02-11", "BBB", "compra", 10, 50.0), op("2026-04-20", "AAA", "compra", 10, 85.0)]
        self.assertFalse(impuestos.ventas(ops, self.UNO)[0]["aplazada"])

    def test_una_ganancia_nunca_se_aplaza(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 120.0),
               op("2026-02-11", "AAA", "compra", 10, 121.0)]
        self.assertFalse(impuestos.ventas(ops, self.UNO)[0]["aplazada"])


class CuentaDelAnio(unittest.TestCase):
    UNO = {"2025-01-10": 1.0}

    def test_las_perdidas_restan_de_las_ganancias_del_mismo_anio(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 150.0),
               op("2026-03-10", "BBB", "compra", 10, 100.0), op("2026-04-10", "BBB", "venta", 10, 80.0)]
        e = impuestos.estado({}, hoy=HOY, ops=ops, tabla=self.UNO)
        self.assertEqual((e["ganancias_eur"], e["perdidas_eur"], e["neto_eur"], e["impuesto_eur"]), (500.0, 200.0, 300.0, 57.0))
        self.assertEqual(e["despues_de_impuestos_eur"], 243.0)
        self.assertEqual(e["tipo_efectivo_pct"], 19.0)

    def test_la_perdida_de_un_anio_resta_del_siguiente(self):
        ops = [op("2025-01-10", "AAA", "compra", 10, 100.0), op("2025-02-10", "AAA", "venta", 10, 70.0),
               op("2026-03-10", "BBB", "compra", 10, 100.0), op("2026-04-10", "BBB", "venta", 10, 150.0)]
        vs = impuestos.ventas(ops, self.UNO)
        a25, a26 = impuestos.anual(vs, 2025), impuestos.anual(vs, 2026)
        self.assertEqual((a25["impuesto_eur"], a25["perdidas_para_anios_siguientes_eur"]), (0.0, 300.0))
        self.assertEqual((a26["compensado_de_anios_anteriores_eur"], a26["base_eur"], a26["impuesto_eur"]), (300.0, 200.0, 38.0))

    def test_una_perdida_caduca_a_los_cuatro_anios(self):
        ops = [op("2021-01-10", "AAA", "compra", 10, 100.0), op("2021-02-10", "AAA", "venta", 10, 70.0),
               op("2026-03-10", "BBB", "compra", 10, 100.0), op("2026-04-10", "BBB", "venta", 10, 150.0)]
        self.assertEqual(impuestos.anual(impuestos.ventas(ops, {"2021-01-10": 1.0}), 2026)["base_eur"], 500.0)

    def test_lo_que_sigue_en_cartera_y_lo_que_costaria_venderlo(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 90.0)]      # −100 €
        pos = [{"ticker": "BBB", "cantidad": 10, "entrada": 100.0, "precio": 130.0, "fecha_entrada": "2026-03-01"},
               {"ticker": "CCC", "cantidad": 10, "entrada": 100.0, "precio": 95.0, "fecha_entrada": "2026-03-01"}]
        sv = impuestos.estado({}, pos, 1.0, hoy=HOY, ops=ops, tabla={"2026-01-10": 1.0})["sin_vender"]
        self.assertEqual(sv["resultado_eur"], 250.0)
        self.assertEqual(sv["impuesto_si_se_vende_todo_hoy_eur"], 28.5)              # (300 − 50 − 100) al 19 %
        self.assertEqual({p["ticker"]: p["impuesto_si_se_vende_eur"] for p in sv["posiciones"]}, {"BBB": 38.0, "CCC": 0.0})

    def test_el_impuesto_que_anaden_las_ventas_de_un_periodo(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 150.0),
               op("2026-03-10", "BBB", "compra", 10, 100.0), op("2026-04-10", "BBB", "venta", 10, 80.0)]
        gana = impuestos.periodo({}, "2026-02-01", "2026-02-28", hoy=HOY, ops=ops, tabla=self.UNO)
        pierde = impuestos.periodo({}, "2026-04-01", "2026-04-30", hoy=HOY, ops=ops, tabla=self.UNO)
        self.assertEqual((gana["resultado_eur"], gana["impuesto_eur"]), (500.0, 57.0))      # sin ella el año acabaría en pérdida
        self.assertEqual((pierde["resultado_eur"], pierde["impuesto_eur"], pierde["ahorro_de_impuestos_eur"]), (-200.0, 0.0, 38.0))


class EnLosInformes(unittest.TestCase):
    OPS = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 150.0)]

    def test_ganancias_tanto_impuestos_tanto(self):
        e = impuestos.estado({}, [], 1.0, hoy=HOY, ops=self.OPS, tabla={"2026-01-10": 1.0})
        p = impuestos.periodo({}, "2026-02-10", "2026-02-10", hoy=HOY, ops=self.OPS, tabla={"2026-01-10": 1.0})
        h = inf.bloque_impuestos(e, p, "hoy")
        self.assertIn("Las ventas de hoy dejan una ganancia de 500,00&nbsp;€. Impuesto estimado: 95,00&nbsp;€. Te quedan 405,00&nbsp;€.", h)
        self.assertIn("abril y junio de 2027", h)
        self.assertIn("Impuestos estimados de 2026", h)
        self.assertNotIn("…", inf.linea_impuestos(e))
        self.assertIn("Impuesto estimado: 95,00&nbsp;€", inf.linea_impuestos(e))

    def test_una_perdida_no_paga_y_queda_guardada(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 90.0)]
        e = impuestos.estado({}, [], 1.0, hoy=HOY, ops=ops, tabla={"2026-01-10": 1.0})
        h = inf.bloque_impuestos(e, impuestos.periodo({}, "2026-02-10", "2026-02-10", hoy=HOY, ops=ops, tabla={"2026-01-10": 1.0}), "hoy", breve=True)
        self.assertIn("Una pérdida no paga impuestos", h)
        self.assertIn("No hay impuesto que pagar. Quedan 100,00&nbsp;€ de pérdidas guardadas", h)

    def test_sin_ventas(self):
        e = impuestos.estado({}, [], 1.0, hoy=HOY, ops=[], tabla={})
        self.assertIn("todavía no se ha vendido nada", inf.linea_impuestos(e))
        self.assertIn("Hoy no se ha vendido nada", inf.bloque_impuestos(e, {"ventas": []}, "hoy"))


class Graficas(unittest.TestCase):
    def test_la_evolucion_lleva_leyenda_con_el_ultimo_valor_y_un_solo_eje(self):
        s = graficas.evolucion(["23 nov", "24 nov", "25 nov"], [("Cartera", [1087.48, 1089.54, 1076.53]),
                                                                ("Mismo dinero en el S&P 500", [1087.04, 1081.62, 1078.87])])
        self.assertTrue(s.startswith("<svg") and s.endswith("</svg>"))
        self.assertIn("Cartera  1.076,53 $", s)
        self.assertIn("Mismo dinero en el S&amp;P 500  1.078,87 $", s)
        self.assertEqual(s.count("<polyline"), 2)
        self.assertNotIn("nan", s.lower())
        self.assertNotIn("stroke-dasharray", s)

    def test_un_solo_punto_o_ninguno_no_rompe(self):
        self.assertIn("<circle", graficas.evolucion(["23 nov"], [("Cartera", [1000.0])]))
        self.assertEqual(graficas.evolucion([], []), "")
        self.assertEqual(graficas.resultados([]), "")
        self.assertEqual(graficas.reparto([("Caja", 0)]), "")

    def test_ganar_o_perder_va_escrito_con_su_signo(self):
        s = graficas.resultados([("AMZN", "vendida", -13.73), ("HII", "en cartera", 8.83)])
        self.assertIn("−13,73 $", s)
        self.assertIn("+8,83 $", s)
        self.assertIn(graficas.GANA, s)
        self.assertIn(graficas.PIERDE, s)
        for texto in s.split("<text")[1:]:          # ningún texto va en el color de la barra
            self.assertNotIn(graficas.GANA, texto.split(">")[0])
            self.assertNotIn(graficas.PIERDE, texto.split(">")[0])

    def test_el_reparto_da_cada_importe_y_la_caja_va_en_gris(self):
        s = graficas.reparto([("HII", 206.04), ("PCG", 298.83), ("Caja", 50.16)])
        self.assertEqual(s.count('rx="4"'), 3)
        self.assertIn("HII  206,04 $", s)
        self.assertIn(graficas.NEUTRO, s)
        estrecha = graficas.reparto([("AAA", 970.0), ("Caja", 30.0)], ancho=300)
        self.assertIn("Caja  30,00 $ (3 %)", estrecha)                 # el porcentaje que no cabe en su trozo va en la leyenda


class Semanal(unittest.TestCase):
    def test_que_semana_se_revisa(self):
        self.assertEqual(semanal.semana(dt.date(2026, 11, 28)), ("2026-11-23", "2026-11-29"))       # sábado: la que acaba
        self.assertEqual(semanal.semana(dt.date(2026, 11, 29)), ("2026-11-23", "2026-11-29"))
        self.assertEqual(semanal.semana(dt.date(2026, 11, 30)), ("2026-11-23", "2026-11-29"))       # lunes: la anterior
        self.assertEqual(semanal.semana(dt.date(2026, 11, 26), en_curso=True), ("2026-11-23", "2026-11-29"))
        self.assertEqual(semanal.semana(dt.date(2026, 11, 26)), ("2026-11-16", "2026-11-22"))

    def test_las_cuentas_no_toman_la_aportacion_por_ganancia(self):
        c = {"aportaciones": [{"fecha": "2026-11-23", "usd": 1000.0}, {"fecha": "2026-12-01", "usd": 200.0}],
             "valor_diario": [{"fecha": "2026-11-27", "valor_usd": 1010.0, "referencia_usd": 990.0},
                              {"fecha": "2026-12-04", "valor_usd": 1240.0, "referencia_usd": 1200.0}]}
        primera = semanal.cuentas(c, "2026-11-23", "2026-11-29")
        self.assertEqual((primera["inicio_usd"], primera["resultado_usd"], primera["resultado_pct"], primera["referencia_pct"]),
                         (1000.0, 10.0, 1.0, -1.0))
        segunda = semanal.cuentas(c, "2026-11-30", "2026-12-06")
        self.assertEqual((segunda["inicio_usd"], segunda["resultado_usd"], segunda["diferencia_usd"]), (1210.0, 30.0, 20.0))

    def test_las_lecciones_se_guardan_conservan_su_fecha_y_no_se_borran_si_falla_la_revision(self):
        semanal.VIGENTES.unlink(missing_ok=True)
        self.assertEqual(semanal.lecciones_para_hoy()["lecciones_vigentes"], [])
        semanal.guardar_lecciones({"lecciones": [{"texto": "No comprar con el índice cayendo un 2 %.", "origen": "25 nov", "estado": "nueva"}],
                                   "retiradas": []}, "2026-11-28", "2026-11-23", "2026-11-29")
        semanal.guardar_lecciones({"lecciones": [{"texto": "No comprar con el índice cayendo un 2 %.", "origen": "25 nov", "estado": "se mantiene"},
                                                 {"texto": "Otra.", "origen": "3 dic", "estado": "nueva"}] + [
                                                     {"texto": "Relleno %d." % i, "origen": "", "estado": "nueva"} for i in range(10)],
                                   "retiradas": [{"texto": "Vieja.", "motivo": "ya está en el código"}]}, "2026-12-05", "2026-11-30", "2026-12-06")
        v = semanal.vigentes()
        self.assertEqual(len(v["lecciones"]), semanal.MAX_LECCIONES)
        self.assertEqual((v["lecciones"][0]["desde"], v["lecciones"][1]["desde"]), ("2026-11-28", "2026-12-05"))
        self.assertEqual(v["retiradas"][0]["fecha"], "2026-12-05")
        semanal.guardar_lecciones({"lecciones": [], "retiradas": []}, "2026-12-12", "2026-12-07", "2026-12-13")     # revisión vacía
        self.assertEqual(len(semanal.vigentes()["lecciones"]), semanal.MAX_LECCIONES)
        self.assertEqual(semanal.lecciones_para_hoy()["lecciones_vigentes"][0],
                         {"leccion": "No comprar con el índice cayendo un 2 %.", "desde": "2026-11-28"})
        semanal.VIGENTES.unlink(missing_ok=True)

    def test_la_retrospectiva_publica_los_textos_enteros(self):
        largo = "Se compró el mismo día del rebote, cuando ya había abierto un 5,45 % arriba y cotizaba cerca de su máximo. " * 3
        h = semanal._retrospectiva({"aciertos": [{"que": "HII sube", "por_que": "Barata y firme."}],
                                    "errores": [{"que": "Amazon", "causa": "análisis", "por_que": largo, "como_evitarlo": "Esperar dos cierres."}],
                                    "retiradas": [{"texto": "Vieja.", "motivo": "ya no aplica"}], "semana_que_viene": ["Vigilar AbbVie."]},
                                   [{"texto": "No comprar con convicción baja.", "origen": "23 nov", "estado": "nueva", "desde": "2026-11-28"}])
        self.assertIn(inf.e(largo), h)
        for trozo in ("Por qué pasó:", "Cómo se evita:", "<b>Nueva.</b> No comprar con convicción baja.", "<b>Retirada.</b> Vieja.", "Vigilar AbbVie."):
            self.assertIn(trozo, h)


class Renta(unittest.TestCase):
    def test_las_cuentas_del_anio(self):
        ops = [op("2026-01-10", "AAA", "compra", 10, 100.0), op("2026-02-10", "AAA", "venta", 10, 150.0),
               op("2027-01-05", "BBB", "compra", 1, 10.0)]
        impuestos.operaciones, original = (lambda: ops), impuestos.operaciones
        try:
            q = renta.cuentas(2026, hoy=dt.date(2027, 1, 20), oficial={"2026-01-10": 1.0})
        finally:
            impuestos.operaciones = original
        self.assertTrue(q["cambio_oficial"])
        self.assertEqual(len(q["operaciones"]), 2)                           # las de 2027 no entran
        self.assertEqual((q["cuenta"]["neto_eur"], q["cuenta"]["impuesto_eur"]), (500.0, 95.0))
        self.assertEqual(q["operaciones"][0]["importe_eur"], 1000.0)
        self.assertEqual(q["hasta"], "2026-12-31")
        self.assertEqual(q["posiciones"], [])                                # año cerrado: no se listan las de hoy


if __name__ == "__main__":
    unittest.main()
