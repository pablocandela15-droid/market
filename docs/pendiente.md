# Estado del trabajo y siguientes pasos

Nota de relevo: si una sesión se corta, aquí está lo necesario para seguir.
Última actualización: 24 de noviembre de 2026, 7:30.

## Hecho y funcionando

- Las cuatro tareas están programadas en el Mac (`tareas/programar.sh estado`):
  7:00 diaria, 15:45 y 22:10 de lunes a viernes, MiroFish los domingos a las 17:00.
- Cartera simulada en Alpaca con dos posiciones (HII y AMZN) desde el 23 nov.
- El bot mira el mercado entero: S&P 500 completo más temas (`bot/universo.py`,
  `bot/dossier.py`), con clasificación de sectores.
- MiroFish (`bot/mirofish.py`) es la guía principal del estratega. Última
  simulación: 23 nov, postura defensiva. Resultado en `estado/mirofish/ultima.json`.
- El cierre abre con "La cuenta del día".
- El estratega no está obligado a operar a diario y puede mantener posiciones.

## Hecho el 24 nov por la mañana

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
4. **Red tipo Twitter de MiroFish:** la variante ligera funciona (ver
   tareas-programadas.md). Para usarla un domingo falta adaptar a esa red
   `encuesta_propia` (lee `reddit_profiles.json`; en Twitter los perfiles van en
   `twitter_profiles.csv`) y `_lo_mas_valorado` (en Twitter no hay comentarios:
   hay citas y reenvíos). Al ser unas cinco veces más barata por ronda, abre la
   puerta a más rondas o a agentes con un modelo mayor.
5. Inversores particulares: X exige su API de pago, Reddit no da acceso y
   StockTwits bloquea a los programas. Hoy se usa lo que devuelve el buscador.
   Si se quiere más, la vía es la API de pago de X.
6. Agentes de MiroFish con Sonnet: descartado por coste con los datos medidos
   (unos 58 $ por simulación). Alternativa por explorar: recortar lo que cada
   agente relee del hilo.
7. En git: todo confirmado y subido hasta `827141a` (24 nov). Lo posterior
   (filtro de la clave de Finnhub y estas notas) está sin confirmar.
