# -*- coding: utf-8 -*-
"""
Reservas SRA - Consulta de reservas de laboratorios del Departamento de
Botánica, Ecología y Fisiología Vegetal (Universidad de Córdoba).

Al abrirlo, muestra la aplicación en el navegador (un pequeño servidor local,
solo accesible desde el propio ordenador). Consulta la ocupación pública
(anónima) del Sistema de Reserva de Aulas (SRA) de la UCO para los laboratorios
y fechas elegidos y muestra una página con:
  - vista semanal (cuadrícula de horas, como en SRA),
  - lista de todas las reservas del periodo,
  - cambios respecto a la consulta anterior (reservas nuevas y desaparecidas).

Solo usa la biblioteca estándar de Python (no hay que instalar nada más).
Uso:  doble clic  o  python reservas_sra.py
"""

import configparser
import datetime as dt
import html
import json
import re
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

URL_CONSULTA = "https://www.uco.es/sra/index.php?go=sra/r2000400/action/r2000400_00.php"
CENTRO = "01"      # Campus de Rabanales
EDIFICIO = "0108"  # Celestino Mutis
CONSULTAS_SIMULTANEAS = 4
TIMEOUT_S = 30

CONFIG_POR_DEFECTO = """\
; Configuración de "Reservas SRA".
; Puede editarse con el Bloc de notas. Las líneas que empiezan por ; son comentarios.

[laboratorios]
; código SRA = nombre que se mostrará
0108LBO = Laboratorio de Botánica
0108LCO = Laboratorio de Botánica 2
0108LEC = Laboratorio de Ecología
0108LFV = Laboratorio de Fisiología Vegetal

; Los cuatrimestres se calculan solos: 1.º de septiembre a enero, 2.º de febrero a julio.
"""


def carpeta_programa() -> Path:
    # Funciona tanto como script .py como empaquetado en .exe (PyInstaller)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def carpeta_resultados() -> Path:
    docs = Path.home() / "Documents"
    base = docs if docs.exists() else Path.home()
    destino = base / "Reservas SRA"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def leer_config():
    ruta = carpeta_programa() / "reservas_sra.ini"
    if not ruta.exists():
        ruta.write_text(CONFIG_POR_DEFECTO, encoding="utf-8")
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",))
    cp.optionxform = str  # respeta mayúsculas de los códigos
    cp.read(ruta, encoding="utf-8")
    labs = {k.strip().upper(): v.strip() for k, v in cp.items("laboratorios")}
    cuatris = []
    if cp.has_section("cuatrimestres"):
        for _, v in cp.items("cuatrimestres"):
            try:
                a, b = [dt.date.fromisoformat(x.strip()) for x in v.split(",")]
                cuatris.append((a, b))
            except ValueError:
                pass
    return labs, sorted(cuatris)


# Cuatrimestres automáticos: 1.º = septiembre-enero, 2.º = febrero-julio; agosto sin asignar.
NOMBRES_CUATRI = {1: "1.er cuatrimestre (septiembre–enero)", 2: "2.º cuatrimestre (febrero–julio)"}


def curso_de(anio_inicio: int) -> str:
    return f"{anio_inicio}-{(anio_inicio + 1) % 100:02d}"


def anio_inicio(curso: str) -> int:
    return int(curso.split("-")[0])


def limites_cuatrimestre(curso: str, n: int):
    a = anio_inicio(curso)
    if n == 1:
        return dt.date(a, 9, 1), dt.date(a + 1, 1, 31)
    return dt.date(a + 1, 2, 1), dt.date(a + 1, 7, 31)


def cuatrimestre_de(fecha: dt.date):
    """(curso, n) del cuatrimestre que contiene la fecha, o None en agosto."""
    if fecha.month >= 9:
        return curso_de(fecha.year), 1
    if fecha.month == 1:
        return curso_de(fecha.year - 1), 1
    if 2 <= fecha.month <= 7:
        return curso_de(fecha.year - 1), 2
    return None


def cuatrimestre_por_defecto(hoy: dt.date):
    """El cuatrimestre en curso; en agosto, el 1.º del curso que empieza."""
    return cuatrimestre_de(hoy) or (curso_de(hoy.year), 1)


def cuatrimestre_siguiente(curso: str, n: int):
    return (curso, 2) if n == 1 else (curso_de(anio_inicio(curso) + 1), 1)


def cuatrimestre_anterior(curso: str, n: int):
    return (curso, 1) if n == 2 else (curso_de(anio_inicio(curso) - 1), 2)


def fecha_hasta_por_defecto(hoy: dt.date, cuatris=None) -> dt.date:
    """Fin del cuatrimestre en curso (o del siguiente, en agosto)."""
    return limites_cuatrimestre(*cuatrimestre_por_defecto(hoy))[1]


# ---------------------------------------------------------------------------
# Lectura de la página semanal de SRA
# ---------------------------------------------------------------------------

DIAS = ("Lun", "Mar", "Mie", "Mié", "Jue", "Vie", "Sáb", "Sab", "Dom")
RE_SEMANA = re.compile(r"del (\d{2}-\d{2}-\d{4}) al (\d{2}-\d{2}-\d{4})")
RE_TITULO = re.compile(r"^\((?P<tipo>[^)]*)\)\s*(?P<asig>.*?)\s*\.\s*Grupos?\s*:\s*(?P<grupo>.*)$")


class _ParserSemana(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.reservas = []      # (fecha 'dd-mm-aaaa', hora 'HH:MM', rowspan, texto)
        self.cabeceras = []     # textos de las celdas <th>
        self._td_ocupado = None  # rowspan del <td class="ocupado"> abierto
        self._a = None           # datos del enlace abierto
        self._th = None

    def handle_starttag(self, tag, attrs):
        at = dict(attrs)
        if tag == "td":
            clases = (at.get("class") or "").split()
            self._td_ocupado = int(at.get("rowspan") or 1) if "ocupado" in clases else None
        elif tag == "a" and self._td_ocupado is not None:
            href = at.get("href") or ""
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            if "fReserva" in q and "cHora" in q:
                self._a = {"f": q["fReserva"][0], "h": q["cHora"][0],
                           "n": self._td_ocupado, "t": []}
        elif tag == "th":
            self._th = []
        elif tag == "br" and self._th is not None:
            self._th.append(" ")

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self._a["t"] = re.sub(r"\s+", " ", "".join(self._a["t"])).strip()
            self.reservas.append(self._a)
            self._a = None
        elif tag == "th" and self._th is not None:
            self.cabeceras.append(re.sub(r"\s+", " ", "".join(self._th)).strip())
            self._th = None
        elif tag == "td":
            self._td_ocupado = None

    def handle_data(self, data):
        if self._a is not None:
            self._a["t"].append(data)
        if self._th is not None:
            self._th.append(data)


def _a_fecha(ddmmaaaa: str) -> dt.date:
    d, m, a = ddmmaaaa.split("-")
    return dt.date(int(a), int(m), int(d))


def limpiar_titulo(texto: str) -> dict:
    """'(P) MICOLOGíA . Grupo : 1 (GM)' -> tipo P, asignatura 'Micología', grupo '1 (GM)'."""
    m = RE_TITULO.match(texto)
    if m:
        asig = m.group("asig").strip().lower()
        asig = re.sub(r"\b(i{1,3}|iv|vi{0,3}|ix|x)\b", lambda x: x.group(0).upper(), asig)  # I, II, IV...
        return {"tipo": m.group("tipo").strip(), "asignatura": asig[:1].upper() + asig[1:],
                "grupo": m.group("grupo").strip()}
    return {"tipo": "", "asignatura": texto, "grupo": ""}


def leer_semana(pagina: str):
    """Devuelve (lunes, reservas, festivos) a partir del HTML de la consulta semanal."""
    p = _ParserSemana()
    p.feed(pagina)
    m = RE_SEMANA.search(pagina)
    lunes = _a_fecha(m.group(1)) if m else None

    reservas = []
    for r in p.reservas:
        fecha = _a_fecha(r["f"])
        h, mi = map(int, r["h"].split(":"))
        ini = dt.datetime.combine(fecha, dt.time(h, mi))
        fin = ini + dt.timedelta(minutes=30 * r["n"])
        reservas.append({"fecha": fecha.isoformat(), "ini": ini.strftime("%H:%M"),
                         "fin": fin.strftime("%H:%M"), "texto": r["t"], **limpiar_titulo(r["t"])})

    festivos = []
    if lunes:
        dias = [c for c in p.cabeceras if c.split(".")[0] in DIAS]
        for i, c in enumerate(dias[:7]):
            resto = c.split(".", 1)[1].strip() if "." in c else ""
            if resto:
                festivos.append({"fecha": (lunes + dt.timedelta(days=i)).isoformat(), "nombre": resto})
    return lunes, reservas, festivos


# ---------------------------------------------------------------------------
# Consulta a SRA
# ---------------------------------------------------------------------------

def descargar_semana(codigo: str, dia: dt.date, intentos: int = 3) -> str:
    datos = urllib.parse.urlencode({
        "tCentro": CENTRO, "tEdificio": EDIFICIO, "tAula": codigo,
        "calendarDate1": dia.isoformat(), "submit": "Enviar",
    }).encode("ascii")
    ultimo_error = None
    for _ in range(intentos):
        try:
            req = urllib.request.Request(URL_CONSULTA, data=datos, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ReservasSRA-DptoBotanica/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.read().decode("iso-8859-1")
        except Exception as e:  # red, timeout...
            ultimo_error = e
    raise RuntimeError(f"No se pudo consultar SRA ({codigo}, semana del {dia}): {ultimo_error}")


def lunes_de(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def consultar(codigos, desde: dt.date, hasta: dt.date, progreso=None):
    """Consulta todas las semanas de [desde, hasta] para cada laboratorio.
    Devuelve {codigo: {"reservas": [...], "festivos": [...]}}."""
    semanas = []
    d = lunes_de(desde)
    while d <= hasta:
        semanas.append(d)
        d += dt.timedelta(days=7)
    tareas = [(c, s) for c in codigos for s in semanas]
    res = {c: {"reservas": [], "festivos": {}} for c in codigos}
    hechas = 0
    with ThreadPoolExecutor(max_workers=CONSULTAS_SIMULTANEAS) as ex:
        futuros = {ex.submit(descargar_semana, c, s): (c, s) for c, s in tareas}
        for fu in as_completed(futuros):
            c, _ = futuros[fu]
            _, reservas, festivos = leer_semana(fu.result())
            res[c]["reservas"].extend(r for r in reservas if desde.isoformat() <= r["fecha"] <= hasta.isoformat())
            for f in festivos:
                res[c]["festivos"][f["fecha"]] = f["nombre"]
            hechas += 1
            if progreso:
                progreso(hechas, len(tareas))
    for c in res:
        res[c]["reservas"].sort(key=lambda r: (r["fecha"], r["ini"]))
        res[c]["festivos"] = [{"fecha": k, "nombre": v} for k, v in sorted(res[c]["festivos"].items())]
    return res


# ---------------------------------------------------------------------------
# Cambios respecto a la consulta anterior
# ---------------------------------------------------------------------------

def _clave(r):
    return (r["fecha"], r["ini"], r["fin"], r["texto"])


def comparar_con_anterior(codigo, actual, desde, hasta, carpeta: Path):
    """Marca reservas nuevas y lista las que han desaparecido. Guarda la consulta actual."""
    ruta = carpeta / f"historial_{codigo}.json"
    anterior = None
    if ruta.exists():
        try:
            anterior = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            anterior = None

    eliminadas, consultado_antes = [], None
    if anterior:
        consultado_antes = anterior.get("consultado")
        # Solo se compara el tramo de fechas que cubren ambas consultas
        a_ini = max(desde.isoformat(), anterior["desde"])
        a_fin = min(hasta.isoformat(), anterior["hasta"])
        previas = {_clave(r): r for r in anterior["reservas"] if a_ini <= r["fecha"] <= a_fin}
        actuales = {_clave(r) for r in actual["reservas"]}
        for r in actual["reservas"]:
            r["nuevo"] = a_ini <= r["fecha"] <= a_fin and _clave(r) not in previas
        eliminadas = [r for k, r in previas.items() if k not in actuales]
        eliminadas.sort(key=lambda r: (r["fecha"], r["ini"]))

    # Se fusiona con el historial para no perder semanas fuera del rango consultado
    fusion = [r for r in (anterior or {}).get("reservas", [])
              if not (desde.isoformat() <= r["fecha"] <= hasta.isoformat())]
    fusion += [{k: v for k, v in r.items() if k != "nuevo"} for r in actual["reservas"]]
    nuevo_hist = {
        "consultado": dt.datetime.now().isoformat(timespec="minutes"),
        "desde": min([desde.isoformat()] + ([anterior["desde"]] if anterior else [])),
        "hasta": max([hasta.isoformat()] + ([anterior["hasta"]] if anterior else [])),
        "reservas": sorted(fusion, key=lambda r: (r["fecha"], r["ini"])),
    }
    ruta.write_text(json.dumps(nuevo_hist, ensure_ascii=False, indent=1), encoding="utf-8")
    return eliminadas, consultado_antes


# ---------------------------------------------------------------------------
# Logotipo de la UCO (se descarga de la web oficial y se guarda en caché)
# ---------------------------------------------------------------------------

URL_LOGO = "https://www.uco.es/assets-portada/images/logo.png"


LOGOS_OFICIALES = ("logo_oficial.png", "logo_oficial.jpg", "logo_oficial.svg")


def _data_uri(ruta: Path) -> str:
    import base64
    tipo = {".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}.get(ruta.suffix.lower(), "image/png")
    return f"data:{tipo};base64," + base64.b64encode(ruta.read_bytes()).decode("ascii")


def logo_data_uri():
    """Devuelve (logo como data URI, recortar).
    - Si existe un logo oficial (logo_oficial.*), se usa tal cual.
    - Si no, se usa la imagen de la web de la UCO, que junta tres logos; la página
      muestra solo la parte izquierda (isotipo + «Universidad de Córdoba») -> recortar=True."""
    bases = []
    if getattr(sys, "frozen", False):
        bases.append(Path(getattr(sys, "_MEIPASS", "")))
    bases += [carpeta_programa(), carpeta_resultados()]
    for b in bases:
        for n in LOGOS_OFICIALES:
            if (b / n).exists() and (b / n).stat().st_size > 0:
                return _data_uri(b / n), False
    for b in bases:
        if (b / "logo_uco.png").exists() and (b / "logo_uco.png").stat().st_size > 0:
            return _data_uri(b / "logo_uco.png"), True
    try:
        req = urllib.request.Request(URL_LOGO, headers={"User-Agent": "ReservasSRA-DptoBotanica/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            datos = r.read()
        if datos[:8] == b"\x89PNG\r\n\x1a\n":
            (carpeta_resultados() / "logo_uco.png").write_bytes(datos)
            return _data_uri(carpeta_resultados() / "logo_uco.png"), True
    except Exception:
        pass
    return URL_LOGO, True


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

def _rellenar(plantilla: str, **valores) -> str:
    for k, v in valores.items():
        plantilla = plantilla.replace(f"__{k}__", v)
    return plantilla


def _json_js(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def _plantilla(nombre: str, incrustada: str) -> str:
    ruta = carpeta_programa() / nombre
    if not getattr(sys, "frozen", False) and ruta.exists():  # durante el desarrollo
        return ruta.read_text(encoding="utf-8")
    return incrustada


def generar_pagina(datos: dict, logo, servidor: bool = False) -> str:
    uri, recortar = logo
    return _rellenar(_plantilla("plantilla_resultados.html", PLANTILLA_RESULTADOS),
                     DATOS=_json_js(datos), LOGO=uri, LOGOCLASE="logo-uco recorte" if recortar else "logo-uco",
                     SERVIDOR="true" if servidor else "false")


N_COLORES = 16


def asignar_colores(asignaturas, carpeta: Path) -> dict:
    """Da a cada asignatura un color fijo (índice de la paleta) y lo recuerda entre consultas.
    Mientras haya menos de 16 asignaturas, ninguna comparte color."""
    ruta = carpeta / "colores_asignaturas.json"
    try:
        mapa = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        mapa = {}
    presentes = sorted(set(asignaturas))
    # colores ya usados por las asignaturas de esta consulta (para no repetirlos)
    usados = {mapa[a] % N_COLORES for a in presentes if a in mapa}
    for a in presentes:
        if a in mapa:
            continue
        libres = [i for i in range(N_COLORES) if i not in usados and i not in set(v % N_COLORES for v in mapa.values())] \
            or [i for i in range(N_COLORES) if i not in usados] or [len(mapa) % N_COLORES]
        mapa[a] = libres[0]
        usados.add(libres[0])
    try:
        ruta.write_text(json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    # Si dos asignaturas de esta consulta comparten color (más de 16 en total), se reasigna solo para esta página
    vistos, resultado = set(), {}
    for a in presentes:
        i = mapa[a] % N_COLORES
        if i in vistos:
            i = next((k for k in range(N_COLORES) if k not in vistos), i)
        vistos.add(i)
        resultado[a] = i
    return resultado


def ejecutar(codigos, labs, desde, hasta, progreso=None, logo=None, servidor=False):
    """Consulta SRA, guarda Documentos\\Reservas SRA\\reservas.html y devuelve (ruta, html_para_servidor)."""
    # Siempre semanas completas: del lunes de la primera semana al domingo de la última,
    # para que no parezca que los días anteriores de la semana no hubo actividad.
    desde, hasta = lunes_de(desde), lunes_de(hasta) + dt.timedelta(days=6)
    resultados = consultar(codigos, desde, hasta, progreso)
    carpeta = carpeta_resultados()
    datos = {"generado": dt.datetime.now().isoformat(timespec="minutes"),
             "desde": desde.isoformat(), "hasta": hasta.isoformat(), "laboratorios": []}
    for c in codigos:
        eliminadas, antes = comparar_con_anterior(c, resultados[c], desde, hasta, carpeta)
        datos["laboratorios"].append({"codigo": c, "nombre": labs.get(c, c),
                                      "reservas": resultados[c]["reservas"],
                                      "festivos": resultados[c]["festivos"],
                                      "eliminadas": eliminadas, "consulta_anterior": antes})
    datos["colores"] = asignar_colores(
        [r["asignatura"] for l in datos["laboratorios"] for r in l["reservas"] + l["eliminadas"]], carpeta)
    logo = logo or logo_data_uri()
    salida = carpeta / "reservas.html"
    salida.write_text(generar_pagina(datos, logo, servidor=False), encoding="utf-8")
    return salida, (generar_pagina(datos, logo, servidor=True) if servidor else None)


# ---------------------------------------------------------------------------
# Aplicación: pequeño servidor local que muestra la interfaz en el navegador
# ---------------------------------------------------------------------------

PUERTO_PREFERIDO = 8765
FIRMA = "reservas-sra-dpto-botanica"
VERSION = "2026-11-26.5"
INACTIVIDAD_MAX_S = 180  # se cierra solo si no hay ninguna pestaña abierta durante 3 minutos


def _parse_fecha(txt: str) -> dt.date:
    txt = txt.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(txt, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Fecha no válida: «{txt}»")


def _leer_preferencias() -> list:
    try:
        return json.loads((carpeta_resultados() / "preferencias.json").read_text(encoding="utf-8")).get("labs", [])
    except Exception:
        return []


def _guardar_preferencias(codigos):
    try:
        (carpeta_resultados() / "preferencias.json").write_text(json.dumps({"labs": codigos}), encoding="utf-8")
    except Exception:
        pass


def aplicacion(abrir_navegador=True, puerto=PUERTO_PREFERIDO):
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    # Si ya hay una copia abierta de esta misma versión, basta con mostrarla.
    # Si es una versión anterior, se le pide que se cierre (o se usa otro puerto).
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/ping", timeout=2) as r:
            firma = r.read().decode()
        if firma == f"{FIRMA} {VERSION}":
            if abrir_navegador:
                webbrowser.open(f"http://127.0.0.1:{puerto}/")
            return
        if firma.startswith(FIRMA):
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{puerto}/api/salir", data=b"", method="POST"), timeout=2)
                time.sleep(1.5)
            except Exception:
                pass
    except Exception:
        pass

    labs, cuatris = leer_config()
    estado = {"ultimo_ping": time.time(), "trabajos": {}, "resultado": None, "logo": None}
    cerrojo = threading.Lock()

    def logo():
        if estado["logo"] is None:
            estado["logo"] = logo_data_uri()
        return estado["logo"]

    threading.Thread(target=logo, daemon=True).start()  # se prepara mientras se abre el navegador

    def pagina_inicio():
        hoy = dt.date.today()
        config = {
            "laboratorios": [{"codigo": c, "nombre": n} for c, n in labs.items()],
            "desde": lunes_de(hoy).isoformat(),
            "hasta": fecha_hasta_por_defecto(hoy, cuatris).isoformat(),
            "seleccion": [c for c in _leer_preferencias() if c in labs],
            "hay_resultado": estado["resultado"] is not None,
        }
        uri, recortar = logo()
        return _rellenar(_plantilla("plantilla_app.html", PLANTILLA_APP), CONFIG=_json_js(config), LOGO=uri,
                         LOGOCLASE="logo-uco recorte" if recortar else "logo-uco")

    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencio
            pass

        def _enviar(self, cuerpo, tipo="text/html; charset=utf-8", codigo=200):
            datos = cuerpo.encode("utf-8") if isinstance(cuerpo, str) else cuerpo
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        def _json(self, obj, codigo=200):
            self._enviar(json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", codigo)

        def do_GET(self):
            ruta = urllib.parse.urlsplit(self.path)
            estado["ultimo_ping"] = time.time()
            if ruta.path == "/":
                self._enviar(pagina_inicio())
            elif ruta.path == "/resultado":
                if estado["resultado"]:
                    self._enviar(estado["resultado"])
                else:
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.end_headers()
            elif ruta.path == "/api/ping":
                self._enviar(f"{FIRMA} {VERSION}", "text/plain")
            elif ruta.path == "/api/estado":
                q = urllib.parse.parse_qs(ruta.query)
                t = estado["trabajos"].get((q.get("id") or [""])[0])
                if not t:
                    return self._json({"error": "Consulta no encontrada"}, 404)
                self._json({k: v for k, v in t.items()})
            else:
                self._enviar("No encontrado", "text/plain", 404)

        def do_POST(self):
            estado["ultimo_ping"] = time.time()
            if self.path == "/api/salir" and not self.headers.get("Origin"):  # solo desde otra copia del programa
                self._json({"ok": True})
                threading.Thread(target=srv.shutdown, daemon=True).start()
                return
            if self.path != "/api/consultar":
                return self._enviar("No encontrado", "text/plain", 404)
            # Solo se aceptan peticiones de la propia página (evita que otra web use el servidor)
            origen = self.headers.get("Origin", "")
            if origen and not origen.startswith(("http://127.0.0.1:", "http://localhost:")):
                return self._json({"error": "Origen no permitido"}, 403)
            try:
                largo = int(self.headers.get("Content-Length") or 0)
                p = json.loads(self.rfile.read(largo) or b"{}")
                codigos = [c for c in p.get("labs", []) if c in labs]
                desde, hasta = _parse_fecha(p["desde"]), _parse_fecha(p["hasta"])
                if not codigos:
                    raise ValueError("Seleccione al menos un laboratorio.")
                if hasta < desde:
                    raise ValueError("La fecha «Hasta» es anterior a «Desde».")
                if (hasta - desde).days > 366:
                    raise ValueError("El periodo no puede superar un año.")
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                return self._json({"error": str(e)}, 400)

            _guardar_preferencias(codigos)
            id_t = f"{time.time():.6f}"
            trabajo = {"hechas": 0, "total": 0, "listo": False, "error": None}
            with cerrojo:
                estado["trabajos"] = {id_t: trabajo}  # solo interesa la última

            def progreso(h, t):
                trabajo["hechas"], trabajo["total"] = h, t

            def tarea():
                try:
                    _, html_srv = ejecutar(codigos, labs, desde, hasta, progreso, logo=logo(), servidor=True)
                    estado["resultado"] = html_srv
                    trabajo["listo"] = True
                except Exception as e:
                    trabajo["error"] = str(e)

            threading.Thread(target=tarea, daemon=True).start()
            self._json({"id": id_t})

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    except OSError:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)  # puerto ocupado: otro cualquiera
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def vigilante():
        while True:
            time.sleep(10)
            if time.time() - estado["ultimo_ping"] > INACTIVIDAD_MAX_S:
                srv.shutdown()
                return

    threading.Thread(target=vigilante, daemon=True).start()
    if abrir_navegador:
        webbrowser.open(url)
    else:
        print(url, flush=True)
    srv.serve_forever()


def linea_de_comandos(args):
    """python reservas_sra.py 0108LBO [0108LEC ...] [--desde dd/mm/aaaa] [--hasta dd/mm/aaaa]"""
    labs, cuatris = leer_config()
    hoy = dt.date.today()
    desde, hasta, codigos = lunes_de(hoy), fecha_hasta_por_defecto(hoy, cuatris), []
    it = iter(args)
    for a in it:
        if a == "--desde":
            desde = _parse_fecha(next(it))
        elif a == "--hasta":
            hasta = _parse_fecha(next(it))
        else:
            codigos.append(a.upper())
    ruta, _ = ejecutar(codigos or list(labs), labs, desde, hasta,
                       lambda h, t: print(f"\r{h}/{t} semanas", end="", flush=True))
    print(f"\nPágina generada: {ruta}")
    webbrowser.open(ruta.as_uri())


PLANTILLA_APP = r"""__PLANTILLA_APP__"""
PLANTILLA_RESULTADOS = r"""__PLANTILLA_RESULTADOS__"""

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--servidor":  # pruebas: no abre el navegador
        aplicacion(abrir_navegador=False, puerto=int(sys.argv[2]) if len(sys.argv) > 2 else PUERTO_PREFERIDO)
    elif len(sys.argv) > 1:
        linea_de_comandos(sys.argv[1:])
    else:
        aplicacion()
