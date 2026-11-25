# Tareas programadas: casos de uso, flujo, modelo y consumo

Tres tareas que el Mac lanza a su hora (`tareas/programar.sh`). Las fuentes que
cita cada paso están en [estado-del-arte.md](estado-del-arte.md).

## Principios de diseño

- **Los datos los recogen scripts**, que no gastan tokens, y los dejan en
  `datos/AAAA-MM-DD/dossier-<tarea>.json` (unos 14.000 tokens a las 7:00). Los
  modelos leen ese archivo y solo salen a internet para noticias y contexto.
- **Los modelos se llaman con `claude -p`.** Cada llamada lleva su modelo, su
  tope de gasto y un formato de respuesta obligatorio. Cómo se pagan lo decide
  `autenticacion.modo` en `config/reglas.json`:
  - `clave`: con la clave de API y en un entorno limpio. Gasta el crédito
    mensual de API y nunca el uso del plan. Las llamadas sin herramientas van en
    modo `--bare`; las de los investigadores no, porque ese modo les quita la
    búsqueda web (así pasó el 24 nov a las 7:00: trabajaron sin internet).
  - `plan`: con la sesión del plan de Claude de este Mac. Se usa el nivel fijo
    `nivel_con_plan` y el coste que se anota es solo un equivalente.
  - `plan_hasta_tener_saldo` (el actual): usa el plan mientras la clave no tenga
    saldo. El primer día que la clave responde, pasa a la clave y ya no vuelve
    al plan aunque el crédito se agote.
- **Las reglas son código.** Un modelo propone; `bot/ordenes.py` aprueba o
  bloquea. Ningún modelo tiene acceso a archivos, terminal ni bróker.
- **Sin clave de API** las tareas funcionan igual, pero el informe trae solo
  datos y no propone operaciones.

## Reglas comunes

| Regla | Valor |
|---|---|
| Capital simulado | 1.000 € iniciales y 200 € el día 1 de cada mes, convertidos a dólares al cambio del día |
| Estilo | Operaciones de varios días o semanas; nada dentro del día |
| Riesgo | 1 % por operación, stop obligatorio, máximo 5 posiciones, ninguna por encima del 25 % |
| Calidad de la operación | Objetivo de al menos 1,5 veces el riesgo; stop entre el 2 % y el 15 % |
| Tipo de orden | Solo compras, con acciones fraccionadas; sin cortos, margen ni opciones |
| Valoración | Sin compra con PEG por encima de 2, sin datos, o a 2 días o menos de resultados |
| Ejecución | El bot decide solo, únicamente contra la dirección de simulación de Alpaca |
| Referencia | El mismo dinero, el mismo día, en el S&P 500 (sin dividendos) |
| Salida | Un PDF por tarea en `Informes/<Mes>/<día>/`, en la carpeta del proyecto. El de la mañana tiene dos páginas; los demás, una. Ningún texto se recorta: si algo no cabe ni con letra pequeña, el informe ocupa una página más |

Todas se cambian en `config/reglas.json`.

---

## Tarea 1 · 7:00 · Informe de la mañana

**Días:** todos.

### Casos de uso

- Saber en dos páginas cómo amanece el mercado, cómo va la cartera y qué ha pasado en el mundo.
- Leer lo más importante de las últimas 24 horas en el mundo (política, economía, tecnología, empresas, España y Europa, energía), tenga o no que ver con la bolsa: sección "El mundo hoy".
- Fijar el plan del día: qué comprar o vender, con entrada, stop y objetivo.
- Entender cada decisión: de cada compra, venta y posición que se mantiene, el
  informe dice por qué, qué se espera que pase y qué haría salir. Con hechos de
  la empresa y del mercado, nunca con recuentos de votos de la simulación.
- Ver qué sectores lideran y cuáles se quedan atrás, sobre el mercado entero.
- Seguir de cerca los valores prioritarios (hoy, NVIDIA).
- Sábado: resumen de la semana. Domingo: preparación del lunes con los grandes inversores.

### Flujo

| Paso | Quién | Qué revisa | Dónde |
|---|---|---|---|
| 0 | Script | Índices, VIX, bono, euro/dólar, futuros, Europa y Asia | Yahoo |
| 0 | Script | Fondos de cada sector y materias primas | Yahoo |
| 0 | Script | Las 500 empresas del S&P 500 y los valores de los temas: precio, tendencia, RSI | Yahoo |
| 0 | Script | Clasificación de los once sectores y de los temas, y qué valores tiran de cada uno | Cálculo propio |
| 0 | Script | Configuraciones técnicas de varios días | `trader-scanner daily` |
| 0 | Script | P/E, P/E futuro, PEG y fecha de resultados de hasta 30 candidatos, con más hueco para los sectores que favorece MiroFish | Finviz |
| 0 | Script | Media de P/E y PEG de cada sector | Damodaran |
| 0 | Script | Titulares de cartera, prioritarios y candidatos | `trader-news` |
| 0 | Script | Tipos, curva, inflación, paro (con clave) | API de FRED |
| 0 | Script | Trimestres y crecimiento (con identificación) | `trader-analyst` (SEC) |
| 0 | Script | Fecha confirmada de resultados de todo el mercado (una consulta al día) y si los 8 valores más interesantes baten lo esperado (últimos 4 trimestres) | Alpha Vantage (`bot/extra.py`) |
| 0 | Script | Recomendaciones de analistas (12 valores) y compras y ventas de directivos en 90 días (8 valores) | Finnhub (`bot/extra.py`) |
| 0 | Script | Stops y objetivos alcanzados desde el último informe | Cartera y Alpaca |
| 1 | Investigador de noticias | Qué mueve el precio y qué ánimo hay, también entre particulares (lo que el buscador devuelve de X y de los foros; no entra en las redes) | Dossier y búsqueda web |
| 1 | Investigador de sectores | Tendencia y valoración por sector | Dossier y búsqueda web |
| 1 | Investigador de macro | Entorno, agenda económica y euro/dólar | Dossier y búsqueda web |
| 1 | Investigador de materias primas | Energía y metales, y a qué valores afectan | Dossier y búsqueda web |
| 1 | Redactor del mundo (Sonnet 5.5 en todos los niveles) | Entre 12 y 14 noticias de las últimas 24 horas, por temas, con qué ha pasado, por qué importa y la fuente | Búsqueda web; titulares generales de Finnhub como punto de partida |
| 2 | Estratega | Une los cuatro informes con la cartera, MiroFish y las reglas; escribe el plan y explica cada decisión (`por_que`, `que_esperamos`, `cuando_saldriamos`) | — |
| 3 | Script | Aprueba o bloquea cada orden y calcula su tamaño | `bot/ordenes.py` |
| 4 | Script | Compone el PDF de dos páginas, con los textos completos | `Informes/<Mes>/<día>/0700-manana.pdf` |

Los investigadores trabajan en paralelo. Los domingos el paso 0 añade lo que
hacen los grandes inversores con hasta 10 valores (Dataroma).

El redactor del mundo se medió el 24 nov: 8 a 11 turnos de búsqueda, unos 45
segundos y entre 0,16 y 0,22 $ por informe. Sus noticias se publican tal cual
en "El mundo hoy" y además llegan al estratega como contexto.

`python3 -m bot.tarea manana --solo-estratega` repite solo los pasos 2 a 4 con
los datos y los informes de los investigadores ya guardados ese día. Sirve
para rehacer el plan sin volver a pagar la investigación.

Alpha Vantage, en su plan gratuito, admite 25 consultas al día y una por
segundo: el bot se limita a 20, guarda cada respuesta (el calendario un día,
las sorpresas de cada empresa 20 días) y espera 1,5 s entre consultas. Su
calendario solo trae fechas confirmadas (68 empresas del S&P 500 el 24 nov);
para las demás se sigue usando la fecha de Finviz.

### Niveles de análisis

Antes de empezar, el bot mira lo que queda del crédito del mes y los días que
faltan, y elige el nivel de hoy con tres reglas (`elegir_nivel` en `bot/gasto.py`):

1. Un día sin mercado (sábado, domingo o festivo) va como mucho en nivel medio
   (`credito_api.nivel_maximo_sin_mercado`).
2. Un nivel cabe hoy si, después de pagarlo, queda para hacer todas las demás
   mañanas en el nivel inmediatamente inferior.
3. El nivel alto se guarda primero para los lunes que quedan, que es cuando se
   estrena la simulación del domingo: otro día de mercado solo lo usa si además
   sobra para ponerlo en todos esos lunes.

Simulado el 24 nov con los costes medidos: en lo que queda de noviembre, alto
todos los días de mercado y medio el festivo y el fin de semana; en un diciembre
entero, alto el primer día y los cuatro lunes, y medio los otros 26 días.

Los niveles:

| Nivel | Investigadores | Estratega | Coste estimado |
|---|---|---|---|
| Alto | 4, con Fable 5.2 | Fable 5.2 | 3,5 $ |
| Medio | 4, con Sonnet 5.5 | Fable 5.2 | 1,6 $ |
| Bajo | 2 (noticias y sectores), con Sonnet 5.5 | Opus 5.5 | 0,6 $ |
| Sin modelo | — | — | 0 $ |

Las estimaciones se sustituyen solas por la media real de las últimas cinco ejecuciones.

---

## Tarea 2 · 15:45 · Apertura

Desde el 24 nov de 2026 la apertura **analiza el mercado siempre**, haya o no
órdenes en el plan, y **explica cada diferencia con el plan de la mañana**:

- Busca en la web (2 a 4 búsquedas) lo ocurrido desde las 7:00: datos
  económicos de las 14:30 y noticias de los valores del plan, de la cartera y
  de los vigilados.
- Una compra prevista que no se hace sale con su porqué en claro, lo decida el
  análisis (una noticia, un dato) o lo impidan las reglas al precio real. Por
  ejemplo, el 24 nov PG&E no se compró: se pensaba comprar a 14,08 $ y cotizaba
  a 14,20 $, y a ese precio ya solo ganaba 1,3 veces lo que arriesgaba.
- Puede hacer **una compra que no estaba en el plan**, solo entre los valores
  que la mañana dejó en vigilancia y solo si ha pasado hoy algo concreto. Pasa
  por las mismas reglas que cualquier compra y sale con "por qué", "esperamos
  que" y "saldríamos si". El tope sigue siendo de dos compras al día.
- Si no se compra nada, dice por qué.
- Un stop solo se sube si queda al menos a `stop_min_pct` del precio.

Todo eso va en la sección "Qué se ha hecho y por qué" del PDF y en
`datos/AAAA-MM-DD/apertura.json` (campo `cambios`), que también lee el cierre.
Cuesta unos 0,12 $ por día (antes 0,06 $).

**Días:** lunes a viernes con mercado abierto. **Modelo:** Sonnet 5.5.

### Casos de uso

- Lanzar las órdenes aprobadas del plan con el mercado ya abierto.
- Descartar las que han dejado de tener sentido.
- Subir stops o cerrar posiciones si una noticia rompe la tesis.

### Flujo

| Paso | Quién | Qué revisa | Dónde |
|---|---|---|---|
| 0 | Script | Si el mercado abre hoy; si no, nota y fin | Calendario del bot |
| 0 | Script | Apertura, último precio y hueco de cada valor del plan y de la cartera | Yahoo (velas de 5 minutos) |
| 0 | Script | Titulares de las últimas 9 horas | `trader-news` |
| 0 | Script | Stops y objetivos alcanzados | Cartera y Alpaca |
| 1 | Modelo | Orden por orden: ejecutar o descartar; stops y ventas | Dossier |
| 2 | Script | Vuelve a pasar las reglas con el precio del momento | `bot/ordenes.py` |
| 3 | Script | Envía las compras a Alpaca (simulación) o las anota en seco | `bot/alpaca.py` |
| 4 | Script | Compone el PDF | `Informes/<Mes>/<día>/1545-apertura.pdf` |

Con acciones enteras, el stop y el objetivo quedan puestos en Alpaca. Con
fracciones Alpaca no lo admite: el stop lo vigila el bot en cada tarea, así que
solo se comprueba tres veces al día.

---

## Tarea 3 · 22:10 · Cierre

**Días:** lunes a viernes con mercado abierto. **Modelo:** Sonnet 5.5.

### Casos de uso

- Conocer el resultado del día en dólares y en euros, frente al S&P 500.
- Llevar el diario de operaciones.
- Medir si el plan de la mañana acertó y anotar qué se aprende.
- Dejar avisos para el informe del día siguiente.

### Flujo

| Paso | Quién | Qué revisa | Dónde |
|---|---|---|---|
| 0 | Script | Cierre de cada posición y de los índices | Yahoo |
| 0 | Script | Stops y objetivos alcanzados | Cartera y Alpaca |
| 0 | Script | Operaciones del día y valor de la cartera | `estado/` |
| 0 | Script | Titulares de la tarde | `trader-news` |
| 1 | Modelo | Compara el plan con lo ocurrido; aciertos, fallos, lecciones y avisos | Dossier |
| 2 | Script | Guarda el valor del día, el diario y las lecciones | `estado/`, `diario/` |
| 3 | Script | Compone el PDF | `Informes/<Mes>/<día>/2210-cierre.pdf` |

Las lecciones y los avisos vuelven al estratega en el informe de la mañana siguiente.

---

## Tarea 4 · Domingos a las 17:00 · Simulación de MiroFish

Es la guía principal de la cartera: de su síntesis salen la postura general y
los sectores en los que el estratega busca compras durante la semana.

**Modelos:** Haiku 5.5 para los agentes (es el que más llamadas hace), Sonnet 5.5
para el reparto y para el informe de MiroFish, y Fable 5.2 para la síntesis final.

### Casos de uso

- Saber qué esperan del mercado y hacia qué sectores irían decenas de
  inversores simulados después de debatir entre ellos.
- Dar al estratega una clasificación de sectores con votos, los valores con más
  convicción y unas predicciones a cinco sesiones.
- Medir con precios reales si esas predicciones aciertan.

### Flujo

| Paso | Quién | Qué hace | Dónde |
|---|---|---|---|
| 0 | Script | Comprueba las predicciones anteriores cuyo plazo ha pasado | Yahoo, `estado/mirofish/` |
| 0 | Script | Mira el mercado entero en ese momento: índices, sectores, destacados | `bot/dossier.py` |
| 1 | Sonnet | Busca qué se dice del mercado y describe unos 34 participantes: particulares, gestores, analistas, bajistas, economistas, periodistas | Búsqueda web |
| 2 | MiroFish | Analiza el documento de partida (mercado y reparto) | Haiku |
| 3 | MiroFish | Construye el grafo de conocimiento | Zep Cloud |
| 4 | MiroFish | Crea un agente por participante | Haiku |
| 5 | MiroFish | Debate en una red tipo Twitter (versión ligera), hasta 120 horas simuladas o hasta gastar el 82 % del tope de 9 $ | Haiku |
| 6 | Script | Encuesta a cada agente: qué hará el mercado, dos sectores a favor, dos en contra y dos valores | Haiku |
| 7 | MiroFish | Escribe su informe | Sonnet |
| 8 | Fable | Síntesis: tesis, dirección del mercado, sectores con votos, valores, predicciones y en qué no hay acuerdo | — |
| 9 | Script | Guarda la síntesis, anota las predicciones y compone el PDF | `estado/mirofish/`, `Informes/<Mes>/<día>/1700-mirofish.pdf` |

Las empresas, índices, fondos y personas reales citadas en las noticias no se
convierten en agentes: son temas de conversación.

### Cómo llega a las decisiones

- La tarea de las 7:00 mira a fondo los valores favoritos de la simulación y da
  más hueco entre los candidatos a los sectores que favorece.
- El estratega toma de ella la postura general y los sectores. Los datos del día
  conservan el derecho de veto: valoración, noticias posteriores, un sector que
  se desploma.
- La síntesis vale ocho días y pierde peso a partir del quinto.

### Cómo está montado

- **Puente local** (`bot/puente.py`): MiroFish habla el formato de OpenAI y envía
  parámetros que los modelos actuales de Claude rechazan. El puente los adapta,
  cambia de modelo entre el debate y el informe, suma el coste y corta al llegar
  al tope (`mirofish.tope_usd`). La clave real de Claude no entra en MiroFish.
- **Imagen propia:** la oficial no existe para procesadores Apple; se construye
  con `docker build -t mirofish-local:latest .` desde `repos/MiroFish`.
- **Sin atropellar a la API:** MiroFish lanza hasta 30 peticiones a la vez y la
  cuenta admite 2 millones de tokens de entrada por minuto con Haiku; el 23 nov
  falló una de cada cinco llamadas por eso. El puente las pone en cola
  (`mirofish.llamadas_a_la_vez`, 8), reparte el envío para no pasar de
  `mirofish.tokens_por_minuto` (1,5 millones) y, si aun así la API frena,
  reintenta con esperas crecientes. Tarda algo más y no pierde llamadas. El
  reparto por minuto está probado sin carga; su primera prueba real es la
  simulación del 29 nov.
- **Red tipo Twitter, versión ligera (la que se usa desde el 24 nov):** la
  original agota la memoria de un Mac de 8 GB porque elige lo que ve cada agente
  con un modelo de más de 1 GB (`twhin-bert`). Con `recomendador_twitter:
  random` el bot monta sobre el contenedor una copia del guion de MiroFish sin
  ese modelo (no toca el repositorio). Ocupa 1 GB. En ella se debate citando
  publicaciones; la encuesta propia y el recuento de lo más valorado entienden
  las dos redes. `plataforma: reddit` vuelve a la anterior.
- **Más rondas por el mismo dinero:** en la red tipo Twitter cada agente lee
  pocas publicaciones cada vez, así que una ronda cuesta mucho menos que en la
  de tipo Reddit (33 rondas costaron 2,90 $ el 23 nov). Medido el 24 nov con
  los mismos 42 agentes: 24 horas simuladas, 0,41 $; 48 horas, 1,31 $ (404
  llamadas, 7 minutos). El coste crece con el cuadrado de las rondas, porque
  cada agente relee lo que lleva dicho: unas 85 horas simuladas por 4 $ y unos
  7 $ para las 120 que planea MiroFish. Desde el 24 nov el tope es de 9 $
  (`mirofish.tope_usd`) y el 82 % va al debate (`mirofish.parte_del_debate`):
  unos 7 $, lo justo para la semana simulada completa; si se queda corto, el
  debate se corta unas rondas antes y la encuesta la hace el bot. Las cifras
  de 85 y 120 horas son estimaciones a partir de las dos medidas. Lo guardado
  de las pruebas está en `datos/2026-11-24/prueba-twitter*/`.
- **Excepciones de un solo día** (`mirofish.solo_el`): cada fecha cambia los
  ajustes solo ese día y caduca sola. La primera es el **domingo 29 nov de
  2026**: agentes y fichas con Sonnet 5.5, 48 horas simuladas y tope de 32 $,
  para aprovechar crédito que caduca el 2 dic. El puente cambia el modelo al
  vuelo (`modelo_perfiles` para las fichas, `modelo` para el debate). Probado el
  24 nov con 9 rondas: 65 llamadas sin fallos y 1,51 $, unas 16 veces lo que
  cuesta Haiku; de ahí salen unos 21–26 $ para las 48 horas. La cuenta admite
  el mismo ritmo para Sonnet que para Haiku (2 millones de tokens de entrada
  por minuto). El presupuesto del mes aparta ese tope para ese domingo.
- **Encuesta propia:** MiroFish solo deja entrevistar a los agentes si el debate
  termina por sí solo, así que la encuesta la hace el bot dándole a cada agente
  su personalidad y lo que escribió.
- **Reanudación:** si la tarea se corta, al relanzarla el mismo día sigue desde
  el último paso hecho. `python3 -m bot.tarea mirofish-sintesis` rehace solo la
  encuesta y la síntesis.

### Lo medido el 23 de noviembre de 2026

| | Primera versión | Versión actual |
|---|---|---|
| Agentes | 90, casi todos empresas e índices | 42, todos participantes con opinión |
| Debate | 13 publicaciones y 53 comentarios | 40 publicaciones y 224 comentarios en 33 rondas |
| Encuesta | No hubo | 39 respuestas |
| Predicciones | 1 | 6 |
| Coste | 0,41 $ | 4,62 $ (debate 2,90 $, informe 0,90 $, síntesis 0,61 $) |

El debate movió 29,7 millones de tokens de entrada: cada agente relee el hilo en
cada turno. Por eso los agentes van con Haiku; con Sonnet el mismo debate
habría costado unos 58 $.

---

## Consumo y crédito de API

Estimaciones previas a la primera ejecución completa; el gasto real de cada
llamada se anota en `estado/gasto_api.jsonl`. Precios de la API a 3 de noviembre
de 2026, por millón de tokens: Fable 5.2, 8 $ de entrada y 40 $ de salida;
Opus 5.5, 4 $ y 20 $; Sonnet 5.5, 2 $ y 10 $. Las búsquedas web se cobran aparte.

| Tarea | Tokens de entrada | Tokens de salida | Coste estimado |
|---|---|---|---|
| 7:00, nivel alto | 400.000–700.000 | 25.000–35.000 | 2,5–4 $ |
| 7:00, nivel medio | 400.000–700.000 | 25.000–35.000 | 1–2 $ |
| 15:45 | 20.000–40.000 | 2.000–4.000 | 0,05–0,15 $ |
| 22:10 | 20.000–40.000 | 2.000–4.000 | 0,05–0,15 $ |

Cómo se reparte el crédito (`config/reglas.json`, bloque `credito_api`):

- Crédito mensual de 100 $, con 10 $ de reserva que el bot no toca.
- Se aparta lo que costarán la apertura y el cierre de los días de mercado que quedan.
- Lo demás se divide entre los días que quedan del mes: es el presupuesto de las 7:00 de hoy.
- Cada llamada lleva además su propio tope; si lo alcanza, se corta.
- También se aparta el tope de MiroFish (`mirofish.tope_usd`) por cada domingo que queda.

**El crédito no da para todo al máximo.** Con lo medido hasta el 24 nov (una
mañana en nivel alto, 3,15 $; en nivel medio, 1,21 $; MiroFish, hasta 9 $ por
domingo desde ese día), un mes entero de 31 mañanas, 22 días de mercado y 4
domingos deja unos 47 $ para las mañanas: 1,5 $ al día de media. Caben unas 5
mañanas en nivel alto y el resto en medio; con MiroFish a 6 $ eran unas 11.
Todas las mañanas en alto costarían 98 $ ellas solas. El bot elige solo el
nivel de cada día (ver "Niveles de análisis"); el usuario decidió el 24 nov dar
prioridad a una simulación semanal más larga y dejar los días sin mercado en
nivel medio.

El crédito no se acumula de un mes a otro. En el ciclo que acaba el 2 dic
sobrarán unos 50 $, porque el bot empezó a gastar el día 23.

## Sin construir o sin probar

| Punto | Estado |
|---|---|
| Alpaca en simulación | Probado el 23 nov 2026: dos compras fraccionadas ejecutadas y cuadradas con la cartera del bot. Las compras con acciones enteras (stop puesto en Alpaca) y las ventas están sin probar |
| Clave de API | Con saldo desde el 23 nov 2026; desde ese día las tareas gastan el crédito y no el plan |
| FRED y SEC | Probados el 23 nov 2026 |
| Tarea de las 7:00 con investigadores y estratega | Probada el 23 nov 2026 en nivel medio y con el plan: 112.000 tokens de entrada, 13.600 de salida, equivalente a 1,01 $ |
| Apertura con modelo | Probada el 23 nov 2026 (0,04 $). El cierre con modelo está sin probar |
| Programación en el Mac | Activada el 23 nov 2026. Mientras el proyecto esté en el Escritorio, necesita que `/bin/bash` tenga acceso total al disco |
| MiroFish | Funciona desde el 23 nov 2026 con una sola red. Su informe y parte del debate salen en chino; la síntesis, en español |
| Tarea de las 7:00 sobre el mercado entero | Probada el 23 nov 2026 en nivel bajo (0,39 $); en nivel alto, sin probar |
| Explicación de cada decisión en el informe de la mañana | En marcha desde el 24 nov 2026 |
| Búsqueda web de los investigadores con clave de API | Corregida el 24 nov 2026; probada con el investigador de noticias (5 turnos, 0,13 $ con Sonnet) |
| Alpha Vantage | Calendario y sorpresas de resultados conectados el 24 nov 2026. El sentimiento de noticias y la ficha de empresa son de pago |
| Finnhub | Funciona desde el 24 nov 2026: recomendaciones de analistas (12 valores) y operaciones de directivos (8 valores) cada mañana. El bot no envía a Finnhub nada que no tenga forma de clave de Finnhub |
| X (Twitter), Reddit y StockTwits | No se leen directamente: X exige su API de pago, Reddit no da acceso y StockTwits bloquea a los programas. Se usa lo que encuentra el buscador |
| Operaciones de congresistas y contraste de valoración con la SEC | Sin construir |
