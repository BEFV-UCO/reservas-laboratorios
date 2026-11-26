# -*- coding: utf-8 -*-
"""
Genera la web pública de reservas de laboratorios (GitHub Pages) y los calendarios .ics.

Cuatrimestres automáticos: 1.º de septiembre a enero, 2.º de febrero a julio (agosto sin asignar).

Cada noche:
- Consulta en SRA el cuatrimestre en curso y el siguiente (en agosto: el 1.º del curso que empieza
  y el 2.º), compara con la noche anterior y anota los cambios en data/cambios.json.
- Los cuatrimestres pasados se consultan una sola vez (desde CURSO_INICIAL) y quedan guardados
  en data/periodos/, porque ya no cambian.
- Escribe en sitio/: index.html (cuatrimestre en curso), datos/<periodo>.json (todos los
  cuatrimestres, para los desplegables) y calendarios/<código>.ics (+ todos.ics).

Uso:  python generar_web.py            (la ejecuta GitHub Actions cada noche)
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
import reservas_sra as R  # noqa: E402

DATOS = RAIZ / "data"
PERIODOS_DIR = DATOS / "periodos"
SITIO = RAIZ / "sitio"
CURSO_INICIAL = "2021-22"     # primer curso que se ofrece en los desplegables
MAX_ANTIGUOS_POR_NOCHE = 12   # cuatrimestres pasados que se descargan como mucho en una ejecución
DIAS_CAMBIOS = 7
DIAS_REGISTRO = 60
TZ = "Europe/Madrid"

DATOS.mkdir(exist_ok=True)
PERIODOS_DIR.mkdir(exist_ok=True)
R.carpeta_resultados = lambda: DATOS  # historial y colores se guardan en el repositorio


def pid(curso, n):
    return f"{curso}-{n}"


def semanas_completas(curso, n):
    a, b = R.limites_cuatrimestre(curso, n)
    return R.lunes_de(a), R.lunes_de(b) + dt.timedelta(days=6)


# ---------------------------------------------------------------------------
# Registro de cambios
# ---------------------------------------------------------------------------

def leer_registro():
    try:
        return json.loads((DATOS / "cambios.json").read_text(encoding="utf-8"))
    except Exception:
        return []


def anotar_cambios(registro, hoy, codigo, reservas, eliminadas):
    for r in reservas:
        if r.get("nuevo"):
            registro.append({"detectado": hoy.isoformat(), "tipo": "nueva", "lab": codigo,
                             "reserva": {k: v for k, v in r.items() if k not in ("nuevo", "detectado")}})
    for r in eliminadas:
        registro.append({"detectado": hoy.isoformat(), "tipo": "anulada", "lab": codigo, "reserva": r})


def cambios_recientes(registro, codigo, actuales, hoy, desde, hasta):
    """Marca como nuevas las reservas detectadas en los últimos DIAS_CAMBIOS días que siguen
    existiendo y devuelve las anuladas en ese plazo que no han vuelto (solo dentro del periodo)."""
    limite = (hoy - dt.timedelta(days=DIAS_CAMBIOS - 1)).isoformat()
    d, h = desde.isoformat(), hasta.isoformat()
    claves_actuales = {R._clave(r): r for r in actuales}
    nuevas, anuladas = {}, {}
    for e in registro:
        if e["lab"] != codigo or e["detectado"] < limite or not (d <= e["reserva"]["fecha"] <= h):
            continue
        k = R._clave(e["reserva"])
        if e["tipo"] == "nueva" and k in claves_actuales:
            nuevas[k] = e["detectado"]
        elif e["tipo"] == "anulada" and k not in claves_actuales:
            anuladas[k] = dict(e["reserva"], detectado=e["detectado"])
    for k, r in claves_actuales.items():
        r["nuevo"] = k in nuevas
        if k in nuevas:
            r["detectado"] = nuevas[k]
    return sorted(anuladas.values(), key=lambda r: (r["fecha"], r["ini"]))


# ---------------------------------------------------------------------------
# Calendarios .ics
# ---------------------------------------------------------------------------

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Madrid
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def _esc(t: str) -> str:
    return str(t).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _plegar(linea: str) -> str:
    """Líneas de máximo 75 octetos (RFC 5545)."""
    if len(linea.encode("utf-8")) <= 75:
        return linea
    partes, actual = [], b""
    for ch in linea:
        cb = ch.encode("utf-8")
        if len(actual) + len(cb) > (75 if not partes else 74):
            partes.append(actual.decode("utf-8"))
            actual = b""
        actual += cb
    partes.append(actual.decode("utf-8"))
    return "\r\n ".join(partes)


def generar_ics(nombre_cal, eventos, generado: dt.datetime, prefijo_lab=False) -> str:
    """eventos: lista de (codigo_lab, nombre_lab, reserva)"""
    sello = generado.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lineas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Dpto Botanica Ecologia y Fisiologia Vegetal UCO//Reservas SRA//ES",
              "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(nombre_cal)}", f"X-WR-TIMEZONE:{TZ}",
              "X-WR-CALDESC:Reservas en el SRA de la Universidad de Córdoba (actualización diaria)",
              "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
    lineas += VTIMEZONE.split("\n")
    for codigo, nombre_lab, r in eventos:
        fecha = r["fecha"].replace("-", "")
        uid = hashlib.sha1(f"{codigo}|{r['fecha']}|{r['ini']}|{r['fin']}|{r['texto']}".encode()).hexdigest()
        grupos = "Grupos" if ("," in r["grupo"] or " y " in r["grupo"]) else "Grupo"
        resumen = r["asignatura"] + (f" · {grupos} {r['grupo']}" if r["grupo"] else "")
        if prefijo_lab:
            resumen = f"[{nombre_lab.replace('Laboratorio de ', '')}] {resumen}"
        desc = f"Reserva en SRA: {r['texto']}\nLaboratorio: {nombre_lab} ({codigo})"
        lineas += ["BEGIN:VEVENT", f"UID:{uid}@befv-uco-reservas", f"DTSTAMP:{sello}",
                   f"DTSTART;TZID={TZ}:{fecha}T{r['ini'].replace(':', '')}00",
                   f"DTEND;TZID={TZ}:{fecha}T{r['fin'].replace(':', '')}00",
                   f"SUMMARY:{_esc(resumen)}", f"LOCATION:{_esc(nombre_lab)}",
                   f"DESCRIPTION:{_esc(desc)}", "TRANSP:OPAQUE", "END:VEVENT"]
    lineas.append("END:VCALENDAR")
    return "\r\n".join(_plegar(l) for l in lineas) + "\r\n"


# ---------------------------------------------------------------------------
# Consulta y almacenamiento por cuatrimestre
# ---------------------------------------------------------------------------

def consultar_periodo(curso, n, labs, ahora, comparar, registro, hoy):
    desde, hasta = semanas_completas(curso, n)
    codigos = list(labs)
    print(f"  {pid(curso, n)}: consultando SRA del {desde} al {hasta}", flush=True)
    res = R.consultar(codigos, desde, hasta, lambda h, t: None)
    instantanea = {"id": pid(curso, n), "curso": curso, "n": n, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
                   "generado": ahora.replace(tzinfo=None).isoformat(timespec="minutes"), "laboratorios": []}
    for c in codigos:
        if comparar:
            hubo = (DATOS / f"historial_{c}.json").exists()
            eliminadas, _ = R.comparar_con_anterior(c, res[c], desde, hasta, DATOS)
            if hubo:
                anotar_cambios(registro, hoy, c, res[c]["reservas"], eliminadas)
        instantanea["laboratorios"].append({
            "codigo": c, "nombre": labs[c], "festivos": res[c]["festivos"],
            "reservas": [{k: v for k, v in r.items() if k not in ("nuevo", "detectado")} for r in res[c]["reservas"]]})
    (PERIODOS_DIR / f"{pid(curso, n)}.json").write_text(
        json.dumps(instantanea, ensure_ascii=False, indent=1), encoding="utf-8")
    return instantanea


def datos_pagina(inst, labs, vivo, registro, hoy):
    curso, n = inst["curso"], inst["n"]
    desde, hasta = dt.date.fromisoformat(inst["desde"]), dt.date.fromisoformat(inst["hasta"])
    d = {"id": inst["id"], "curso": curso, "n": n, "nombre": R.NOMBRES_CUATRI[n],
         "etiqueta": f"Curso {curso} · {R.NOMBRES_CUATRI[n]}",
         "generado": inst["generado"], "desde": inst["desde"], "hasta": inst["hasta"], "laboratorios": []}
    if vivo:
        d["cambios_dias"] = DIAS_CAMBIOS
    for l in inst["laboratorios"]:
        reservas = [dict(r) for r in l["reservas"]]
        eliminadas = cambios_recientes(registro, l["codigo"], reservas, hoy, desde, hasta) if vivo else []
        d["laboratorios"].append({"codigo": l["codigo"], "nombre": labs.get(l["codigo"], l["nombre"]),
                                  "reservas": reservas, "festivos": l["festivos"], "eliminadas": eliminadas,
                                  "consulta_anterior": inst["generado"] if vivo else None})
    d["colores"] = R.asignar_colores(
        [r["asignatura"] for l in d["laboratorios"] for r in l["reservas"] + l["eliminadas"]], DATOS)
    return d


def main():
    from zoneinfo import ZoneInfo
    ahora = dt.datetime.now(ZoneInfo(TZ))
    hoy = ahora.date()
    labs, _ = R.leer_config()
    registro = leer_registro()

    actual = R.cuatrimestre_por_defecto(hoy)
    siguiente = R.cuatrimestre_siguiente(*actual)
    vivos = [actual, siguiente]

    # Cuatrimestres pasados que faltan (se descargan una sola vez)
    pasados, p = [], R.cuatrimestre_anterior(*actual)
    while R.anio_inicio(p[0]) >= R.anio_inicio(CURSO_INICIAL):
        pasados.append(p)
        p = R.cuatrimestre_anterior(*p)
    faltan = [p for p in pasados if not (PERIODOS_DIR / f"{pid(*p)}.json").exists()][:MAX_ANTIGUOS_POR_NOCHE]

    print(f"Laboratorios: {', '.join(labs)}")
    print(f"Cuatrimestre en curso: {pid(*actual)} · siguiente: {pid(*siguiente)} · pasados por descargar: {len(faltan)}")
    for curso, n in vivos:
        consultar_periodo(curso, n, labs, ahora, True, registro, hoy)
    for curso, n in faltan:
        consultar_periodo(curso, n, labs, ahora, False, registro, hoy)

    limite = (hoy - dt.timedelta(days=DIAS_REGISTRO)).isoformat()
    registro = [e for e in registro if e["detectado"] >= limite]
    (DATOS / "cambios.json").write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- Sitio web ---
    (SITIO / "datos").mkdir(parents=True, exist_ok=True)
    (SITIO / "calendarios").mkdir(parents=True, exist_ok=True)
    instantaneas = {}
    for f in sorted(PERIODOS_DIR.glob("*.json")):
        inst = json.loads(f.read_text(encoding="utf-8"))
        if R.anio_inicio(inst["curso"]) >= R.anio_inicio(CURSO_INICIAL):
            instantaneas[inst["id"]] = inst
    id_vivos = {pid(*v) for v in vivos}
    periodos = [{"id": i, "curso": inst["curso"], "n": inst["n"], "nombre": R.NOMBRES_CUATRI[inst["n"]],
                 "url": f"datos/{i}.json", "actual": i == pid(*actual)}
                for i, inst in sorted(instantaneas.items())]
    ics = {**{c: f"calendarios/{c}.ics" for c in labs}, "todos": "calendarios/todos.ics"}

    pagina_por_defecto = None
    for i, inst in instantaneas.items():
        d = datos_pagina(inst, labs, i in id_vivos, registro, hoy)
        (SITIO / "datos" / f"{i}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        if i == pid(*actual):
            pagina_por_defecto = dict(d, periodos=periodos, ics=ics)

    pagina = R.generar_pagina(pagina_por_defecto, R.logo_data_uri(), servidor=False)
    pagina = pagina.replace("<title>Reservas de laboratorios · SRA</title>",
                            "<title>Reservas de laboratorios · Dpto. Botánica, Ecología y Fisiología Vegetal · UCO</title>")
    (SITIO / "index.html").write_text(pagina, encoding="utf-8")

    # Calendarios: cuatrimestre anterior, en curso y siguiente
    en_cal = [pid(*R.cuatrimestre_anterior(*actual)), pid(*actual), pid(*siguiente)]
    todos = []
    for c, nombre in labs.items():
        ev = [(c, nombre, r) for i in en_cal if i in instantaneas
              for l in instantaneas[i]["laboratorios"] if l["codigo"] == c for r in l["reservas"]]
        todos += ev
        (SITIO / "calendarios" / f"{c}.ics").write_text(
            generar_ics(f"Reservas · {nombre}", ev, ahora), encoding="utf-8", newline="")
    (SITIO / "calendarios" / "todos.ics").write_text(
        generar_ics("Reservas · Todos los laboratorios", sorted(todos, key=lambda e: (e[2]["fecha"], e[2]["ini"])),
                    ahora, prefijo_lab=True), encoding="utf-8", newline="")
    (SITIO / ".nojekyll").write_text("")

    for i in sorted(instantaneas):
        tot = sum(len(l["reservas"]) for l in instantaneas[i]["laboratorios"])
        print(f"  {i}: {tot} reservas{' (en curso)' if i == pid(*actual) else ''}")
    print(f"Web generada en {SITIO}")


if __name__ == "__main__":
    main()
