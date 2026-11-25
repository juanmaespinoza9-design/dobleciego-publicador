import argparse
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
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
        os.environ.pop("IG_TOKEN_FECHA", None)
        self.llamadas = []
        self.issues = []
        self.media = []
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
            return 200, json.dumps({"id": "M1"}).encode()
        if url.endswith("/42/media") and metodo == "POST":
            return 200, json.dumps({"id": "C1"}).encode()
        if "/42/media?" in url:
            return 200, json.dumps({"data": self.media}).encode()
        if "/C1?" in url or "/C9?" in url:
            return 200, json.dumps({"status_code": self.estado_contenedor.pop(0) if len(self.estado_contenedor) > 1 else self.estado_contenedor[0]}).encode()
        if "/M1?" in url:
            return 200, json.dumps({"permalink": "https://instagram.com/reel/M1"}).encode()
        if sha:
            return 200, "abc"
        return 200, b"x"

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

    def test_caption_de_2201_rechazado(self):
        self.agregar(programa("A", "2026-11-29", "x" * 2201))
        rc, salida = self.correr()
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.estado("A")["estado"], "error")
        self.assertIn("2201", salida)
        self.assertEqual([c for c in self.llamadas if c[0] == "POST"], [])

    def test_mas_de_30_hashtags(self):
        self.agregar(programa("A", "2026-11-29", "T\n" + " ".join(f"#h{i}" for i in range(31))))
        rc, _ = self.correr()
        self.assertNotEqual(rc, 0)

    def test_sha_distinto(self):
        p = programa("A", "2026-11-29")
        p["sha256_video"] = "otro"
        self.agregar(p)
        rc, _ = self.correr()
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.publicaciones(), [])

    def test_simulacro_no_escribe_ni_publica(self):
        self.agregar(programa("A", "2026-11-29"))
        rc, _ = self.correr("--simulacro")
        self.assertEqual(rc, 0)
        self.assertEqual(self.publicaciones(), [])
        self.assertIsNone(self.estado("A"))

    def test_token_enmascarado_en_errores(self):
        def mala(metodo, url, datos=None, sha=False):
            raise publicar.Fallo(publicar.mascara(f"fallo en {url}"))
        self.agregar(programa("A", "2026-11-29"))
        with mock.patch.object(publicar, "http", mala):
            rc, salida = self.correr()
        self.assertNotEqual(rc, 0)
        self.assertNotIn(TOKEN, salida)

    def test_mascara(self):
        t = publicar.mascara(f"https://g/x?fields=a&access_token={TOKEN}&y=1 y {TOKEN}")
        self.assertNotIn(TOKEN, t)

    def test_error_http_de_meta_sin_token(self):
        def error(metodo, url, datos=None, sha=False):
            return 400, json.dumps({"error": {"message": "Invalid parameter", "code": 100}}).encode()
        with mock.patch.object(publicar, "http", error):
            with self.assertRaises(publicar.Fallo) as c:
                publicar.api("GET", "42/media", fields="id")
        self.assertIn("Invalid parameter", str(c.exception))
        self.assertNotIn(TOKEN, str(c.exception))


if __name__ == "__main__":
    unittest.main()
