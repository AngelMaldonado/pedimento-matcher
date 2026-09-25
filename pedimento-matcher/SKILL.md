---
name: pedimento-matcher
description: Cotejar Previo (fotos) contra Factura (Partidas) y Proforma/Pedimento (Secciones) en despacho aduanal mexicano, y generar un reporte de discrepancias con evidencia. Usar cuando el usuario pida validar, cotejar o auditar una Proforma/Pedimento contra su Factura y fotos de Previo.
license: MIT
---

Contrato de invocación: argumento opcional = ruta del directorio de trabajo. Sin argumento, usa el directorio actual (cwd).

Diseño: jerarquía de verdad (Previo > Factura > sin_evidencia), matching determinista con gates humanos agrupados, pipeline de 10 pasos -- documentado paso a paso mas abajo en este archivo.

**Estado actual: Pasos 1-10 implementados y corridos end-to-end sobre el piloto real (v0.0.1).** Checklist de campos: 10 (`modelo` y `codigo_producto` van separados desde el refinamiento posterior a v0.0.1 — ver Paso 5).

## Paso 1 — Preflight

Layout esperado al final de esta etapa:

```
<directorio>/
  previo/fotos_vN/P<n>/*.jpg          # fotos de Previo, agrupadas por partida
  facturas/invoice_vN/invoice_vN.pdf  # Factura
  proformas/proforma_vN/proforma_vN.pdf  # Proforma / Pedimento borrador
  artifacts/                          # reportes generados (vacío al inicio)
```

1. Inspecciona `<directorio>` (no recursivo salvo para confirmar patrones):
   - ¿Existen ya `previo/`, `facturas/`, `proformas/`, `artifacts/` con contenido consistente con el layout de arriba? → **layout ya esquematizado**, repórtalo y detente (nada que reorganizar).
   - Si no, busca sueltos en la raíz: PDFs cuyo nombre o contenido sugiera Factura/Invoice, PDFs que sugieran Proforma/Pedimento, y carpetas de fotos agrupadas por partida (patrón `P<n>` o similar). → **layout suelto**.
   - Cualquier archivo que no encaje en ninguna categoría (p. ej. un reporte de referencia ya existente) no se mueve por default; se reporta aparte y se pregunta qué hacer solo si genera ambigüedad real.

2. Si el layout está suelto, construye un **plan concreto de movimientos** — cada línea `origen -> destino`, con nombres de versión `_v1` la primera vez que se ve una Factura o Proforma en esa corrida. No inventes una segunda versión si solo hay una.

3. **Nunca ejecutes el plan sin confirmación explícita.** Esto es no negociable, independiente de cualquier otro gate del diseño general. Usa `AskUserQuestion` mostrando el plan exacto de movimientos (cada `origen -> destino`) y pide confirmar/ajustar/cancelar antes de tocar un solo archivo.

4. Solo tras confirmación explícita, ejecuta los movimientos (crear carpetas destino, mover archivos — preferir `git mv` si el archivo está trackeado, `mv` si no). Si el usuario ajusta el plan, aplica el ajuste, no el plan original.

5. Reporta al final: layout detectado al inicio, plan propuesto, y si se ejecutó o quedó pendiente de confirmación.

## Paso 2 — Merge de Previo

Si `<directorio>` ya no tiene layout esquematizado (Paso 1 pendiente), detente
y corre el Paso 1 primero.

Corre `scripts/merge_previo.py <directorio>`. Fusiona los paquetes
`previo/fotos_vN/` de forma **acumulativa**: si una partida (`P<n>`) aparece en
más de un paquete, gana el paquete con `N` más alto **completo** para esa
partida — no se mezclan fotos individuales de dos paquetes distintos dentro de
la misma partida, gana la carpeta entera más reciente. Escribe
`previo/merged.json` con el mapeo `partida -> paquete ganador + lista de
fotos`. No copia ni mueve nada — `fotos_vN/` sigue siendo la fuente de verdad
en disco; `merged.json` es lo que el Paso 4 (no implementado todavía) debe
leer para saber qué carpeta usar por partida.

Con un solo paquete (`fotos_v1`) el merge es trivial: todas las partidas
vienen de `fotos_v1`. La lógica ya queda correcta para cuando exista
`fotos_v2` — no hace falta tocarla entonces.

## Paso 3 — Parseo de Factura (crudo, sin API externa)

**Decisión de diseño:** el parseo es visión de Claude
sobre páginas renderizadas + `pdftotext -layout` como apoyo determinístico.
Nunca una API de extracción externa — se evaluó y se descartó explícitamente
por no dar bounding boxes utilizables para recortar filas y por truncar en
tablas largas repetidas.

Checklist fijo de campos por Partida: NP (número de parte), Marca, Modelo/Código
producto, Lote, Número de serie, País de origen, Cantidad/Peso, Descripción,
Fracción arancelaria. **Si la Factura no trae una columna para un campo del
checklist, ese campo se marca `"sin_evidencia"` — nunca se inventa ni se
infiere de otra columna.**

Procedimiento:

1. Localiza `facturas/invoice_vN/invoice_vN.pdf` (la versión más alta).
2. Corre `scripts/prepare_invoice_pages.py facturas/invoice_vN/invoice_vN.pdf facturas/invoice_vN/` (soporte determinístico, no vision):
   - Renderiza cada página a `facturas/invoice_vN/pages/page-NN.png` (una imagen completa por página — necesario porque la cabecera y otros elementos pueden venir rasterizados dentro del PDF, no como texto).
   - **Autodetecta la columna "NO." de partida** — no asume ninguna posición horizontal fija: agrupa por posición todas las palabras que son enteros puros de todo el documento y elige la banda vertical que sostiene la corrida ascendente 1, 2, 3... más larga sin huecos. Esto generaliza entre Facturas de distinto layout; si ninguna banda sostiene una secuencia creíble, el script se detiene con error en vez de seguir de largo con una columna equivocada.
   - Ancla cada fila a las **líneas divisorias reales de la tabla** (detectadas en el PNG renderizado, no con un padding fijo sobre el texto — el padding interno de celda varía de fila a fila) y recorta cada fila en `facturas/invoice_vN/crops/partida-NN.png`.
   - Escribe `facturas/invoice_vN/rows.json` con la geometría (página, bbox, ruta del crop) de cada Partida detectada — **esto es soporte de geometría, no el contenido**: los valores de campo todavía no están ahí.
   - **Gate de silencio:** si una página no produce ninguna fila pero `pdftotext -layout` de esa página sugiere contenido tabular (varias líneas con columnas alineadas), el script lo reporta como advertencia explícita en su salida — nunca sigue de largo callado asumiendo que esa página simplemente no tenía partidas.
   - Bug de entorno documentado en el propio script: `pdftotext -bbox` truena (SIGABRT) en poppler 26.04.0 si el PDF tiene `/Keywords` vacío en su Info dict — el script lo esquiva generando una copia temporal con `pypdf` con metadata parcheada, sólo para extraer la geometría; el PDF original y el render de páginas no se tocan.
3. **La factura puede tener páginas sin tabla** (portada/cabecera rasterizada, página de totales al final) — no asumas que la tabla ocupa el documento completo ni que cabe en una sola pasada de lectura. Recorre **cada página renderizada con visión, una por una** (o agrupando pocas páginas por llamada, nunca las 13 de un jalón) para no caer en el failure mode de truncado en tablas largas documentado en el diseño.
4. Para cada Partida detectada en `rows.json`, lee su crop (y la página completa si el crop no trae suficiente contexto de columna) con visión, cruza contra el texto de `pdftotext -layout -f <pagina> -l <pagina>` de esa misma página como apoyo determinístico, y llena los campos del checklist. Marca `"sin_evidencia"` los que la Factura no cubre.
5. Ensambla `facturas/invoice_vN/invoice_vN.json`: lista de objetos, uno por Partida, con todos los campos del checklist + `crop` (ruta relativa) + `pagina_pdf` (de dónde salió). Verifica que el conteo de Partidas en el JSON sea consistente y sin huecos en la numeración; si hay un hueco o una Partida duplicada, es un gate — repórtalo, no lo silencies.

## Paso 4 — Análisis de fotos de Previo (subagentes en paralelo, sólo extracción)

Si `previo/merged.json` no existe, detente y corre el Paso 2 primero.

**A diferencia de la Factura, no hace falta script de recorte/geometría.** Cada
foto ya es la unidad de evidencia completa — no hay una tabla continua que
recortar en filas. El único soporte determinístico es leer
`previo/merged.json` para saber qué carpeta de fotos corresponde a cada
partida.

Checklist fijo de campos (el mismo que Factura, para que Paso 6 compare ambos
JSON directo): NP, Marca, Modelo/Código producto, Lote, Número de serie, País
de origen, Cantidad/Peso, Descripción, Fracción arancelaria. Igual que en
Paso 3: si no se ve en las fotos, `"sin_evidencia"` — nunca se inventa ni se
infiere de otro campo (ni de otra foto de la misma partida).

**Un subagente por partida.** Cada subagente:
- Recibe la lista de rutas absolutas de fotos de su carpeta (de
  `previo/merged.json` → `partidas.P<n>.path` + `.fotos[]`).
- Lee cada foto con su herramienta de visión (Read).
- Hace SOLO extracción — nunca matching, nunca comparación contra Factura,
  nunca juicio de si algo "está mal". Eso es Paso 6+.
- Devuelve un único JSON con el checklist de campos + una nota de qué foto(s)
  respaldan cada valor no-`sin_evidencia` (nombre de archivo) + una nota libre
  de anomalías de calidad de imagen (borrosa, mal ángulo, muy oscura) si
  aplica — sin inferir contenido a partir de eso, sólo reportarlo.
- Si la carpeta no tiene fotos legibles (vacía, o todas corruptas/ilegibles),
  el subagente lo marca explícito — esa partida entra al consolidado en su
  propia categoría de "sin evidencia fotográfica", nunca se cae en silencio.

**Paralelismo:** lanza los subagentes en tandas razonables (p. ej. bloques de
6-10 concurrentes vía Agent tool), no las 53 en un solo instante si la
herramienta lo vuelve poco práctico — pero corre las 53 partidas reales,
nunca una muestra.

**Consolidado:** `previo/fotos_v1/previo_v1.json` (o ruta equivalente
versionada junto al paquete de fotos usado) — lista de objetos, uno por
Partida, con: número de partida, checklist de campos, carpeta/fotos que
respaldan cada valor, categoría de evidencia (`completa` / `parcial` /
`ninguna`), y notas de calidad de imagen si las hubo. Verifica que las 53
partidas de `merged.json` tengan entrada en el consolidado — un hueco es un
gate, repórtalo.

**Tipos de campo — para que Paso 6 pueda comparar contra `invoice_vN.json` sin
parsear texto libre:**
- `cantidad` es **numérico** (`int`) cuando el valor es limpio y sin
  ambigüedad — igual que en `invoice_vN.json` — nunca string (ni siquiera
  `"1,500"`). `"sin_evidencia"` sigue siendo el único string válido para ese
  campo cuando no hay evidencia.
- **Contradicción real dentro de la misma partida** (dos fotos de la misma
  carpeta muestran valores genuinamente distintos para el mismo campo —
  típicamente `cantidad`, pero puede pasar con cualquier campo del
  checklist): el campo deja de ser un valor escalar y pasa a ser un **array
  de candidatos**, cada uno `{"valor": ..., "fotos": [...]}` (y `"unidad"`
  cuando aplica a `cantidad`) — nunca se aplasta en un string humano-legible
  tipo `"1500 / 750 - valores distintos"`. No es trabajo de este paso
  resolver cuál candidato es el correcto; eso es Paso 6/7 vía gate humano.
  Un campo con más de un código/valor visible pero que **no** es una
  contradicción limpia de dos lecturas del mismo dato (p. ej. un MPN de
  fabricante y un código de distribuidor distinto para el mismo producto, o
  varios códigos sueltos sin indicación de cuál es cuál) se queda como string
  con el valor más plausible, y la ambigüedad va a `notas_calidad` — no todo
  texto con "/" o dos números es una contradicción estructurable.

**Nota pendiente para una futura pasada del Paso 4 (no aplicada a las 51
partidas restantes, sólo documentada):** al corregir P26 y P47 tras un cruce
con el reporte de referencia humano (ver evaluación de Paso 7 más abajo), se
encontraron dos patrones puntuales que podrían valer la pena atacar en el
prompt de extracción, no sólo en estos 2 casos:
- **Fotos boca abajo (180°):** P26 tenía `marca` en `sin_evidencia` porque la
  etiqueta clave estaba fotografiada invertida — al rotarla se leyó sin
  problema. El subagente no intentó "leer mentalmente" el texto invertido, lo
  marcó directamente como ilegible.
- **Autocensura ante incertidumbre real pero resoluble:** P47 ya traía en
  `notas_calidad` "posible marca, borroso, no se puede leer con certeza" —
  es decir, el subagente SÍ notó que probablemente había una marca ahí, pero
  optó por `sin_evidencia` en vez de intentar confirmarla con más atención
  antes de rendirse (correcto no inventar, pero el umbral de "me rindo" pudo
  ser más alto).

No se sabe si esto es un patrón amplio en las 51 partidas no revisadas de
nuevo — sólo se vio en estos 2 casos puntuales, encontrados porque un reporte
humano ya los había señalado. No se reabrió el resto del dataset para esta
corrección puntual.

## Paso 5 — Parseo de Proforma (crop de Secciones + proforma_vN.json)

Si `facturas/invoice_vN/invoice_vN.json` no existe, detente y corre el Paso 3
primero.

**La Proforma no es una Factura con otro layout — es otra estructura.** En la
Factura cada Partida es una fila simple de una tabla. En la Proforma (en
realidad un Pedimento aduanal mexicano; "PARTIDAS" es el encabezado de esa
sección del documento) cada **Sección** es un **bloque** de varias líneas: una
fila de cabecera con el número SEC + columnas (FRACCION, NICO, VINC, MET VAL,
UMC, CANTIDAD UMC, UMT, CANTIDAD UMT, P.V/C, P.O/D, CON, TASA, T.T., F.P.,
IMPORTE), debajo la DESCRIPCIÓN, debajo una fila de valores numéricos (VAL
ADU/USD, IMP. PRECIO PAG., PRECIO UNIT., VAL. AGREG.), a veces una MARCA,
a veces una o más filas IDENTIF./COMPLEMENTO (NOM-xxx), y por último NP: (o
NS: cuando no hay número de parte) + opcionalmente LOTE: + la línea "Factura
\<n\> Partida \<m\>" — la referencia cruzada que el propio documento hace hacia
la Factura. Cada bloque cierra con la barra gris "OBSERVACIONES A NIVEL
PARTIDA" (con o sin texto real debajo) antes de que empiece el siguiente
bloque. El número SEC **no tiene por qué arrancar en 1** — la autodetección de
columna busca la corrida ascendente consecutiva más larga en cualquier banda
vertical, sin asumir valor de arranque (en el piloto sí arrancó en 1, pero el
código no lo asume).

Mismo checklist fijo de 10 campos que Factura y Previo (np, marca, modelo,
codigo_producto, lote, numero_serie, pais_origen, cantidad, descripcion,
fraccion_arancelaria) — `"sin_evidencia"` lo que la Sección no traiga, nunca
inventado ni inferido de otra columna. `modelo` y `codigo_producto` se
separaron (antes un solo campo `modelo_codigo_producto`) porque el layout de
la Proforma trae dos columnas dedicadas distintas (MODELO | CODIGO PRODUCTO,
ver captura del usuario) y porque Previo mostró casos reales donde son datos
distintos (P52: modelo del fabricante vs código interno de LCSC, ambos
rotulados en la misma etiqueta).

**Dos campos extra por Sección** (no forman parte del checklist, son clave de
matching para el Paso 6):
- `factura_partida_citada`: `{"factura": <n de factura citado>, "partida_citada": <m>}`, tal cual los cita la propia línea "Factura \<n\> Partida \<m\>" del bloque — **aunque `m` no coincida con el número SEC**. Esa posible discrepancia es justo lo que Paso 6/7 tiene que detectar; este paso sólo la captura, nunca la resuelve ni la silencia. `null` si el bloque no trae esa línea (no debería pasar; gate si ocurre).
- `observaciones_a_nivel_partida`: el texto libre bajo la barra gris "OBSERVACIONES A NIVEL PARTIDA", tal cual, o `null` si la barra no trae contenido real (el diseño anota que a veces cita que una Sección agrupa más de una Partida de Factura — eso dispara un gate en Paso 6, acá sólo se captura).

Procedimiento:

1. Localiza `proformas/proforma_vN/proforma_vN.pdf` (la versión más alta).
2. Corre `scripts/prepare_proforma_pages.py proformas/proforma_vN/proforma_vN.pdf proformas/proforma_vN/` (soporte determinístico, no vision):
   - Renderiza cada página a `pages/page-NN.png`.
   - **Autodetecta la columna SEC** igual de riguroso que la columna NO. de Factura (corrida ascendente más larga, banda vertical sin posición fija), con un filtro adicional necesario en este documento: sólo se consideran candidatos que son **el token más a la izquierda de su fila** (`_leftmost_in_row`). Sin ese filtro, el sufijo numérico de la propia línea "Factura \<n\> Partida \<m\>" (p. ej. el "7" de "Partida 7") compite con SEC — también son enteros puros y también ascienden 1, 2, 3... de Sección en Sección — y puede ganarle: verificado en el piloto, esa banda falsa produjo una corrida de 54 (arrancando en 0) contra las 53 reales de la columna SEC, y sin el filtro el script habría recortado por la columna equivocada en silencio.
   - Ancla cada recorte a las **líneas divisorias reales del bloque completo** (detectadas por análisis de píxeles del PNG renderizado, igual que Factura): el techo de la Sección N es la línea real inmediata arriba de su propia fila de cabecera, y el piso es la línea real inmediata arriba de la fila de cabecera de la Sección N+1 (o, para la última Sección de una página, la línea real inmediata arriba del pie de página fijo "AGENTE ADUANAL..." — localizado dinámicamente por su propio texto, no una coordenada fija). Esto trae el bloque completo (cabecera + descripción + valores + marca + posibles filas IDENTIF./COMPLEMENTO + NP/NS + LOTE + la línea Factura...Partida + la barra gris de Observaciones) en un solo recorte, sin depender de contar cuántas filas internas trae cada bloque — varía: algunas Secciones traen filas IDENTIF./COMPLEMENTO que otras no traen, y no todas traen MARCA.
   - Escribe `rows.json` con la geometría (página, bbox, ruta del crop) de cada Sección — sólo geometría, no contenido.
   - **Gate de silencio:** igual que Factura, si una página no produce ninguna Sección pero `pdftotext -layout` de esa página sugiere contenido tabular, se reporta como advertencia explícita.
   - **Gate contra el total impreso:** el propio Pedimento imprime al final "\*\*\*\*\* FIN DE PEDIMENTO \*\*\*\*\*\* NUM. TOTAL DE PARTIDAS: N \*\*\*\*\*" — el script cruza ese N contra el total de Secciones detectadas y advierte si no coinciden (hueco, duplicado, o falla de autodetección).
   - Reusa el mismo workaround del Paso 3 para el bug de poppler 26.04.0 (`pdftotext -bbox` truena con SIGABRT si `/Keywords` viene vacío) — **se repitió tal cual en esta Proforma**, confirmando que no es específico de la Factura piloto.
3. Recorre **cada página renderizada con visión, una por una** (nunca las 9 de un jalón), cruzando contra `pdftotext -layout -f <pagina> -l <pagina>` de esa misma página como apoyo determinístico — en este documento (Pedimento generado por sistema, texto nativo no escaneado) `pdftotext -layout` resultó extremadamente confiable y ya trae prácticamente cada campo en texto plano, pero la vista de página completa sigue siendo necesaria para no perderse una anomalía de layout que el texto por sí solo no delata (ver hallazgos abajo).
4. Para cada Sección detectada en `rows.json`, llena los 10 campos del checklist + los 2 campos extra, cruzando el recorte con el texto de layout de esa página. Marca `"sin_evidencia"` lo que la Sección no cubra. Verifica el conteo de Secciones sin huecos ni duplicados en la numeración SEC — es un gate, repórtalo si ocurre (no en este piloto).
5. Ensambla `proformas/proforma_vN/proforma_vN.json`: mismo shape que `invoice_vN.json` (`version`, `fuente_pdf`, `total_secciones`, `checklist_campos`, `secciones[]`) + los 2 campos extra por sección + `pagina_pdf` + `crop`.

**Hallazgos del piloto (Proforma Q0000000, 53 Secciones, 9 páginas):**

- Columna SEC autodetectada en x=[30.3, 47.7] pt, arranca en 1 — 53/53 Secciones, sin huecos ni duplicados, coincide exacto con el "NUM. TOTAL DE PARTIDAS: 53" impreso al final del documento.
- De los 10 campos del checklist, esta Proforma sólo trae evidencia directa para 6: NP (52/53 — ver más abajo), Marca (19/53), Lote (12/53), Número de serie (1/53), País de origen (53/53, columna P.O/D = "CHN" en todas), Cantidad (53/53, columna CANTIDAD UMC — coincide exacto con la Cantidad de `invoice_v1.json` sección por sección, p. ej. Sección 1 = 1500, igual que Partida 1 de Factura) y Descripción (53/53, versión corta tipo categoría — p. ej. "DIODOS", no la descripción larga de Factura). Modelo/Código producto trae columna dedicada en el layout (MODELO | CODIGO PRODUCTO) pero viene vacía en las 53 Secciones — `"sin_evidencia"`, no inventado. Fracción arancelaria = columna FRACCION (8 dígitos), 53/53.
- **Anomalía real de layout — Sección 37 no trae NP:** en vez de "NP: \<código\>" trae "NS: BA000925072917115" (Número de Serie) seguido de "LOTE: 000925072900327" — es la única de las 53 sin número de parte. El script/checklist lo capturó correcto: `np: "sin_evidencia"`, `numero_serie: "BA000925072917115"` — nunca se copió el NS al campo NP ni viceversa.
- **Anomalía real de layout — mayúsculas inconsistentes:** en las 12 Secciones que traen línea LOTE: (17, 20, 26, 35, 37, 39, 40, 43, 45, 47, 49, 52), la línea de referencia cruzada se imprime en mayúsculas ("FACTURA FACT2026-0001 PARTIDA 17") en vez del formato normal en mayúscula/minúscula ("Factura FACT2026-0001 Partida 18") que usan las otras 41 Secciones. Mismo contenido, formato distinto — el parseo lo trata case-insensitive a propósito por este hallazgo.
- **Cruce SEC vs. Partida citada — sin discrepancias en este piloto:** las 53 Secciones citan "Factura FACT2026-0001 Partida N" con N == su propio número SEC. No hay reordenamiento ni split/merge visible en esta Proforma — el campo `factura_partida_citada` queda listo para cuando Paso 6/7 sí necesite detectarlo en otra corrida.
- **Sin observaciones reales:** las 53 barras "OBSERVACIONES A NIVEL PARTIDA" están vacías (sin texto debajo) — `observaciones_a_nivel_partida: null` en las 53. El campo queda estructurado para cuando sí traigan contenido.
- 4 Secciones (2, 23, 33, 42) tienen filas IDENTIF./COMPLEMENTO (NOM-xxx) entre la fila de valores y la barra de Observaciones — ninguna de ellas trae MARCA (el layout nunca combina ambas en las Secciones observadas); esto se validó explícitamente en el parseo, no se asumió.

## Paso 6 — Matching centralizado (Partida ↔ Sección)

Si `facturas/invoice_vN/invoice_vN.json` o `proformas/proforma_vN/proforma_vN.json`
no existen, detente y corre los Pasos 3/5 primero.

**Este paso corre centralizado en el flujo principal — no se delega a
subagentes.** Es lógica determinística + fallback de juicio sobre datos ya
extraídos; no requiere releer imágenes. Emparejar Partida↔Sección solamente —
**no compara valores de campo entre sí** (eso es Paso 7, el diff).

`scripts/match_partidas_secciones.py <directorio>` implementa el motor:

**Señales, en orden de peso** (ninguna es oráculo infalible por sí sola):
NP (peso 3) > Cantidad (peso 2) > Marca / Modelo-código / Lote / Número de
serie (peso 1 cada uno). Un campo `"sin_evidencia"` en cualquiera de los dos
lados no cuenta ni a favor ni en contra — sólo se compara donde hay evidencia
real en ambos. `cantidad` compara por intersección de valores candidatos (así
funciona igual si algún lado trae un array de conflicto tipo `previo_vN.json`,
aunque en Factura/Proforma de este piloto siempre es un `int` limpio).

`factura_partida_citada` es una **señal adicional de corroboración** (peso
+2 si el número de Partida citado coincide con el candidato evaluado), nunca
la única fuente de verdad — la cita puede estar mal escrita por el agente
aduanal. Si el resto de señales coincide fuerte pero la cita apunta a otra
Partida, es su propio hallazgo (`confianza: "media"`, gate con motivo
explícito) — el script nunca decide solo cuál de las dos tiene razón.

**Clasificación de confianza:**
- **`alta` (automático, `needs_gate: false`)**: NP y Cantidad coinciden (las
  dos señales requeridas) sin discordancias en ningún otro campo, y la cita
  corrobora (o no está presente, caso raro).
- **`media`/`baja` (`needs_gate: true`)**, con motivo explícito según el caso:
  acuerdo parcial (falta NP o Cantidad), señales contradictorias (un campo
  coincide pero otro discorda de plano), cita que no coincide con el resto de
  señales, empate real entre ≥2 candidatos con el mismo score, o falta de
  reciprocidad (la Partida elegida por esta Sección no elige a su vez a esta
  Sección como su mejor candidata).

**Detección de N:N (split/merge)** — nunca se aplica sola, siempre
`needs_gate: true`: (1) más de una Sección citando la misma Partida
(`factura_partida_citada.partida_citada` repetido) — se reporta si la suma de
`cantidad` de esas Secciones cuadra con la `cantidad` de la Partida citada
(corrobora split real) o no (para revisar antes de asumir nada); (2) texto en
`observaciones_a_nivel_partida` que mencione más de un número de Partida
(regex `partida\s+(\d+)`, case-insensitive).

**Ninguna Partida ni Sección se cae en silencio:** toda Partida que ninguna
Sección elige como mejor candidata, y toda Sección sin ningún candidato con
score positivo, se listan aparte (`partidas_sin_match` / `secciones_sin_match`)
con `needs_gate: true`.

**Consolidado:** `matching/match_vN.json` — `pares[]` (uno por Sección
matcheada, con `partida`, `seccion`, `score`, `senales_coincidentes`,
`senales_discordantes`, `citacion_coincide`, `confianza`, `needs_gate`,
`motivo_gate`), más `partidas_sin_match`, `secciones_sin_match`,
`posibles_nn` y un `resumen` con los conteos.

**Validación con casos sintéticos** (sin tocar los JSON reales — construidos
en memoria contra la función `match()` del script): contradicción de campo
(NP coincide, Cantidad no → `needs_gate` con discordancia), ambigüedad real
(dos Partidas idénticas en NP+Cantidad y ninguna cita que desempate → empate
detectado), split N:N con suma que cuadra, y observación citando dos
Partidas — los 4 caminos disparan correctamente, no son código muerto.

**Corrida real sobre el piloto (53 Partidas × 53 Secciones):** 52/53 pares
automáticos (`confianza: alta`), 0 Partidas sin match, 0 Secciones sin match,
0 posibles N:N — consistente con lo ya sabido de los Pasos 3/5 (la cita
coincide con el SEC en las 53, sin observaciones reales). El único
`needs_gate` es la **Sección 37**, la misma anomalía de layout ya documentada
en Paso 5 (trae `NS:` en vez de `NP:`): como Cantidad es la única señal con
evidencia en ambos lados (NP es `"sin_evidencia"` del lado Proforma), no
llega al umbral de `alta` pese a que la cita sí coincide — queda
correctamente en `confianza: media`, `needs_gate: true`, en vez de aceptarse
solo porque el dataset es fácil.

## Paso 7 — Diff centralizado (jerarquía de verdad + checklist de campos)

Si `matching/match_vN.json` no existe, detente y corre el Paso 6 primero.

**Centralizado, sin subagentes, sin releer imágenes** — es comparación de
datos ya extraídos (Factura, Previo, Proforma, Matching). Sólo empareja
VALORES de campo por cada par Partida↔Sección ya resuelto en Paso 6 — no
decide gates (Paso 8) ni resuelve ambigüedades, sólo las documenta.

`scripts/diff_partidas_secciones.py <directorio>` implementa el motor:

**"Debe decir" por campo del checklist, jerarquía de verdad:** valor de
Previo (si tiene evidencia real, escalar, no `sin_evidencia`) > valor de
Factura (si Previo no la tiene) > `"sin_evidencia"` (si ninguno la tiene).
"Dice" es el valor actual en la Proforma. Si no coinciden (comparación
normalizada — case-insensitive, país de origen con tabla de alias CN/CHN/
China → CHINA, cantidad por intersección de candidatos), es un hallazgo con
`campo`, `dice`, `debe_decir`, `fuente_debe_decir` y la evidencia que
respalda el "Debe decir" (fotos de Previo o `crop`+página de Factura). Si
coinciden (o ambos `sin_evidencia`), no hay hallazgo — no genera ruido.

**Previo como array de candidatos en conflicto** (Paso 4): nunca se usa como
"Debe decir" con confianza ciega. Si Factura tiene valor claro, se usa ese
como "Debe decir" con nota de que Previo es ambiguo ahí (`previo_ambiguo:
true` + `previo_candidatos`); si Factura tampoco lo tiene, `sin_evidencia`.

**Caso especial — Previo contradice a Factura:** cuando AMBOS tienen
evidencia real para un campo pero discrepan, es un hallazgo de tipo distinto
(`contradicciones_previo_factura`, separado de `hallazgos`) — posible error
de Factura, no de Proforma — `needs_gate: true` siempre, nunca se resuelve
solo (aunque el "Debe decir" del checklist normal sigue calculándose por
jerarquía, Previo gana igual; la contradicción es una ADEMÁS, no un
reemplazo). **Excepción deliberada:** `descripcion` se excluye de este
chequeo de contradicción — Previo (texto crudo de etiqueta) y Factura
(descripción catalogada en inglés) difieren en formato SIEMPRE por diseño
(ya documentado en Paso 3/5), no es una discrepancia real de dato; se
etiqueta como `formato_distinto: true` en el hallazgo normal en vez de
inflar la lista de contradicciones con 53/53 falsos positivos de formato.

**Alcance del reporte** (dos niveles, del diseño), por par: `verificado_con_foto`
(al menos un campo se resolvió con evidencia real de Previo),
`solo_texto` (todo resuelto con Factura, sin foto para ningún campo),
`sin_evidencia_todavia` (ni Previo ni Factura tienen evidencia para ningún
campo del checklist — categoría rara pero existe en el código).

**No-matcheados** (`partidas_sin_match` / `secciones_sin_match` de Paso 6):
generan su propio hallazgo tipo "Falta esta partida en la proforma" /
"Sección sin partida correspondiente en la factura", `needs_gate: true` —
nunca se caen en silencio (vacías en este piloto, pero la lógica existe).

**Patrones sistémicos:** además de la lista plana de hallazgos por par, el
script agrupa patrones que se repiten con la misma forma en muchas partidas
(para que no queden enterrados como N ítems sueltos) — ver hallazgos del
piloto abajo. No reemplaza los hallazgos individuales, sólo los agrupa.

**Juicio abierto ("pink elephants"):** cada par tiene un array
`pink_elephants` para cualquier cosa que no encaje en el checklist pero
huela mal, a criterio de quien corre este paso mirando los 4 JSON juntos —
no es mecánico, requiere leer los datos ya extraídos con atención.

**Consolidado:** `diff/diff_vN.json` — `pares[]` (uno por Partida/Sección,
con `needs_gate_matching` heredado de Paso 6, `categoria_alcance`,
`hallazgos[]`, `contradicciones_previo_factura[]`, `pink_elephants[]`),
`patrones_sistemicos[]`, y `resumen` con los conteos.

**Corrida real sobre el piloto (53 pares):** 230 hallazgos totales (177
excluyendo `descripcion`, que discrepa en formato en las 53 por diseño), 18
contradicciones Previo↔Factura (todas en `np`), 53/53 pares
`verificado_con_foto`. Desglose de los 177 hallazgos reales por campo:
`fraccion_arancelaria` 53 (ninguna fuente la respalda — ni Previo ni Factura
la traen nunca; es dato de Proforma sin verificación posible en este
pipeline, no un "error" comprobado), `modelo` 38 y `codigo_producto` 6
(Previo sí los tiene, Proforma nunca los trae — antes un solo campo
`modelo_codigo_producto` 43, separado tras el split de Paso 5), `lote` 30,
`np` 18, `marca` 18, `pais_origen` 11, `numero_serie` 2.

**Dos patrones sistémicos reales, no ruido de un campo aislado:**
- **`pais_origen_siempre_CHN_en_proforma` (11 partidas: 9, 11, 13, 24, 25,
  27, 31, 33, 34, 40, 49):** la Proforma declara `CHN` en las 53 Secciones
  (hallazgo ya documentado en Paso 5), pero Previo (y Factura) tienen
  evidencia real de un país distinto en estas 11 — posible declaración
  incorrecta de origen a nivel Proforma completo, no error aislado.
- **`np_previo_parece_codigo_distribuidor_no_mpn_fabricante` (16 partidas):**
  el `np` de Previo tiene forma de código interno de distribuidor/almacén
  (LCSC `C123456`, o `NNNN-A-XXX-NNN`), distinto del MPN de fabricante que
  usa Factura — probablemente el subagente de Paso 4 priorizó el código más
  prominente de la etiqueta en vez del MPN real. Candidato fuerte para
  revisión en bloque en Paso 8, no 16 casos independientes.
- Quedan 2 contradicciones de `np` fuera de ambos patrones (**P32**: `18JRV253`
  vs `189RV253`, un solo carácter de diferencia — compatible con confusión
  OCR "J"/"9"; **P43**: `6S0M`, forma atípica que no calza con ningún patrón
  conocido, probable extracción fallida de Paso 4 sobre esa etiqueta).

**Cruce contra el reporte de referencia humano** (`Reporte de cambios con evidencia (PDF del piloto)`, 14 cambios documentados en 11 partidas, sobre una
revisión manual de sólo 15/53): **9 de las 11 partidas coinciden exacto**
(P7, 17, 35, 37, 39, 43, 45, 49, 52 — incluyendo el caso `país de origen` de
P49 que valida el patrón sistémico de arriba, y el caso `np`+`marca` de P37
que valida el diseño de "Previo contradice Factura" — el motor eligió
Previo como "Debe decir" por jerarquía, pero el reporte humano concluyó que
Factura tenía razón ahí; exactamente el tipo de caso para el que existe el
gate, no una falla del motor). **2 no coinciden — P26 (marca `COILANK`) y
P47 (marca `R+O`)** — no por falla del diff, sino porque `previo_v1.json`
tiene `marca: "sin_evidencia"` en esas dos partidas: el subagente de Paso 4
no capturó una marca que el revisor humano sí vio en la foto. Es un gap de
extracción del Paso 4, documentado como `pink_elephant` en esos dos pares —
no se re-extrajo (violaría "sin releer imágenes" de este paso).

No se implementó Paso 8 (agrupar y preguntar con `AskUserQuestion`) ni
Paso 9/10.

## Paso 8 — Ronda de gates agrupada (AskUserQuestion)

Si `diff/diff_vN.json` no existe, detente y corre el Paso 7 primero.

**Este paso SÍ interactúa con el humano — es el único punto del pipeline
donde se espera bloquear esperando respuesta real.** Recolecta TODOS los
`needs_gate` de matching (Paso 6) + diff (Paso 7): pares de matching con
`needs_gate: true`, `contradicciones_previo_factura`, y `patrones_sistemicos`
marcados `needs_gate: true`. No hay hallazgos individuales de checklist con
`needs_gate` propio en este diseño — esos ya quedaron resueltos por
jerarquía en Paso 7; sólo los 4 tipos de arriba llegan a este paso.

**Agrupar antes de preguntar:** cualquier gate que un `patrón_sistemico` ya
cubra se pregunta UNA vez por patrón, no una por partida — 16 partidas del
mismo patrón de NP son una sola pregunta, no 16. Lo que no entra en un
patrón (contradicciones sueltas, matching de baja confianza, hallazgos
puntuales que huelan mal) se agrupa en llamadas de `AskUserQuestion` de
**hasta 4 preguntas cada una** — nunca una pregunta por ocurrencia.

**Cada pregunta lleva una opción recomendada concreta y fundamentada**, no
genérica — basada en lo que ya se investigó en Paso 6/7 (p. ej. para
contradicciones Previo-vs-Factura: comparar cuál fuente es más confiable en
ESE caso puntual — texto nativo de PDF vs. lectura de foto — no un default
ciego de "usar Previo siempre").

**Si `AskUserQuestion` devuelve sin respuesta** (el humano no llegó a
contestar esa tanda todavía): NO se inventa una respuesta ni se asume la
recomendada — se vuelve a preguntar en la siguiente vuelta, tantas veces
como haga falta hasta tener respuesta real.

Las respuestas de `AskUserQuestion` se escriben apenas se obtienen a
`gates/respuestas_vN.json` (mismo esquema de versión que `match_vN`/`diff_vN`)
— nunca se hardcodean en ningún script. `scripts/apply_gates.py <directorio>`
busca el `gates/respuestas_vN.json` de versión más alta (mismo patrón
`find_latest` de match/diff) y lo lee; si no existe, falla con mensaje claro
en vez de inventar valores por defecto. Correrlo de nuevo con el mismo
`respuestas_vN.json` es idempotente; una corrida futura con gates distintos
necesita su propio `respuestas_vN.json`. El script escribe
el "Debe decir" DEFINITIVO — esto es lo que Paso 9/10 debe leer, no el diff
crudo de Paso 7 donde todavía había ambigüedad sin resolver. Para los
patrones/gates donde la jerarquía de Paso 7 YA daba el valor correcto
(p. ej. país de origen — Previo ya ganaba por jerarquía, el gate sólo
confirma que no hay que revertir a lo que dice la Proforma), el "Debe decir"
no cambia de valor, sólo se marca confirmado. Para los gates donde la
resolución humana CAMBIA el "Debe decir" respecto al de Paso 7 (los 18 `np`
en contradicción — jerarquía por defecto favorecía Previo, pero el humano
resolvió usar Factura en los 18), el consolidado trae el valor nuevo +
el valor anterior para trazabilidad.

**Consolidado:** `gates/gates_vN.json` — `gates[]` (una entrada por
pregunta hecha, con `pregunta`, `opciones`, `respuesta`), y
`resoluciones_aplicadas[]` (una entrada por partida+campo afectado, con
`debe_decir_final`, `fuente_final`, y `debe_decir_anterior` cuando cambió).

**Fuente única de las preguntas, generica:** `scripts/gate_definitions.py`
(`construir_preguntas(matching, diff)`) recorre `matching/match_vN.json` +
`diff/diff_vN.json` de ESTA corrida y arma tantas preguntas como haga falta
-- 0, 1, 5, 20 -- nunca un numero fijo. `apply_gates.py` y la interfaz web
(abajo) importan la misma funcion, nunca pueden divergir en el texto. Las 3
fuentes mecanicas que recorre (ver bug corregido mas abajo):
1. Pares de `diff["pares"]` con `needs_gate_matching` (confianza baja, o
   partida/seccion sin contraparte) -- una pregunta por par.
2. `patrones_sistemicos` con `needs_gate: true` -- una pregunta por patron,
   agrupando todas las partidas que comparte (con `valor_actual`/
   `valor_propuesto`/`recomienda_propuesto` estandarizados en
   `detectar_patrones_sistemicos` de Paso 7, para que el gate no necesite
   conocer el detalle de cada tipo de patron).
3. `contradicciones_previo_factura` con `needs_gate: true` que ningun
   patron ya cubrio -- una pregunta por contradiccion suelta.

**Bug real corregido (post v0.0.3):** la primera version de
`construir_preguntas` tenia las 6 preguntas del piloto hardcodeadas
(`by_partida[32]`, `p["tipo"] == "...distribuidor..."`, etc.) -- una
corrida con un diff de forma distinta (otro numero/tipo de patrones,
otras partidas en contradiccion) no encontraba esas claves exactas y
`gate_server.py` nunca llegaba a levantarse; el humano terminaba
respondiendo por chat en vez de la interfaz web, que es justo lo que el
diseño queria evitar. Ahora el servidor web se abre siempre, sin importar
cuantas preguntas produzca el diff real.

**Limitación conocida que sigue sin generalizarse:** hallazgos "huelen mal"
descubiertos por juicio humano al leer los 4 JSON con atención (el
`pink_elephants` de Paso 7, ej. el lote con formato nunca antes visto de
P47 en el piloto) no tienen fuente mecánica -- `pink_elephants` sigue
vacío por diseño salvo que alguien lo llene a mano. Si nadie lo llena, esos
casos quedan como hallazgos normales (jerarquía Previo>Factura por
defecto), sin pasar por un gate.

**Interfaz web (recomendada sobre AskUserQuestion crudo):**
`scripts/gate_server.py <directorio> [--port 8765] [--no-browser]` levanta
un servidor local (sólo librería estándar de Python -- sin Flask, sin
npm/node, corre en una máquina sin dev tools) que muestra las preguntas como
un stepper visual: una por una, con el crop de Factura, el crop de Proforma
y las fotos de Previo de la(s) partida(s) en cuestión al lado de las
opciones (para patrones sistémicos, además una tabla con todas las partidas
afectadas y un botón "Abrir evidencia" por fila que la expande ahi mismo,
sin scroll a un panel aparte). Abre el navegador solo. Al responder la
última pregunta escribe `gates/respuestas_v{N}.json` (siguiente versión
libre, nunca pisa una corrida anterior) y se apaga solo -- no queda un
proceso colgado. No reemplaza `apply_gates.py`: sigue siendo ese script el
que aplica las respuestas y decide el "Debe decir" definitivo.

**Invocación por el agente (bloqueante, sin `&`):** el agente corre
`gate_server.py` en primer plano -- igual que ya bloquea con
`AskUserQuestion` -- y esa llamada no retorna hasta que el humano contesta
la última pregunta y el servidor se apaga solo. Cuando retorna, el agente
sigue de inmediato con `apply_gates.py`: el humano no tiene que correr nada
a mano, ni el mensaje final de la pagina se lo pide (dice sólo que el
pipeline continúa solo). Si el agente lo manda a segundo plano (`&`), pierde
esa señal de "ya terminé" y tiene que sondear el archivo de respuestas —
evitar ese patrón. Limitación conocida: si se cierra la pestaña a medias, no
hay estado persistido -- hay que volver a correr `gate_server.py` desde cero
(las respuestas ya dadas no se guardan hasta contestar la última pregunta).

**Corrida real sobre el piloto (con `construir_preguntas` generico):** 5
preguntas — 1 matching (Sección 37, confianza media) + 2 patrones (país de
origen, 11 partidas; NP-código-distribuidor, 16 partidas) + 2
contradicciones individuales sueltas (`np` de P32, `np` de P43 — el lote
"sospechoso" de P47 ya no genera pregunta, ver limitación de arriba: era
`pink_elephant`, no mecánico). Respuestas usadas: confirmar Sección 37,
usar el país real de Previo/Factura para los 11 de país de origen (opción
"mantener el valor ya vigente", que es la jerarquía por defecto), usar el
MPN de Factura para los 16 de NP-distribuidor, usar Factura para P32 y P43.
30 partidas afectadas por alguna resolución (27 de los 2 patrones + 2
contradicciones individuales + el par de matching confirmado; nótese que
ahora TODAS las ramas de un patrón —incluida "mantener el valor
actual"— quedan explícitamente registradas en `resoluciones_aplicadas`,
corrigiendo un hueco de la versión anterior donde sólo la rama "cambiar"
quedaba trazada).

Nota histórica: `gates/respuestas_v1.json` (v0.0.1–v0.0.3) usa claves de
la versión hardcodeada anterior (`matching_seccion_37`,
`hallazgo_lote_p47`, etc.) y ya no es compatible con el
`construir_preguntas` genérico — se conserva como registro histórico, no
como respuestas reejecutables.

## Paso 9 — Render de evidencia (merge diff+gates, collages con recuadro real)

Si `gates/gates_vN.json` no existe, detente y corre el Paso 8 primero.

**Dos scripts, dos responsabilidades separadas.**

### 9a. Merge de diff+gates → hallazgos finales

`scripts/merge_diff_gates.py <directorio>` combina `diff/diff_vN.json` +
`gates/gates_vN.json` en `hallazgos/hallazgos_finales_vN.json` — la lista
DEFINITIVA de discrepancias (Partida × Campo con Dice != Debe decir), que
Paso 9b y Paso 10 leen en vez del diff crudo. `descripcion` se excluye
siempre (formato_distinto por diseño, ver Paso 7 — no es una discrepancia
real).

Por cada hallazgo real de diff: si `gates_vN.json` trae una resolución para
esa partida+campo, se aplica su `debe_decir_final`/fuente en vez del valor
de Paso 7. Si tras aplicar el gate el nuevo Debe decir coincide con Dice
(la Proforma tenía razón — el problema real era Previo discrepando con
Factura, no la Proforma), el hallazgo se cae de la lista final pero queda
su rastro en `resueltos_sin_discrepancia` para auditoría. Si no hay
resolución para ese campo, el hallazgo pasa tal cual con
`resuelto_por_gate: null`.

**Corrida real:** 177 hallazgos reales de Paso 7 → **160 hallazgos
finales** + **17 resueltos sin discrepancia** (16 del patrón
NP-código-distribuidor + P32, todos con `dice` == MPN de Factura ya en la
Proforma — el gap real estaba en Previo, no en la Proforma; sólo P37 del
mismo patrón sigue siendo hallazgo real porque ahí la Proforma no traía NP
en absoluto). Desglose final por campo: `fraccion_arancelaria` 53,
`modelo` 38, `codigo_producto` 6 (antes un solo campo `modelo_codigo_producto`
43 — ver Paso 5 sobre el split), `lote` 29 (1 marcado sospechoso, P47),
`marca` 20, `pais_origen` 11 (las 11 confirmadas por el gate, sin cambio de
valor), `numero_serie` 2, `np` 1 (P37, único caso donde el gate SÍ cambió
el valor y la discrepancia sigue viva). Fuente final: `previo` 105,
`ninguno` 54 (ninguna fuente respalda el campo — sobre todo
`fraccion_arancelaria`, dato de Proforma sin verificación posible en este
pipeline), `factura` 1 (P37, tras el gate).

### 9b. Collages de evidencia con recuadro real

`scripts/render_evidencia.py <directorio>` genera un collage por hallazgo
final, guardado en `evidencia/vN/images/p<NN>_<campo>.png`, e indexado en
`evidencia/vN/index_vN.json` (mapea cada hallazgo a su ruta de imagen).

**Mecanismo de recuadro real (nunca inventado):** para el panel de
Proforma (siempre, "Dice") y para el panel de Factura (sólo cuando
`fuente_debe_decir == "factura"`), se re-corre `pdftotext -bbox` sobre el
PDF correspondiente (reusando el workaround de metadata de Paso 3/5,
centralizado ahora en `scripts/_bbox_common.py` para no triplicarlo) — esta
vez a nivel de PALABRA, no de fila. Se agrupan las palabras por línea
dentro del rango vertical de la fila/sección ya conocido por `rows.json`, y
se busca el texto del valor (`dice` o `debe_decir`) como substring de la
línea (con y sin espacios, y sin anotaciones cosméticas tipo `(MPN)` — sólo
para buscar, nunca para lo que se muestra). Si se ubica, se dibuja un
recuadro rojo real sobre esa posición exacta (Pillow) en una copia de la
página completa, y de ahí se recorta la fila — el recuadro y el recorte
quedan en el mismo sistema de coordenadas, sin doble transformación. Si el
valor es `sin_evidencia` o la búsqueda no encuentra un match seguro, se usa
el recorte plano SIN recuadro — nunca se adivina una posición.

**Evidencia de Previo (fuente `previo`, sin bbox posible):** Paso 4 fue
lectura de visión pura sin coordenadas — no se le pide a ningún modelo que
"adivine" un bbox de píxel sobre la foto. Se incluye la foto (o fotos)
completa que `previo_vN.json` ya lista en `fotos_respaldo[campo]`,
resueltas a ruta absoluta vía `previo/merged.json`, sin dibujar nada
encima, con la etiqueta explícita "Evidencia física, ver foto completa".

**Sin segunda fuente (`fuente_debe_decir == "ninguno"`):** collage de un
solo panel (Proforma con o sin recuadro) + nota de texto "ninguna fuente
(Previo ni Factura) respalda este campo" — nunca se fabrica un panel vacío
con recuadro falso.

**Composición:** barra de título (Partida/Sección/Campo + flag si quedó
`[VALOR DE PROFORMA MARCADO SOSPECHOSO]` o `[confirmado/resuelto en Paso
8]`) + panel Proforma + panel de "Debe decir" (Factura/Previo/nota), apilados
verticalmente, anchos normalizados a 1300px (Proforma y Factura se
renderizan a distinto ancho de página — 1700px vs 2339px a 200dpi — así que
sin normalizar el collage quedaría descuadrado). Los labels se pliegan a
ASCII antes de dibujarse (`fold_ascii`) porque el bitmap font por defecto de
Pillow no tiene glyphs acentuados y renderiza "Sección" como basura — el
plegado es sólo cosmético, nunca toca los datos de los JSON.

**Corrida real: 159 collages, uno por hallazgo final** (no se deduplicó
ninguno en este piloto — cada campo tenía posición de recuadro o foto
distinta, ni un solo par resultó pixel-idéntico). **74 con recuadro real**
(Factura o Proforma), **104 con foto plana sin recuadro** (Previo — nota:
la suma pasa de 159 porque algunos hallazgos con fuente Previo también
tienen recuadro real del lado Proforma), **54 sin segundo panel** (ninguna
fuente). DPI verificado en 200 (proforma 1700px/612pt y factura 2339px/
841.88pt, ambos ≈ 200/72) antes de fijar la constante de escala.

## Paso 10 — Reporte HTML final (autocontenido, base64, versionado)

Si `hallazgos/hallazgos_finales_vN.json` o `evidencia/vN/index_vN.json` no
existen, detente y corre el Paso 9 primero. **Último paso del pipeline.**

`scripts/render_reporte_html.py <directorio>` arma `artifacts/rev_artifact_vN.html`
— un solo archivo HTML autocontenido (imágenes en base64 inline, modales
JS vanilla, sin dependencias externas, sin llamar a ningún servicio). No
decide nada nuevo, es el último render sobre datos ya extraídos/mergeados.

**Header:** importador + identificador de operación (número de Pedimento +
Tráfico) leídos de la propia página 1 de la Proforma/Pedimento (texto nativo
vía `pdftotext -layout`) — nunca inventados. El nombre viene en layout de 2
columnas (la etiqueta "RAZON SOCIAL:" en una fila, el valor en la fila
siguiente compartiendo línea con otro campo de la izquierda como "CURP:") —
se extrae tomando el último segmento no vacío de esa línea al partir por
corridas de 2+ espacios, no la línea completa (bug encontrado y corregido en
el camino: la primera versión capturaba "CURP:" pegado al nombre).

**Links de la sección "Cambios a la proforma":** rutas relativas a
`facturas/invoice_vN/invoice_vN.pdf` y `proformas/proforma_vN/proforma_vN.pdf`,
más un `.zip` de las fotos de Previo generado en cada corrida
(`previo/previo_vN.zip`, armado desde `previo/merged.json` — respeta el
paquete ganador por partida, no asume `fotos_v1` a secas).

**Tabla de cambios**, columnas exactas: Partida Factura | Sección Proforma |
Campo | Dice | Debe decir | Evidencia — una fila por hallazgo final (159).
Partida/Sección son links que abren un modal con el recorte fuente completo
(`crops/partida-NN.png` / `crops/seccion-NNN.png`, ya en `invoice_vN.json`/
`proforma_vN.json`) — el crop de cada partida/sección se embebe UNA sola vez
en un `<div class="hidden-store">` aunque varios campos de esa misma
partida generen fila propia, y los links sólo referencian ese elemento por
id (evita embeber el mismo PNG 3-4 veces cuando una partida tiene varios
campos con hallazgo). La columna Evidencia embebe el collage de
`evidencia/vN/images/` ya generado en Paso 9 (recuadro real o foto plana de
Previo, según corresponda) y es clickable para verla a tamaño completo,
reusando el mismo modal genérico (`openModal(src, titulo)`) que las columnas
de Partida/Sección (`openModalFromId`) — un solo overlay compartido, no uno
por fila. `marcado_sospechoso` se muestra como badge rojo con el motivo en
`title`; `resuelto_por_gate` (sin sospechoso) como badge azul "Confirmado/
resuelto en Paso 8".

**Sección aparte "Revisados y sin discrepancia":** los 17
`resueltos_sin_discrepancia` de Paso 9, en su propia tabla (Partida | Campo |
Valor descartado en Paso 7 | Valor confirmado | Motivo del gate) — separados
visualmente de los 159 hallazgos reales, con una nota explícita de que NO
son pendientes de corrección.

**Corrida real:** header extraído correcto — Importador "IMPORTADORA EJEMPLO SA DE CV", Núm. Pedimento "00 00 0000 0000000", Tráfico "Q0000000" (los 3
ya estaban en el propio Pedimento, ninguno inventado). `previo/previo_v1.zip`
generado, 17.2 MB, 53 carpetas de partida. Tabla principal: **159 filas**,
resumen: 1 marcado sospechoso, 13 confirmados/resueltos en Paso 8. Tabla de
revisados: 17 filas. Archivo final: **`artifacts/rev_artifact_v1.html`,
110.9 MB** (159 collages de Paso 9 ya pesaban 76MB en disco — el +33% de
base64 más los 53+53 crops de Partida/Sección explican el tamaño; es el
costo esperado de "autocontenido" del diseño, no un bug).

**Verificación visual real (Chrome headless, no sólo "no tiró error"):**
`google-chrome --headless=new --screenshot` contra el archivo final — carga
en ~8s, header/resumen/tabla renderizan con datos y colores correctos
(Dice en rojo, Debe decir en verde, badges visibles), miniaturas de
evidencia decodifican y se ven. Modal verificado inyectando una llamada a
`openModalFromid` en una COPIA temporal del archivo (nunca en el artifact
real) y tomando una segunda captura: el overlay abre, oscurece el fondo, y
muestra el recorte de Factura correcto (Partida 1, fila completa con NP
`SMBJ24A`) — confirma que `openModal`/`openModalFromId` funcionan de verdad,
no sólo que el HTML parsea.

Con esto se completa el pipeline de 10 pasos sobre el dataset piloto.
