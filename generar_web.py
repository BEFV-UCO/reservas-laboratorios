# -*- coding: utf-8 -*-
"""
Genera la web pública de reservas de laboratorios (GitHub Pages) y los calendarios .ics.

- Consulta en SRA el cuatrimestre en curso (o el siguiente) para todos los laboratorios
  de reservas_sra.ini.
- Compara con la ejecución anterior (data/historial_*.json) y anota los cambios en
  data/cambios.json; la página muestra los de los últimos DIAS_CAMBIOS días.
- Escribe en sitio/: index.html y calendarios/<código>.ics (+ todos.ics).

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
SITIO = RAIZ / "sitio"
DIAS_CAMBIOS = 7
DIAS_REGISTRO = 60  # cuánto tiempo se guarda el registro de cambios
TZ = "Europe/Madrid"

DATOS.mkdir(exist_ok=True)
R.carpeta_resultados = lambda: DATOS  # historial y colores se guardan en el repositorio


def periodo(hoy: dt.date, cuatris):
    """Cuatrimestre en curso; si no hay ninguno en curso, el siguiente; si no, 18 semanas."""
    for a, b in cuatris:
        if a <= hoy <= b:
            return a, b
    for a, b in cuatris:
        if a > hoy:
            return a, b
    return R.lunes_de(hoy), hoy + dt.timedelta(weeks=18)


# ---------------------------------------------------------------------------
# Registro de cambios
# ---------------------------------------------------------------------------

def actualizar_registro(hoy, por_lab):
    """por_lab: {codigo: (reservas_actuales_con_flag_nuevo, eliminadas, hubo_anterior)}"""
    ruta = DATOS / "cambios.json"
    try:
        registro = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        registro = []
    for codigo, (reservas, eliminadas, hubo_anterior) in por_lab.items():
        if not hubo_anterior:
            continue  # primera ejecución: todo sería «nuevo»
        for r in reservas:
            if r.get("nuevo"):
                registro.append({"detectado": hoy.isoformat(), "tipo": "nueva", "lab": codigo,
                                 "reserva": {k: v for k, v in r.items() if k != "nuevo"}})
        for r in eliminadas:
            registro.append({"detectado": hoy.isoformat(), "tipo": "anulada", "lab": codigo, "reserva": r})
    limite = (hoy - dt.timedelta(days=DIAS_REGISTRO)).isoformat()
    registro = [e for e in registro if e["detectado"] >= limite]
    ruta.write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
    return registro


def cambios_recientes(registro, codigo, actuales, hoy):
    """Nuevas (que siguen existiendo) y anuladas (que no han vuelto) en los últimos DIAS_CAMBIOS días."""
    limite = (hoy - dt.timedelta(days=DIAS_CAMBIOS - 1)).isoformat()
    claves_actuales = {R._clave(r): r for r in actuales}
    nuevas, anuladas = {}, {}
    for e in registro:
        if e["lab"] != codigo or e["detectado"] < limite:
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
    b = linea.encode("utf-8")
    if len(b) <= 75:
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


def generar_ics(nombre_cal, eventos, generado: dt.datetime) -> str:
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
        resumen = r["asignatura"] + (f" · {'Grupos' if (',' in r['grupo'] or ' y ' in r['grupo']) else 'Grupo'} {r['grupo']}" if r["grupo"] else "")
        if nombre_cal.startswith("Reservas · Todos"):
            resumen = f"[{nombre_lab.replace('Laboratorio de ', '')}] {resumen}"
        desc = f"Reserva en SRA: {r['texto']}\nLaboratorio: {nombre_lab} ({codigo})"
        lineas += ["BEGIN:VEVENT", f"UID:{uid}@befv-uco-reservas", f"DTSTAMP:{sello}",
                   f"DTSTART;TZID={TZ}:{fecha}T{r['ini'].replace(':', '')}00",
                   f"DTEND;TZID={TZ}:{fecha}T{r['fin'].replace(':', '')}00",
                   f"SUMMARY:{_esc(resumen)}", f"LOCATION:{_esc(nombre_lab)}",
                   f"DESCRIPTION:{_esc(desc)}",
                   "TRANSP:OPAQUE", "END:VEVENT"]
    lineas.append("END:VCALENDAR")
    return "\r\n".join(_plegar(l) for l in lineas) + "\r\n"


# ---------------------------------------------------------------------------

def main():
    from zoneinfo import ZoneInfo
    ahora = dt.datetime.now(ZoneInfo(TZ))
    hoy = ahora.date()
    labs, cuatris = R.leer_config()
    desde, hasta = periodo(hoy, cuatris)
    desde, hasta = R.lunes_de(desde), R.lunes_de(hasta) + dt.timedelta(days=6)
    codigos = list(labs)
    print(f"Consultando SRA: {len(codigos)} laboratorios, del {desde} al {hasta}")
    resultados = R.consultar(codigos, desde, hasta, lambda h, t: None)

    por_lab = {}
    for c in codigos:
        hubo = (DATOS / f"historial_{c}.json").exists()
        eliminadas, _ = R.comparar_con_anterior(c, resultados[c], desde, hasta, DATOS)
        por_lab[c] = (resultados[c]["reservas"], eliminadas, hubo)
    registro = actualizar_registro(hoy, por_lab)

    datos = {"generado": ahora.replace(tzinfo=None).isoformat(timespec="minutes"),
             "desde": desde.isoformat(), "hasta": hasta.isoformat(), "laboratorios": [],
             "cambios_dias": DIAS_CAMBIOS,
             "ics": {**{c: f"calendarios/{c}.ics" for c in codigos}, "todos": "calendarios/todos.ics"}}
    for c in codigos:
        anuladas = cambios_recientes(registro, c, resultados[c]["reservas"], hoy)
        hay_historial = any(e["lab"] == c for e in registro) or por_lab[c][2]
        datos["laboratorios"].append({"codigo": c, "nombre": labs[c], "reservas": resultados[c]["reservas"],
                                      "festivos": resultados[c]["festivos"], "eliminadas": anuladas,
                                      "consulta_anterior": datos["generado"] if hay_historial else None})
    datos["colores"] = R.asignar_colores(
        [r["asignatura"] for l in datos["laboratorios"] for r in l["reservas"] + l["eliminadas"]], DATOS)

    (SITIO / "calendarios").mkdir(parents=True, exist_ok=True)
    pagina = R.generar_pagina(datos, R.logo_data_uri(), servidor=False)
    pagina = pagina.replace("<title>Reservas de laboratorios · SRA</title>",
                            "<title>Reservas de laboratorios · Dpto. Botánica, Ecología y Fisiología Vegetal · UCO</title>")
    (SITIO / "index.html").write_text(pagina, encoding="utf-8")
    todos = []
    for l in datos["laboratorios"]:
        ev = [(l["codigo"], l["nombre"], r) for r in l["reservas"]]
        todos += ev
        (SITIO / "calendarios" / f"{l['codigo']}.ics").write_text(
            generar_ics(f"Reservas · {l['nombre']}", ev, ahora), encoding="utf-8", newline="")
    (SITIO / "calendarios" / "todos.ics").write_text(
        generar_ics("Reservas · Todos los laboratorios", sorted(todos, key=lambda e: (e[2]["fecha"], e[2]["ini"])), ahora),
        encoding="utf-8", newline="")
    (SITIO / ".nojekyll").write_text("")
    for l in datos["laboratorios"]:
        print(f"  {l['nombre']}: {len(l['reservas'])} reservas, "
              f"{sum(1 for r in l['reservas'] if r.get('nuevo'))} nuevas y {len(l['eliminadas'])} anuladas en {DIAS_CAMBIOS} días")
    print(f"Web generada en {SITIO}")


if __name__ == "__main__":
    main()
