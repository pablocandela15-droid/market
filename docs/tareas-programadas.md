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
  - `clave`: con la clave de API, en modo `--bare`. Gasta el crédito mensual de
    API y nunca el uso del plan.
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
| Salida | Un PDF de una página por tarea en `Informes/<Mes>/<día>/`, en la carpeta del proyecto |

Todas se cambian en `config/reglas.json`.

---

## Tarea 1 · 7:00 · Informe de la mañana

**Días:** todos.

### Casos de uso

- Saber en una página cómo amanece el mercado y cómo va la cartera.
- Fijar el plan del día: qué comprar o vender, con entrada, stop y objetivo.
- Seguir de cerca NVIDIA y los cuatro sectores.
- Sábado: resumen de la semana. Domingo: preparación del lunes con los grandes inversores.

### Flujo

| Paso | Quién | Qué revisa | Dónde |
|---|---|---|---|
| 0 | Script | Índices, VIX, bono, euro/dólar, futuros, Europa y Asia | Yahoo |
| 0 | Script | Fondos de cada sector y materias primas | Yahoo |
| 0 | Script | Los 70 valores del universo: precio, tendencia, RSI, rango diario | Yahoo |
| 0 | Script | Configuraciones técnicas de varios días | `trader-scanner daily` |
| 0 | Script | P/E, P/E futuro, PEG y fecha de resultados de hasta 20 candidatos | Finviz |
| 0 | Script | Media de P/E y PEG de cada sector | Damodaran |
| 0 | Script | Titulares de cartera, prioritarios y candidatos | `trader-news` |
| 0 | Script | Tipos, curva, inflación, paro (con clave) | API de FRED |
| 0 | Script | Trimestres y crecimiento (con identificación) | `trader-analyst` (SEC) |
| 0 | Script | Stops y objetivos alcanzados desde el último informe | Cartera y Alpaca |
| 1 | Investigador de noticias | Qué mueve el precio y qué ánimo hay | Dossier y búsqueda web |
| 1 | Investigador de sectores | Tendencia y valoración por sector | Dossier y búsqueda web |
| 1 | Investigador de macro | Entorno, agenda económica y euro/dólar | Dossier y búsqueda web |
| 1 | Investigador de materias primas | Energía y metales, y a qué valores afectan | Dossier y búsqueda web |
| 2 | Estratega | Une los cuatro informes con la cartera y las reglas; escribe el plan | — |
| 3 | Script | Aprueba o bloquea cada orden y calcula su tamaño | `bot/ordenes.py` |
| 4 | Script | Compone el PDF | `Informes/<Mes>/<día>/0700-manana.pdf` |

Los investigadores trabajan en paralelo. Los domingos el paso 0 añade lo que
hacen los grandes inversores con hasta 10 valores (Dataroma).

### Niveles de análisis

Antes de empezar, el bot reparte lo que queda del crédito del mes entre los
días que quedan y elige el nivel más alto que cabe:

| Nivel | Investigadores | Estratega | Coste estimado |
|---|---|---|---|
| Alto | 4, con Fable 5.2 | Fable 5.2 | 3,5 $ |
| Medio | 4, con Sonnet 5.5 | Fable 5.2 | 1,6 $ |
| Bajo | 2 (noticias y sectores), con Sonnet 5.5 | Opus 5.5 | 0,6 $ |
| Sin modelo | — | — | 0 $ |

Las estimaciones se sustituyen solas por la media real de las últimas cinco ejecuciones.

---

## Tarea 2 · 15:45 · Apertura

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

## Sin construir o sin probar

| Punto | Estado |
|---|---|
| Alpaca en simulación | Probado el 23 nov 2026: dos compras fraccionadas ejecutadas y cuadradas con la cartera del bot. Las compras con acciones enteras (stop puesto en Alpaca) y las ventas están sin probar |
| Clave de API | Con saldo desde el 23 nov 2026; desde ese día las tareas gastan el crédito y no el plan |
| FRED y SEC | Probados el 23 nov 2026 |
| Tarea de las 7:00 con investigadores y estratega | Probada el 23 nov 2026 en nivel medio y con el plan: 112.000 tokens de entrada, 13.600 de salida, equivalente a 1,01 $ |
| Apertura con modelo | Probada el 23 nov 2026 (0,04 $). El cierre con modelo está sin probar |
| Programación en el Mac | Activada el 23 nov 2026. Mientras el proyecto esté en el Escritorio, necesita que `/bin/bash` tenga acceso total al disco |
| MiroFish | Clonado, sin configurar ni integrar |
| Operaciones de congresistas y contraste de valoración con la SEC | Sin construir |
