# market · bot de bolsa con cartera simulada

Analiza el mercado tres veces al día, decide operaciones de varios días sobre
una cartera simulada (1.000 € iniciales y 200 € al mes) y deja un PDF de una
página en cada ejecución, ordenado por mes y día. **No opera con dinero real.**

No es asesoramiento financiero. Es software que lee datos públicos, aplica unas
reglas y registra una simulación.

## Qué hace cada tarea

| Hora (Madrid) | Tarea | Qué hace |
|---|---|---|
| 7:00, todos los días | `manana` | Recoge datos, cuatro investigadores y un estratega escriben el plan del día, y las reglas aprueban o bloquean cada orden |
| 15:45, lunes a viernes | `apertura` | Revisa el plan con el mercado abierto y lanza las órdenes aprobadas en la cuenta simulada |
| 22:10, lunes a viernes | `cierre` | Resultado del día frente al S&P 500, diario de operaciones y lecciones |

El detalle está en [docs/tareas-programadas.md](docs/tareas-programadas.md) y
las fuentes en [docs/estado-del-arte.md](docs/estado-del-arte.md).

## Puesta en marcha

1. Copia `.env.example` como `.env` y escribe tus claves. El archivo no se sube al repositorio.

   | Clave | Para qué | Sin ella |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | Llamar a los modelos con el crédito de API | Se usa la sesión del plan de Claude si `autenticacion.modo` lo permite; si no, los informes salen solo con datos |
   | `ALPACA_API_KEY` y `ALPACA_SECRET_KEY` | Ejecutar en la cuenta de simulación de Alpaca | Las operaciones se anotan "en seco" al último precio |
   | `FRED_API_KEY` | Datos macro (tipos, inflación, paro) | El bloque macro usa solo datos de mercado |
   | `SEC_USER_AGENT` | Resultados trimestrales y hechos relevantes de la SEC | No se consultan |

2. Prueba una tarea a mano, sin tocar la cartera real:

   ```bash
   BOT_PRUEBA=1 python3 -m bot.tarea manana --sin-modelo
   ```

3. Activa las tres tareas en el Mac:

   ```bash
   tareas/programar.sh activar
   ```

   `tareas/programar.sh estado` las lista, `quitar` las elimina y `probar` lanza
   una comprobación inofensiva tal como las lanzará el Mac.

   macOS no deja que las tareas en segundo plano lean carpetas dentro de
   Escritorio, Documentos o Descargas. El proyecto tiene que estar fuera de
   ellas (por ejemplo en `~/bot-bolsa`) para que la programación funcione.

## Órdenes útiles

```bash
python3 -m bot.tarea manana            # ejecutar una tarea ahora
python3 -m bot.tarea apertura
python3 -m bot.tarea cierre
python3 -m unittest discover -s tests  # pruebas de las reglas y los stops
```

Opciones: `--sin-modelo` (solo datos y reglas) y `--forzar` (ignora el horario
del mercado; solo para pruebas). Con `BOT_PRUEBA=1` delante, todo se escribe en
carpetas terminadas en `-prueba`.

## Dónde está cada cosa

| Carpeta | Contenido |
|---|---|
| `bot/` | El código. Solo biblioteca estándar de Python |
| `config/reglas.json` | Capital, riesgo, filtros de valoración, modelos y reparto del crédito de API |
| `config/universo.json` | Valores vigilados por sector |
| `tareas/guiones/` | Instrucciones de cada modelo |
| `tareas/` | Lanzador y programación en el Mac |
| `../Informes/<Mes>/<día>/` | Los PDF, fuera del repositorio: por ejemplo `Informes/Noviembre/23/0700-manana.pdf`. La carpeta se cambia en `config/reglas.json` |
| `estado/` | Cartera, operaciones, lecciones y gasto de API |
| `diario/` | Balance de cada día en texto |
| `datos/`, `registros/` | Datos crudos y registros de cada ejecución |

En git solo van el código, la configuración y la documentación. Todo lo que
genera el bot (`estado/`, `diario/`, `datos/`, `registros/` y los informes) se
queda en este Mac.

Las herramientas de datos de `day-trader-skill` se usan desde `../repos/`, sin copiar su código.

## Reglas de la cartera

Están en `config/reglas.json` y las aplica el código, no el modelo:

- Riesgo del 1 % de la cartera por operación, con stop obligatorio.
- Máximo 5 posiciones; ninguna por encima del 25 % de la cartera.
- El objetivo debe ganar al menos 1,5 veces lo que arriesga el stop.
- Stop entre el 2 % y el 15 % del precio de entrada.
- No se compra con PEG por encima de 2, sin datos de valoración, ni a 2 días o menos de presentar resultados.
- En la apertura se descarta la orden si el precio se ha ido más de un 3 % por encima de lo previsto.
- Solo compras: sin ventas en corto, margen ni opciones.

## Seguridad

- El cliente de Alpaca tiene fija la dirección de simulación y rechaza claves que no sean de simulación.
- Los modelos no tienen acceso a archivos, terminal ni bróker. Los investigadores solo pueden buscar y leer en la web.
- Cada llamada a un modelo lleva un tope de gasto, y el bot reparte el crédito mensual entre los días que quedan.
