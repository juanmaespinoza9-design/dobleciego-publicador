"""
Publica en Instagram, como máximo un reel por corrida, el primer reel pendiente del programa.

programa/<ENSAYO>.json lo escribe la Mac; estado/<ENSAYO>.json lo escribe solo este script.
Si existe el archivo PAUSA en la raíz, no se publica nada.

Uso: python3 publicar.py [--ensayo NOMBRE] [--simulacro] [--conciliar] [--listar] [--sin-git] [--verificar]
Entorno: IG_TOKEN (token de usuario del sistema, no vence), IG_USER_ID, GH_TOKEN.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException

RAIZ = os.path.dirname(os.path.abspath(__file__))
API_HOST = "https://graph.facebook.com"
API_VERSION = "v27.0"
# Parámetro de Meta que rotula el contenido como generado por IA (confirmado en la documentación
# de publicación; falta confirmarlo para reels). Si el contenedor falla por él, ponerlo en None.
ROTULO_IA = "is_ai_generated"

MAX_CAPTION = 2200
MAX_HASHTAGS = 30
MAX_MENCIONES = 20
HORAS_ENTRE_PUBLICACIONES = 20
SONDEO_SEG = 60
SONDEO_LIMITE_SEG = 600
UTC_AR = dt.timedelta(hours=-3)
ESTADOS = ("en_curso", "publicado", "error")
REQUERIDOS = ("ensayo", "fecha", "video_url", "cover_url", "caption", "sha256_video")
NOMBRE_ENSAYO = re.compile(r"^[A-Za-z0-9 _-]+$")
SECRETOS_EXTRA = []


class Fallo(Exception):
    pass


MSG_TOKEN = ("El token de Instagram no es válido: hay que generar uno nuevo en la aplicación de Meta "
             "y cargarlo como secreto IG_TOKEN")


class TokenInvalido(Exception):
    """Token rechazado por Meta (código 190): corta la corrida sin tocar estados."""


class Transitorio(Fallo):
    """Red, timeout, 5xx o 429: no se escribe estado y se reintenta en la próxima corrida."""


def mascara(texto):
    texto = str(texto)
    for token in [os.environ.get("IG_TOKEN", ""), *SECRETOS_EXTRA]:
        if token:
            texto = texto.replace(token, "***")
    return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", texto)


def aviso(texto):
    print(mascara(texto), flush=True)


def ahora():
    return dt.datetime.now(dt.timezone.utc)


def hoy():
    return (ahora() + UTC_AR).date()


def dormir(seg):
    time.sleep(seg)


def http(metodo, url, datos=None, sha=False):
    """Única función que toca la red. Devuelve (código, cuerpo) o (código, sha256) si sha."""
    cuerpo = urllib.parse.urlencode(datos).encode() if datos is not None else None
    req = urllib.request.Request(url, data=cuerpo, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if not sha:
                return r.status, r.read()
            h = hashlib.sha256()
            for bloque in iter(lambda: r.read(1 << 20), b""):
                h.update(bloque)
            return r.status, h.hexdigest()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, HTTPException, OSError) as e:
        raise Transitorio("Error de red: " + mascara(e))


def transitorio(codigo):
    return codigo >= 500 or codigo == 429


def api(metodo, ruta, **params):
    params["access_token"] = os.environ["IG_TOKEN"]
    base = f"{API_HOST}/{API_VERSION}/{ruta}"
    if metodo == "GET":
        codigo, cuerpo = http("GET", base + "?" + urllib.parse.urlencode(params))
    else:
        codigo, cuerpo = http(metodo, base, datos=params)
    try:
        r = json.loads(cuerpo)
    except (ValueError, TypeError):
        raise (Transitorio if transitorio(codigo) else Fallo)(mascara(f"Respuesta no válida de la API (HTTP {codigo})"))
    if codigo >= 400 or "error" in r:
        e = r.get("error", {}) if isinstance(r, dict) else {}
        if e.get("code") == 190:
            raise TokenInvalido(MSG_TOKEN)
        clase = Transitorio if transitorio(codigo) or e.get("is_transient") else Fallo
        raise clase(mascara(f"API de Meta (HTTP {codigo}, código {e.get('code')}): {e.get('message', r)}"))
    return r


def leer_json(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def leer(ruta, defecto=None):
    try:
        return leer_json(ruta)
    except (OSError, ValueError):
        return defecto


def ruta_de(carpeta, ensayo):
    return os.path.join(RAIZ, carpeta, f"{ensayo}.json")


def faltantes(p):
    if not isinstance(p, dict):
        return ["JSON ilegible"]
    falta = [k for k in REQUERIDOS if not p.get(k)]
    try:
        dt.date.fromisoformat(str(p.get("fecha")))
    except ValueError:
        falta.append("fecha válida")
    return falta


def leer_estado(ensayo):
    ruta = ruta_de("estado", ensayo)
    if not os.path.exists(ruta):
        return None
    try:
        est = leer_json(ruta)
    except (OSError, ValueError):
        est = None
    if not isinstance(est, dict) or est.get("estado") not in ESTADOS:
        raise Fallo(f"BLOQUEADO: estado/{ensayo}.json está dañado o tiene un estado desconocido; corregirlo a mano.")
    return est


def cola():
    """Lista [(programa, estado o None)] ordenada por fecha y nombre."""
    filas = []
    carpeta = os.path.join(RAIZ, "programa")
    for nombre in sorted(os.listdir(carpeta)) if os.path.isdir(carpeta) else []:
        if not nombre.endswith(".json"):
            continue
        p = leer(os.path.join(carpeta, nombre))
        falta = faltantes(p)
        if falta:
            aviso(f"programa/{nombre}: inválido ({', '.join(falta)}); se saltea.")
            continue
        filas.append((p, leer_estado(p["ensayo"])))
    filas.sort(key=lambda f: (f[0]["fecha"], f[0]["ensayo"]))
    return filas


def primera_linea(caption):
    lineas = (caption or "").strip().splitlines()
    return re.sub(r"\s+", " ", lineas[0]).strip() if lineas else ""


def git(*args):
    r = subprocess.run(["git", "-C", RAIZ, *args], capture_output=True, text=True)
    if r.returncode:
        raise Fallo(mascara(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}"))
    return r.stdout


def subir_cambios(ruta, mensaje):
    git("add", "-A", "--", ruta)
    if subprocess.run(["git", "-C", RAIZ, "diff", "--cached", "--quiet", "--", ruta]).returncode == 0:
        return
    git("commit", "-m", mensaje)
    try:
        git("push")
    except Fallo:
        git("pull", "--rebase")
        git("push")


def guardar_estado(ensayo, datos, args):
    """Escribe estado/<ensayo>.json (o lo borra si datos es None) y lo sube a git."""
    if args.simulacro:
        return
    ruta = ruta_de("estado", ensayo)
    if datos is None:
        if os.path.exists(ruta):
            os.remove(ruta)
        mensaje = f"estado: {ensayo} vuelve a pendiente"
    else:
        datos = {"estado": "", "contenedor_id": "", "media_id": "", "enlace": "", "detalle": "", **datos,
                 "momento": ahora().strftime("%Y-%m-%dT%H:%M:%SZ")}
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            f.write("\n")
        mensaje = f"estado: {ensayo} {datos['estado']}"
    if not args.sin_git:
        subir_cambios(ruta, mensaje)


def pausa_en_disco():
    return any(n.lower().startswith("pausa") and os.path.isfile(os.path.join(RAIZ, n)) for n in os.listdir(RAIZ))


def hay_pausa(args):
    if pausa_en_disco():
        return True
    if args.sin_git:
        return False
    git("fetch", "origin")
    rama = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    return any(n.lower().startswith("pausa") for n in git("ls-tree", "--name-only", f"origin/{rama}").splitlines())


def listar_media(paginas=1):
    media, despues = [], None
    for _ in range(paginas):
        params = {"fields": "id,caption,timestamp,permalink", "limit": 50}
        if despues:
            params["after"] = despues
        r = api("GET", f"{os.environ['IG_USER_ID']}/media", **params)
        media += r.get("data", [])
        despues = r.get("paging", {}).get("cursors", {}).get("after") if r.get("paging", {}).get("next") else None
        if not despues:
            break
    return media


def buscar_por_linea(media, caption):
    clave = primera_linea(caption)
    return next((m for m in media if clave and primera_linea(m.get("caption")) == clave), None)


def momento(m):
    return dt.datetime.fromisoformat(re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", m["timestamp"].replace("Z", "+00:00")))


def resolver_en_curso(args):
    """Cierra los reels `en_curso` de una corrida anterior. Devuelve código de salida o None."""
    for p, est in cola():
        if not est or est["estado"] != "en_curso":
            continue
        ensayo = p["ensayo"]
        aviso(f"{ensayo}: quedó en_curso; consultando el contenedor.")
        try:
            codigo = api("GET", est["contenedor_id"], fields="status_code").get("status_code")
            detalle = f"El contenedor está en {codigo}; revisar a mano."
        except Transitorio as e:
            aviso(f"{ensayo}: no se pudo consultar el contenedor ahora, se reintenta luego: {e}")
            return 1
        except Fallo as e:
            aviso(f"{ensayo}: el contenedor no se puede consultar ({e}); se busca en las publicaciones.")
            codigo, detalle = None, f"El contenedor no se pudo consultar: {e}"
        if codigo == "PUBLISHED" or codigo is None:
            m = buscar_por_linea(listar_media(1), p["caption"])
            if m or codigo == "PUBLISHED":
                m = m or {}
                guardar_estado(ensayo, {**est, "estado": "publicado", "media_id": m.get("id", est.get("media_id", "")),
                                        "enlace": m.get("permalink", ""), "detalle": "Confirmado como publicado."}, args)
                aviso(f"{ensayo}: ya estaba publicado; estado corregido.")
                return 0
        guardar_estado(ensayo, {**est, "estado": "error", "detalle": detalle}, args)
        aviso(f"{ensayo}: marcado como error ({detalle}) No se reintenta solo.")
        return 1
    return None


def controlar(p):
    caption = p["caption"].strip()
    if len(caption) > MAX_CAPTION:
        raise Fallo(f"Caption de {len(caption)} caracteres (máximo {MAX_CAPTION}).")
    etiquetas = len(re.findall(r"(?<!\w)#\w+", caption))
    if etiquetas > MAX_HASHTAGS:
        raise Fallo(f"Caption con {etiquetas} hashtags (máximo {MAX_HASHTAGS}).")
    menciones = len(re.findall(r"(?<!\w)@\w", caption))
    if menciones > MAX_MENCIONES:
        raise Fallo(f"Caption con {menciones} menciones (máximo {MAX_MENCIONES}).")
    codigo, huella = http("GET", p["video_url"], sha=True)
    if transitorio(codigo):
        raise Transitorio(f"El video respondió HTTP {codigo}.")
    if codigo != 200:
        raise Fallo(f"El video respondió HTTP {codigo}.")
    if huella != p["sha256_video"]:
        raise Fallo("El sha256 del video descargado no coincide con el programa.")
    codigo, _ = http("GET", p["cover_url"])
    if transitorio(codigo):
        raise Transitorio(f"La portada respondió HTTP {codigo}.")
    if codigo != 200:
        raise Fallo(f"La portada respondió HTTP {codigo}.")
    return caption


def esperar_contenedor(cid):
    espera = 0
    while True:
        try:
            codigo = api("GET", cid, fields="status_code").get("status_code")
        except Transitorio as e:
            aviso(f"Consulta del contenedor fallida, se sigue esperando: {e}")
            codigo = None
        aviso(f"Contenedor {cid}: {codigo}")
        if codigo == "FINISHED":
            return
        if codigo in ("ERROR", "EXPIRED"):
            raise Fallo(f"El contenedor terminó en {codigo}.")
        if espera >= SONDEO_LIMITE_SEG:
            raise Fallo(f"El contenedor no terminó en {SONDEO_LIMITE_SEG // 60} minutos (último estado: {codigo}).")
        dormir(SONDEO_SEG)
        espera += SONDEO_SEG


def abrir_issue(p, enlace_ig):
    cuerpo = (f"{p['tiktok']}\n\nVideo: {p['video_url']}\nInstagram: {enlace_ig}\n\n"
              "Recordatorio: al subirlo, activar «contenido generado por IA».")
    r = subprocess.run(["gh", "issue", "create", "--title", f"TikTok: {p['ensayo']}", "--body", cuerpo],
                       capture_output=True, text=True)
    if r.returncode:
        aviso(f"ADVERTENCIA: no se pudo abrir el issue de TikTok: {r.stderr.strip()}")


def publicar(args):
    if args.ensayo:
        p = leer(ruta_de("programa", args.ensayo))
        falta = faltantes(p)
        if falta:
            aviso(f"programa/{args.ensayo}.json no sirve ({', '.join(falta)}).")
            return 1
        est = leer_estado(args.ensayo)
        if est and est["estado"] == "publicado":
            aviso(f"{args.ensayo} ya figura como publicado ({est.get('enlace', '')}); no se republica.")
            return 0
    else:
        pendientes = [x for x, est in cola() if est is None and x["fecha"] <= hoy().isoformat()]
        if not pendientes:
            aviso("No hay reels pendientes para hoy o antes.")
            return 0
        p = pendientes[0]
    ensayo = p["ensayo"]

    media = listar_media(1)
    repetido = buscar_por_linea(media, p["caption"])
    if repetido:
        guardar_estado(ensayo, {"estado": "publicado", "media_id": repetido["id"], "enlace": repetido.get("permalink", ""),
                                "detalle": "Ya figuraba en Instagram."}, args)
        aviso(f"{ensayo}: ya está publicado ({repetido.get('permalink', '')}); estado actualizado.")
        return 0
    limite = ahora() - dt.timedelta(hours=HORAS_ENTRE_PUBLICACIONES)
    if any(momento(m) > limite for m in media):
        aviso(f"Hay una publicación de las últimas {HORAS_ENTRE_PUBLICACIONES} horas; hoy no se publica {ensayo}.")
        return 0

    try:
        caption = controlar(p)
    except Transitorio as e:
        aviso(f"{ensayo}: falla transitoria en los controles, se reintenta en la próxima corrida: {e}")
        return 1
    except Fallo as e:
        aviso(f"{ensayo}: control fallido: {e}")
        guardar_estado(ensayo, {"estado": "error", "detalle": f"Control previo: {e}"}, args)
        return 1

    params = {"media_type": "REELS", "video_url": p["video_url"], "cover_url": p["cover_url"],
              "caption": caption, "share_to_feed": "true"}
    if ROTULO_IA:
        params[ROTULO_IA] = "true"
    try:
        cid = api("POST", f"{os.environ['IG_USER_ID']}/media", **params)["id"]
    except Transitorio as e:
        aviso(f"{ensayo}: falla transitoria al crear el contenedor, se reintenta en la próxima corrida: {e}")
        return 1
    except Fallo as e:
        detalle = f"Crear contenedor: {e}"
        if ROTULO_IA and ROTULO_IA in str(e):
            detalle += f" Si el error es por «{ROTULO_IA}», poner ROTULO_IA = None en publicar.py."
        aviso(f"{ensayo}: {detalle}")
        guardar_estado(ensayo, {"estado": "error", "detalle": detalle}, args)
        return 1
    aviso(f"{ensayo}: contenedor {cid} creado.")
    guardar_estado(ensayo, {"estado": "en_curso", "contenedor_id": cid}, args)

    try:
        esperar_contenedor(cid)
    except Fallo as e:
        aviso(f"{ensayo}: {e}")
        guardar_estado(ensayo, {"estado": "error", "contenedor_id": cid, "detalle": str(e)}, args)
        return 1
    if args.simulacro:
        aviso(f"{ensayo}: simulacro terminado; el contenedor quedó FINISHED y no se publicó.")
        return 0

    try:
        pausado = hay_pausa(args)
    except Fallo as e:
        aviso(f"{ensayo}: no se pudo confirmar que no haya PAUSA, no se publica: {e}")
        guardar_estado(ensayo, None, args)
        return 1
    if pausado:
        aviso(f"{ensayo}: apareció PAUSA antes de publicar; el reel vuelve a pendiente.")
        guardar_estado(ensayo, None, args)
        return 0

    try:
        media_id = api("POST", f"{os.environ['IG_USER_ID']}/media_publish", creation_id=cid)["id"]
    except Transitorio as e:
        aviso(f"{ensayo}: media_publish falló sin respuesta clara; queda en_curso y la próxima corrida lo resuelve: {e}")
        return 1
    except Fallo as e:
        aviso(f"{ensayo}: media_publish rechazado: {e}")
        guardar_estado(ensayo, {"estado": "error", "contenedor_id": cid, "detalle": f"media_publish: {e}"}, args)
        return 1
    try:
        enlace = api("GET", media_id, fields="permalink").get("permalink", "")
    except Fallo as e:
        aviso(f"ADVERTENCIA: publicado, pero no se obtuvo el enlace: {e}")
        enlace = ""
    aviso(f"{ensayo}: publicado {enlace}")
    rc = 0
    try:
        guardar_estado(ensayo, {"estado": "publicado", "contenedor_id": cid, "media_id": media_id, "enlace": enlace}, args)
    except Fallo as e:
        aviso(f"ADVERTENCIA: publicado pero no se pudo guardar el estado en git: {e}")
        rc = 1
    abrir_issue(p, enlace)
    return rc


def conciliar(args):
    media = listar_media(20)
    publicados, pendientes = [], []
    for p, est in cola():
        ya = est and est["estado"] == "publicado"
        m = None if ya else buscar_por_linea(media, p["caption"])
        if m:
            guardar_estado(p["ensayo"], {"estado": "publicado", "media_id": m["id"], "enlace": m.get("permalink", ""),
                                         "detalle": "Conciliado con Instagram."}, args)
        if ya or m:
            publicados.append((p, est.get("enlace") if ya else m.get("permalink", "")))
        else:
            pendientes.append((p, est))
    print("PUBLICADOS")
    for p, enlace in publicados:
        print(f"  {p['fecha']}  {p['ensayo']:<20} {enlace}")
    print("PENDIENTES")
    for p, est in pendientes:
        print(f"  {p['fecha']}  {p['ensayo']:<20} {(est or {}).get('estado', 'pendiente')}")
    return 0


def listar():
    for p, est in cola():
        print(f"{p['fecha']}  {p['ensayo']:<20} {(est or {}).get('estado', 'pendiente')}")
    return 0


def verificar():
    uid = os.environ["IG_USER_ID"]
    cuenta = api("GET", uid, fields="username,media_count")
    limite = api("GET", f"{uid}/content_publishing_limit", fields="quota_usage,config")
    datos = (limite.get("data") or [{}])[0]
    cfg = datos.get("config", {})
    print(f"Usuario: {cuenta.get('username')}")
    print(f"Publicaciones: {cuenta.get('media_count')}")
    print(f"Cuota de publicación: {datos.get('quota_usage')} de {cfg.get('quota_total')} "
          f"(ventana de {cfg.get('quota_duration')} s)")
    for m in listar_media(paginas=20):
        print(f"  {m.get('timestamp', '')[:10]}  {primera_linea(m.get('caption', ''))[:70]}")
    return 0


def ensayo_valido(texto):
    if not NOMBRE_ENSAYO.match(texto):
        raise argparse.ArgumentTypeError("solo letras, números, espacios, guion y guion bajo")
    return texto


def ejecutar(args):
    if args.listar:
        return listar()
    if not (args.conciliar or args.verificar) and pausa_en_disco():
        print("Existe un archivo PAUSA: no se publica nada.")
        return 0
    if not os.environ.get("IG_TOKEN") or not os.environ.get("IG_USER_ID"):
        print("Faltan IG_TOKEN o IG_USER_ID en el entorno.")
        return 1
    if args.verificar:
        return verificar()
    if args.conciliar:
        return conciliar(args)
    rc = resolver_en_curso(args)
    return publicar(args) if rc is None else rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ensayo", type=ensayo_valido, help="fuerza un ensayo puntual")
    ap.add_argument("--simulacro", action="store_true", help="llega hasta FINISHED sin publicar ni escribir estado")
    ap.add_argument("--conciliar", action="store_true", help="marca como publicados los que ya están en Instagram")
    ap.add_argument("--listar", action="store_true", help="muestra la cola sin usar la red")
    ap.add_argument("--sin-git", action="store_true", help="escribe el estado sin commit ni push; PAUSA solo en disco")
    ap.add_argument("--verificar", action="store_true", help="solo lectura: cuenta, publicaciones y cuota")
    args = ap.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") and os.environ.get("IG_TOKEN"):
        print(f"::add-mask::{os.environ['IG_TOKEN']}", flush=True)
    try:
        return ejecutar(args)
    except TokenInvalido as e:
        aviso(f"Error: {e}")
    except Fallo as e:
        aviso(f"Error: {e}")
    except Exception as e:
        aviso(f"Error inesperado ({type(e).__name__}): {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
