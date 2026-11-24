# Hacia una terminal completa: qué hay, qué falta y qué pedir

Revisión hecha el 23 de noviembre de 2026. Los precios de terceros salen de
páginas de comparativas y de los propios proveedores; hay que confirmarlos en
cada web antes de contratar nada.

## Lo que dice la evidencia

Antes de añadir piezas conviene saber qué se puede esperar de ellas:

- Los agentes de inteligencia artificial que operan solos **no baten al mercado
  de forma consistente**. Un repaso de once competiciones públicas encontró que
  el modelo mediano solo ganaba dinero en dos; los estudios académicos con
  dinero simulado (StockBench, LiveTradeBench) llegan a la misma conclusión.
- Donde sí funcionan bien es en lo que hace una terminal: reunir información,
  resumirla y no olvidar nada.
- Por eso el bot mide siempre contra "el mismo dinero en el S&P 500" y
  comprueba con precios reales las predicciones de MiroFish. Más datos dan
  mejores informes; no garantizan mejores resultados.

## Las capas de una terminal

| Capa | Qué tiene hoy el bot | Siguiente paso | Qué hace falta |
|---|---|---|---|
| Precios | Yahoo (no oficial, con retraso), 533 valores en 27 s | Datos de mercado de Alpaca: tiempo real, valores que más se mueven | Nada: tus claves de Alpaca ya lo incluyen |
| Sectores y mercados | Clasificación propia de 11 sectores y 4 temas; 20 índices y divisas | Flujos de dinero hacia fondos por sector | Fuente por decidir |
| Valoración | P/E, P/E futuro y PEG de Finviz; medias de Damodaran; recomendaciones de analistas (Finnhub) | Revisiones de estimaciones y precio objetivo | Plan de pago de Finnhub |
| Resultados | Trimestres de la SEC; fechas confirmadas y sorpresas de los últimos 4 trimestres (Alpha Vantage) | Transcripciones de las presentaciones | Fuente por decidir |
| Noticias | Titulares de Yahoo y búsqueda web de los investigadores | Noticias de Benzinga por valor | Nada: van con Alpaca |
| Sentimiento de noticias | Ninguno | Puntuación por valor y por tema | Plan de pago de Alpha Vantage (el gratuito no lo incluye) |
| Inversores particulares | Lo que el buscador devuelve de X y de los foros (2–3 búsquedas del investigador de noticias) | Lectura directa de X | API de pago de X. Reddit no da acceso y StockTwits bloquea a los programas |
| Directivos | Compras y ventas en mercado de los últimos 90 días (Finnhub) | Detalle de cada operación (Form 4) | Nada: SEC |
| Grandes inversores | Dataroma, los domingos | Posiciones trimestrales (13F) de cualquier fondo | Nada: SEC |
| Congresistas | Herramienta clonada, sin conectar | Conectarla, o Quiver | Nada, o Quiver (de pago) |
| Opciones | Ninguno | Ratio de opciones de venta y de compra, volatilidad implícita | Nada: datos básicos de Alpaca |
| Flujo de opciones y operaciones fuera de mercado | Ninguno | Órdenes grandes en tiempo real | Unusual Whales (de pago, caro) |
| Macro | Diez series de FRED | Calendario de publicaciones, posicionamiento en futuros (CFTC) | Nada: misma clave de FRED y datos públicos |
| Debate | MiroFish semanal | Agentes con un modelo mayor | Más crédito de API, o un equipo con más memoria |
| Ejecución | Alpaca en simulación | — | — |

## Qué pedirte

### Gratis (por orden de utilidad)

1. **Finnhub** — finnhub.io, "Get free API key". Recomendaciones de analistas
   y operaciones de directivos. **Conectada el 24 nov.** La clave aparece en
   finnhub.io/dashboard como "API Key" (letras minúsculas y números) y va en la
   línea `FINNHUB_API_KEY` de `market/.env`.
2. **Alpha Vantage** — conectada el 24 nov. El plan gratuito da calendario y
   sorpresas de resultados, 25 consultas al día. El sentimiento de noticias es
   de pago.
3. **Reddit** — descartado: no concede el acceso.
4. **X (Twitter)** — sin su API de pago no se puede leer con un programa sin
   saltarse sus protecciones, y eso el bot no lo hace. Se usa lo que devuelve
   el buscador.

### De pago (ninguno es necesario para empezar)

| Servicio | Qué añade | Precio orientativo | Mi opinión |
|---|---|---|---|
| Quiver Quantitative | Congresistas, directivos, 13F, contratos públicos, operaciones fuera de mercado | 20–30 $/mes | El más razonable si quieres "dinero inteligente" sin montarlo a mano |
| Massive (antes Polygon) | Precios en tiempo real de todo el mercado | Tiene plan gratuito limitado; los de pago, a confirmar | Solo si Alpaca se queda corto |
| EODHD o Financial Modeling Prep | Fundamentales y estimaciones de todo el mundo en una sola fuente | Unos 80 $/mes (EODHD) | Solo si quieres salir de EE. UU. |
| Unusual Whales (API) | Flujo de opciones y operaciones fuera de mercado | 125–150 $/mes | Caro para una cartera de 1.000 € |

### Lo que limita hoy más que los datos

- **Crédito de API: 100 $ al mes para todo.** El análisis diario con Fable se
  lleva la mayor parte. Agentes de MiroFish con Sonnet serían unos 18–20 $ por
  simulación.
- **Memoria del Mac: 8 GB.** Obliga a simular una sola red social en MiroFish.

## Herramientas que conviene conocer

- **OpenBB** — plataforma de código abierto que da acceso a casi cien fuentes
  de datos con una sola interfaz. Candidata a sustituir las lecturas hechas a
  mano de Finviz y Yahoo.
- **TradingAgents** — marco de código abierto con analistas, debate y gestor de
  riesgo. Es el mismo esquema que ya sigue el bot; sirve de referencia.
- **Conectores oficiales (MCP)** de Alpaca, Massive y Quiver — permiten
  consultar esos datos desde una conversación con Claude.

## Fuentes

- [OpenBB: plataforma](https://openbb.co/products/platform)
- [Comparativa de APIs de datos de mercado, 2026](https://eodhd.com/financial-academy/financial-faq/the-2026-market-data-api-scorecard-comparing-6-leading-providers)
- [Mejores servidores MCP de datos financieros, 2026](https://geekflare.com/guides/best-financial-data-mcp-servers/)
- [TradingAgents](https://github.com/TauricResearch/TradingAgents)
- [Servidor MCP oficial de Alpaca](https://github.com/alpacahq/alpaca-mcp-server)
- [Los bots de IA que se prueban en bolsa pierden dinero en su mayoría](https://www.business-standard.com/markets/news/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing-money-126050701793_1.html)
- [StockBench](https://arxiv.org/abs/2510.02209) y [LiveTradeBench](https://arxiv.org/abs/2511.03628)
- [Quiver Quantitative: precios de la API](https://api.quiverquant.com/pricing/)
- [Unusual Whales: precios de la API](https://unusualwhales.com/pricing?product=api)
- [Acceso a la API de Reddit en 2026](https://www.activepieces.com/blog/reddit-api-access-in-2026-how-to-get-started)
