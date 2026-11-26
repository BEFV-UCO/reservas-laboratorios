# Reservas de laboratorios · Departamento de Botánica, Ecología y Fisiología Vegetal (UCO)

**Web:** https://befv-uco.github.io/reservas-laboratorios/

Muestra las reservas de los laboratorios del departamento registradas en el Sistema
de Reserva de Aulas (SRA) de la Universidad de Córdoba, a partir de su consulta
pública de ocupación. Se actualiza **cada noche a las 23:59** (hora de Córdoba).

- Vista semanal y lista de todo el cuatrimestre, por laboratorio.
- Cambios detectados en los últimos 7 días (reservas nuevas y anuladas).
- Impresión de la semana o de todo el periodo (una semana por página).
- Calendarios para suscribirse desde Outlook (botón «Suscribirse al calendario»):
  - `calendarios/0108LBO.ics` · Laboratorio de Botánica
  - `calendarios/0108LCO.ics` · Laboratorio de Botánica 2
  - `calendarios/0108LEC.ics` · Laboratorio de Ecología
  - `calendarios/0108LFV.ics` · Laboratorio de Fisiología Vegetal
  - `calendarios/todos.ics` · todos

## Mantenimiento

- **Cada curso:** actualizar las fechas de los cuatrimestres en `reservas_sra.ini`
  (se puede editar desde la web de GitHub: abrir el archivo → icono del lápiz → «Commit changes»).
  La web muestra el cuatrimestre en curso o, entre cuatrimestres, el siguiente.
- **Añadir un laboratorio:** nueva línea en `[laboratorios]` de `reservas_sra.ini`
  (código SRA = nombre). De momento, espacios del edificio Celestino Mutis (Rabanales).
- **Actualizar a mano:** pestaña *Actions* → «Actualizar reservas» → *Run workflow*.
- Si una actualización falla (por ejemplo, porque el SRA cambia su página), GitHub
  envía un aviso por correo y la web sigue mostrando los últimos datos correctos.

## Archivos

| Archivo | Para qué |
|---|---|
| `generar_web.py` | Consulta SRA y genera la web y los calendarios (lo ejecuta GitHub cada noche) |
| `reservas_sra.py` | Lectura del SRA y páginas; también es la aplicación de escritorio (`Reservas SRA.exe`) |
| `plantilla_*.html`, `logo_oficial.png` | Diseño (identidad visual UCO, Manual de Identidad 2020) |
| `reservas_sra.ini` | Laboratorios y fechas de los cuatrimestres |
| `data/` | Historial de la última consulta y registro de cambios (lo actualiza GitHub) |
| `.github/workflows/actualizar.yml` | Programación diaria y publicación en GitHub Pages |
