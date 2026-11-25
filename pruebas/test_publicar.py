import re
import argparse
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import publicar  # noqa: E402

AHORA = dt.datetime(2026, 12, 1, 21, 17, tzinfo=dt.timezone.utc)  # 18:17 en Argentina
TOKEN = "TOKEN_SECRETO_123"


def programa(ensayo, fecha, caption=None):
    return {"ensayo": ensayo, "serie": "ESC 2026", "fecha": fecha, "video_url": f"https://x/{ensayo}.mp4",
            "cover_url": f"https://x/{ensayo}.jpg", "caption": caption or f"Titulo {ensayo}\n\n#a #b",
            "sha256_video": "abc", "tiktok": "texto tiktok"}


def meta(codigo, mensaje="fallo", **extra):
    return codigo, json.dumps({"error": {"message": mensaje, "code": 1, **extra}}).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for c in ("programa", "estado"):
            os.makedirs(os.path.join(self.dir, c))
        patches = [mock.patch.object(publicar, "RAIZ", self.dir), mock.patch.object(publicar, "ahora", lambda: AHORA),
                   mock.patch.object(publicar, "dormir", lambda s: None),
                   mock.patch.object(publicar, "abrir_issue", lambda p, e: self.issues.append(p["ensayo"])),
                   mock.patch.dict(os.environ, {"IG_TOKEN": TOKEN, "IG_USER_ID": "42"}, clear=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for v in ("GITHUB_ACTIONS",):
            os.environ.pop(v, None)
        self.llamadas, self.issues, self.media = [], [], []
        self.fallos = {}
        self.al_consultar = None
        self.estado_contenedor = ["FINISHED"]
        self.red = mock.patch.object(publicar, "http", self.falsa_red)
        self.red.start()
        self.addCleanup(self.red.stop)
        self.addCleanup(shutil.rmtree, self.dir)

    def agregar(self, p, estado=None):
        for carpeta, datos in (("programa", p), ("estado", estado)):
            if datos:
                with open(os.path.join(self.dir, carpeta, p["ensayo"] + ".json"), "w") as f:
                    json.dump(datos, f)

    def estado(self, ensayo):
        try:
            with open(os.path.join(self.dir, "estado", ensayo + ".json")) as f:
                return json.load(f)
        except OSError:
            return None

    def falsa_red(self, metodo, url, datos=None, sha=False):
        self.llamadas.append((metodo, url, datos))
        if "/media_publish" in url:
            clave, ok = "publish", (200, json.dumps({"id": "M1"}).encode())
        elif url.endswith("/42/media") and metodo == "POST":
            clave, ok = "crear", (200, json.dumps({"id": "C1"}).encode())
        elif "/42/media?" in url:
            clave, ok = "listar", (200, json.dumps({"data": self.media}).encode())
        elif "/C1?" in url or "/C9?" in url:
            clave = "contenedor"
            if self.al_consultar:
                self.al_consultar()
            e = self.estado_contenedor
            ok = (200, json.dumps({"status_code": e.pop(0) if len(e) > 1 else e[0]}).encode())
        elif "/M1?" in url:
            clave, ok = "permalink", (200, json.dumps({"permalink": "https://instagram.com/reel/M1"}).encode())
        elif "content_publishing_limit" in url:
            clave, ok = "limite", (200, json.dumps({"data": [{"quota_usage": 3, "config": {"quota_total": 100, "quota_duration": 86400}}]}).encode())
        elif "/v27.0/42?" in url:
            clave, ok = "cuenta", (200, json.dumps({"username": "doblecielo", "media_count": 12}).encode())
        elif sha:
            clave, ok = "sha", (200, "abc")
        else:
            clave, ok = "portada", (200, b"x")
        v = self.fallos.get(clave)
        if isinstance(v, Exception):
            raise v
        return v or ok

    def correr(self, *argv):
        with redirect_stdout(io.StringIO()) as s:
            rc = publicar.main(["--sin-git", *argv])
        return rc, s.getvalue()

    def publicaciones(self):
        return [c for c in self.llamadas if "/media_publish" in c[1]]


class Pruebas(Base):
    def test_pausa(self):
        self.agregar(programa("A", "2026-11-30"))
        open(os.path.join(self.dir, "PAUSA"), "w").close()
        rc, salida = self.correr()
        self.assertEqual(rc, 0)
        self.assertIn("PAUSA", salida)
        self.assertEqual(self.llamadas, [])

    def test_pausa_cualquier_mayuscula_y_sufijo(self):
        self.agregar(programa("A", "2026-11-30"))
        open(os.path.join(self.dir, "Pausa.txt"), "w").close()
        self.correr()
        self.assertEqual(self.llamadas, [])

    def test_pausa_aparece_entre_contenedor_y_publicacion(self):
        self.agregar(programa("A", "2026-11-30"))
        self.al_consultar = lambda: open(os.path.join(self.dir, "pausa"), "w").close()
        rc, salida = self.correr()
        self.assertEqual(rc, 0)
        self.assertEqual(self.publicaciones(), [])
        self.assertIsNone(self.estado("A"))
        self.assertIn("PAUSA", salida)

    def test_elige_pendiente_por_fecha(self):
        self.agregar(programa("B", "2026-11-29"))
        self.agregar(programa("A", "2026-11-29"))
        self.agregar(programa("C", "2026-11-20"), {"estado": "publicado"})
        self.agregar(programa("FUTURO", "2026-12-02"))
        rc, _ = self.correr()
        self.assertEqual(rc, 0)
        self.assertEqual(self.estado("A")["estado"], "publicado")
        self.assertIsNone(self.estado("B"))
        self.assertIsNone(self.estado("FUTURO"))
        self.assertEqual(len(self.publicaciones()), 1)
        self.assertEqual(self.issues, ["A"])

    def test_envia_rotulo_ia(self):
        self.agregar(programa("A", "2026-11-29"))
        self.correr()
        crear = next(c for c in self.llamadas if c[0] == "POST" and c[1].endswith("/42/media"))
        self.assertEqual(crear[2]["is_ai_generated"], "true")

    def test_rotulo_rechazado_sugiere_ponerlo_en_none(self):
        self.agregar(programa("A", "2026-11-29"))
        self.fallos["crear"] = meta(400, "Param is_ai_generated is not supported")
        rc, _ = self.correr()
        self.assertEqual(rc, 1)
        self.assertIn("ROTULO_IA = None", self.estado("A")["detalle"])

    def test_hoy_es_hora_argentina(self):
        with mock.patch.object(publicar, "ahora", lambda: dt.datetime(2026, 12, 2, 1, 0, tzinfo=dt.timezone.utc)):
            self.assertEqual(publicar.hoy(), dt.date(2026, 12, 1))

    def test_regla_de_20_horas(self):
        self.agregar(programa("A", "2026-11-29"))
        self.media = [{"id": "1", "caption": "Otro reel", "timestamp": "2026-12-01T10:00:00+0000", "permalink": "p"}]
        rc, salida = self.correr()
        self.assertEqual(rc, 0)
        self.assertIn("20 horas", salida)
        self.assertEqual(self.publicaciones(), [])
        self.assertIsNone(self.estado("A"))

    def test_publica_si_la_ultima_es_vieja(self):
        self.agregar(programa("A", "2026-11-29"))
        self.media = [{"id": "1", "caption": "Otro", "timestamp": "2026-11-29T10:00:00+0000", "permalink": "p"}]
        self.correr()
        self.assertEqual(len(self.publicaciones()), 1)

    def test_duplicado_por_primera_linea(self):
        self.agregar(programa("A", "2026-11-29", "Titulo   A \n\n#x"))
        self.media = [{"id": "9", "caption": "  Titulo A\notra cosa", "timestamp": "2026-11-01T10:00:00+0000",
                       "permalink": "https://ig/9"}]
        rc, _ = self.correr()
        self.assertEqual(rc, 0)
        self.assertEqual(self.estado("A")["estado"], "publicado")
        self.assertEqual(self.estado("A")["enlace"], "https://ig/9")
        self.assertEqual(self.publicaciones(), [])

    def test_en_curso_huerfano_publicado(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "en_curso", "contenedor_id": "C9"})
        self.agregar(programa("B", "2026-11-29"))
        self.estado_contenedor = ["PUBLISHED"]
        self.media = [{"id": "7", "caption": "Titulo A", "timestamp": "2026-11-30T10:00:00+0000", "permalink": "https://ig/7"}]
        rc, _ = self.correr()
        self.assertEqual(rc, 0)
        self.assertEqual(self.estado("A")["estado"], "publicado")
        self.assertEqual(self.estado("A")["media_id"], "7")
        self.assertIsNone(self.estado("B"))
        self.assertEqual(self.publicaciones(), [])

    def test_en_curso_sin_publicar_es_error(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "en_curso", "contenedor_id": "C9"})
        self.estado_contenedor = ["IN_PROGRESS"]
        rc, _ = self.correr()
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.estado("A")["estado"], "error")

    def test_en_curso_contenedor_inconsultable_definitivo(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "en_curso", "contenedor_id": "C9"})
        self.fallos["contenedor"] = meta(400, "contenedor inexistente")
        self.media = [{"id": "7", "caption": "Titulo A", "timestamp": "2026-11-30T10:00:00+0000", "permalink": "https://ig/7"}]
        self.assertEqual(self.correr()[0], 0)
        self.assertEqual(self.estado("A")["estado"], "publicado")

    def test_en_curso_contenedor_inconsultable_y_no_publicado(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "en_curso", "contenedor_id": "C9"})
        self.fallos["contenedor"] = meta(400, "contenedor inexistente")
        self.assertEqual(self.correr()[0], 1)
        self.assertEqual(self.estado("A")["estado"], "error")

    def test_en_curso_contenedor_transitorio_no_cambia_nada(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "en_curso", "contenedor_id": "C9"})
        self.fallos["contenedor"] = meta(503)
        self.assertEqual(self.correr()[0], 1)
        self.assertEqual(self.estado("A")["estado"], "en_curso")

    def test_fallo_en_media_publish_deja_en_curso_y_se_resuelve(self):
        self.agregar(programa("A", "2026-11-29"))
        for fallo in (meta(503), publicar.Transitorio("timeout")):
            self.fallos["publish"] = fallo
            self.assertEqual(self.correr()[0], 1)
            self.assertEqual(self.estado("A")["estado"], "en_curso")
            self.fallos = {}
            self.estado_contenedor = ["PUBLISHED"]
            self.media = [{"id": "7", "caption": "Titulo A", "timestamp": "2026-12-01T21:00:00+0000", "permalink": "https://ig/7"}]
            self.assertEqual(self.correr()[0], 0)
            self.assertEqual(self.estado("A")["estado"], "publicado")
            os.remove(os.path.join(self.dir, "estado", "A.json"))
            self.media, self.estado_contenedor = [], ["FINISHED"]

    def test_contenedor_error_y_expired(self):
        for terminal in ("ERROR", "EXPIRED"):
            self.agregar(programa("A", "2026-11-29"))
            self.estado_contenedor = [terminal]
            self.assertNotEqual(self.correr()[0], 0)
            self.assertEqual(self.estado("A")["estado"], "error")
            self.assertIn(terminal, self.estado("A")["detalle"])
            self.assertEqual(self.publicaciones(), [])
            os.remove(os.path.join(self.dir, "estado", "A.json"))

    def test_sondeo_agotado(self):
        self.agregar(programa("A", "2026-11-29"))
        self.estado_contenedor = ["IN_PROGRESS"]
        self.assertNotEqual(self.correr()[0], 0)
        self.assertEqual(self.estado("A")["estado"], "error")

    def test_fallo_transitorio_al_crear_contenedor_reintenta_despues(self):
        self.agregar(programa("A", "2026-11-29"))
        for fallo in (meta(503), meta(429), publicar.Transitorio("timeout")):
            self.fallos["crear"] = fallo
            self.assertEqual(self.correr()[0], 1)
            self.assertIsNone(self.estado("A"))
        self.fallos = {}
        self.assertEqual(self.correr()[0], 0)
        self.assertEqual(self.estado("A")["estado"], "publicado")

    def test_fallo_transitorio_al_descargar_video(self):
        self.agregar(programa("A", "2026-11-29"))
        self.fallos["sha"] = (503, "")
        self.assertEqual(self.correr()[0], 1)
        self.assertIsNone(self.estado("A"))

    def test_estado_corrupto_bloquea(self):
        self.agregar(programa("A", "2026-11-29"))
        self.agregar(programa("B", "2026-11-29"))
        with open(os.path.join(self.dir, "estado", "A.json"), "w") as f:
            f.write("{no es json")
        rc, salida = self.correr()
        self.assertEqual(rc, 1)
        self.assertIn("BLOQUEADO", salida)
        self.assertEqual(self.llamadas, [])

    def test_estado_sin_campo_reconocido_bloquea(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "raro"})
        self.assertEqual(self.correr()[0], 1)
        self.assertEqual(self.llamadas, [])

    def test_programa_invalido_se_saltea(self):
        sin_fecha = programa("MALO", "2026-11-28")
        del sin_fecha["fecha"]
        self.agregar(sin_fecha)
        self.agregar(programa("A", "2026-11-29"))
        rc, salida = self.correr()
        self.assertEqual(rc, 0)
        self.assertIn("MALO", salida)
        self.assertEqual(self.estado("A")["estado"], "publicado")

    def test_reel_en_error_no_se_reintenta(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "error"})
        rc, _ = self.correr()
        self.assertEqual(rc, 0)
        self.assertEqual(self.llamadas, [])

    def test_ensayo_ya_publicado_no_se_republica(self):
        self.agregar(programa("A", "2026-11-29"), {"estado": "publicado", "enlace": "https://ig/1"})
        rc, salida = self.correr("--ensayo", "A")
        self.assertEqual(rc, 0)
        self.assertIn("no se republica", salida)
        self.assertEqual(self.llamadas, [])

    def test_ensayo_con_caracteres_raros_se_rechaza(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", io.StringIO()):
                publicar.main(["--ensayo", "../x"])

    def test_caption_de_2201_rechazado(self):
        self.agregar(programa("A", "2026-11-29", "x" * 2201))
        rc, salida = self.correr()
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.estado("A")["estado"], "error")
        self.assertIn("2201", salida)
        self.assertEqual([c for c in self.llamadas if c[0] == "POST"], [])

    def test_mas_de_30_hashtags_y_20_menciones(self):
        for caption in ("T\n" + " ".join(f"#h{i}" for i in range(31)), "T\n" + " ".join(f"@u{i}" for i in range(21))):
            self.agregar(programa("A", "2026-11-29", caption))
            self.assertNotEqual(self.correr()[0], 0)
            os.remove(os.path.join(self.dir, "estado", "A.json"))

    def test_sha_distinto(self):
        p = programa("A", "2026-11-29")
        p["sha256_video"] = "otro"
        self.agregar(p)
        rc, _ = self.correr()
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.estado("A")["estado"], "error")
        self.assertEqual(self.publicaciones(), [])

    def test_simulacro_no_escribe_ni_publica(self):
        self.agregar(programa("A", "2026-11-29"))
        rc, _ = self.correr("--simulacro")
        self.assertEqual(rc, 0)
        self.assertEqual(self.publicaciones(), [])
        self.assertIsNone(self.estado("A"))

    def test_excepcion_inesperada_sale_con_1_y_sin_token(self):
        self.agregar(programa("A", "2026-11-29"))
        self.fallos["listar"] = KeyError(f"raro {TOKEN}")
        rc, salida = self.correr()
        self.assertEqual(rc, 1)
        self.assertNotIn(TOKEN, salida)

    def test_mascara(self):
        self.assertNotIn(TOKEN, publicar.mascara(f"https://g/x?fields=a&access_token={TOKEN}&y=1 y {TOKEN}"))

    def test_error_http_de_meta_sin_token(self):
        self.fallos["listar"] = meta(400, "Invalid parameter")
        with self.assertRaises(publicar.Fallo) as c:
            publicar.api("GET", "42/media", fields="id")
        self.assertIn("Invalid parameter", str(c.exception))
        self.assertNotIn(TOKEN, str(c.exception))


class Verificar(Base):
    def test_add_mask_en_actions(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        rc, salida = self.correr("--verificar")
        self.assertEqual(rc, 0)
        self.assertTrue(salida.startswith(f"::add-mask::{TOKEN}"))

    def test_verificar_imprime_y_no_escribe(self):
        self.agregar(programa("A", "2026-11-29"))
        rc, salida = self.correr("--verificar")
        self.assertEqual(rc, 0)
        self.assertIn("Usuario: doblecielo", salida)
        self.assertIn("Publicaciones: 12", salida)
        self.assertIn("3 de 100", salida)
        self.assertTrue(all(c[0] == "GET" and not re.search(r"/me([/?]|$)", c[1]) for c in self.llamadas))
        self.assertTrue(all(c[1].startswith("https://graph.facebook.com/v27.0/42") for c in self.llamadas))
        self.assertIsNone(self.estado("A"))

    def test_verificar_con_pausa_igual_responde(self):
        open(os.path.join(self.dir, "PAUSA"), "w").close()
        self.assertEqual(self.correr("--verificar")[0], 0)

    def test_verificar_falla(self):
        self.fallos["cuenta"] = meta(400, "no existe")
        self.assertEqual(self.correr("--verificar")[0], 1)

    def test_token_invalido_190_en_cualquier_modo(self):
        self.agregar(programa("A", "2026-11-29"))
        self.fallos["listar"] = (400, json.dumps({"error": {"message": "Session has expired", "code": 190}}).encode())
        for modo in ((), ("--conciliar",), ("--simulacro",)):
            rc, salida = self.correr(*modo)
            self.assertEqual(rc, 1)
            self.assertIn("El token de Instagram no es válido", salida)
            self.assertIn("IG_TOKEN", salida)
        self.assertIsNone(self.estado("A"))
        self.fallos = {"cuenta": (400, json.dumps({"error": {"message": "x", "code": 190}}).encode())}
        rc, salida = self.correr("--verificar")
        self.assertEqual(rc, 1)
        self.assertIn("no es válido", salida)


class MascaraEnRedReal(unittest.TestCase):
    def test_url_con_token_no_aparece(self):
        url = "https://graph.instagram.com/x?access_token=SECRETO"
        errores = [urllib.error.URLError(f"fallo conectando a {url}"),
                   urllib.error.HTTPError(url, 500, f"error en {url}", {}, io.BytesIO(
                       b'{"error": {"message": "mal access_token=SECRETO"}}')),
                   urllib.error.HTTPError(url, 400, "x", {}, io.BytesIO(
                       b'{"error": {"message": "mal access_token=SECRETO y SECRETO"}}'))]
        with mock.patch.dict(os.environ, {"IG_TOKEN": "SECRETO", "IG_USER_ID": "42"}):
            for err in errores:
                with mock.patch("urllib.request.urlopen", side_effect=err):
                    with self.assertRaises(publicar.Fallo) as c:
                        publicar.api("GET", "42/media", fields="id")
                self.assertNotIn("SECRETO", str(c.exception))


class GitReal(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.bare, self.a, self.b = (os.path.join(self.dir, n) for n in ("bare.git", "a", "b"))
        self.g("init", "-q", "--bare", "-b", "main", self.bare)
        self.g("clone", "-q", self.bare, self.a)
        self.g("-C", self.a, "checkout", "-q", "-b", "main")
        with open(os.path.join(self.a, "LEAME"), "w") as f:
            f.write("x")
        self.g("-C", self.a, "add", "LEAME")
        self.g("-C", self.a, "commit", "-q", "-m", "inicio")
        self.g("-C", self.a, "push", "-q", "-u", "origin", "main")
        self.g("clone", "-q", self.bare, self.b)
        pr = mock.patch.object(publicar, "RAIZ", self.a)
        pr.start()
        self.addCleanup(pr.stop)
        self.args = argparse.Namespace(simulacro=False, sin_git=False)

    def g(self, *a):
        return subprocess.run(["git", *a], check=True, capture_output=True, text=True).stdout

    def commits(self):
        return self.g("-C", self.bare, "log", "--format=%s", "main").splitlines()

    def test_commit_y_push(self):
        publicar.guardar_estado("A", {"estado": "en_curso", "contenedor_id": "C1"}, self.args)
        self.assertEqual(self.commits()[0], "estado: A en_curso")

    def test_reintenta_con_pull_si_el_remoto_avanzo(self):
        with open(os.path.join(self.b, "otro.txt"), "w") as f:
            f.write("y")
        self.g("-C", self.b, "add", "otro.txt")
        self.g("-C", self.b, "commit", "-q", "-m", "desde el teléfono")
        self.g("-C", self.b, "push", "-q")
        publicar.guardar_estado("A", {"estado": "publicado"}, self.args)
        self.assertEqual(self.commits()[:2], ["estado: A publicado", "desde el teléfono"])

    def test_estado_a_pendiente_borra_y_sube(self):
        publicar.guardar_estado("A", {"estado": "en_curso"}, self.args)
        publicar.guardar_estado("A", None, self.args)
        self.assertEqual(self.commits()[0], "estado: A vuelve a pendiente")
        self.assertFalse(os.path.exists(os.path.join(self.a, "estado", "A.json")))

    def test_pausa_remota(self):
        self.assertFalse(publicar.hay_pausa(self.args))
        open(os.path.join(self.b, "Pausa"), "w").close()
        self.g("-C", self.b, "add", "Pausa")
        self.g("-C", self.b, "commit", "-q", "-m", "pausa")
        self.g("-C", self.b, "push", "-q")
        self.assertTrue(publicar.hay_pausa(self.args))


if __name__ == "__main__":
    unittest.main()
