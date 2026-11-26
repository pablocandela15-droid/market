# Estado del trabajo y siguientes pasos

Nota de relevo: si una sesión se corta, aquí está lo necesario para seguir.
Última actualización: 26 de noviembre de 2026, 6:50.

## Hecho y funcionando

- Las cuatro tareas están programadas en el Mac (`tareas/programar.sh estado`):
  7:00 diaria, 15:45 y 22:10 de lunes a viernes, MiroFish los domingos a las 17:00.
- Cartera simulada en Alpaca con dos posiciones (HII y AMZN) desde el 23 nov.
- El bot mira el mercado entero: S&P 500 completo más temas (`bot/universo.py`,
  `bot/dossier.py`), con clasificación de sectores.
- MiroFish (`bot/mirofish.py`) es la guía principal del estratega. Última
  simulación: 23 nov, postura defensiva. Resultado en `estado/mirofish/ultima.json`.
- El cierre abre con "La cuenta del día".
- El estratega puede mantener posiciones sin plazo. Desde el 25 nov busca compras
  cada día mientras haya caja y huecos (perfil agresivo).

## Hecho el 26 nov (revisión a fondo): las cuentas usan el precio real de Alpaca

- **Fallo encontrado.** Cuando el bot descubría un stop, anotaba la venta al
  precio del stop y después vendía en Alpaca a mercado. El 25 nov anotó AMZN a
  242,00 $ y Alpaca la ejecutó a 237,968 $: la pérdida real fue de 13,73 $ y no
  de 10,92 $. Las ventas decididas en la apertura tenían el mismo defecto (se
  anotaban a la última cotización).
- **Arreglado** (`ejecutar_venta`, `sincronizar` y `descuadres` en
  `bot/ordenes.py`; `detectar_salidas` en `bot/cartera.py`; `_vigilar` en
  `bot/tarea.py`): toda venta se anota al precio que devuelve Alpaca, con
  `precio_previsto` y `deslizamiento_usd` en `operaciones.jsonl`. Con la bolsa
  cerrada la venta queda como `venta_pendiente` en la posición y se anota al
  ejecutarse. Sin conexión con Alpaca no se anota ninguna venta.
- **Cuadre con Alpaca en cada tarea:** si las posiciones del bot y las de
  Alpaca no coinciden, sale en "Avisos de datos" del informe.
- La venta de AMZN está corregida en `estado/` (caja 50,16 $; cierre del 25
  nov, 1.076,53 $). Copia de lo anterior en
  `estado/copias/2026-11-26-antes-de-corregir-AMZN/`.
- Sin probar con una ejecución real: la primera será la mañana del 27 nov.
- **Pendiente de decidir con el usuario** (el 26 nov dijo que es para un
  trabajo de la universidad, que quiere más de un 10 % de rentabilidad, que el
  bot aprenda solo y que "si se ve convencido entre con todo"): stop diario en
  Alpaca para las posiciones con fracciones (HII y ABBV no tienen stop en el
  bróker; Alpaca admite órdenes stop de un día con fracciones), marcador de
  todas las ideas para aprender con datos, tamaño de la compra según la
  convicción, prueba con datos históricos, y fiabilidad del Mac (se duerme al
  minuto si nada lo impide, no hay aviso si una tarea falla y `estado/` no
  tiene copia de seguridad).

## Hecho el 26 nov: impuestos, informe semanal con retrospectiva, gráficas e informe para la renta

Pedido por el usuario el 26 nov. Hecho en otra sesión a la vez que la revisión de arriba; los cambios conviven.

- **Impuestos en todos los informes** (`bot/impuestos.py`, `bloque_impuestos` y `linea_impuestos` en `bot/informe.py`):
  cada venta en euros con el cambio oficial del BCE, cuenta del año, impuesto estimado y lo que queda después.
  `cartera.resumen` trae `impuestos`, así que también lo ven los modelos.
- **Informe semanal** (`bot/semanal.py`, `tareas/guiones/semanal.md`): los sábados tras la mañana. Retrospectiva, errores
  con su causa, gráficas y lecciones vigentes (`estado/lecciones_vigentes.json`) que el estratega, la apertura y el cierre
  reciben cada día. Primera revisión real hecha el 26 nov con la semana a medias (0,94 $).
- **Gráficas** (`bot/graficas.py`): evolución frente al S&P 500, resultado por valor y reparto del dinero.
- **Informe para la renta** (`bot/renta.py`): `python3 -m bot.tarea renta --anio 2026`. Si el usuario pide "el informe de
  la renta", es esta orden: se le pasa el PDF y los dos CSV de `Informes/Renta/<año>/`.
- Cada operación nueva guarda el cambio euro/dólar del momento (`cartera.fijar_cambio`).
- Pruebas en `tests/test_impuestos.py` (27).
- **Al cambiar reglas o cuando el dueño corrija al bot:** añadir una entrada a `config/historial_de_reglas.json` o a
  `config/notas_del_dueno.json`. La revisión semanal juzga con eso.
- Sin probar en real: la sección de impuestos del cierre (primera vez, viernes 27 nov a las 22:10) y el lanzamiento
  automático del semanal (sábado 28 nov tras las 7:00).
- Lo que la propia revisión ha pedido para el código y no está hecho: un freno que impida comprar el día en que el índice
  abre con caída fuerte (hoy es lección y texto del guion).

## Hecho el 25 y el 26 nov

- **Perfil agresivo, corregido el 26 nov.** El usuario pidió el 25 "más agresivo
  y tome algo más de riesgos". Lo leí como "comprar más y estar invertido" y fue
  un error: ese día el bot compró TSN, PCG y ABBV (defensivos, convicción media)
  con el S&P 500 abriendo un 3,2 % abajo, y dejó la caja en 53 $. El 26 lo
  aclaró: "nadie te dice que tengas que invertir todo el capital"; quiere tomar
  más riesgos para ganar más dinero. Ahora: 1,5 % de riesgo por operación, 30 %
  por posición, objetivo de al menos 2 veces lo arriesgado (1,5 al recalcular
  en la apertura), 2 compras al día, PEG hasta 2,5, y guiones sin presión por
  invertir (caja válida, pocas compras y con recorrido, esperar si el mercado
  cae con fuerza, rotar si hay algo claramente mejor). Detalle en "Perfil
  agresivo" de tareas-programadas.md. Las cifras son lectura mía de lo que pidió.
- Las tres compras del 25 nov siguen en cartera con su stop y su objetivo: no
  se han tocado a mano. El estratega las revisa cada mañana y puede rotarlas.

## Hecho el 24 nov

- **La apertura analiza el mercado y explica cada cambio de plan** (`apertura`,
  `_lo_hecho_en_la_apertura` en `bot/tarea.py`; `tareas/guiones/apertura.md`):
  por qué no se hace una compra prevista, y por qué se hace una que no lo
  estaba (solo entre los vigilados de la mañana). Pedido por el usuario el 24
  nov tras ver que PG&E no se compró y el informe no lo explicaba. La mañana
  avisa además cuando una orden tiene el margen justo.
- **En modo de prueba (`BOT_PRUEBA=1`) el bot ya no puede enviar órdenes a
  Alpaca**: antes los ensayos de la apertura habrían llegado al bróker.
- **Reparto del crédito entre las mañanas** (`elegir_nivel` en `bot/gasto.py`):
  días sin mercado en nivel medio como mucho y nivel alto para los días de
  mercado, los lunes primero. Pedido por el usuario el 24 nov.
- **El informe de la mañana tiene dos páginas, no recorta ningún texto** y
  trae la sección "El mundo hoy": un redactor (`tareas/guiones/mundo.md`,
  `_mundo` y `_el_mundo_hoy` en `bot/tarea.py`) busca en la web lo más
  importante de las últimas 24 horas. Pedido por el usuario el 24 nov.
- **El informe de la mañana explica cada decisión.** De cada compra, venta y
  posición que se mantiene: por qué, qué esperamos y cuándo saldríamos
  (`ESQ_ESTRATEGA` y `_que_hacemos` en `bot/tarea.py`, sección "Cómo explicar
  cada decisión" de `tareas/guiones/estratega.md`). Sin recuentos de votos.
- **Los investigadores vuelven a buscar en internet.** A las 7:00 del 24 nov
  trabajaron sin búsqueda web: el modo `--bare` la quita. Corregido en
  `bot/modelo.py` (`_correr`).
- **Alpha Vantage conectada** (`bot/extra.py`): fechas confirmadas de
  resultados y sorpresas de los últimos cuatro trimestres. La regla de "no
  comprar antes de resultados" usa la fecha confirmada cuando existe.
- `python3 -m bot.tarea manana --solo-estratega` rehace el plan sin repetir la investigación.

## Sin probar todavía

- Compras con acciones enteras en Alpaca (stop y objetivo puestos en el bróker):
  la primera será la de la apertura del 24 nov, si el plan sigue trayendo una compra.
- Ventas en Alpaca y cambio de objetivo.

## Siguientes pasos

1. **Finnhub:** funciona desde el 24 nov (analistas y directivos). Entra en el
   informe a partir de la mañana del 25 nov.
2. Revisar los informes automáticos de las 15:45 y las 22:10.
3. Añadir lo que no necesita claves nuevas: noticias y valores que más se
   mueven de Alpaca, calendario de publicaciones de FRED, operaciones de
   directivos de la SEC.
4. **Red tipo Twitter de MiroFish:** es la red por defecto desde el 24 nov, con
   más rondas (ver tareas-programadas.md). Probados por separado el debate (48
   horas simuladas), la encuesta propia y el recuento. Sin probar de punta a
   punta: lo será la simulación del domingo 29 nov; revisar su registro, cuántas
   rondas hizo y si el reparto por minuto evitó los rechazos de la API.
   El usuario pidió más rondas y el 24 nov subió el tope a 9 $ para simular la
   semana completa (120 horas). Comprobar el domingo si el coste real se parece
   al estimado (unos 7 $ de debate) y ajustar `parte_del_debate` si hace falta.
   Consecuencia avisada: en un mes entero quedan unos 47 $ para las mañanas, así
   que la mayoría irá en nivel medio (ver "Consumo y crédito de API").
   **El domingo 29 nov es especial** (`mirofish.solo_el` en `config/reglas.json`):
   agentes Sonnet, 48 horas simuladas, tope de 32 $. Lo pidió el usuario el 24
   nov para aprovechar crédito que caduca el 2 dic. Después, comparar esa
   síntesis con la del 23 nov (Haiku) y contarle si se nota la diferencia; el
   6 dic vuelve sola a Haiku con 120 horas.
5. Inversores particulares: X exige su API de pago, Reddit no da acceso y
   StockTwits bloquea a los programas. Hoy se usa lo que devuelve el buscador.
   Si se quiere más, la vía es la API de pago de X.
6. Agentes de MiroFish con Sonnet: descartado por coste con los datos medidos
   (unos 58 $ por simulación). Alternativa por explorar: recortar lo que cada
   agente relee del hilo.
7. En git: confirmado y subido el 26 nov todo lo de ese día en un solo cambio
   (perfil agresivo corregido, ventas al precio real de Alpaca, impuestos,
   informe semanal, gráficas e informe de la renta). Venía de dos sesiones a la vez.
8. Revisar los informes con el perfil corregido: mañana del 26 nov (festivo en
   EE. UU., sin órdenes) y, sobre todo, mañana y apertura del viernes 27 nov
   (media sesión): que no proponga compras de relleno, que las que proponga
   ganen al menos el doble de lo arriesgado y qué hace con TSN, PCG y ABBV.
9. Impuestos, semanal y renta: hechos el 26 nov (ver arriba).
