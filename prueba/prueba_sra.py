# Prueba: ¿responde el SRA de la UCO a peticiones hechas desde los servidores de GitHub?
# Escribe el resultado en prueba/resultado.md (se guarda en el repositorio).
import datetime as dt
import socket
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reservas_sra as R

LABS = {"0108LBO": "Laboratorio de Botánica", "0108LCO": "Laboratorio de Botánica 2",
        "0108LEC": "Laboratorio de Ecología", "0108LFV": "Laboratorio de Fisiología Vegetal"}
salida = []
w = salida.append
ahora = dt.datetime.now(dt.timezone.utc)
w(f"# Prueba de conexión con SRA desde GitHub Actions\n\nEjecutada: {ahora:%Y-%m-%d %H:%M} UTC\n")

try:
    w(f"- www.uco.es resuelve a: `{socket.gethostbyname('www.uco.es')}`")
except Exception as e:
    w(f"- No se pudo resolver www.uco.es: `{e}`")

# 1) Una semana (la actual) por laboratorio
w("\n## 1. Una semana por laboratorio (semana actual)\n")
w("| Laboratorio | Resultado | Tiempo | Tamaño | Reservas leídas |\n|---|---|---|---|---|")
lunes = R.lunes_de(dt.date.today())
ok_1 = 0
for c, n in LABS.items():
    t0 = time.time()
    try:
        html = R.descargar_semana(c, lunes, intentos=1)
        _, res, _ = R.leer_semana(html)
        sem = R.RE_SEMANA.search(html)
        estado = "✅ OK" if sem else "⚠️ respuesta sin tabla de semana"
        ok_1 += bool(sem)
        w(f"| {n} | {estado} | {time.time()-t0:.1f} s | {len(html)/1024:.0f} KB | {len(res)} |")
    except Exception as e:
        w(f"| {n} | ❌ {type(e).__name__}: {str(e)[:150]} | {time.time()-t0:.1f} s | – | – |")

# 2) Cuatrimestre completo de los 4 laboratorios (lo que haría la tarea programada)
w("\n## 2. Cuatrimestre completo, 4 laboratorios\n")
if ok_1:
    try:
        t0 = time.time()
        res = R.consultar(list(LABS), dt.date(2026, 9, 7), dt.date(2027, 1, 31))
        w(f"- Tiempo total: **{time.time()-t0:.0f} s**")
        for c, n in LABS.items():
            w(f"- {n}: {len(res[c]['reservas'])} reservas, {len(res[c]['festivos'])} días no lectivos")
        w("\nReferencia (consulta hecha desde el navegador el 26/11/2026): Botánica 77, Botánica 2 18, Ecología 22, Fisiología Vegetal 39.")
    except Exception:
        w("```\n" + traceback.format_exc()[-1500:] + "\n```")
else:
    w("- No se hace: la prueba 1 no obtuvo ninguna respuesta válida.")

w(f"\n## Conclusión\n\n{'✅ SRA responde desde GitHub.' if ok_1 == len(LABS) else '❌ SRA no responde correctamente desde GitHub.' if not ok_1 else '⚠️ Respuesta parcial.'}")
Path(__file__).with_name("resultado.md").write_text("\n".join(salida) + "\n", encoding="utf-8")
print("\n".join(salida))
