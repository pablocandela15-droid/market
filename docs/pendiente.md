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

1. **Finnhub:** la clave guardada en `.env` es rechazada ("Invalid API key").
   Cuando el usuario la vuelva a copiar, comprobar con
   `python3 -c "from bot import extra; e=[]; print(extra.analistas(['AMZN'], e), e)"`.
   El código ya está conectado al dossier.
2. Revisar los informes automáticos de las 15:45 y las 22:10.
3. Añadir lo que no necesita claves nuevas: noticias y valores que más se
   mueven de Alpaca, calendario de publicaciones de FRED, operaciones de
   directivos de la SEC.
4. Inversores particulares: X exige su API de pago, Reddit no da acceso y
   StockTwits bloquea a los programas. Hoy se usa lo que devuelve el buscador.
   Si se quiere más, la vía es la API de pago de X.
5. Agentes de MiroFish con Sonnet: descartado por coste con los datos medidos
   (unos 58 $ por simulación). Alternativa por explorar: recortar lo que cada
   agente relee del hilo.
6. Confirmar en git y subir: nada de lo hecho desde la primera confirmación
   (`8290aa7`) está confirmado.
