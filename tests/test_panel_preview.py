"""Testy GET /api/devices/{alias}/preview — JPEG podglądu bez prawdziwego telefonu."""
from __future__ import annotations

import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.panel.preview import (  # noqa: E402
    PREVIEW_MAX_WIDTH,
    PreviewResult,
    _PreviewWriter,
    capture_device_preview,
    preview_interval_s,
    preview_ui_fps,
    read_device_scrcpy_fps,
    reset_preview_cache,
    to_preview_jpeg,
    write_preview_jpeg,
)


def _png_bytes(width: int = 800, height: int = 1200, color=(30, 80, 140)) -> bytes:
    """Syntetyczny PNG — uiautodev / screencap oddają zwykle PNG."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width: int = 240, height: int = 400) -> bytes:
    img = Image.new("RGB", (width, height), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _write_device(config_root: Path, alias: str, serial: str = "SERIAL1") -> None:
    devices = config_root / "devices"
    devices.mkdir(parents=True, exist_ok=True)
    doc = {
        "alias": alias,
        "adb_id": serial,
        "uiautodev": {"base_url": "http://127.0.0.1:20242"},
    }
    (devices / f"{alias}.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8",
    )


class JpegScaleTests(unittest.TestCase):
    def test_png_skaluje_do_max_szerokosci_i_jest_jpeg(self):
        raw = _png_bytes(800, 1600)
        out = to_preview_jpeg(raw)
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith(b"\xff\xd8"))
        img = Image.open(io.BytesIO(out))
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(img.size[0], PREVIEW_MAX_WIDTH)
        self.assertEqual(img.size[1], 960)

    def test_puste_bajty_to_none(self):
        self.assertIsNone(to_preview_jpeg(b""))
        self.assertIsNone(to_preview_jpeg(b"not-an-image"))


class CaptureLogicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.config = Path(self.tmp.name)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        _write_device(self.config, "phone_x", "SERIAL1")
        self.addCleanup(self.tmp.cleanup)

    def test_bez_sesji_204_nawet_gdy_jest_plik(self):
        write_preview_jpeg(self.data, "phone_x", _jpeg_bytes())
        result = capture_device_preview(
            "phone_x", self.config, session_running=False, data_root=self.data,
        )
        self.assertIsNone(result.jpeg)
        self.assertEqual(result.reason, "no_session")

    def test_sesja_czyta_swiezy_preview_jpg(self):
        write_preview_jpeg(self.data, "phone_x", _jpeg_bytes())
        result = capture_device_preview(
            "phone_x", self.config, session_running=True, data_root=self.data,
        )
        self.assertIsNotNone(result.jpeg)
        self.assertTrue(result.jpeg.startswith(b"\xff\xd8"))
        self.assertIsNone(result.reason)

    def test_klatka_po_kilku_sekundach_nadal_swieza(self):
        """Zapis ~0,75 s; próg 8 s — 4 s nie może dać fałszywego 204."""
        from v2.panel.preview import PREVIEW_FILE_MAX_AGE_S
        self.assertGreaterEqual(PREVIEW_FILE_MAX_AGE_S, 5.0)
        dest = write_preview_jpeg(self.data, "phone_x", _jpeg_bytes())
        os.utime(dest, (time.time() - 4.0, time.time() - 4.0))
        result = capture_device_preview(
            "phone_x", self.config, session_running=True, data_root=self.data,
        )
        self.assertIsNotNone(result.jpeg)

    @patch("v2.panel.preview.grab_uiauto_screenshot")
    def test_sesja_bez_pliku_fallback_uiauto(self, mock_uiauto):
        mock_uiauto.return_value = _png_bytes(640, 1280)
        result = capture_device_preview(
            "phone_x", self.config, session_running=True, data_root=self.data,
        )
        self.assertIsNotNone(result.jpeg)
        mock_uiauto.assert_called_once()

    @patch("v2.panel.preview.grab_uiauto_screenshot", return_value=None)
    def test_sesja_bez_klatki_to_no_session(self, _uiauto):
        result = capture_device_preview(
            "phone_x", self.config, session_running=True, data_root=self.data,
        )
        self.assertIsNone(result.jpeg)
        self.assertEqual(result.reason, "no_session")

    def test_brak_sesji_nie_wolamy_uiauto(self):
        result = capture_device_preview("nie_ma", self.config, session_running=False)
        self.assertIsNone(result.jpeg)
        self.assertEqual(result.reason, "no_session")


class PreviewFpsCadenceTests(unittest.TestCase):
    """Wyższe max_fps → częstszy JPEG/poll (klamra UI 1–15)."""

    def test_wyzsze_fps_krotszy_interwal(self):
        self.assertEqual(preview_ui_fps(1), 1)
        self.assertEqual(preview_ui_fps(15), 15)
        self.assertEqual(preview_ui_fps(60), 15)
        self.assertGreater(preview_interval_s(1), preview_interval_s(15))
        self.assertAlmostEqual(preview_interval_s(1), 1.0)
        self.assertAlmostEqual(preview_interval_s(15), 1.0 / 15)
        self.assertAlmostEqual(preview_interval_s(60), preview_interval_s(15))

    def test_writer_czyta_fps_z_json(self):
        """Hot-change: writer bierze interwał z JSON, nie ze stałej 0,75 s."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = Path(tmp.name)
        data = config / "data"
        data.mkdir()
        _write_device(config, "phone_x")
        path = config / "devices" / "phone_x.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["scrcpy"] = {"max_fps": 10}
        path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(read_device_scrcpy_fps(config, "phone_x"), 10)
        writer = _PreviewWriter(None, "phone_x", data, config_root=config)
        self.assertAlmostEqual(writer._interval(), 0.1)


class PreviewEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.config = Path(self.tmp.name)
        _write_device(self.config, "phone_x")
        reset_preview_cache()
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", self.config)
        self.cfg_patch.start()
        self.addCleanup(self.cfg_patch.stop)
        self.addCleanup(reset_preview_cache)
        self.addCleanup(self.tmp.cleanup)
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def test_nieznany_alias_404(self):
        r = self.client.get("/api/devices/nieistnieje/preview")
        self.assertEqual(r.status_code, 404)

    @patch("v2.panel.server.get_device_preview")
    def test_offline_204_bez_500(self, mock_get):
        mock_get.return_value = PreviewResult(jpeg=None, reason="offline")
        r = self.client.get("/api/devices/phone_x/preview")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers.get("X-Preview-Reason"), "offline")
        self.assertFalse(r.content)

    @patch("v2.panel.server.get_device_preview")
    def test_no_session_204(self, mock_get):
        mock_get.return_value = PreviewResult(jpeg=None, reason="no_session")
        r = self.client.get("/api/devices/phone_x/preview")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers.get("X-Preview-Reason"), "no_session")

    @patch("v2.panel.server.get_device_preview")
    def test_happy_path_jpeg(self, mock_get):
        jpeg = _jpeg_bytes()
        mock_get.return_value = PreviewResult(jpeg=jpeg, reason=None)
        r = self.client.get("/api/devices/phone_x/preview")
        self.assertEqual(r.status_code, 200)
        self.assertIn("image/jpeg", r.headers.get("content-type", ""))
        self.assertTrue(r.content.startswith(b"\xff\xd8"))
        self.assertEqual(r.content, jpeg)

    @patch("v2.panel.server.get_device_preview", side_effect=RuntimeError("boom"))
    def test_wyjatek_capture_to_204_nie_500(self, _mock_get):
        r = self.client.get("/api/devices/phone_x/preview")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers.get("X-Preview-Reason"), "offline")

    @patch("v2.panel.server.SESSION.is_running_on", return_value=True)
    @patch("v2.panel.preview.capture_device_preview")
    def test_throttle_drugie_wywolanie_nie_rusza_telefonu(self, mock_cap, _running):
        # 1 fps → okno cache 1 s, dwa fetch w teście muszą trafić w cache.
        path = self.config / "devices" / "phone_x.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["scrcpy"] = {"max_fps": 1}
        path.write_text(json.dumps(doc), encoding="utf-8")
        jpeg = _jpeg_bytes()
        mock_cap.return_value = PreviewResult(jpeg=jpeg, reason=None)
        r1 = self.client.get("/api/devices/phone_x/preview")
        r2 = self.client.get("/api/devices/phone_x/preview")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(mock_cap.call_count, 1)
        self.assertEqual(r2.headers.get("X-Preview-Cached"), "1")

    @patch("v2.panel.server.SESSION.is_running_on", return_value=False)
    @patch("v2.panel.preview.capture_device_preview")
    def test_endpoint_bez_sesji_204_nie_capture(self, mock_cap, _running):
        r = self.client.get("/api/devices/phone_x/preview")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers.get("X-Preview-Reason"), "no_session")
        mock_cap.assert_not_called()


class AppShellTests(unittest.TestCase):
    """Nowy panel (`/app`) i klasyczny `/` — sklejenie fazy 1."""

    def test_pliki_nowego_panelu_istnieja(self):
        static = ROOT / "panel" / "static"
        self.assertTrue((static / "app.html").is_file())
        self.assertTrue((static / "app.css").is_file())
        self.assertTrue((static / "app.js").is_file())
        html = (static / "app.html").read_text(encoding="utf-8")
        self.assertIn('id="device-grid"', html)
        self.assertIn('id="runy-editor"', html)
        self.assertIn('id="runy-lib"', html)
        self.assertIn('id="runy-lib-tab-base"', html)
        self.assertIn('id="runy-lib-tab-saved"', html)
        self.assertIn("Akcje bazowe", html)
        self.assertIn("Zapisane akcje", html)
        self.assertIn("run-lib-tray", html)
        self.assertIn("run-lib-base", html)
        self.assertIn('id="runy-lib-saved"', html)
        self.assertIn('id="runy-steps"', html)
        self.assertIn('id="runy-insp"', html)
        # Bazowe nad trzema kolumnami, tacka zapisanych na dole po inspectorze.
        self.assertLess(html.find("run-lib-base"), html.find('id="runy-insp"'))
        self.assertLess(html.find("run-lib-base"), html.find("run-lib-tray"))
        self.assertLess(html.find('id="runy-insp"'), html.find("run-lib-tray"))
        self.assertIn('id="tl-week"', html)
        self.assertIn('id="st-kpi"', html)
        self.assertIn('id="st-frame"', html)
        self.assertIn('id="live-preview"', html)
        self.assertIn('id="live-progress-body"', html)
        self.assertIn('id="live-log-body"', html)
        self.assertIn('id="live-stop"', html)
        self.assertIn('id="live-fps-input"', html)
        self.assertIn("Bieżąca sesja", html)
        self.assertNotIn("Wkrótce — klatka telefonu", html)
        self.assertIn('href="/static/app.css"', html)
        self.assertIn('src="/static/app.js"', html)
        self.assertIn('src="/static/stats-view.js"', html)
        self.assertIn('src="/static/konta.js"', html)
        self.assertIn('http-equiv="Cache-Control"', html)
        self.assertTrue((static / "konta.js").is_file())
        self.assertTrue((static / "stats-view.js").is_file())
        self.assertTrue((static / "share.html").is_file())
        self.assertTrue((static / "share.js").is_file())
        js = (static / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/accounts/", js)
        self.assertIn("addRunTaskFromAction", js)
        self.assertIn("saveTaskAsAction", js)
        self.assertIn("removeRunTask", js)
        self.assertIn("step-del", js)
        self.assertIn("Zapisz jako akcję", js)
        self.assertIn("PLUGIN_ICONS", js)
        self.assertIn("libraryPlugins", js)
        self.assertIn("bindLibChipDrag", js)

    def test_app_i_root_to_rozne_html(self):
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        client = TestClient(app)
        root = client.get("/")
        new = client.get("/app")
        self.assertEqual(root.status_code, 200)
        self.assertEqual(new.status_code, 200)
        self.assertIn("Klasyczny panel", new.text)
        self.assertIn('id="device-grid"', new.text)
        self.assertIn('id="runy-editor"', new.text)
        self.assertNotIn('id="device-grid"', root.text)
        css = client.get("/static/app.css")
        js = client.get("/static/app.js")
        self.assertEqual(css.status_code, 200)
        self.assertEqual(js.status_code, 200)
        self.assertIn("text/css", css.headers.get("content-type", ""))
        stjs = client.get("/static/stats-view.js")
        self.assertEqual(stjs.status_code, 200)
        kjs = client.get("/static/konta.js")
        self.assertEqual(kjs.status_code, 200)
        self.assertIn("refreshKonta", kjs.text)
        self.assertIn("no-store", (kjs.headers.get("cache-control") or "").lower())
        self.assertIn("no-store", (new.headers.get("cache-control") or "").lower())
        # Komentarz: ?v=mtime — Chrome nie użyje starego /static/konta.js z dysku
        self.assertRegex(new.text, r'src="/static/konta\.js\?v=\d+"')
        self.assertRegex(new.text, r'src="/static/app\.js\?v=\d+"')
        self.assertRegex(new.text, r'href="/static/app\.css\?v=\d+"')
        self.assertIn('id="st-kpi"', new.text)
        self.assertIn('id="st-frame"', new.text)
        self.assertIn('id="live-preview"', new.text)

    def test_with_static_bust_dopina_mtime(self):
        from v2.panel.server import _with_static_bust
        raw = '<script src="/static/konta.js" defer></script>'
        out = _with_static_bust(raw)
        self.assertRegex(out, r'src="/static/konta\.js\?v=\d+"')


class LiveViewTests(unittest.TestCase):
    """Widok Na żywo: split postęp+flota | telefon | log ludzki, bez dumpów JSONL."""

    def test_html_split_i_idle(self):
        html = (ROOT / "panel" / "static" / "app.html").read_text(encoding="utf-8")
        self.assertIn('id="view-na-zywo"', html)
        self.assertIn('class="live-split"', html)
        self.assertIn('id="live-preview"', html)
        self.assertIn('id="live-progress-body"', html)
        self.assertIn('id="live-log-body"', html)
        self.assertIn('id="live-stop"', html)
        self.assertIn('id="live-idle"', html)
        self.assertIn("brak sesji", html)
        self.assertIn('id="live-autoscroll"', html)
        self.assertIn('id="live-log-filter"', html)
        self.assertIn('id="live-terminal"', html)
        self.assertIn('id="live-fleet"', html)
        self.assertIn("live-log-fill", html)
        self.assertIn("Bieżąca sesja", html)
        self.assertIn('id="live-fps-input"', html)
        self.assertIn('id="live-run-sub"', html)
        self.assertIn("podgląd panelu: od razu", html)
        self.assertNotIn("Wkrótce — klatka telefonu", html)
        # Kolejność: sesja | telefon | log; flota pod sesją (osobny rząd siatki).
        prog = html.find('class="live-col live-progress"')
        phone = html.find('class="live-col live-phone"')
        log = html.find('class="live-col live-log"')
        fleet = html.find('id="live-fleet"')
        self.assertLess(prog, phone)
        self.assertLess(phone, log)
        self.assertLess(log, fleet)
        self.assertNotIn('class="live-left"', html)
        # Kanoniczny hash nava zostaje #na-zywo (alias #nazywo w JS)
        self.assertIn('href="#na-zywo"', html)

    def test_js_preview_sse_stop_human_log(self):
        js = (ROOT / "panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function livePreviewIntervalMs", js)
        self.assertIn("function previewUiFps", js)
        self.assertIn("function setLiveScrcpyFps", js)
        self.assertIn("Bieżąca sesja", js)
        # wyższe fps → krótszy poll (nie stała 1 s)
        self.assertIn("livePreviewIntervalMs(15) < livePreviewIntervalMs(1)", js)
        self.assertIn("nazywo: \"na-zywo\"", js)
        self.assertIn("/api/events/stream", js)
        self.assertIn("/api/stats/", js)
        self.assertIn('fetch("/api/stop"', js)
        self.assertIn("function humanLiveLine", js)
        self.assertIn("function ingestLiveProgress", js)
        self.assertIn("function sessionProgressBits", js)
        self.assertIn("Kafelek", js)
        self.assertIn("Fraza", js)
        # Log: stdout ludzki, nie surowy dump kroków
        self.assertIn('p.src === "stdout"', js)
        self.assertIn("session.progress aktualizuje panel postępu", js)
        self.assertIn('kind === "session.start"', js)
        self.assertIn("currentView() !== \"na-zywo\"", js)
        # Krok 4: focus + SSE ?device= + Stop tylko tego serialu
        self.assertIn("function refreshLive", js)
        self.assertIn("function connectLiveStream", js)
        self.assertIn("?device=", js)
        self.assertIn("device_alias: alias", js)
        self.assertIn("LIVE_FOCUS_KEY", js)
        # Throw w jednym panelu nie gasi całego #na-zywo (Stop/log/SSE).
        self.assertIn("try { refreshLive(); }", js)
        self.assertIn("try { paintLiveChrome(); }", js)
        self.assertIn("try { paintLiveLog(); }", js)
        self.assertIn("Array.isArray(rawQueue)", js)
        self.assertIn("typeof EventSource", js)
        # Bind Runy/Lejek nie może uciąć applyView — inaczej section zostaje hidden.
        self.assertIn("if (runyAcc)", js)
        self.assertIn("livePreviewOnScreen = true", js)
        self.assertIn("widok zostaje widoczny", js)

    def test_js_live_progress_preferuje_current_total(self):
        """Bieżący krok: live.progress / liveBits, nie items_done===0 z set_task."""
        js = (ROOT / "panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("current: null, total: null", js)
        self.assertIn("liveBits.current", js)
        self.assertIn("live.progress", js)
        self.assertIn("items_done === 0", js)
        self.assertIn("liveStats.live.progress", js)
        self.assertIn("formatXy(liveBits)", js)
        # Restyle listy: X/Y aktywnego kroku z current/total, nie draft kolejki
        self.assertIn("const fromLive = formatXy(liveBits) || formatXy(live.progress);", js)
        self.assertIn("live-jedzie", js)
        self.assertIn("live-step-xy", js)
        self.assertIn("live-step-bar", js)
        # SSE: payload EventLogger albo płaskie pola + natychmiastowy paint
        self.assertIn("d.payload", js)
        self.assertIn("sessionProgressBits(parsed)", js)
        self.assertIn("nie czekaj na poll", js)
        self.assertIn("paintLiveProgress();", js)

    def test_css_split_layout(self):
        css = (ROOT / "panel" / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn(".live-split", css)
        self.assertIn(".live-preview", css)
        self.assertIn(".live-log-body", css)
        self.assertIn("#view-na-zywo:not([hidden])", css)
        self.assertIn(".live-jedzie", css)
        self.assertIn(".live-step-dot", css)
        self.assertIn(".live-fps", css)
        # Log = telefon: fill absolute, body flex+scroll, nie height:auto treści.
        self.assertIn(".live-log-fill", css)
        self.assertIn("flex: 1 1 auto", css)
        self.assertIn("inset: 0", css)
        # Trzy okna: height:0 żeby lista kroków nie puchła rzędu.
        self.assertIn("height: 0", css)

    def test_app_endpoint_oddaje_widok(self):
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        client = TestClient(app)
        r = client.get("/app")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="live-preview"', r.text)
        self.assertIn('id="live-stop"', r.text)
        self.assertIn("brak sesji", r.text)
        js = client.get("/static/app.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("humanLiveLine", js.text)

    def test_sse_device_nie_jest_404(self):
        """Stary panel bez ?device= oddawał 404 — cały log/SSE padał."""
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        client = TestClient(app)
        # bez slotu: 400 (brak konta), nie 404 trasy
        r = client.get("/api/events/stream?device=phone_x")
        self.assertNotEqual(r.status_code, 404)
        self.assertIn(r.status_code, (200, 400))


class ShareStubTests(unittest.TestCase):
    """Readonly właściciel: /share/schedule + /share/stats, bez auth i sterowania."""

    # Ciągi, których nie wolno pokazać właścicielowi (plan: zero Start/Stop / haseł / ADB / JSON).
    FORBIDDEN = (
        "btn-start",
        "btn-stop",
        "Ułóż tydzień",
        "ig_password",
        "runy-save",
        "runy-del",
        "/api/run-queue",
        "/api/start",
        "/api/stop",
        "/api/adb-devices",
        "credentials",
        "regenerate",
    )

    def setUp(self):
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def test_share_sciezki_oddaja_readonly_html(self):
        for path in ("/share", "/share/schedule", "/share/stats"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("text/html", r.headers.get("content-type", ""))
            self.assertIn('class="readonly"', r.text)
            self.assertIn("tylko odczyt", r.text)
            self.assertIn("/share/schedule", r.text)
            self.assertIn("/share/stats", r.text)
            self.assertIn('id="sh-week"', r.text)
            self.assertIn('id="sh-kpi"', r.text)
            self.assertIn("/static/app.css", r.text)
            self.assertIn("/static/share.js", r.text)
            self.assertNotIn("Klasyczny panel", r.text)
            for needle in self.FORBIDDEN:
                self.assertNotIn(needle, r.text, f"{path} zawiera {needle!r}")

    def test_share_js_tylko_get(self):
        js = self.client.get("/static/share.js")
        self.assertEqual(js.status_code, 200)
        body = js.text
        self.assertNotIn("method: \"POST\"", body)
        self.assertNotIn("method: 'POST'", body)
        for needle in self.FORBIDDEN:
            self.assertNotIn(needle, body, needle)

    def test_app_view_owner_redirectuje_do_share(self):
        r = self.client.get("/app", params={"view": "owner"}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 303, 307))
        loc = r.headers.get("location") or ""
        self.assertTrue(loc.endswith("/share/schedule"), loc)

    def test_operator_app_nadal_ma_start_w_js(self):
        # Operator zostaje pełny — Start jest w app.js, nie w share.
        js = self.client.get("/static/app.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("btn-start", js.text)


if __name__ == "__main__":
    unittest.main()
