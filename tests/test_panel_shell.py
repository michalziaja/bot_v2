"""Smoke shellu /app: hashe speca, rail, redirect #konto/#system."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_HTML = (ROOT / "panel" / "static" / "app.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "panel" / "static" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "panel" / "static" / "app.css").read_text(encoding="utf-8")
STATS_JS = (ROOT / "panel" / "static" / "stats-view.js").read_text(encoding="utf-8")

# Kanoniczne hashe z planu kroku 2.
CANONICAL = [
    "pulpit", "harmonogram", "runy", "tresci", "lejek", "na-zywo",
    "statystyki", "konta", "flota", "ustawienia",
]


def _js_string_list(name: str) -> list[str]:
    """Wyciąga tablicę stringów `const NAME = [...]` z app.js."""
    m = re.search(rf"const {name}\s*=\s*\[(.*?)\]", APP_JS, re.S)
    if not m:
        raise AssertionError(f"brak const {name} w app.js")
    return re.findall(r'"([^"]+)"', m.group(1))


def _js_string_map(name: str) -> dict[str, str]:
    """Wyciąga obiekt string→string `const NAME = { k: "v", ... }`."""
    m = re.search(rf"const {name}\s*=\s*\{{(.*?)\}}", APP_JS, re.S)
    if not m:
        raise AssertionError(f"brak const {name} w app.js")
    return dict(re.findall(r"(\w+)\s*:\s*\"([^\"]+)\"", m.group(1)))


class PanelShellHashTests(unittest.TestCase):
    def test_views_to_kanoniczne_hashe_speca(self):
        self.assertEqual(_js_string_list("VIEWS"), CANONICAL)

    def test_aliasy_na_zywo_zostaja_bez_redirectu(self):
        aliases = _js_string_map("VIEW_ALIASES")
        self.assertEqual(aliases["nazywo"], "na-zywo")
        self.assertEqual(aliases["sesja"], "na-zywo")
        redirects = _js_string_map("VIEW_REDIRECTS")
        self.assertNotIn("nazywo", redirects)
        self.assertNotIn("sesja", redirects)

    def test_alias_tresci_z_ogonkiem(self):
        """Wklejony #treści ląduje na ASCII #tresci — bez osobnego redirectu URL."""
        self.assertIn('"treści": "tresci"', APP_JS)
        self.assertIn("decodeURIComponent", APP_JS)
        self.assertIn("normalize(\"NFC\")", APP_JS)
        self.assertIn('id="view-tresci"', APP_HTML)
        self.assertIn('href="#tresci"', APP_HTML)
        self.assertIn("publish_media", APP_JS)
        self.assertIn("Ikony 8 pluginów", APP_JS)
        self.assertIn("function refreshTresci", APP_JS)
        self.assertIn('id="tr-drop"', APP_HTML)
        self.assertIn('id="tr-insp"', APP_HTML)
        # Happy path: wrzutka na PC; ręczny Push nie jest w UX.
        self.assertIn("ręczny Push nie jest potrzebny", APP_HTML)
        self.assertIn("plugin wypchnie przy sesji", APP_JS)
        self.assertNotIn("id=\"tr-push\"", APP_JS)
        self.assertNotIn("Push na telefon", APP_JS)
        self.assertNotIn("Push ręczny (debug)", APP_JS)
        self.assertIn("function renderPmPicksBlock", APP_JS)
        self.assertIn("/api/publish?account=", APP_JS)

    def test_tresci_applyview_zawsze_zdejmuje_hidden(self):
        """Throw w paincie Treści nie może zostawić #view-tresci jako hidden (jak Na żywo)."""
        self.assertIn('try { refreshTresci().catch(() => {}); }', APP_JS)
        self.assertIn("try { trPaintAccounts(); }", APP_JS)
        self.assertIn("try { trPaintGrid(); }", APP_JS)
        self.assertIn("try { trPaintInsp(); }", APP_JS)
        self.assertIn("try { trSyncDrop(); }", APP_JS)
        self.assertIn("bindTresciUi();", APP_JS)
        self.assertIn("nie blokuje applyView", APP_JS)
        self.assertIn("if (!sel) return;", APP_JS)
        self.assertIn("#view-tresci:not([hidden])", APP_CSS)
        self.assertIn(".view[hidden]", APP_CSS)
        # Sekcja jest rodzeństwem innych widoków, nie dzieckiem #view-runy.
        runy = re.search(
            r'<section class="view" id="view-runy".*?</section>',
            APP_HTML, re.S,
        )
        self.assertIsNotNone(runy)
        self.assertNotIn('id="view-tresci"', runy.group(0))
        self.assertIn('data-view="tresci"', APP_HTML)
        self.assertIn('"tresci"', APP_JS)

    def test_redirect_starych_hashy_konto_system(self):
        aliases = _js_string_map("VIEW_ALIASES")
        redirects = _js_string_map("VIEW_REDIRECTS")
        self.assertEqual(aliases["konto"], "konta")
        self.assertEqual(aliases["system"], "flota")
        self.assertEqual(redirects["konto"], "konta")
        self.assertEqual(redirects["system"], "flota")
        self.assertIn("history.replaceState", APP_JS)

    def test_stare_widoki_konto_system_nie_zyja(self):
        self.assertNotIn('data-view="konto"', APP_HTML)
        self.assertNotIn('data-view="system"', APP_HTML)
        self.assertNotIn('id="view-konto"', APP_HTML)
        self.assertNotIn('id="view-system"', APP_HTML)


class PanelShellMarkupTests(unittest.TestCase):
    def test_rail_i_canvas_w_html(self):
        self.assertIn('class="app-shell"', APP_HTML)
        self.assertIn('class="rail"', APP_HTML)
        self.assertIn("bot v2", APP_HTML)
        self.assertIn("flota IG", APP_HTML)
        self.assertIn('id="canvas-title"', APP_HTML)
        self.assertIn('id="alert-bell"', APP_HTML)
        self.assertIn('id="canvas-clock"', APP_HTML)

    def test_kazdy_hash_ma_sekcje_i_link(self):
        for view in CANONICAL:
            self.assertIn(f'id="view-{view}"', APP_HTML, view)
            self.assertIn(f'href="#{view}"', APP_HTML, view)
            self.assertIn(f'data-view="{view}"', APP_HTML, view)

    def test_lejek_ma_pola_proxy_i_timeout(self):
        """Lejek: URL-e proxy + dłuższy timeout/backoff."""
        self.assertIn('id="lj-proxy-urls"', APP_HTML)
        self.assertIn('id="lj-proxy-save"', APP_HTML)
        self.assertIn('id="lj-timeout"', APP_HTML)
        self.assertIn('id="lj-backoff-base"', APP_HTML)
        self.assertIn("/api/filter-proxies", APP_JS)

    def test_klasyczny_panel_schowany_poza_glowna_lista(self):
        self.assertIn('href="/" class="nav-classic"', APP_HTML)
        # Nie w #main-nav — tylko w stopce raila.
        nav = re.search(
            r'<nav class="nav" id="main-nav".*?</nav>', APP_HTML, re.S,
        )
        self.assertIsNotNone(nav)
        self.assertNotIn("Klasyczny panel", nav.group(0))
        self.assertIn("Klasyczny panel", APP_HTML)
        foot = re.search(r'<div class="rail-foot">.*?</div>', APP_HTML, re.S)
        self.assertIsNotNone(foot)
        self.assertIn("Klasyczny panel", foot.group(0))
        self.assertIn("#ustawienia", foot.group(0))

    def test_flota_to_karty_nie_placeholder(self):
        """Krok 9: #flota ma siatkę / rejestr / ADB raw, nie „wkrótce"."""
        self.assertIn('id="flota-grid"', APP_HTML)
        self.assertIn('id="flota-reg-submit"', APP_HTML)
        self.assertIn('id="flota-adb-raw"', APP_HTML)
        self.assertNotIn("Wkrótce — rejestr urządzeń", APP_HTML)
        self.assertIn("refreshFlota", APP_JS)
        self.assertIn("/api/adb-devices", APP_JS)
        # Diagnostyka ekranu zostaje w klasyku.
        self.assertNotIn("/api/where", APP_JS)
        self.assertNotIn("Gdzie jestem", APP_HTML)
        # Klonowanie tylko na Kontach — Flota to urządzenia.
        self.assertNotIn('id="flota-clone-panel"', APP_HTML)
        self.assertNotIn("btn-clone", APP_JS)
        self.assertNotIn("Utwórz klon", APP_HTML)
        # Kalibracja nowego telefonu: dump → verify → screen + agent
        self.assertIn('id="flota-calib-panel"', APP_HTML)
        self.assertIn("btn-calib", APP_JS)
        self.assertIn("/calibrate/reply", APP_JS)
        self.assertIn("function startFlotaCalib", APP_JS)
        self.assertIn("function refreshFlotaCalib", APP_JS)

    def test_konta_to_siatka_nie_placeholder(self):
        """Krok 8: #konta ma karty + inspector, nie „wkrótce"."""
        self.assertIn('id="konta-grid"', APP_HTML)
        self.assertIn('id="konta-insp"', APP_HTML)
        self.assertIn('id="konta-new-dlg"', APP_HTML)
        self.assertIn('id="kn-spoof"', APP_HTML)
        self.assertIn(">Utwórz</button>", APP_HTML)
        self.assertIn("/static/konta.js", APP_HTML)
        self.assertNotIn("Wkrótce — karty kont", APP_HTML)
        konta_js = (ROOT / "panel" / "static" / "konta.js").read_text(encoding="utf-8")
        self.assertIn("refreshKonta", konta_js)
        self.assertIn("/api/bot-config", konta_js)
        self.assertIn("verification-code", konta_js)
        # Źródło klona: APK z dysku; stock na telefonie tylko jako info
        self.assertIn('q.set("device_alias"', konta_js)
        self.assertIn("Wersja APK z dysku", APP_HTML)
        self.assertIn("knIdentityPayload", konta_js)
        self.assertNotIn("knEnsureSourceFromDevice", konta_js)
        self.assertNotIn("aptoide:", konta_js)
        self.assertNotIn('loadKnApks({ remote: true })', konta_js)
        # Override: odznaczone „własne” kasuje klucz z JSON konta.
        self.assertIn("delete lim[f.key]", konta_js)
        self.assertNotIn("jobsaver", konta_js)
        self.assertNotIn("galaxy", konta_js)

    def test_konta_new_dlg_odswiez_i_model(self):
        """Dialog Utwórz konto: Odśwież + powierzchnia modelu, bez joba klona."""
        self.assertIn('id="kn-refresh"', APP_HTML)
        self.assertIn('id="kn-device-model"', APP_HTML)
        self.assertIn('id="kn-stock-ver"', APP_HTML)
        self.assertIn('id="kn-clone-meta"', APP_HTML)
        self.assertRegex(APP_HTML, r'id="kn-refresh"[^>]*>Odśwież</button>')
        konta_js = (ROOT / "panel" / "static" / "konta.js").read_text(encoding="utf-8")
        self.assertIn("async function refreshNewAccountDialog", konta_js)
        self.assertIn("loadKnApks()", konta_js)
        self.assertNotIn('loadKnApks({ remote: true })', konta_js)
        self.assertNotIn('id="kn-apk-remote"', APP_HTML)
        self.assertNotIn("Lista Aptoide", APP_HTML)
        self.assertIn('id="kn-apk-device"', APP_HTML)
        self.assertIn("/api/clones/preview", konta_js)
        self.assertIn('q.set("device_alias"', konta_js)
        self.assertIn("paintKnStock", konta_js)
        m = re.search(
            r"async function refreshNewAccountDialog\(\) \{",
            konta_js,
        )
        self.assertIsNotNone(m)
        nxt = konta_js.find("\nfunction ", m.start() + 10)
        body = konta_js[m.start():nxt if nxt > 0 else m.start() + 3500]
        self.assertNotIn("startCloneJob", body)
        self.assertNotIn('$("kn-alias").value = ""', body)
        self.assertNotIn('$("kn-ig").value = ""', body)
        self.assertIn("paintKnDeviceModel", APP_JS)
        self.assertIn("fmtModel", APP_JS)
        self.assertIn(".kn-meta-row", APP_CSS)
        self.assertIn(".kn-spoof-grid", APP_CSS)

    def test_konta_preview_on_open_change_refresh(self):
        """Utwórz / zmiana urządzenia / Odśwież wołają POST /api/clones/preview."""
        konta_js = (ROOT / "panel" / "static" / "konta.js").read_text(encoding="utf-8")
        self.assertIn("/api/clones/preview", konta_js)
        self.assertIn('id="kn-spoof-mac"', APP_HTML)
        self.assertIn('id="kn-spoof-imei"', APP_HTML)
        self.assertIn('id="kn-spoof-aid"', APP_HTML)
        self.assertIn('id="kn-spoof-fp"', APP_HTML)
        self.assertIn('id="kn-spoof-model"', APP_HTML)
        self.assertIn('id="kn-spoof-gaid"', APP_HTML)
        self.assertIn('id="kn-spoof-drm"', APP_HTML)
        self.assertIn('id="kn-avatar"', APP_HTML)
        self.assertIn('id="kn-bio"', APP_HTML)
        self.assertIn('id="kn-spoof"', APP_HTML)
        self.assertIn("<details", APP_HTML[APP_HTML.find('id="kn-spoof"') - 80:])
        self.assertIn('data-spoof-use="gaid"', APP_HTML)
        self.assertIn('data-spoof-use="mac"', APP_HTML)
        self.assertIn("knStartLogin", konta_js)
        self.assertIn("knHasSavedCredentials", konta_js)
        self.assertIn("paintKnAvatarHint", konta_js)
        self.assertIn("knSaveBio", konta_js)
        self.assertIn("saveKontaProfile", konta_js)
        self.assertIn("profile/apply", konta_js)
        self.assertIn("loguję", konta_js)

        def fn_body(name: str) -> str:
            m = re.search(rf"(?:async )?function {name}\(", konta_js)
            self.assertIsNotNone(m, name)
            nxt = konta_js.find("\nfunction ", m.start() + 10)
            nxt_async = konta_js.find("\nasync function ", m.start() + 10)
            cuts = [i for i in (nxt, nxt_async) if i > 0]
            end = min(cuts) if cuts else m.start() + 4000
            return konta_js[m.start():end]

        open_body = fn_body("openNewAccount")
        fill_body = fn_body("fillNewAccountDevices")
        load_body = fn_body("loadKnPreview")
        refresh_body = fn_body("refreshNewAccountDialog")
        self.assertIn("fillNewAccountDevices", open_body)
        self.assertIn("await fillNewAccountDevices", open_body)
        # Kasowanie spoofa musi być PRZED preview, nie po starcie fetcha.
        paint_at = open_body.find("paintKnSpoof(null)")
        fill_at = open_body.find("fillNewAccountDevices")
        self.assertGreaterEqual(paint_at, 0)
        self.assertGreater(fill_at, paint_at)
        self.assertIn("loadKnPreview", fill_body)
        self.assertIn("/api/clones/preview", load_body)
        self.assertIn('showBanner("kn-msg"', load_body)
        self.assertIn("paintKnSpoof", load_body)
        self.assertIn("loadKnPreview", refresh_body)
        self.assertIn('id="kn-refresh"', APP_HTML)
        bind_body = fn_body("bindKontaOnce")
        self.assertIn("loadKnPreview", bind_body)
        self.assertIn("refreshNewAccountDialog", bind_body)
        # fillSelect: puste value przy tej samej liście i tak wybiera alias.
        self.assertIn("if (!sel.value && values.length)", APP_JS)

    def test_ustawienia_to_edytor_nie_placeholder(self):
        """Krok 11: #ustawienia ma sekrety / scheduler / bot.json, nie „wkrótce" na całość."""
        self.assertIn('id="ust-api-key"', APP_HTML)
        self.assertIn('id="ust-sched-enabled"', APP_HTML)
        self.assertIn('id="ust-bot-save"', APP_HTML)
        self.assertIn("w schemacie, nie w runtime", APP_HTML)
        # Auth to wiersz, nie fake lock.
        self.assertIn("Auth panelu", APP_HTML)
        self.assertIn(">wkrótce<", APP_HTML)
        self.assertNotIn("Wkrótce — edytor config/bot.json", APP_HTML)
        self.assertIn("/api/bot-config", APP_JS)

    def test_css_rail_200px_tokeny_ops(self):
        self.assertRegex(APP_CSS, r"\.rail\s*\{[^}]*width:\s*200px")
        self.assertIn("--bg: #0f1215", APP_CSS)
        self.assertIn("--accent: #4ea1ff", APP_CSS)
        # Ciemny ops, nie szkło.
        self.assertNotIn("backdrop-filter", APP_CSS)
        self.assertNotIn("glass", APP_CSS.lower())
        # Runy: bazowe nad trzema kolumnami, tacka zapisanych na całą szerokość dołu.
        self.assertIn('"base base base"', APP_CSS)
        self.assertIn('"list steps insp"', APP_CSS)
        self.assertIn('"lib  lib   lib"', APP_CSS)

    def test_runy_chance_to_suwak_procent(self):
        """Szansa lajka: suwak 0–100%, nie surowy 0.15 i nie goła „formuła”."""
        self.assertIn("function renderChanceField", APP_JS)
        self.assertIn("allow_formula === true", APP_JS)
        self.assertIn("stała %", APP_JS)
        self.assertIn("z wyniku AI", APP_JS)
        self.assertIn("ch-pct", APP_JS)
        self.assertNotIn('["p", "stała"], ["formula", "formuła"]', APP_JS)
        self.assertIn(".chance-field", APP_CSS)


class PanelKontaModelSafeguardsPrefsTests(unittest.TestCase):
    """Dialog Utwórz: lista modeli, zabezpieczenia startu, prefs w localStorage."""

    def setUp(self):
        self.konta = (ROOT / "panel" / "static" / "konta.js").read_text(encoding="utf-8")

    def _fn(self, name: str) -> str:
        m = re.search(rf"(?:async )?function {name}\(", self.konta)
        self.assertIsNotNone(m, name)
        nxt = self.konta.find("\nfunction ", m.start() + 10)
        nxt_async = self.konta.find("\nasync function ", m.start() + 10)
        cuts = [i for i in (nxt, nxt_async) if i > 0]
        end = min(cuts) if cuts else m.start() + 8000
        return self.konta[m.start():end]

    def test_model_pick_nad_inputem_modelu(self):
        self.assertIn('id="kn-spoof-model-pick"', APP_HTML)
        pick_at = APP_HTML.find('id="kn-spoof-model-pick"')
        model_at = APP_HTML.find('id="kn-spoof-model"')
        self.assertGreater(pick_at, 0)
        self.assertGreater(model_at, pick_at)
        self.assertIn("jak ten telefon", self.konta)
        self.assertIn("spoof_profiles", self.konta)
        self.assertIn("knApplyModelPick", self.konta)
        self.assertIn("regenFp", self.konta)
        # Zmiana selectu nie rusza unikalnych identyfikatorów.
        apply = self._fn("knApplyModelPick")
        self.assertIn("kn-spoof-model", apply)
        self.assertIn("kn-spoof-fp", apply)
        self.assertNotIn("kn-spoof-aid", apply)
        self.assertNotIn("kn-spoof-mac", apply)
        self.assertNotIn("kn-spoof-imei", apply)
        self.assertNotIn("kn-spoof-gaid", apply)

    def test_safeguards_sekcja_i_checkboxy(self):
        self.assertIn('id="kn-safeguards"', APP_HTML)
        self.assertIn("Zabezpieczenia startu", APP_HTML)
        self.assertIn("com.instagram.androi*", APP_HTML)
        self.assertIn("tylko 1 Instagram", APP_HTML)
        self.assertIn('data-safeguard="kill_before_verify"', APP_HTML)
        self.assertIn('data-safeguard="no_parallel_ig"', APP_HTML)
        self.assertIn('data-safeguard="one_clone_job"', APP_HTML)
        self.assertIn('data-safeguard="stop_stock"', APP_HTML)
        self.assertIn('data-safeguard="sole_ig_pid"', APP_HTML)
        self.assertRegex(
            APP_HTML,
            r'data-safeguard="kill_before_verify"[^>]*checked',
        )
        self.assertRegex(
            APP_HTML,
            r'data-safeguard="no_parallel_ig"[^>]*checked',
        )
        self.assertRegex(
            APP_HTML,
            r'data-safeguard="one_clone_job"[^>]*checked[^>]*disabled'
            r'|data-safeguard="one_clone_job"[^>]*disabled[^>]*checked',
        )
        self.assertRegex(
            APP_HTML,
            r'data-safeguard="stop_stock"[^>]*checked[^>]*disabled'
            r'|data-safeguard="stop_stock"[^>]*disabled[^>]*checked',
        )
        self.assertRegex(
            APP_HTML,
            r'data-safeguard="sole_ig_pid"[^>]*checked[^>]*disabled'
            r'|data-safeguard="sole_ig_pid"[^>]*disabled[^>]*checked',
        )
        payload = self._fn("knIdentityPayload")
        self.assertIn("safeguards", payload)
        self.assertIn("knSafeguards", payload)
        self.assertIn(".kn-safeguards", APP_CSS)
        self.assertIn(".kn-sg-list", APP_CSS)

    def test_persist_prefs_bez_resetu_przy_odswiez(self):
        self.assertIn("v2.kn.prefs", self.konta)
        self.assertIn("lastModel", self.konta)
        self.assertIn("knLoadPrefs", self.konta)
        self.assertIn("knSavePrefs", self.konta)
        self.assertIn("knPersistUse", self.konta)
        self.assertIn("knPersistSafeguards", self.konta)
        paint = self._fn("paintKnSpoof")
        self.assertIn("knLoadPrefs", paint)
        self.assertIn("prefs.use", paint)
        self.assertIn("prefs.safeguards", paint)
        self.assertIn("prefs.lastModel", paint)
        self.assertNotIn("resetKnSpoofUse(id.use)", paint)
        self.assertNotIn("id.use", paint)
        refresh = self._fn("refreshNewAccountDialog")
        self.assertNotIn("resetKnSpoofUse", refresh)
        load = self._fn("loadKnPreview")
        self.assertNotIn("resetKnSpoofUse", load)
        bind = self._fn("bindKontaOnce")
        self.assertIn("knPersistUse", bind)
        self.assertIn("knPersistSafeguards", bind)
        self.assertIn("knOnModelPickChange", bind)

    def test_preview_przekazuje_wybrany_model(self):
        load = self._fn("loadKnPreview")
        self.assertIn("spoof_profiles", load)
        self.assertIn("knChosenPreviewModel", load)
        self.assertIn("body.model", load)
        self.assertIn("fillKnModelPick", self.konta)
        self.assertIn("KN_MODEL_DEVICE", self.konta)

    def test_zakladki_login_i_signup(self):
        # Komentarz: dialog Utwórz — Zaloguj vs Załóż nowe konto
        self.assertIn('id="kn-tab-login"', APP_HTML)
        self.assertIn('id="kn-tab-signup"', APP_HTML)
        self.assertIn("Załóż nowe konto", APP_HTML)
        self.assertIn('id="kn-pane-signup"', APP_HTML)
        self.assertIn('id="kn-su-name"', APP_HTML)
        self.assertIn('id="kn-su-user"', APP_HTML)
        self.assertIn('id="kn-su-pass"', APP_HTML)
        self.assertIn('id="kn-su-bday"', APP_HTML)
        self.assertIn('id="kn-su-contact"', APP_HTML)
        self.assertIn('id="kn-ok-signup"', APP_HTML)
        self.assertIn("setKnAuthTab", self.konta)
        self.assertIn("knStartSignup", self.konta)
        self.assertIn("knSignupPayload", self.konta)
        self.assertIn("/api/signup", self.konta)
        self.assertIn("lastAuthTab", self.konta)
        self.assertIn(".kn-auth-tabs", APP_CSS)
        submit = self._fn("submitNewAccount")
        self.assertIn("doSignup", submit)
        self.assertIn("knStartSignup", submit)

    def test_insp_gotowego_klona_ma_zaloz_konto(self):
        # Komentarz: ustawienia istniejącego klona — nie tylko Login
        self.assertIn('id="ki-tab-signup"', self.konta)
        self.assertIn('id="ki-pane-signup"', self.konta)
        self.assertIn('id="ki-signup"', self.konta)
        self.assertIn("setKiAuthTab", self.konta)
        self.assertIn("saveKontaSignup", self.konta)
        self.assertIn("kiSignupPayload", self.konta)


class PanelHarmonogramGridTests(unittest.TestCase):
    """Krok 5: siatka floty (telefony × Pn–Nd), nie karty jednego selecta."""

    def test_markup_siatki_floty(self):
        self.assertIn('id="tl-week-sel"', APP_HTML)
        self.assertIn('id="tl-sched-toggles"', APP_HTML)
        self.assertIn('id="tl-insp"', APP_HTML)
        self.assertIn('class="tl-fleet"', APP_HTML)
        self.assertIn('id="tl-legend"', APP_HTML)
        self.assertIn('id="tl-extras"', APP_HTML)
        self.assertNotIn('id="tl-device"', APP_HTML)

    def test_js_mapuje_cykl_na_daty_i_preselect(self):
        self.assertIn("function cycleIndexForDate", APP_JS)
        self.assertIn("719163", APP_JS)
        self.assertIn("function goPlan", APP_JS)
        self.assertIn("TL_PRESELECT_KEY", APP_JS)
        self.assertIn("paintPlayhead", APP_JS)
        self.assertIn("/api/schedule-accounts/", APP_JS)
        self.assertIn("scheduler_enabled", APP_JS)

    def test_css_playhead_i_inspector(self):
        self.assertIn(".tl-playhead", APP_CSS)
        self.assertIn(".tl-insp", APP_CSS)
        self.assertIn(".tl-row.offline", APP_CSS)


class PanelStatsNativeTests(unittest.TestCase):
    """Krok 10: native KPI w #statystyki, iframe nie jest głównym widokiem."""

    def test_markup_native_nie_iframe_jako_home(self):
        self.assertIn('id="st-kpi"', APP_HTML)
        self.assertIn('id="st-modes"', APP_HTML)
        self.assertIn('data-mode="last"', APP_HTML)
        self.assertIn('data-mode="24h"', APP_HTML)
        self.assertIn('data-mode="range"', APP_HTML)
        self.assertIn('id="st-plugins"', APP_HTML)
        self.assertIn('id="st-limits"', APP_HTML)
        self.assertIn('id="st-sessions"', APP_HTML)
        self.assertIn('id="st-fleet"', APP_HTML)
        self.assertIn('id="st-more"', APP_HTML)
        self.assertIn("/stats", APP_HTML)
        # Iframe zostaje pod foldem — nie jedyny widok.
        self.assertIn('id="st-classic"', APP_HTML)
        self.assertIn("<details", APP_HTML)
        self.assertIn('id="st-frame"', APP_HTML)
        self.assertNotIn("Odzyskaj", APP_HTML)

    def test_js_tryby_i_auto_refresh_ostatniej(self):
        self.assertIn('mode = "24h"', STATS_JS)
        self.assertIn("const POLL_MS = 4000", STATS_JS)
        self.assertIn("range=24h", STATS_JS)
        self.assertIn("/api/stats/", STATS_JS)
        self.assertIn("loadUsedAndCaps", STATS_JS)
        self.assertIn("/api/bot-config", STATS_JS)
        self.assertNotIn("Odzyskaj", STATS_JS)
        # Iframe tylko gdy <details> otwarte.
        self.assertIn("syncIframe", STATS_JS)
        self.assertIn("details.open", STATS_JS)

    def test_css_paski_bez_chart_lib(self):
        self.assertIn(".st-bar-fill", APP_CSS)
        self.assertIn(".st-fleet", APP_CSS)
        self.assertIn(".st-modes", APP_CSS)
        self.assertNotIn("chart.js", APP_CSS.lower())
        self.assertNotIn("chartjs", STATS_JS.lower())

    def test_share_nie_dostaje_sterowania(self):
        """Readonly /share zostaje — ten krok nie dopieszcza share.js."""
        share_js = (ROOT / "panel" / "static" / "share.js").read_text(encoding="utf-8")
        share_html = (ROOT / "panel" / "static" / "share.html").read_text(encoding="utf-8")
        self.assertIn('id="sh-kpi"', share_html)
        self.assertNotIn("Odzyskaj", share_html)
        self.assertNotIn("st-modes", share_html)
        self.assertNotIn("method: \"POST\"", share_js)


if __name__ == "__main__":
    unittest.main()
