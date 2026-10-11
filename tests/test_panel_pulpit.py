"""Pulpit /app#pulpit — Start/Stop per telefon z status.sessions (bez ADB)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_HTML = (ROOT / "panel" / "static" / "app.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "panel" / "static" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "panel" / "static" / "app.css").read_text(encoding="utf-8")


def _view_pulpit() -> str:
    """Wycina sekcję #view-pulpit — Hydrate ma być na Flocie, nie tu."""
    m = re.search(
        r'<section class="view" id="view-pulpit".*?</section>',
        APP_HTML,
        re.S,
    )
    if not m:
        raise AssertionError("brak #view-pulpit w app.html")
    return m.group(0)


def _fn(name: str) -> str:
    """Ciało funkcji top-level `function name` albo `async function name`."""
    m = re.search(
        rf"(?:async\s+)?function {name}\s*\([^)]*\)\s*\{{",
        APP_JS,
    )
    if not m:
        raise AssertionError(f"brak function {name} w app.js")
    start = m.end() - 1
    depth = 0
    for i, ch in enumerate(APP_JS[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return APP_JS[start : i + 1]
    raise AssertionError(f"nie domknięto {name}")


class PulpitMarkupTests(unittest.TestCase):
    def test_kpi_i_os_dzis_w_html(self):
        pulpit = _view_pulpit()
        for kid in ("kpi-online", "kpi-running", "kpi-today", "kpi-funnel", "kpi-alerts"):
            self.assertIn(f'id="{kid}"', pulpit, kid)
        # Ikony kafelków z mocka (wifi / play / kalendarz / lejek / alert).
        self.assertIn("kpi-ico online", pulpit)
        self.assertIn("kpi-ico running", pulpit)
        self.assertIn("kpi-ico today", pulpit)
        self.assertIn("kpi-ico funnel", pulpit)
        self.assertIn("kpi-ico alerts", pulpit)
        self.assertIn('id="device-grid"', pulpit)
        self.assertIn('id="pulpit-today"', pulpit)
        self.assertIn('id="pulpit-today-axis"', pulpit)

    def test_pulpit_bez_hydrate_i_bez_hardcode_aliasow(self):
        pulpit = _view_pulpit()
        self.assertNotIn("hydrate", pulpit.lower())
        self.assertNotIn("phone_A", pulpit)
        self.assertNotIn("galaxy", pulpit)

    def test_tokeny_css_zostaja(self):
        self.assertIn("--bg: #0f1215", APP_CSS)
        self.assertIn("--accent: #4ea1ff", APP_CSS)
        self.assertNotIn("backdrop-filter", APP_CSS)


class PulpitSessionContractTests(unittest.TestCase):
    def test_session_on_device_czyta_status_sessions(self):
        body = _fn("sessionOnDevice")
        self.assertIn("device_alias", body)
        # Nie globalne running + account (stary jeden slot).
        self.assertNotIn("status.running", body)
        self.assertNotIn("status.account", body)
        self.assertIn("sessionsList", body)
        self.assertIn("status.sessions", _fn("sessionsList"))

    def test_start_karty_nie_patrzy_na_global_running(self):
        body = _fn("updateCard")
        self.assertIn("sessionOnDevice", body)
        self.assertIn("schedulerBlocksManual", body)
        self.assertNotIn("!!status.running", body)
        self.assertNotIn("const busy", body)
        # Disable tylko TEN slot / offline / scheduler tego device.
        self.assertIn("btn-start", body)
        self.assertIn("online && !sess", body)

    def test_stop_wysyla_device_alias(self):
        body = _fn("stopSession")
        self.assertIn("/api/stop", body)
        self.assertIn("device_alias", body)
        self.assertIn("card.dataset.alias", body)

    def test_preview_tylko_gdy_sesja(self):
        body = _fn("refreshPreview")
        self.assertIn("sessionOnDevice", body)
        self.assertIn("/preview", body)

    def test_live_plan_preselect_bez_implementacji_siatki(self):
        live = _fn("goLive")
        plan = _fn("goPlan")
        self.assertIn("na-zywo", live)
        self.assertIn("harmonogram", plan)
        self.assertTrue(
            "setLiveFocus" in live or "LIVE_FOCUS_KEY" in live or "liveFocusDevice" in live,
            "goLive ma zapisać focus telefonu",
        )
        self.assertTrue(
            "TL_PRESELECT_KEY" in plan or "tlDevice" in plan or "tlFocusAlias" in plan,
            "goPlan ma preselect urządzenia",
        )

    def test_os_dzis_kolor_konta_i_okno(self):
        body = _fn("renderTodayAxis")
        self.assertIn("accColor", body)
        self.assertIn("active_hours", body)
        self.assertIn("axis-now", body)
        self.assertIn("pulpit-today-lab", body)
        self.assertIn("axis-hour", body)
        # Nie malować bloku samym statusem (zielony/czerwony pasek).
        self.assertNotIn("axis-track", body)

    def test_brak_hydrate_w_build_card(self):
        body = _fn("buildCard")
        self.assertNotIn("hydrate", body.lower())
        self.assertIn("btn-start", body)
        self.assertIn("btn-stop", body)
        self.assertIn("btn-live", body)
        self.assertIn("btn-plan", body)
        self.assertIn("card-offline-banner", body)


if __name__ == "__main__":
    unittest.main()
