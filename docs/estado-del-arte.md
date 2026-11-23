# Estado del arte: fuentes y referencias del bot

Todo lo que el bot consulta o toma como método. Cada fuente indica qué aporta,
cómo se lee y qué tarea la usa. Las tareas están descritas en
[tareas-programadas.md](tareas-programadas.md).

Acceso comprobado el 23 de noviembre de 2026 desde este Mac.

## 1. Webs de datos

| Fuente | Qué aporta | Lectura automática | Quién la usa | Frecuencia |
|---|---|---|---|---|
| [Finviz](https://finviz.com) | P/E, P/E futuro, PEG, posiciones cortas, operaciones de directivos, mapa y rendimiento por sectores | Sí, la ficha de cada valor. Sin API gratuita; conviene revisar sus condiciones de uso y leer pocas páginas al día | 7:00 (valoración y sectores) | Diaria, solo candidatos y cartera |
| [FRED](https://fred.stlouisfed.org) | Datos macro de EE. UU.: tipos, curva, inflación, paro, VIX, dólar/euro, petróleo | La web corta las peticiones automáticas. La API oficial responde y pide una clave gratuita | 7:00 (macro) | Diaria |
| Yahoo Finance | Precios de valores, índices, divisas y materias primas | Sí, por su punto de datos público (no oficial) | Las tres | En cada tarea |
| [Dataroma](https://www.dataroma.com) | Carteras de grandes inversores según sus declaraciones trimestrales (13F): qué compran y venden | Sí | 7:00 del domingo (ideas y contraste) | Semanal; el dato es trimestral y llega con hasta 45 días de retraso |
| [Damodaran (NYU)](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html) | Medias por sector: P/E, PEG, márgenes, coste de capital, prima de riesgo | Sí, hojas de cálculo descargables | 7:00 (comparar cada empresa con su sector) | Se descarga una vez; se actualiza cada enero |
| [Macrotrends](https://www.macrotrends.net) | Históricos largos: P/E de 10 a 15 años, márgenes, ingresos | No. Responde con un control anti-bots y no se va a saltar | Consulta manual tuya. El histórico del bot se calcula con datos de la SEC y precios | A demanda |
| [CNMV](https://www.cnmv.es) | Registro de entidades autorizadas en España y lista de advertencias sobre entidades no autorizadas | Sí | Comprobación previa a operar con dinero real | Antes de la fase real y si cambia el bróker |

### Series de FRED que se leen cada día

| Serie | Qué mide |
|---|---|
| `DFF` | Tipo de interés oficial efectivo |
| `DGS2`, `DGS10`, `T10Y2Y` | Bonos a 2 y 10 años y la pendiente de la curva |
| `VIXCLS` | Volatilidad esperada del S&P 500 |
| `BAMLH0A0HYM2` | Diferencial de la deuda de alto riesgo (apetito por el riesgo) |
| `CPIAUCSL`, `UNRATE` | Inflación y paro (mensuales) |
| `DEXUSEU` | Dólares por euro (el cambio que usa la cartera sale de Yahoo, en tiempo real) |
| `DCOILWTICO` | Petróleo WTI |

## 2. Repos clonados en `../repos/`

| Repo | Qué es | Papel |
|---|---|---|
| `day-trader-skill` | 11 skills en Python sin dependencias, con datos públicos gratuitos (Yahoo, Nasdaq Trader, SEC, Cámara de Representantes). Licencia MIT | Motor de datos y de riesgo: escáner, noticias, analista de resultados, tamaño de posición, control de límites |
| `MiroFish` | Simulador de opinión pública con miles de agentes. Necesita una clave de un modelo de pago y una cuenta en Zep Cloud. Licencia AGPL-3.0 | Experimento semanal y antes de eventos grandes. Su acierto se mide aparte |

Piezas de `day-trader-skill` que se usan y dónde:

| Herramienta | Uso | Tarea |
|---|---|---|
| `trader-scanner/scan.py daily` | Configuraciones de varios días en el universo | 7:00 |
| `trader-news/news.py` | Titulares y, con identificación ante la SEC, hechos relevantes (8-K) | Las tres |
| `trader-analyst/facts.py` | Trimestres, crecimiento y beneficio por acción (pide identificación ante la SEC) | 7:00 |

El resto no se usa por ahora:

- `trader-budget`, `trader-risk` y `trader-exit` calculan tamaño, límites y
  salidas para operaciones dentro del día (horas de entrada, cierre antes de las
  22:00). Para operaciones de varios días esas reglas están escritas en
  `bot/ordenes.py`.
- `trader-entry`, `trader-timemachine` y `scan.py orb` vigilan la sesión vela a vela.
- `trader-pelosi` (operaciones de congresistas) queda pendiente para el domingo.

## 3. Métodos de referencia

| Referencia | Qué se toma de ella |
|---|---|
| Prompt del asesor en cinco pasos | Aportación mensual de 200 €, qué revisar cada mes y cómo saber si la cartera va bien |
| "Maya": cinco agentes | Estructura de las 7:00: cuatro investigadores y un estratega que decide |
| Agente de noticias de la cartera | Bloque fijo de noticias de los valores en cartera en cada informe |
| P/E, P/E futuro y PEG | Filtro previo a cualquier compra (ver abajo) |
| Alpaca | Cuenta de simulación. Con dinero real desde España, sin confirmar |

### Filtro de valoración

| Dato | Lectura |
|---|---|
| P/E | Cuánto se paga hoy por cada dólar que la empresa gana |
| P/E futuro | Lo mismo con el beneficio esperado del próximo año |
| PEG | P/E ajustado por crecimiento: menos de 1, puede estar barata; entre 1 y 2, precio razonable; más de 2, cara |

Cómo lo aplica el bot:

- Las cifras salen de Finviz. Contrastarlas con un cálculo propio a partir de
  la SEC está pendiente: hoy es una sola fuente.
- El P/E futuro se muestra junto a la media de su sector según Damodaran.
- Con PEG por encima de 2 no se abre posición nueva, y tampoco sin datos de
  valoración. Lo aplica el código, no el modelo.
- Empresas sin beneficios en los últimos doce meses (habituales en renovables
  y nuclear pequeñas) no tienen P/E ni PEG: se permiten, con aviso en el informe.
- No se compra a dos días o menos de la presentación de resultados.

## 4. Universo y vigilancia

- Acciones de EE. UU. grandes, medianas y pequeñas.
- Sectores: energías renovables, nuclear, tecnología y defensa.
- NVIDIA en vigilancia prioritaria, con bloque propio en el informe de las 7:00.
- Divisas y materias primas se analizan como contexto; no se compran.
