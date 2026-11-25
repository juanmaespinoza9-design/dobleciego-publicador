"""
Publica en Instagram, como máximo un reel por corrida, el primer reel pendiente del programa.

programa/<ENSAYO>.json lo escribe la Mac; estado/<ENSAYO>.json lo escribe solo este script.
Si existe el archivo PAUSA en la raíz, no se publica nada.

Uso: python3 publicar.py [--ensayo NOMBRE] [--simulacro] [--conciliar] [--listar] [--sin-git]
Entorno: IG_TOKEN, IG_USER_ID, IG_TOKEN_FECHA (AAAA-MM-DD), GH_TOKEN, GH_PAT_SECRETS.
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

RAIZ = os.path.dirname(os.path.abspath(__file__))
API = "https://graph.instagram.com"
VERSION = "v23.0"
# Nombre del parámetro de Meta que rotula el reel como contenido generado por IA.
# Sin verificar que exista: mientras sea None no se envía nada.
ROTULO_IA = None

MAX_CAPTION = 2200
MAX_HASHTAGS = 30
HORAS_ENTRE_PUBLICACIONES = 20
SONDEO_SEG = 30
SONDEO_LIMITE_SEG = 600
DIAS_RENOVAR_TOKEN = 7
UTC_AR = dt.timedelta(hours=-3)


class Fallo(Exception):
    pass


def mascara(texto):
    texto = str(texto)
    token = os.environ.get("IG_TOKEN", "")
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
    except (urllib.error.URLError, OSError) as e:
        raise Fallo("Error de red: " + mascara(e))


def api(metodo, ruta, **params):
    params["access_token"] = os.environ["IG_TOKEN"]
    base = f"{API}/{ruta}" if ruta == "refresh_access_token" else f"{API}/{VERSION}/{ruta}"
    if metodo == "GET":
        codigo, cuerpo = http("GET", base + "?" + urllib.parse.urlencode(params))
    else:
        codigo, cuerpo = http(metodo, base, datos=params)
    try:
        r = json.loads(cuerpo)
    except (ValueError, TypeError):
        raise Fallo(mascara(f"Respuesta no válida de la API (HTTP {codigo})"))
    if codigo >= 400 or "error" in r:
        e = r.get("error", {}) if isinstance(r, dict) else {}
        raise Fallo(mascara(f"API de Meta (HTTP {codigo}, código {e.get('code')}): {e.get('message', r)}"))
    return r


def leer(ruta, defecto=None):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def ruta_de(carpeta, ensayo):
    return os.path.join(RAIZ, carpeta, f"{ensayo}.json")


def cola():
    """Lista [(programa, estado o None)] ordenada por fecha y nombre."""
    filas = []
    carpeta = os.path.join(RAIZ, "programa")
    for nombre in sorted(os.listdir(carpeta)) if os.path.isdir(carpeta) else []:
        if not nombre.endswith(".json"):
            continue
        p = leer(os.path.join(carpeta, nombre))
        if p:
            filas.append((p, leer(ruta_de("estado", p["ensayo"]))))
    filas.sort(key=lambda f: (f[0]["fecha"], f[0]["ensayo"]))
    return filas


def primera_linea(caption):
    lineas = (caption or "").strip().splitlines()
    return re.sub(r"\s+", " ", lineas[0]).strip() if lineas else ""


def git(*args):
    r = subprocess.run(["git", "-C", RAIZ, *args], capture_output=True, text=True)
    if r.returncode:
        raise Fallo(mascara(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}"))


def guardar_estado(ensayo, datos, args):
    if args.simulacro:
        return
    datos = {"estado": "", "contenedor_id": "", "media_id": "", "enlace": "", "detalle": "", **datos,
             "momento": ahora().strftime("%Y-%m-%dT%H:%M:%SZ")}
    ruta = ruta_de("estado", ensayo)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if args.sin_git:
        return
    git("add", ruta)
    git("commit", "-m", f"estado: {ensayo} {datos['estado']}")
    try:
        git("push")
    except Fallo:
        git("pull", "--rebase")
        git("push")


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
        if not est or est.get("estado") != "en_curso":
            continue
        ensayo = p["ensayo"]
        aviso(f"{ensayo}: quedó en_curso; consultando el contenedor.")
        try:
            codigo = api("GET", est["contenedor_id"], fields="status_code").get("status_code")
        except Fallo as e:
            aviso(f"{ensayo}: no se pudo consultar el contenedor: {e}")
            return 1
        if codigo == "PUBLISHED":
            m = buscar_por_linea(listar_media(1), p["caption"]) or {}
            guardar_estado(ensayo, {**est, "estado": "publicado", "media_id": m.get("id", est.get("media_id", "")),
                                    "enlace": m.get("permalink", ""), "detalle": "Confirmado como publicado."}, args)
            aviso(f"{ensayo}: ya estaba publicado; estado corregido.")
            return 0
        guardar_estado(ensayo, {**est, "estado": "error", "detalle": f"Quedó en curso y el contenedor está en {codigo}; revisar a mano."}, args)
        aviso(f"{ensayo}: marcado como error (contenedor en {codigo}). No se reintenta solo.")
        return 1
    return None


def controlar(p):
    caption = p["caption"].strip()
    if len(caption) > MAX_CAPTION:
        raise Fallo(f"Caption de {len(caption)} caracteres (máximo {MAX_CAPTION}).")
    etiquetas = len(re.findall(r"(?<!\w)#\w+", caption))
    if etiquetas > MAX_HASHTAGS:
        raise Fallo(f"Caption con {etiquetas} hashtags (máximo {MAX_HASHTAGS}).")
    codigo, huella = http("GET", p["video_url"], sha=True)
    if codigo != 200:
        raise Fallo(f"El video respondió HTTP {codigo}.")
    if huella != p["sha256_video"]:
        raise Fallo("El sha256 del video descargado no coincide con el programa.")
    codigo, _ = http("GET", p["cover_url"])
    if codigo != 200:
        raise Fallo(f"La portada respondió HTTP {codigo}.")
    return caption


def esperar_contenedor(cid):
    inicio = 0
    while True:
        codigo = api("GET", cid, fields="status_code").get("status_code")
        aviso(f"Contenedor {cid}: {codigo}")
        if codigo == "FINISHED":
            return
        if codigo in ("ERROR", "EXPIRED"):
            raise Fallo(f"El contenedor terminó en {codigo}.")
        if inicio >= SONDEO_LIMITE_SEG:
            raise Fallo(f"El contenedor no terminó en {SONDEO_LIMITE_SEG // 60} minutos (último estado: {codigo}).")
        dormir(SONDEO_SEG)
        inicio += SONDEO_SEG


def abrir_issue(p, enlace_ig):
    cuerpo = (f"{p['tiktok']}\n\nVideo: {p['video_url']}\nInstagram: {enlace_ig}\n\n"
              "Recordatorio: al subirlo, activar «contenido generado por IA».")
    r = subprocess.run(["gh", "issue", "create", "--title", f"TikTok: {p['ensayo']}", "--body", cuerpo],
                       capture_output=True, text=True)
    if r.returncode:
        aviso(f"ADVERTENCIA: no se pudo abrir el issue de TikTok: {r.stderr.strip()}")


def publicar(args):
    p = None
    if args.ensayo:
        p = leer(ruta_de("programa", args.ensayo))
        if not p:
            aviso(f"No existe programa/{args.ensayo}.json")
            return 1
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
    except Fallo as e:
        aviso(f"{ensayo}: no se pudo crear el contenedor: {e}")
        guardar_estado(ensayo, {"estado": "error", "detalle": f"Crear contenedor: {e}"}, args)
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
        media_id = api("POST", f"{os.environ['IG_USER_ID']}/media_publish", creation_id=cid)["id"]
    except Fallo as e:
        aviso(f"{ensayo}: falló media_publish; queda en_curso para revisarlo en la próxima corrida: {e}")
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
        ya = est and est.get("estado") == "publicado"
        m = None if ya else buscar_por_linea(media, p["caption"])
        if m:
            guardar_estado(p["ensayo"], {"estado": "publicado", "media_id": m["id"], "enlace": m.get("permalink", ""),
                                         "detalle": "Conciliado con Instagram."}, args)
        if ya or m:
            publicados.append((p, (est or {}).get("enlace") if ya else m.get("permalink", "")))
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


def renovar_token():
    fecha = os.environ.get("IG_TOKEN_FECHA", "").strip()
    if not fecha or not os.environ.get("IG_TOKEN"):
        return 0
    try:
        if (hoy() - dt.date.fromisoformat(fecha)).days <= DIAS_RENOVAR_TOKEN:
            return 0
        nuevo = api("GET", "refresh_access_token", grant_type="ig_refresh_token")["access_token"]
        env = {**os.environ, "GH_TOKEN": os.environ.get("GH_PAT_SECRETS", "")}
        repo = ["--repo", os.environ["GITHUB_REPOSITORY"]] if os.environ.get("GITHUB_REPOSITORY") else []
        r = subprocess.run(["gh", "secret", "set", "IG_TOKEN", *repo], input=nuevo, env=env, capture_output=True, text=True)
        if r.returncode:
            raise Fallo(f"gh secret set: {r.stderr.strip()}")
        r = subprocess.run(["gh", "variable", "set", "IG_TOKEN_FECHA", "--body", hoy().isoformat(), *repo],
                           env=env, capture_output=True, text=True)
        if r.returncode:
            raise Fallo(f"gh variable set: {r.stderr.strip()}")
    except (Fallo, ValueError, KeyError) as e:
        print(mascara(f"!!! NO SE PUDO RENOVAR EL TOKEN DE INSTAGRAM: {e}. Vence a los 60 días; renovarlo a mano."), flush=True)
        return 1
    aviso("Token de Instagram renovado.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ensayo", help="fuerza un ensayo puntual")
    ap.add_argument("--simulacro", action="store_true", help="llega hasta FINISHED sin publicar ni escribir estado")
    ap.add_argument("--conciliar", action="store_true", help="marca como publicados los que ya están en Instagram")
    ap.add_argument("--listar", action="store_true", help="muestra la cola sin usar la red")
    ap.add_argument("--sin-git", action="store_true", help="escribe el estado sin commit ni push")
    args = ap.parse_args(argv)

    if args.listar:
        return listar()
    if not args.conciliar and os.path.exists(os.path.join(RAIZ, "PAUSA")):
        print("Existe el archivo PAUSA: no se publica nada.")
        return renovar_token()
    if not os.environ.get("IG_TOKEN") or not os.environ.get("IG_USER_ID"):
        print("Faltan IG_TOKEN o IG_USER_ID en el entorno.")
        return 1
    try:
        if args.conciliar:
            rc = conciliar(args)
        else:
            rc = resolver_en_curso(args)
            if rc is None:
                rc = publicar(args)
    except Fallo as e:
        aviso(f"Error: {e}")
        rc = 1
    if not args.simulacro:
        rc = renovar_token() or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
