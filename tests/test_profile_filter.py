"""Testy filtra profili: kryteria, promocja store’ów, scheduler (bez sieci IG)."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.profile_filter.core import (
    FilterCriteria,
    UserData,
    build_proxy_pool,
    evaluate_user,
    expand_sticky_sessions,
    mint_fresh_proxy_url,
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

    def test_candidates_add_dedup(self):
        store = TargetCandidatesStore("acc1", self.data)
        self.assertTrue(store.add("x", source="t"))
        self.assertFalse(store.add("x", source="t"))
        self.assertEqual(len(store.read_all()), 1)


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


if __name__ == "__main__":
    unittest.main()
