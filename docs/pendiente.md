# Estado del trabajo y siguientes pasos

Nota de relevo: si una sesión se corta, aquí está lo necesario para seguir.
Última actualización: 25 de noviembre de 2026, 6:50.

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

## Hecho el 25 nov

- **Perfil agresivo**, pedido por el usuario ("más agresivo y tome algo más de
  riesgos"): 1,5 % de riesgo por operación, 30 % por posición, 3 compras al
  día, PEG hasta 2,5, hueco de apertura del 4 % y, al recalcular en la
  apertura, basta con ganar 1,2 veces lo arriesgado (`validar` en
  `bot/ordenes.py`, con `previsto`). Los guiones del estratega y de la apertura
  buscan compras en vez de esperar. Tabla completa y lo que no cambia, en
  "Perfil agresivo" de tareas-programadas.md. Las cifras las elegí yo como
  lectura de "algo más": si pide más o menos, se tocan en `config/reglas.json`.
  Estrena en el informe de las 7:00 y la apertura de las 15:45 del 25 nov.

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
7. En git: confirmado y subido hasta `57b86cd` (25 nov, la apertura que analiza
   y explica). El perfil agresivo está sin confirmar.
8. Primera mañana y primera apertura automáticas con el perfil agresivo y la
   apertura nueva: miércoles 25 nov, 7:00 y 15:45. Revisar sus informes: si el
   estratega propone más compras, si el tamaño sale con el 1,5 % y si la
   apertura las ejecuta.
