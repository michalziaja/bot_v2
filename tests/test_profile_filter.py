"""Testy filtra profili: kryteria, promocja store’ów, scheduler (bez sieci IG)."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.profile_filter.core import (
    FilterCriteria,
    InstagramScraper,
    UserData,
    build_proxy_pool,
    evaluate_user,
    expand_sticky_sessions,
    is_html_not_found,
    mint_fresh_proxy_url,
    parse_profile_html,
)
from v2.profile_filter.pipeline import (
    merge_profile_filter_cfg,
    pending_usernames,
    run_filter_for_account,
)
from v2.data.stores import (
    RawFilterStateStore,
    RawProfilesStore,
    TargetCandidatesStore,
)
from v2.scheduler import profile_filter_tick as pft


class FilterCriteriaTests(unittest.TestCase):
    def test_ok_public_in_range(self):
        u = UserData(False, 5, 500, "hello gym")
        self.assertEqual(evaluate_user("x", u, FilterCriteria()), "ok")

    def test_remove_private(self):
        u = UserData(True, 5, 500, "hello")
        self.assertEqual(evaluate_user("x", u, FilterCriteria()), "remove")

    def test_remove_arabic_bio(self):
        u = UserData(False, 5, 500, "مرحبا")
        self.assertEqual(evaluate_user("x", u, FilterCriteria()), "remove")

    def test_allow_arabic_when_disabled(self):
        u = UserData(False, 5, 500, "مرحبا")
        c = FilterCriteria(reject_arabic_bio=False)
        self.assertEqual(evaluate_user("x", u, c), "ok")

    def test_followers_strict_bounds(self):
        c = FilterCriteria(followers_min=60, followers_max=20000)
        # równe min — poza zakresem (jak proxy1: min < n < max)
        self.assertEqual(
            evaluate_user("x", UserData(False, 1, 60, ""), c), "remove")
        self.assertEqual(
            evaluate_user("x", UserData(False, 1, 61, ""), c), "ok")

    def test_min_posts(self):
        c = FilterCriteria(min_posts=2)
        self.assertEqual(
            evaluate_user("x", UserData(False, 1, 100, ""), c), "remove")
        self.assertEqual(
            evaluate_user("x", UserData(False, 2, 100, ""), c), "ok")


class ParseProfileHtmlTests(unittest.TestCase):
    """Parser HTML — og:description + follower_count w blobie."""

    def test_og_description_and_json_fields(self):
        html = (
            '<meta property="og:description" '
            'content="233 Followers, 8 Following, 34 Posts - See Instagram"/>'
            '"follower_count":233,"is_private":false,"biography":"hello gym"'
        )
        u = parse_profile_html(html)
        self.assertIsNotNone(u)
        self.assertEqual(u.follower_count, 233)
        self.assertEqual(u.post_count, 34)
        self.assertFalse(u.is_private)
        self.assertEqual(u.biography, "hello gym")

    def test_compact_millions_and_comma_posts(self):
        html = (
            '<meta property="og:description" '
            'content="687M Followers, 290 Following, 8,581 Posts"/>'
        )
        u = parse_profile_html(html)
        self.assertEqual(u.follower_count, 687_000_000)
        self.assertEqual(u.post_count, 8581)

    def test_private_flag(self):
        html = (
            '<meta property="og:description" content="80 Followers, 1 Following, 3 Posts"/>'
            '"is_private":true'
        )
        u = parse_profile_html(html)
        self.assertTrue(u.is_private)

    def test_empty_html_is_none(self):
        self.assertIsNone(parse_profile_html(""))
        self.assertIsNone(parse_profile_html("<html>login wall</html>"))

    def test_not_found_page(self):
        self.assertTrue(is_html_not_found("Sorry, this page isn't available."))
        self.assertFalse(is_html_not_found("233 Followers, 34 Posts"))


class StoreBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.cfg = self.tmp / "config"
        (self.cfg / "accounts").mkdir(parents=True)
        (self.cfg / "devices").mkdir(parents=True)
        # Minimalny device + account
        (self.cfg / "devices" / "dev.json").write_text(json.dumps({
            "adb_id": "x", "resolution": [1080, 1920],
            "uiautodev": {"base_url": "http://127.0.0.1:9000"},
        }), encoding="utf-8")
        acc = {
            "meta": {"account_id": "acc1", "ig_username": "u"},
            "device": {"device_alias": "dev",
                       "app_clone_package": "com.instagram.android"},
            "humanizer": {"profile_name": "default"},
            "profile_filter": {
                "enabled": True,
                "times": ["10:30"],
                "limit_per_run": 10,
                "proxy": "losowo",
                "sleep_between_s": 0,
                "sleep_request_s": 0,
            },
        }
        (self.cfg / "accounts" / "acc1.json").write_text(
            json.dumps(acc), encoding="utf-8")
        self.raw = RawProfilesStore("acc1", self.data)
        self.raw.add_many(["alice", "bob", "carol"], source="reel_likers",
                          from_target="gym")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_pending_excludes_decided(self):
        RawFilterStateStore("acc1", self.data).mark("alice", "ok")
        pending = pending_usernames("acc1", self.data, limit=10)
        names = {r["username"] for r in pending}
        self.assertNotIn("alice", names)
        self.assertIn("bob", names)

    def test_promote_ok_and_remove(self):
        decisions = {"alice": "ok", "bob": "remove", "carol": "skip"}

        def scrape(u):
            return decisions[u]

        summary = run_filter_for_account(
            "acc1",
            data_root=self.data,
            config_root=self.cfg,
            scrape_fn=scrape,
            log=lambda _m: None,
        )
        self.assertEqual(summary["saved"], 1)
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(summary["skipped"], 1)
        cand = TargetCandidatesStore("acc1", self.data).read_all()
        self.assertEqual([c["username"] for c in cand], ["alice"])
        decided = RawFilterStateStore("acc1", self.data).decided_usernames()
        self.assertEqual(decided, {"alice", "bob"})
        # skip wraca do pending
        pending = {r["username"] for r in pending_usernames("acc1", self.data, 10)}
        self.assertEqual(pending, {"carol"})

    def test_max_saved_continues_past_skips(self):
        """Jak proxy1: limit = liczba Save, skip nie kończy runu."""
        acc = json.loads(
            (self.cfg / "accounts" / "acc1.json").read_text(encoding="utf-8"))
        acc["profile_filter"]["limit_per_run"] = 1
        (self.cfg / "accounts" / "acc1.json").write_text(
            json.dumps(acc), encoding="utf-8")
        seen: list[str] = []

        def scrape(u):
            seen.append(u)
            return "skip" if u != "carol" else "ok"

        summary = run_filter_for_account(
            "acc1",
            data_root=self.data,
            config_root=self.cfg,
            scrape_fn=scrape,
            log=lambda _m: None,
        )
        self.assertEqual(seen, ["alice", "bob", "carol"])
        self.assertEqual(summary["saved"], 1)
        self.assertEqual(summary["skipped"], 2)
        self.assertEqual(summary["usernames_ok"], ["carol"])

    def test_skip_goes_to_end_of_pending(self):
        """Skip na ogonie kolejki — jak append na koniec all2.txt."""
        RawFilterStateStore("acc1", self.data).mark("alice", "skip")
        names = [r["username"] for r in pending_usernames("acc1", self.data, 10)]
        self.assertEqual(names[0], "bob")
        self.assertEqual(names[-1], "alice")
        self.assertIn("carol", names)

    def test_candidates_add_dedup(self):
        store = TargetCandidatesStore("acc1", self.data)
        self.assertTrue(store.add("x", source="t"))
        self.assertFalse(store.add("x", source="t"))
        self.assertEqual(len(store.read_all()), 1)

    def test_sleep_between_usernames(self):
        """Pauza tylko między nickami (nie przed pierwszym)."""
        sleeps: list[float] = []
        acc = json.loads(
            (self.cfg / "accounts" / "acc1.json").read_text(encoding="utf-8"))
        acc["profile_filter"]["sleep_between_s"] = 3.5
        (self.cfg / "accounts" / "acc1.json").write_text(
            json.dumps(acc), encoding="utf-8")

        def scrape(_u):
            return "remove"

        with mock.patch("v2.profile_filter.pipeline.time.sleep",
                        side_effect=sleeps.append):
            run_filter_for_account(
                "acc1",
                data_root=self.data,
                config_root=self.cfg,
                scrape_fn=scrape,
                log=lambda _m: None,
            )
        # 3 nicki → 2 przerwy
        self.assertEqual(sleeps, [3.5, 3.5])


class SchedulerSlotTests(unittest.TestCase):
    def test_matching_slot_window(self):
        now = datetime(2026, 8, 11, 10, 30, 15)
        self.assertEqual(pft.matching_slot(["10:30", "18:00"], now), "10:30")
        now2 = datetime(2026, 8, 11, 10, 32, 0)
        self.assertIsNone(pft.matching_slot(["10:30"], now2))

    def test_tick_runs_once_per_slot(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            data = tmp / "data"
            cfg = tmp / "config"
            (cfg / "accounts").mkdir(parents=True)
            (cfg / "accounts" / "a.json").write_text(json.dumps({
                "meta": {"account_id": "a"},
                "profile_filter": {
                    "enabled": True,
                    "times": ["10:30"],
                    "limit_per_run": 5,
                },
            }), encoding="utf-8")
            calls = []

            def fake_run(account_id, **kwargs):
                calls.append(account_id)
                return {"account": account_id, "checked": 0, "saved": 0,
                        "removed": 0, "skipped": 0}

            now = datetime(2026, 8, 11, 10, 30, 5)
            a1 = pft.tick_once(data, cfg, now=now, run_fn=fake_run,
                               log=lambda _m: None)
            self.assertEqual(len(calls), 1)
            self.assertEqual(a1[0]["result"], "started_ok")

            # Drugi tick w tej samej minucie — already_done
            a2 = pft.tick_once(data, cfg, now=now, run_fn=fake_run,
                               log=lambda _m: None)
            self.assertEqual(len(calls), 1)
            self.assertTrue(any(x.get("result") == "already_done" for x in a2))
        finally:
            shutil.rmtree(tmp)

    def test_disabled_account_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            data = tmp / "data"
            cfg = tmp / "config"
            (cfg / "accounts").mkdir(parents=True)
            (cfg / "accounts" / "a.json").write_text(json.dumps({
                "profile_filter": {"enabled": False, "times": ["10:30"]},
            }), encoding="utf-8")
            calls = []
            pft.tick_once(
                data, cfg,
                now=datetime(2026, 8, 11, 10, 30),
                run_fn=lambda *a, **k: calls.append(1) or {},
                log=lambda _m: None,
            )
            self.assertEqual(calls, [])
        finally:
            shutil.rmtree(tmp)


class MergeCfgTests(unittest.TestCase):
    def test_defaults(self):
        m = merge_profile_filter_cfg(None)
        self.assertFalse(m["enabled"])
        self.assertIn("10:30", m["times"])
        self.assertEqual(m["sleep_between_s"], 0)
        self.assertGreaterEqual(m["sleep_request_s"], 2)
        self.assertGreaterEqual(m["retry_backoff_base_s"], 1)
        self.assertGreaterEqual(m["retry_backoff_max_s"],
                                m["retry_backoff_base_s"])
        self.assertGreaterEqual(m["request_timeout_s"], 20)


class ProxyPoolTests(unittest.TestCase):
    """Rotacja IP per request — jak pula PROXY_URLs w TARGETY/proxy1.py."""

    def test_rotate_gateway_unchanged(self):
        url = "http://user-rotate:pass@p.webshare.io:80/"
        self.assertEqual(mint_fresh_proxy_url(url), url)
        self.assertEqual(expand_sticky_sessions(url), [url])

    def test_rayobyte_fresh_session_each_mint(self):
        url = "http://user:pass@la.residential.rayobyte.com:8000"
        a = mint_fresh_proxy_url(url)
        b = mint_fresh_proxy_url(url)
        self.assertIn("session-", a)
        self.assertIn("country-PL", a)
        self.assertNotEqual(a, b)

    def test_mars_session_id_replaced(self):
        url = ("http://u:pass_country-pl_session-abcdef12_lifetime-45s"
               "@ultra.marsproxies.com:44443")
        out = mint_fresh_proxy_url(url)
        self.assertIn("session-", out)
        self.assertNotIn("session-abcdef12", out)

    def test_build_pool_keeps_bases_not_sticky_expand(self):
        proxies = {
            "webshare": "http://u-rotate:p@host:80/",
            "mars": [
                "http://a:b@ultra.marsproxies.com:44443",
                "http://c:d@ultra.marsproxies.com:44443",
            ],
            "rayobyte": "http://u:p@la.residential.rayobyte.com:8000",
        }
        pool = build_proxy_pool(proxies, "losowo")
        # Bazy: 1+2+1 — świeże session-ID dopiero przy requeście
        self.assertEqual(len(pool), 4)
        self.assertEqual(sum(1 for u in pool if "marsproxies" in u), 2)
        self.assertEqual(sum(1 for u in pool if "rayobyte" in u), 1)


class ScraperRetryTests(unittest.TestCase):
    """Sprawdza łagodny backoff retry bez prawdziwej sieci."""

    class _Resp:
        def __init__(self, status_code: int, payload: dict, text: str | None = None,
                     url: str = ""):
            self.status_code = status_code
            self._payload = payload
            self._text = text
            self.url = url

        def json(self):
            return self._payload

        @property
        def text(self):
            # Body jak z prawdziwego HTTP.
            if self._text is not None:
                return self._text
            return json.dumps(self._payload)

    @staticmethod
    def _valid_payload() -> dict:
        return {
            "data": {
                "user": {
                    "is_private": False,
                    "edge_owner_to_timeline_media": {"count": 3},
                    "edge_followed_by": {"count": 321},
                    "biography_with_entities": {"raw_text": "ok"},
                }
            }
        }

    def test_backoff_after_401(self):
        responses = [
            self._Resp(401, {}),
            self._Resp(200, self._valid_payload()),
        ]
        sleeps: list[float] = []

        class _FakeSession:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, *_args, **_kwargs):
                return responses.pop(0)

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.random.uniform",
                           return_value=0.0), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           side_effect=sleeps.append), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=lambda seq: seq[0]):
            s = InstagramScraper(
                log_exit_ip=False,
                proxies={"x": "http://u:p@proxy.example:8080"},
                log=lambda _m: None,
                request_sleep_s=0,
                retry_backoff_base_s=1.0,
                retry_backoff_max_s=5.0,
            )
            out = s.scrape_user("alice")

        self.assertEqual(out, "ok")
        self.assertEqual(sleeps, [8.0])

    def test_backoff_after_user_null(self):
        responses = [
            self._Resp(200, {"data": {"user": None}}),
            self._Resp(200, self._valid_payload()),
        ]
        sleeps: list[float] = []

        class _FakeSession:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, *_args, **_kwargs):
                return responses.pop(0)

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.random.uniform",
                           return_value=0.0), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           side_effect=sleeps.append), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=lambda seq: seq[0]):
            s = InstagramScraper(
                log_exit_ip=False,
                proxies={"x": "http://u:p@proxy.example:8080"},
                log=lambda _m: None,
                request_sleep_s=0,
                retry_backoff_base_s=1.0,
                retry_backoff_max_s=5.0,
            )
            out = s.scrape_user("alice")

        self.assertEqual(out, "ok")
        self.assertEqual(sleeps, [0.8])

    def test_retry_after_proxy_error_uses_other_provider_when_possible(self):
        """Po ProxyError kolejna próba wybiera innego providera (gdy dostępny)."""
        responses = [
            RuntimeError("proxy down"),
            self._Resp(200, self._valid_payload()),
        ]
        providers_seen: list[str] = []
        sleeps: list[float] = []

        class _FakeSession:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, *_args, **kwargs):
                proxy_url = (kwargs.get("proxies") or {}).get("https", "")
                if "p.webshare.io" in proxy_url:
                    providers_seen.append("webshare")
                elif "rayobyte.com" in proxy_url:
                    providers_seen.append("rayobyte")
                item = responses.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        def _choice(seq):
            # Pierwszy wybór providera: webshare, po avoid_provider: rayobyte.
            if seq and isinstance(seq[0], str) and "webshare" in seq:
                return "webshare"
            if seq and isinstance(seq[0], str) and "rayobyte" in seq:
                return "rayobyte"
            return seq[0]

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.random.uniform",
                           return_value=0.0), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           side_effect=sleeps.append), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=_choice):
            s = InstagramScraper(
                log_exit_ip=False,
                proxies={
                    "webshare": "http://u:p@p.webshare.io:80/",
                    "rayobyte": "http://u:p@us-east.gw.rayobyte.com:8000",
                },
                log=lambda _m: None,
                request_sleep_s=0,
                retry_backoff_base_s=1.0,
                retry_backoff_max_s=5.0,
            )
            out = s.scrape_user("alice")

        self.assertEqual(out, "ok")
        self.assertEqual(providers_seen[:2], ["webshare", "rayobyte"])
        self.assertEqual(sleeps, [1.0])

    def test_retry_after_401_switches_provider(self):
        """HTTP 401 też od razu zmienia dostawcę na retry."""
        responses = [
            self._Resp(401, {}),
            self._Resp(200, self._valid_payload()),
        ]
        providers_seen: list[str] = []
        sleeps: list[float] = []

        class _FakeSession:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, *_args, **kwargs):
                proxy_url = (kwargs.get("proxies") or {}).get("https", "")
                if "p.webshare.io" in proxy_url:
                    providers_seen.append("webshare")
                elif "rayobyte.com" in proxy_url:
                    providers_seen.append("rayobyte")
                return responses.pop(0)

        def _choice(seq):
            if seq and isinstance(seq[0], str) and "webshare" in seq:
                return "webshare"
            if seq and isinstance(seq[0], str) and "rayobyte" in seq:
                return "rayobyte"
            return seq[0]

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.random.uniform",
                           return_value=0.0), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           side_effect=sleeps.append), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=_choice):
            s = InstagramScraper(
                log_exit_ip=False,
                proxies={
                    "webshare": "http://u:p@p.webshare.io:80/",
                    "rayobyte": "http://u:p@us-east.gw.rayobyte.com:8000",
                },
                log=lambda _m: None,
                request_sleep_s=0,
                retry_backoff_base_s=1.0,
                retry_backoff_max_s=5.0,
            )
            out = s.scrape_user("alice")

        self.assertEqual(out, "ok")
        self.assertEqual(providers_seen[:2], ["webshare", "rayobyte"])
        self.assertEqual(sleeps, [8.0])

    def test_second_401_skips_without_third_try(self):
        """Drugi 400/401 → skip od razu (ogon kolejki), bez 3. i 4. próby."""
        responses = [
            self._Resp(401, {}),
            self._Resp(400, {}),
            self._Resp(200, self._valid_payload()),
        ]
        sleeps: list[float] = []
        gets = {"n": 0}

        class _FakeSession:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, *_args, **_kwargs):
                gets["n"] += 1
                return responses.pop(0)

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.random.uniform",
                           return_value=0.0), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           side_effect=sleeps.append), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=lambda seq: seq[0]):
            s = InstagramScraper(
                log_exit_ip=False,
                proxies={"x": "http://u:p@proxy.example:8080"},
                log=lambda _m: None,
                request_sleep_s=0,
            )
            out = s.scrape_user("alice")

        self.assertEqual(out, "skip")
        self.assertEqual(gets["n"], 2)
        self.assertEqual(sleeps, [8.0])

    def test_chrome_rotates_between_120_and_124(self):
        """Każdy request to naprzemiennie chrome120 / chrome124."""
        impersonates: list[str] = []
        responses = [
            self._Resp(401, {}),
            self._Resp(200, self._valid_payload()),
        ]

        class _FakeSession:
            def __init__(self, **kwargs):
                impersonates.append(kwargs.get("impersonate"))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, *_args, **_kwargs):
                return responses.pop(0)

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           return_value=None), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=lambda seq: seq[0]):
            s = InstagramScraper(
                log_exit_ip=False,
                proxies={"x": "http://u:p@proxy.example:8080"},
                log=lambda _m: None,
                request_sleep_s=0,
            )
            s.scrape_user("alice")

        self.assertEqual(impersonates, ["chrome120", "chrome124"])
        self.assertTrue(set(impersonates) <= {"chrome120", "chrome124"})

    def test_logs_exit_ip_before_ig_request(self):
        """Tymczasowy podgląd IP — linia przed requestem do IG."""
        logs: list[str] = []
        responses = [self._Resp(200, self._valid_payload())]

        class _FakeSession:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, url, **_kwargs):
                if "ipify" in str(url):
                    return self._RespIp()
                return responses.pop(0)

            class _RespIp:
                def json(self):
                    return {"ip": "203.0.113.9"}

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           return_value=None), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=lambda seq: seq[0]):
            s = InstagramScraper(
                proxies={"x": "http://u:p@proxy.example:8080"},
                log=logs.append,
                request_sleep_s=0,
                log_exit_ip=True,
            )
            s.scrape_user("alice")

        self.assertTrue(any("IP wyjscia: 203.0.113.9" in m for m in logs))

    def test_html_200_parses_og_and_hits_profile_url(self):
        """Główna ścieżka: GET HTML profilu, nie JSON API."""
        html = (
            '<meta property="og:description" '
            'content="233 Followers, 8 Following, 34 Posts"/>'
            '"follower_count":233,"is_private":false'
        )
        seen: dict = {}

        class _FakeSession:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, url, **kwargs):
                seen["url"] = url
                seen["headers"] = kwargs.get("headers") or {}
                return ScraperRetryTests._Resp(200, {}, text=html)

        with mock.patch("v2.profile_filter.core.creq",
                        types.SimpleNamespace(Session=_FakeSession)), \
                mock.patch("v2.profile_filter.core.time.sleep",
                           return_value=None), \
                mock.patch("v2.profile_filter.core.random.choice",
                           side_effect=lambda seq: seq[0]):
            s = InstagramScraper(
                log_exit_ip=False,
                proxies={"x": "http://u:p@proxy.example:8080"},
                log=lambda _m: None,
                request_sleep_s=0,
            )
            out = s.scrape_user("alice")

        self.assertEqual(out, "ok")
        self.assertEqual(seen["url"], "https://www.instagram.com/alice/")
        self.assertIn("text/html", seen["headers"].get("Accept", ""))


class FilterStopTests(unittest.TestCase):
    """Ręczne STOP subprocessu filtra — bez prawdziwego CLI / sieci."""

    def setUp(self):
        from v2.panel import server as ps
        self.ps = ps
        with ps._filter_job_lock:
            ps._filter_proc = None
            ps._filter_job.update({
                "running": False,
                "stopping": False,
                "cancel": False,
                "account": None,
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "stdout_path": None,
                "stderr_path": None,
                "pid": None,
            })

    def tearDown(self):
        self.setUp()

    def test_stop_idle_raises(self):
        with self.assertRaises(ValueError) as cm:
            self.ps.stop_filter_job("acc1")
        self.assertIn("nie działa", str(cm.exception))

    def test_stop_other_account_raises(self):
        self.ps._filter_job["running"] = True
        self.ps._filter_job["account"] = "other"
        with self.assertRaises(ValueError) as cm:
            self.ps.stop_filter_job("acc1")
        self.assertIn("innym koncie", str(cm.exception))

    def test_stop_kills_proc_and_clears_running(self):
        class FakeProc:
            def __init__(self):
                self.pid = 4242
                self._code = None
                self.sigs = []

            def poll(self):
                return self._code

            def wait(self, timeout=None):
                if self._code is not None:
                    return self._code
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)

            def terminate(self):
                self.sigs.append("term")
                self._code = -15

            def kill(self):
                self.sigs.append("kill")
                self._code = -9

            def send_signal(self, sig):
                self.sigs.append(sig)
                self._code = -15

        proc = FakeProc()
        self.ps._filter_job["running"] = True
        self.ps._filter_job["account"] = "acc1"
        self.ps._filter_proc = proc

        def fake_killpg(pid, sig):
            proc.sigs.append(sig)
            proc._code = -9 if sig == 9 else -15

        with mock.patch.object(self.ps.os, "killpg", side_effect=fake_killpg):
            out = self.ps.stop_filter_job("acc1")
        self.assertTrue(out["stopped"])
        self.assertFalse(self.ps._filter_job["running"])
        self.assertEqual(self.ps._filter_job["error"], "zatrzymany")
        self.assertIsNone(self.ps._filter_proc)
        st = self.ps._filter_job_status()
        self.assertNotIn("cancel", st)
        self.assertFalse(st["running"])

    def test_status_omits_cancel(self):
        self.ps._filter_job["cancel"] = True
        st = self.ps._filter_job_status()
        self.assertNotIn("cancel", st)


if __name__ == "__main__":
    unittest.main()
