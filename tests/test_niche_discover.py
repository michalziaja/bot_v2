"""Testy niche_discover — normalizacja, write_niche, stub AI, catalog, gate."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from v2.ai.vision import AiVision
from v2.plugins.catalog import get_plugin
from v2.plugins.impl.niche_discover import (
    normalize_niche, _captions_numbered, _sync_phrases,
    extract_hashtags_from_texts, _keyword_mirrors_hashtag,
    _expand_short_keywords,
)
from v2.plugins.registry import is_implemented, load_all
from v2.plugins import store


class NormalizeNicheTests(unittest.TestCase):
    def test_hashtags_get_hash_and_dedup(self):
        # „hiking” jest lustrem #hiking → odpada z keywords
        out = normalize_niche({
            "description": "  Tech outdoor  ",
            "audience": "miłośnicy natury",
            "keywords": ["Camping", "camping", "hiking"],
            "hashtags": ["nature", "#Nature", "#hiking"],
            "exclusions": ["spam"],  # AI — ignorowane
        })
        self.assertEqual(out["description"], "Tech outdoor")
        self.assertEqual(out["keywords"], ["Camping"])
        self.assertEqual(out["hashtags"], ["#nature", "#hiking"])
        self.assertEqual(out["exclusions"], [])

    def test_keyword_mirror_rejected(self):
        # spacje / podkreślenia / # nie ratują lustra keyword ↔ hashtag
        self.assertTrue(_keyword_mirrors_hashtag("job search", {"jobsearch"}))
        self.assertTrue(_keyword_mirrors_hashtag("job_search", {"jobsearch"}))
        self.assertTrue(_keyword_mirrors_hashtag("#JobSearch", {"jobsearch"}))
        self.assertFalse(_keyword_mirrors_hashtag("aplikacja do pracy", {"jobsearch"}))
        out = normalize_niche({
            "description": "x", "audience": "y",
            "keywords": ["jobsearch", "CV AI", "job search"],
            "hashtags": ["#jobsearch", "#career"],
        })
        self.assertEqual(out["keywords"], ["CV AI"])
        self.assertEqual(out["hashtags"], ["#jobsearch", "#career"])

    def test_extract_hashtags_from_texts(self):
        tags = extract_hashtags_from_texts([
            "Hello #JobSearch and #CV",
            "more #jobsearch #aitools",
            "",
            None,
        ])
        self.assertEqual(tags, ["#JobSearch", "#CV", "#aitools"])

    def test_preserves_existing_exclusions_ignores_ai(self):
        out = normalize_niche(
            {"exclusions": ["B"], "description": "x", "audience": "",
             "keywords": [], "hashtags": []},
            existing={"exclusions": ["A"]},
        )
        self.assertEqual(out["exclusions"], ["A"])

    def test_overwrites_description_keeps_exclusions(self):
        # nowa analiza nadpisuje opis; exclusions z existing zawsze zostają
        out = normalize_niche(
            {"description": "nowy opis", "audience": "nowe",
             "keywords": ["a"], "hashtags": ["#b"], "exclusions": ["AI"]},
            existing={"description": "stary", "exclusions": ["ręczne"]},
        )
        self.assertEqual(out["description"], "nowy opis")
        self.assertEqual(out["audience"], "nowe")
        self.assertEqual(out["exclusions"], ["ręczne"])

    def test_short_keywords_split_joiners_and_trim(self):
        # sklejone tematy → osobne keywords; >3 słowa → pierwsze 3; PL znaki OK
        self.assertEqual(
            _expand_short_keywords([
                "makeup tutorial i poradniki",
                "stylizacja outfitów i lookboki",
                "fashion inspiration and styling tips",
                "moda & beauty",
                "reels creator strategia content tips",
                "laminacja rzęs",
            ]),
            [
                "makeup tutorial", "poradniki",
                "stylizacja outfitów", "lookboki",
                "fashion inspiration", "styling tips",
                "moda", "beauty",
                "reels creator strategia",
                "laminacja rzęs",
            ],
        )
        out = normalize_niche({
            "description": "x", "audience": "y",
            "keywords": [
                "makeup tutorial i poradniki",
                "stylizacja outfitów",
                "elegancka moda wieczorowa premium",
            ],
            "hashtags": ["#makeup"],
        }, keywords_limit=10)
        self.assertEqual(out["keywords"], [
            "makeup tutorial", "poradniki",
            "stylizacja outfitów",
            "elegancka moda wieczorowa",
        ])


class NicheAcceptedTests(unittest.TestCase):
    def test_missing_key_is_true(self):
        self.assertTrue(store.niche_is_accepted({}))
        self.assertTrue(store.niche_is_accepted({"description": "x"}))

    def test_false_and_true(self):
        self.assertFalse(store.niche_is_accepted({"accepted": False}))
        self.assertTrue(store.niche_is_accepted({"accepted": True}))

    def test_account_object(self):
        acc = mock.Mock()
        acc.niche = {"accepted": False}
        self.assertFalse(store.niche_is_accepted(acc))


class WriteNicheTests(unittest.TestCase):
    def test_write_niche_bak_and_accepted_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts = root / "accounts"
            accounts.mkdir()
            path = accounts / "demo.json"
            path.write_text(json.dumps({
                "account_id": "demo",
                "ig_username": "demo",
                "niche": {"description": "old", "accepted": True},
            }, ensure_ascii=False), encoding="utf-8")

            with mock.patch.object(store, "CONFIG_ROOT", root):
                saved = store.write_niche("demo", {
                    "description": "nowa nisza",
                    "audience": "fani X",
                    "keywords": ["a", "b"],
                    "hashtags": ["#a"],
                    "exclusions": [],
                    "accepted": False,
                    "discovered_at": "2026-08-08T01:00:00",
                    "last_ai_model": "claude-haiku-x",
                    "last_ai_tokens_in": 1200,
                    "last_ai_tokens_out": 340,
                    "last_ai_usage": {
                        "model": "claude-haiku-x",
                        "tokens_in": 1200,
                        "tokens_out": 340,
                        "calls": 3,
                    },
                })
            self.assertFalse(saved["accepted"])
            self.assertEqual(saved["description"], "nowa nisza")
            self.assertEqual(saved["last_ai_tokens_in"], 1200)
            self.assertEqual(saved["last_ai_usage"]["tokens_out"], 340)
            self.assertTrue(path.with_suffix(".json.bak").exists())
            cfg = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["niche"]["audience"], "fani X")
            self.assertEqual(cfg["niche"]["last_ai_model"], "claude-haiku-x")

    def test_clear_niche_empties_and_bak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts = root / "accounts"
            accounts.mkdir()
            path = accounts / "demo.json"
            path.write_text(json.dumps({
                "account_id": "demo",
                "niche": {
                    "description": "stara",
                    "audience": "x",
                    "keywords": ["a"],
                    "hashtags": ["#a"],
                    "exclusions": ["e"],
                    "accepted": True,
                    "discovered_at": "2026-08-08T01:00:00",
                },
            }, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(store, "CONFIG_ROOT", root):
                cleared = store.clear_niche("demo")
            self.assertEqual(cleared["description"], "")
            self.assertEqual(cleared["keywords"], [])
            self.assertEqual(cleared["keywords_manual"], [])
            self.assertEqual(cleared["keywords_discovered"], [])
            self.assertNotIn("accepted", cleared)
            self.assertNotIn("discovered_at", cleared)
            self.assertTrue(path.with_suffix(".json.bak").exists())
            cfg = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["niche"]["description"], "")
            self.assertNotIn("accepted", cfg["niche"])


class NichePhraseSourcesTests(unittest.TestCase):
    """manual vs discovered: append/replace + reconcile panelu."""

    def test_normalize_replace_keeps_manual(self):
        existing = {
            "keywords_manual": ["moje slowo"],
            "keywords_discovered": ["stare_ai"],
            "hashtags_manual": ["#moje"],
            "hashtags_discovered": ["#stare"],
            "keywords": ["moje slowo", "stare_ai"],
            "hashtags": ["#moje", "#stare"],
        }
        out = normalize_niche(
            {"description": "x", "audience": "y",
             "keywords": ["nowe_ai", "moje slowo"],
             "hashtags": ["#nowe", "#moje"]},
            existing=existing,
            discovered_merge="replace",
            keywords_limit=10, hashtags_limit=10,
        )
        # manual bez zmian; discovered = wynik AI minus lustra/manual
        self.assertEqual(out["keywords_manual"], ["moje slowo"])
        self.assertEqual(out["hashtags_manual"], ["#moje"])
        self.assertEqual(out["keywords_discovered"], ["nowe_ai"])
        self.assertEqual(out["hashtags_discovered"], ["#nowe"])
        # scalona: manual pierwsze
        self.assertEqual(out["keywords"], ["moje slowo", "nowe_ai"])
        self.assertEqual(out["hashtags"], ["#moje", "#nowe"])

    def test_normalize_append_discovered(self):
        existing = {
            "keywords_manual": ["reczne"],
            "keywords_discovered": ["stare"],
            "hashtags_manual": [],
            "hashtags_discovered": ["#stare"],
        }
        out = normalize_niche(
            {"description": "x", "audience": "y",
             "keywords": ["nowe slowo"], "hashtags": ["#nowetag"]},
            existing=existing,
            discovered_merge="append",
        )
        self.assertEqual(out["keywords_manual"], ["reczne"])
        self.assertEqual(out["keywords_discovered"], ["stare", "nowe slowo"])
        self.assertEqual(out["hashtags_discovered"], ["#stare", "#nowetag"])
        self.assertEqual(
            out["keywords"], ["reczne", "stare", "nowe slowo"])

    def test_legacy_flat_becomes_discovered(self):
        src = store.ensure_niche_phrase_sources({
            "keywords": ["a", "b"], "hashtags": ["#x"],
        })
        self.assertEqual(src["keywords_manual"], [])
        self.assertEqual(src["keywords_discovered"], ["a", "b"])
        self.assertEqual(src["hashtags_discovered"], ["#x"])

    def test_reconcile_panel_add_and_remove(self):
        prev = {
            "keywords_manual": ["reczne"],
            "keywords_discovered": ["z_ai"],
            "hashtags_manual": ["#reczne"],
            "hashtags_discovered": ["#ai"],
            "description": "stary",
        }
        # UI: usunięto z_ai, dodano nowe_reczne; hashtagi bez zmian treści
        new = {
            "description": "nowy opis",
            "keywords": ["reczne", "nowe_reczne"],
            "hashtags": ["#reczne", "#ai"],
        }
        out = store.reconcile_niche_phrases(prev, new)
        self.assertEqual(out["description"], "nowy opis")
        self.assertEqual(out["keywords_manual"], ["reczne", "nowe_reczne"])
        self.assertEqual(out["keywords_discovered"], [])  # z_ai usunięte
        self.assertEqual(out["hashtags_manual"], ["#reczne"])
        self.assertEqual(out["hashtags_discovered"], ["#ai"])
        self.assertEqual(out["keywords"], ["reczne", "nowe_reczne"])

    def test_write_niche_persists_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts = root / "accounts"
            accounts.mkdir()
            path = accounts / "demo.json"
            path.write_text(json.dumps({
                "account_id": "demo",
                "niche": {},
            }, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(store, "CONFIG_ROOT", root):
                saved = store.write_niche("demo", {
                    "description": "d",
                    "keywords_manual": ["m"],
                    "keywords_discovered": ["d1"],
                    "keywords": ["m", "d1"],
                    "hashtags_manual": ["#m"],
                    "hashtags_discovered": ["#d"],
                    "hashtags": ["#m", "#d"],
                })
            self.assertEqual(saved["keywords_manual"], ["m"])
            self.assertEqual(saved["keywords_discovered"], ["d1"])
            cfg = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["niche"]["hashtags_discovered"], ["#d"])


class SyncPhrasesTests(unittest.TestCase):
    def test_append_keywords_and_hashtags_dedup(self):
        # append-only + case-insensitive; nie nadpisuje całej listy
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "accounts").mkdir()
            (root / "devices").mkdir()
            (root / "accounts" / "demo.json").write_text(json.dumps({
                "meta": {"account_id": "demo", "ig_username": "demo"},
                "device": {"device_alias": "dev1",
                           "app_clone_package": "com.instagram.android"},
                "humanizer": {"profile_name": "default"},
                "plugins": {
                    "content_search": {
                        "phrases": {"list": ["#Gym", "camping"]},
                    },
                },
            }, ensure_ascii=False), encoding="utf-8")
            (root / "devices" / "dev1.json").write_text(json.dumps({
                "adb_id": "x", "resolution": [1080, 2340],
                "uiautodev": {"base_url": "http://127.0.0.1:20242"},
            }), encoding="utf-8")
            with mock.patch.object(store, "CONFIG_ROOT", root):
                _sync_phrases("demo", ["Camping", "hiking"], ["#gym", "#nature"])
                settings = store.read_settings("demo")
            lst = ((settings.get("content_search") or {})
                   .get("phrases") or {}).get("list") or []
            # istniejące zostają; duplikaty (camping/#gym) pominięte
            self.assertEqual(lst[0], "#Gym")
            self.assertEqual(lst[1], "camping")
            self.assertIn("hiking", lst)
            self.assertIn("#nature", lst)
            self.assertEqual(len(lst), 4)


class CaptionsNumberedTests(unittest.TestCase):
    def test_numbered_plain_captions(self):
        # Opisy 1. 2. … bez tagów źródła — Vision dostaje siatkę + ten tekst
        text = _captions_numbered([
            {"kind": "post", "caption": "hello",
             "source_kind": "own", "source_account": "own"},
            {"kind": "reel", "caption": "vid",
             "source_kind": "competitor", "source_account": "@rival"},
            {"kind": "post", "caption": "",
             "source_kind": "own", "source_account": "own"},
        ])
        self.assertIn("1. hello", text)
        self.assertIn("2. vid", text)
        self.assertIn("3. (brak opisu)", text)
        self.assertNotIn("[own:own]", text)
        self.assertNotIn("(post)", text)


class ContentFingerprintTests(unittest.TestCase):
    """Dedup roli vs post — ten sam caption → skip, inny → zbieraj."""

    def test_same_caption_matches_across_viewers(self):
        from v2.plugins.impl.niche_discover import (
            content_fingerprint, fingerprint_matches,
        )
        # pełny caption z viewera posta
        post_fp = content_fingerprint(
            caption="Poranny trening w górach #hiking #outdoor",
            author="me",
        )
        self.assertIsNotNone(post_fp)
        self.assertTrue(post_fp.startswith("cap:"))
        seen = {post_fp}
        # skrócony dump z viewera rolki (prefix) — nadal match
        reel_fp = content_fingerprint(
            caption="Poranny trening w górach #hiking",
            author="me",
        )
        self.assertTrue(fingerprint_matches(reel_fp, seen))

    def test_different_captions_do_not_match(self):
        from v2.plugins.impl.niche_discover import (
            content_fingerprint, fingerprint_matches,
        )
        seen = {content_fingerprint(caption="Pierwszy post o kawie specialty")}
        other = content_fingerprint(caption="Całkiem inna rolka o bieganiu")
        self.assertFalse(fingerprint_matches(other, seen))

    def test_short_or_empty_caption_no_skip(self):
        from v2.plugins.impl.niche_discover import (
            content_fingerprint, fingerprint_matches,
        )
        # za krótki caption → None → nie skipujemy
        self.assertIsNone(content_fingerprint(caption="hi", author="x"))
        self.assertIsNone(content_fingerprint(caption="", author="x"))
        self.assertFalse(fingerprint_matches(None, {"cap:somethinglong"}))

    def test_normalizes_whitespace_and_more_suffix(self):
        from v2.plugins.impl.niche_discover import content_fingerprint
        a = content_fingerprint(caption="Hello   World  test caption here")
        b = content_fingerprint(caption="hello world test caption here… więcej")
        self.assertEqual(a, b)

    def test_author_likes_fallback_exact(self):
        from v2.plugins.impl.niche_discover import (
            content_fingerprint, fingerprint_matches,
        )
        # krótki/pusty opis — fallback author+likes
        fp = content_fingerprint(caption="x", author="@demo", likes=42)
        self.assertTrue(fp.startswith("au:"))
        self.assertTrue(fingerprint_matches(fp, {fp}))
        self.assertFalse(fingerprint_matches(
            content_fingerprint(caption="x", author="@demo", likes=99),
            {fp},
        ))


class StubVisionDiscoverTests(unittest.TestCase):
    def test_posts_and_account_and_merge_keys(self):
        v = AiVision(account={"ai": {"stub": True}})
        posts = v.run("niche_discover_posts", {
            "captions_numbered": "[1] (post) [own:own] camping gear review",
            "seed": "outdoor tech",
            "bio": "gear",
            "source_kind": "own",
            "source_account": "own",
            "username": "me",
        }, opts={"strictness": "balanced"})
        for k in ("themes", "keywords", "hashtags", "exclusions"):
            self.assertIn(k, posts)

        acc = v.run("niche_discover_account", {
            "posts_partial_json": json.dumps(posts),
            "reels_partial_json": "{}",
            "bio": "gear lover",
            "seed": "outdoor",
            "existing_niche_json": "{}",
            "source_kind": "own",
            "source_account": "own",
            "username": "me",
        }, opts={"strictness": "strict"})
        for k in ("audience", "description", "keywords", "hashtags"):
            self.assertIn(k, acc)
        self.assertEqual(acc.get("source_account") or "own", "own")

        merged = v.run("niche_discover_merge", {
            "seed": "outdoor",
            "account_results_json": json.dumps([
                {**acc, "source_kind": "own", "source_account": "own"},
                {"source_kind": "competitor", "source_account": "@x",
                 "username": "x", "description": "other",
                 "audience": "y", "keywords": ["z"], "hashtags": ["#z"],
                 "exclusions": []},
            ]),
        }, opts={"strictness": "strict"})
        self.assertIn("description", merged)
        self.assertIn("audience", merged)


class CatalogDiscoverTests(unittest.TestCase):
    def test_ready_defaults_and_registry(self):
        # jak w test_registry: load_all bez clear — import już załadowanych
        # modułów nie re-rejestruje; wystarczy upewnić się że load_all był
        load_all()
        pl = get_plugin("niche_discover")
        self.assertEqual(pl["status"], "ready")
        self.assertFalse(pl["queueable"])
        d = pl["defaults"]
        self.assertEqual(d["ai"]["batch_size"], 4)
        self.assertEqual(d["ai"]["keywords_count"], 8)
        self.assertEqual(d["ai"]["hashtags_count"], 8)
        self.assertEqual(d["write"]["discovered_merge"], "append")
        self.assertFalse(d["write"]["sync_content_search_phrases"])
        # batch_size dotyczy postów; rolki zawsze 1:1
        from v2.plugins.impl.niche_discover import (
            _analysis_batch_size, _exact_list_count, normalize_niche,
        )
        self.assertEqual(
            _analysis_batch_size("niche_discover_posts", {"batch_size": 4}), 4)
        self.assertEqual(
            _analysis_batch_size("niche_discover_reels", {"batch_size": 4}), 1)
        self.assertEqual(
            _analysis_batch_size("niche_discover_reels", {}), 1)
        self.assertEqual(_exact_list_count({"keywords_count": 5}, "keywords_count"), 5)
        # ucięcie nadmiaru do dokładnej liczby
        trimmed = normalize_niche(
            {"description": "x", "audience": "y",
             "keywords": [f"k{i}" for i in range(12)],
             "hashtags": [f"#h{i}" for i in range(12)]},
            keywords_limit=8, hashtags_limit=8,
        )
        self.assertEqual(len(trimmed["keywords"]), 8)
        self.assertEqual(len(trimmed["hashtags"]), 8)

    def test_collect_slug_and_samples_json(self):
        from v2.plugins.impl.niche_discover import (
            _collect_slug, _samples_for_json,
        )
        self.assertEqual(_collect_slug("own", "own", ""), "own")
        self.assertEqual(
            _collect_slug("competitor", "@apply4me.app", "apply4me.app"),
            "competitor_apply4me.app")
        samples = _samples_for_json([{
            "index": 1, "kind": "post", "caption": "hi",
            "image_path": "/tmp/a.jpg", "source_kind": "own",
            "source_account": "own", "tile_kind": "reel",
            "extra_runtime": object(),
        }])
        self.assertEqual(samples[0]["caption"], "hi")
        self.assertNotIn("extra_runtime", samples[0])
        # defaults / sekcje panelu — osobno od slug/samples
        load_all()
        pl = get_plugin("niche_discover")
        d = pl["defaults"]
        self.assertTrue(d["sources"]["own_profile"])
        self.assertIn("posts", d["collect"])
        self.assertIn("reels", d["collect"])
        self.assertEqual(d["collect"]["posts"]["count"], 4)
        self.assertEqual(d["collect"]["reels"]["count"], 4)
        self.assertEqual(d["collect"]["reel_frames"], 4)
        self.assertEqual(d["collect"]["reel_frame_interval_s"], 1.5)
        # panel: min 1 (odblokowane — wcześniej min=4)
        reel_frames_field = None
        for sec in pl["sections"]:
            for f in sec.get("fields") or []:
                if f.get("key") == ["collect", "reel_frames"]:
                    reel_frames_field = f
                    break
        self.assertIsNotNone(reel_frames_field)
        self.assertEqual(reel_frames_field["min"], 1)
        self.assertEqual(reel_frames_field["max"], 12)
        self.assertNotIn("max", d["competitors"])
        self.assertNotIn("skip_pinned", d["collect"])
        self.assertNotIn("read_caption", d["collect"])
        self.assertNotIn("capture_media", d["collect"])
        self.assertNotIn("posts", d["competitors"])
        self.assertNotIn("budget", d)
        self.assertTrue(is_implemented("niche_discover"))
        # sekcja Budżet / Zapis usunięte — oneshot; sync fraz w panelu Nisza
        titles = [s["title"] for s in pl["sections"]]
        self.assertNotIn("Budżet", titles)
        self.assertNotIn("Zapis", titles)
        self.assertIn("write", d)
        self.assertEqual(d["write"].get("sync_content_search_phrases"), False)
        self.assertNotIn("merge_exclusions", d.get("write") or {})


class CollectCountTests(unittest.TestCase):
    def test_plain_int(self):
        from v2.plugins.impl.niche_discover import _collect_count
        self.assertEqual(_collect_count({"posts": {"count": 5}}, "posts"), 5)

    def test_legacy_range_uses_max(self):
        from v2.plugins.impl.niche_discover import _collect_count
        self.assertEqual(
            _collect_count({"reels": {"count": {"min": 2, "max": 4}}}, "reels"),
            4)


class CompositeOrFramesTests(unittest.TestCase):
    def test_single_path_not_padded(self):
        """Jeden obraz (np. kolaż rolki) nie dostaje czarnego kafelka."""
        from v2.plugins.impl.niche_discover import _composite_or_frames
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "alone.jpg"
            p.write_bytes(b"fake")
            out = _composite_or_frames(
                [{"image_path": str(p)}], Path(td), 0)
            self.assertEqual(out, [str(p)])

    def test_empty_samples(self):
        from v2.plugins.impl.niche_discover import _composite_or_frames
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(_composite_or_frames([], Path(td), 0), [])


class CaptureReelMultiTests(unittest.TestCase):
    def test_builds_grid_from_four_frames(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy required")
        from v2.plugins.impl import niche_discover as nd

        frames = [np.full((80, 40, 3), i * 40, dtype=np.uint8) for i in range(4)]
        calls = {"i": 0}

        def fake_grab(_ctx):
            i = calls["i"]
            calls["i"] += 1
            return frames[i] if i < len(frames) else None

        ctx = mock.Mock()
        ctx.log = mock.Mock()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(nd, "_grab_frame_bgr", side_effect=fake_grab), \
                mock.patch.object(nd.time, "sleep"):
            out = nd._capture_reel_multi(
                ctx, Path(td) / "reel.jpg", n_frames=4, interval_s=1.0)
            self.assertIsNotNone(out)
            self.assertTrue(Path(out).exists())
            self.assertEqual(calls["i"], 4)
            # 4 klatki → 2×2 (thumb_w=400), potem fit_jpeg_box ≤900×1950
            from PIL import Image
            with Image.open(out) as img:
                self.assertLessEqual(img.size[0], 900)
                self.assertLessEqual(img.size[1], 1950)
                self.assertGreater(img.size[0], 350)

    def test_allows_fewer_than_four_frames(self):
        """1–3 klatki dozwolone; min odstęp nadal ≥1s."""
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy required")
        from v2.plugins.impl import niche_discover as nd

        sleeps = []
        ctx = mock.Mock()
        ctx.log = mock.Mock()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(
                    nd, "_grab_frame_bgr",
                    return_value=np.zeros((60, 30, 3), dtype=np.uint8)), \
                mock.patch.object(nd.time, "sleep", side_effect=lambda s: sleeps.append(s)):
            # n=2 (dawniej wymuszano 4) — jedna przerwa ≥1s
            out = nd._capture_reel_multi(
                ctx, Path(td) / "r.jpg", n_frames=2, interval_s=0.2)
            self.assertIsNotNone(out)
            self.assertTrue(Path(out).exists())
        self.assertEqual(len(sleeps), 1)  # przerwa między 2 klatkami
        self.assertTrue(all(s >= 1.0 for s in sleeps))

    def test_single_frame_capture(self):
        """1 klatka → composite bez paddingu do 4."""
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy required")
        from v2.plugins.impl import niche_discover as nd

        sleeps = []
        ctx = mock.Mock()
        ctx.log = mock.Mock()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(
                    nd, "_grab_frame_bgr",
                    return_value=np.full((80, 40, 3), 90, dtype=np.uint8)), \
                mock.patch.object(nd.time, "sleep", side_effect=lambda s: sleeps.append(s)):
            out = nd._capture_reel_multi(
                ctx, Path(td) / "one.jpg", n_frames=1, interval_s=1.0)
            self.assertIsNotNone(out)
            self.assertTrue(Path(out).exists())
        self.assertEqual(len(sleeps), 0)  # jedna klatka — bez sleep

class PostRegionsTests(unittest.TestCase):
    def test_next_header_below_buttons_sets_end_y(self):
        from v2.plugins.impl.niche_discover import _find_post_regions
        flat = [
            {"resource-id": "com.instagram.android:id/media_group",
             "bounds": "[0,100][1080,900]"},
            {"resource-id": "com.instagram.android:id/row_feed_view_group_buttons",
             "bounds": "[0,900][1080,1000]"},
            {"resource-id": "com.instagram.android:id/caption_text_view",
             "bounds": "[0,1000][1080,1500]", "text": "long caption"},
            {"text": "mniej", "bounds": "[0,1480][100,1520]"},
            {"resource-id": "com.instagram.android:id/row_feed_profile_header",
             "bounds": "[0,1600][1080,1750]"},
            {"resource-id": "com.instagram.android:id/media_group",
             "bounds": "[0,1750][1080,2200]"},
        ]
        reg = _find_post_regions(flat, 2340)
        self.assertEqual(reg["next_header_y"], 1600)
        self.assertLessEqual(reg["end_y"], 1600)
        self.assertEqual(reg["media"][1], 100)
        self.assertGreater(reg["caption_bottom"], 1400)

    def test_no_next_post_uses_caption_bottom(self):
        from v2.plugins.impl.niche_discover import _find_post_regions
        flat = [
            {"resource-id": "com.instagram.android:id/media_group",
             "bounds": "[0,80][1080,800]"},
            {"resource-id": "com.instagram.android:id/row_feed_view_group_buttons",
             "bounds": "[0,800][1080,900]"},
            {"text": "mniej", "bounds": "[50,1200][150,1240]"},
        ]
        reg = _find_post_regions(flat, 2340)
        self.assertIsNone(reg["next_header_y"])
        self.assertGreaterEqual(reg["end_y"], 1240)


class StitchVerticalTests(unittest.TestCase):
    def test_stitches_two_crops(self):
        try:
            from PIL import Image
            import io
        except ImportError:
            self.skipTest("PIL required")
        from v2.core.composite import stitch_vertical_crops

        def _png(color, h=100):
            img = Image.new("RGB", (40, h), color)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "st.jpg"
            res = stitch_vertical_crops(
                [_png((255, 0, 0), 80), _png((0, 255, 0), 60)],
                [(0, 0, 40, 80), (0, 0, 40, 60)],
                out,
            )
            self.assertIsNotNone(res)
            with Image.open(res) as img:
                self.assertEqual(img.size, (40, 140))


class FitJpegBoxTests(unittest.TestCase):
    def test_fits_small_jpeg_into_box(self):
        try:
            from PIL import Image
            import io
        except ImportError:
            self.skipTest("PIL required")
        from v2.core.composite import fit_jpeg_box

        # mały portrait — skala ≤2.0 → 200×300 (nie całe pudełko 900×1950)
        img = Image.new("RGB", (100, 150), (10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        raw = buf.getvalue()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fit.jpg"
            res = fit_jpeg_box(raw, out, max_w=900, max_h=1950)
            self.assertIsNotNone(res)
            with Image.open(res) as got:
                self.assertEqual(got.size, (200, 300))
                self.assertEqual(got.format, "JPEG")

    def test_downscales_large_into_box(self):
        try:
            from PIL import Image
            import io
        except ImportError:
            self.skipTest("PIL required")
        from v2.core.composite import fit_jpeg_box

        # ten sam stosunek boków co 900×1950 — contain → dokładnie pudełko
        img = Image.new("RGB", (1800, 3900), (40, 50, 60))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "down.jpg"
            res = fit_jpeg_box(buf.getvalue(), out, max_w=900, max_h=1950)
            self.assertIsNotNone(res)
            with Image.open(res) as got:
                self.assertEqual(got.size, (900, 1950))


class SelectGridTabTests(unittest.TestCase):
    def test_reveal_skips_scroll_when_grid_visible(self):
        # tabs + kafelek w hierarchy → _reveal_profile_grid nie woła swipe
        from v2.plugins.impl import niche_discover as nd
        flat = [
            {"resource-id": "com.instagram.android:id/profile_tab_icon_view",
             "content-desc": "Widok siatki", "bounds": "[0,1442][270,1568]",
             "package": "com.instagram.android"},
            {"resource-id": "com.instagram.android:id/image_button",
             "content-desc": "wiersz 1 kolumna 1",
             "bounds": "[0,1600][360,1960]",
             "package": "com.instagram.android"},
        ]
        ctx = mock.Mock()
        ctx.device.hierarchy.return_value = {"ignored": True}
        ctx.ht.swipe = mock.Mock()
        ctx.log = mock.Mock()
        with mock.patch.object(nd, "flatten_hierarchy", return_value=flat):
            self.assertTrue(nd._profile_grid_visible(ctx))
            nd._reveal_profile_grid(ctx)
        ctx.ht.swipe.assert_not_called()
        ctx.log.assert_any_call(
            "niche_discover.grid_already_visible", scroll=False)

    def test_reveal_scrolls_when_grid_missing(self):
        # brak kafelków → swipe w dół, żeby odsłonić siatkę
        from v2.plugins.impl import niche_discover as nd
        flat = [
            {"resource-id": "com.instagram.android:id/profile_tab_icon_view",
             "content-desc": "Widok siatki", "bounds": "[0,1442][270,1568]",
             "package": "com.instagram.android"},
        ]
        ctx = mock.Mock()
        ctx.device.hierarchy.return_value = {}
        ctx.account.device = {"_hardware": {"resolution": (1080, 2340)}}
        ctx.ht.swipe = mock.Mock()
        ctx.log = mock.Mock()
        with mock.patch.object(nd, "flatten_hierarchy", return_value=flat), \
                mock.patch.object(nd.time, "sleep"):
            self.assertFalse(nd._profile_grid_visible(ctx))
            nd._reveal_profile_grid(ctx)
        ctx.ht.swipe.assert_called_once()
        ctx.log.assert_any_call("niche_discover.grid_revealed", scroll=True)

    def test_taps_rolki_tab(self):
        from v2.plugins.impl import niche_discover as nd
        flat = [
            {"resource-id": "com.instagram.android:id/profile_tab_icon_view",
             "content-desc": "Widok siatki", "bounds": "[0,1442][270,1568]",
             "clickable": True},
            {"resource-id": "com.instagram.android:id/profile_tab_icon_view",
             "content-desc": "Rolki", "bounds": "[270,1442][540,1568]",
             "clickable": True},
        ]
        ctx = mock.Mock()
        ctx.device.hierarchy.return_value = {"ignored": True}
        ctx.ht.tap = mock.Mock()
        ctx.log = mock.Mock()
        with mock.patch.object(nd, "flatten_hierarchy", return_value=flat), \
                mock.patch.object(nd.time, "sleep"):
            ok = nd._select_profile_grid_tab(ctx, "reels")
        self.assertTrue(ok)
        ctx.ht.tap.assert_called_once()
        # środek bounds Rolki: x=(270+540)//2=405
        self.assertEqual(ctx.ht.tap.call_args[0][:2], (405, 1505))

    def test_reels_fallback_by_index(self):
        # gdy desc pusty — druga ikona od lewej = Rolki
        from v2.plugins.impl import niche_discover as nd
        flat = [
            {"resource-id": "com.instagram.android:id/profile_tab_icon_view",
             "content-desc": "", "bounds": "[0,1442][270,1568]"},
            {"resource-id": "com.instagram.android:id/profile_tab_icon_view",
             "content-desc": "", "bounds": "[270,1442][540,1568]"},
            {"resource-id": "com.instagram.android:id/profile_tab_icon_view",
             "content-desc": "", "bounds": "[540,1442][810,1568]"},
        ]
        ctx = mock.Mock()
        ctx.device.hierarchy.return_value = {}
        ctx.ht.tap = mock.Mock()
        ctx.log = mock.Mock()
        with mock.patch.object(nd, "flatten_hierarchy", return_value=flat), \
                mock.patch.object(nd.time, "sleep"):
            ok = nd._select_profile_grid_tab(ctx, "reels")
        self.assertTrue(ok)
        self.assertEqual(ctx.ht.tap.call_args[0][:2], (405, 1505))

    def test_ensure_reels_scrolls_then_taps(self):
        from v2.plugins.impl import niche_discover as nd
        ctx = mock.Mock()
        with mock.patch.object(nd, "_return_to_profile_main", return_value=True), \
                mock.patch.object(nd, "_reels_tab_is_selected",
                                  side_effect=[False, False, True]), \
                mock.patch.object(nd, "_dump_profile_tabs",
                                  return_value=[{"content-desc": "x"}]), \
                mock.patch.object(nd, "_select_profile_grid_tab",
                                  return_value=True) as sel, \
                mock.patch.object(nd.time, "sleep"):
            ok = nd._ensure_reels_tab(ctx, baseline="profile_other", attempts=3)
        self.assertTrue(ok)
        self.assertGreaterEqual(sel.call_count, 1)

    def test_return_to_profile_stops_when_tabs_visible(self):
        # nie wolno robić kolejnego BACK gdy tabs już w hierarchy
        from v2.plugins.impl import niche_discover as nd
        ctx = mock.Mock()
        ctx.device.press_back = mock.Mock()
        tabs = [{"content-desc": "Rolki", "bounds": "[270,0][540,100]",
                 "selected": False}]
        with mock.patch.object(nd, "_dump_profile_tabs", return_value=tabs):
            ok = nd._return_to_profile_main(ctx, "profile_other")
        self.assertTrue(ok)
        ctx.device.press_back.assert_not_called()

    def test_return_to_profile_one_back_from_posty(self):
        from v2.plugins.impl import niche_discover as nd
        ctx = mock.Mock()
        ctx.device.press_back = mock.Mock()
        ctx.humanizer.pause = mock.Mock()
        seq = [[], [{"content-desc": "Rolki", "selected": False,
                     "bounds": "[270,0][540,100]"}]]
        with mock.patch.object(nd, "_dump_profile_tabs", side_effect=seq), \
                mock.patch.object(nd.time, "sleep"):
            ok = nd._return_to_profile_main(ctx, "profile_other")
        self.assertTrue(ok)
        self.assertEqual(ctx.device.press_back.call_count, 1)


class OneshotWiringTests(unittest.TestCase):
    """Panel → SessionManager → CLI --plugin → JobRunner (bez 501 stub)."""

    def test_api_run_plugin_nie_ma_stub_501(self):
        import inspect
        from v2.panel.server import api_run_plugin, SessionManager
        src = inspect.getsource(api_run_plugin)
        self.assertNotIn("niepodpięty", src)
        self.assertIn('plugin_oneshot="niche_discover"', src)
        start_src = inspect.getsource(SessionManager.start)
        self.assertIn("--plugin=", start_src)

    def test_jobrunner_oneshot_wymusza_enabled(self):
        from v2.plugins.runner import JobRunner
        # syntetyczna kolejka: overrides.enabled=True + flaga _oneshot
        tasks_holder = {}

        class FakeCtx:
            session_id = "t"
            def log(self, *a, **k): pass
            class navigator:
                @staticmethod
                def ensure(*a, **k): return True

        runner = JobRunner(ctx=FakeCtx(), data_root=Path("."),
                           plugin_oneshot="niche_discover")
        # przechwyć budowę tasks bez prologu/telefonu
        orig_prologue = runner._prologue
        orig_run_task = runner._run_task
        orig_epilogue = runner._epilogue

        def capture_run(account, task, n, total):
            tasks_holder["task"] = task
            return {"status": "ok", "items_done": 0, "stopped_by": "items",
                    "id": task["id"], "plugin": task["plugin"],
                    "stats": {}, "error": None}

        runner._prologue = lambda: None
        runner._epilogue = lambda: None
        runner._run_task = capture_run
        runner._checkpoint = lambda *a, **k: None
        runner._banner = lambda *a, **k: None
        runner._summary = lambda *a, **k: {"ok": True, "tasks_ok": 1,
            "tasks_skipped": 0, "tasks_failed": 0, "items_done": 0}

        load_all()
        runner.run("demo")
        t = tasks_holder["task"]
        self.assertEqual(t["plugin"], "niche_discover")
        self.assertTrue(t.get("_oneshot"))
        self.assertEqual(t.get("overrides", {}).get("enabled"), True)


class ReelCaptionDumpFilterTests(unittest.TestCase):
    """Filtr dumpa: nie bierz like_count / UFI jako opisu rolki."""

    def test_strict_ignores_like_count_fallback(self):
        from v2.plugins.impl.niche_discover import pick_caption_from_flat
        # jak live dump: pusty clips_caption_component + długi like_count
        flat = [
            {"resource-id": "com.instagram.android:id/clips_caption_component",
             "text": "", "content-desc": ""},
            {"resource-id": "com.instagram.android:id/like_count",
             "text": "Liczba polubień: 2525. Wyświetl polubienia.",
             "content-desc": "Liczba polubień: 2525. Wyświetl polubienia."},
            {"resource-id": "com.instagram.android:id/comment_count",
             "text": "Liczba komentarzy: 29. Wyświetl komentarze.",
             "content-desc": "Liczba komentarzy: 29. Wyświetl komentarze."},
        ]
        self.assertEqual(pick_caption_from_flat(flat, strict=True), "")
        # non-strict też nie wolno brać UFI
        self.assertEqual(pick_caption_from_flat(flat, strict=False), "")

    def test_prefers_caption_text_view(self):
        from v2.plugins.impl.niche_discover import pick_caption_from_flat
        flat = [
            {"resource-id": "com.instagram.android:id/clips_caption_component",
             "text": "", "content-desc": ""},
            {"resource-id": "com.instagram.android:id/caption_text_view",
             "text": "Pełny opis rolki o karierze i CV #job",
             "content-desc": ""},
            {"resource-id": "com.instagram.android:id/like_count",
             "text": "Liczba polubień: 99. Wyświetl polubienia."},
        ]
        out = pick_caption_from_flat(flat, strict=True)
        self.assertIn("Pełny opis", out)
        self.assertNotIn("polubień", out)

    def test_rejects_ufi_text_even_on_caption_id(self):
        from v2.plugins.impl.niche_discover import (
            pick_caption_from_flat, _is_ufi_count_text,
        )
        self.assertTrue(_is_ufi_count_text(
            "Liczba polubień: 10. Wyświetl polubienia."))
        flat = [
            {"resource-id": "com.instagram.android:id/caption_text_view",
             "text": "Liczba komentarzy: 3. Wyświetl komentarze."},
        ]
        self.assertEqual(pick_caption_from_flat(flat, strict=True), "")

    def test_ignores_own_profile_insights_stats(self):
        from v2.plugins.impl.niche_discover import (
            _is_insights_stats_text, sanitize_content_caption,
            _caption_text_ok, pick_caption_from_flat,
        )
        raw = "7,6 tys. · Wyświetl statystyki"
        self.assertTrue(_is_insights_stats_text(raw))
        self.assertEqual(sanitize_content_caption(raw), "")
        self.assertFalse(_caption_text_ok(raw))
        flat = [
            {"resource-id": "com.instagram.android:id/caption_text_view",
             "text": raw},
        ]
        self.assertEqual(pick_caption_from_flat(flat, strict=True), "")
        self.assertEqual(
            sanitize_content_caption("Prawdziwy opis z wakacji #travel"),
            "Prawdziwy opis z wakacji #travel")

    def test_composer_alone_is_not_expanded_panel(self):
        # UFI ma comment_composer — to NIE rozwinięty panel opisu
        from v2.plugins.impl.niche_discover import caption_panel_expanded
        flat = [
            {"resource-id": "com.instagram.android:id/comment_composer_text_view"},
            {"resource-id": "com.instagram.android:id/clips_caption_component",
             "text": ""},
            {"resource-id": "com.instagram.android:id/like_count",
             "text": "Liczba polubień: 1."},
        ]
        self.assertFalse(caption_panel_expanded(flat))
        flat2 = flat + [
            {"resource-id": "com.instagram.android:id/layout_comment_thread"},
        ]
        self.assertTrue(caption_panel_expanded(flat2))

    def test_caption_tap_prefers_caption_id_bounds(self):
        from v2.plugins.impl.niche_discover import find_reel_caption_tap_target
        flat = [
            {"resource-id": "com.instagram.android:id/clips_caption_component",
             "text": "", "bounds": "[40,1800][700,2100]"},
            {"resource-id": "com.instagram.android:id/like_count",
             "text": "Liczba polubień: 9.", "bounds": "[40,2100][400,2200]"},
        ]
        t = find_reel_caption_tap_target(flat, w=1080, h=2340)
        self.assertIsNotNone(t)
        self.assertEqual(t["source"], "clips_caption_component")
        # środek [40,1800]–[700,2100]
        self.assertEqual(t["x"], (40 + 700) // 2)
        self.assertEqual(t["y"], (1800 + 2100) // 2)

    def test_caption_tap_fallback_zone_not_ufi_right(self):
        from v2.plugins.impl.niche_discover import find_reel_caption_tap_target
        t = find_reel_caption_tap_target([], w=1080, h=2340)
        self.assertEqual(t["source"], "fallback_zone")
        self.assertLess(t["x"], int(1080 * 0.75))
        self.assertGreater(t["y"], int(2340 * 0.75))

    def test_bio_looks_truncated_markers(self):
        # skrócony opis / bio z „…więcej" — wymaga expand przed zapisem
        from v2.plugins.impl.niche_discover import _bio_looks_truncated
        self.assertTrue(_bio_looks_truncated("R… więcej"))
        self.assertTrue(_bio_looks_truncated("tekst …więcej"))
        self.assertTrue(_bio_looks_truncated("cut off..."))
        self.assertTrue(_bio_looks_truncated("See this … more"))
        # teaser bez markera (Compose) — NIE wygląda na truncated,
        # ale guarded i tak zawsze robi tap+expand przed dumpem
        self.assertFalse(_bio_looks_truncated(
            "inez_cialowicz udostępnia notatkę:"))
        self.assertFalse(_bio_looks_truncated(
            "Pełny opis rolki bez skrótu."))

    def test_extract_reel_caption_from_recyclerview_viewgroup(self):
        # ścieżka jak na żywym viewerze: RecyclerView → ViewGroup → ViewGroup
        from v2.plugins.impl.niche_discover import (
            extract_reel_caption_from_tree, find_reel_caption_tap_target,
        )

        def vg(text, bounds, children=None):
            return {"properties": {
                "class": "android.view.ViewGroup", "text": text,
                "resource-id": "", "bounds": bounds,
                "content-desc": "",
            }, "children": children or []}

        tree = {
            "properties": {"class": "android.widget.FrameLayout",
                           "bounds": "[0,0][1080,2340]"},
            "children": [{
                "properties": {
                    "class": "androidx.recyclerview.widget.RecyclerView",
                    "bounds": "[0,0][1080,2340]", "resource-id": "",
                },
                "children": [vg("", "[0,1600][800,2200]", children=[
                    vg("autor_x", "[40,1650][600,1720]"),
                    vg("Jak zwiększyć zasięgi rolek w 2026 #reels "
                       "Sprawdź bio 👇", "[40,1730][700,2100]"),
                ])],
            }],
        }
        cap = extract_reel_caption_from_tree(tree)
        self.assertIn("zasięgi rolek", cap or "")
        self.assertNotIn("Liczba polubień", cap or "")

        flat = []  # bez CAPTION_IDS
        t = find_reel_caption_tap_target(flat, w=1080, h=2340, tree=tree)
        self.assertEqual(t["source"], "rv_viewgroup")
        self.assertIn("zasięgi", t.get("preview") or "")


class ObservedHashtagsPriorityTests(unittest.TestCase):
    """observed z captionów na początku listy — przeżywają limit normalize."""

    def test_observed_survive_hashtags_limit(self):
        # jak _finalize_account: obs + AI, potem normalize z limitem
        obs = ["#real1", "#real2", "#real3"]
        ai_tags = [f"#gen{i}" for i in range(10)]
        merged = {"description": "x", "audience": "y",
                  "keywords": ["nisza AI"], "hashtags": list(obs) + ai_tags}
        out = normalize_niche(merged, hashtags_limit=5, keywords_limit=5)
        self.assertEqual(out["hashtags"][:3], obs)
        self.assertEqual(len(out["hashtags"]), 5)

    def test_finalize_account_prepending(self):
        from v2.plugins.impl.niche_discover import NicheDiscover
        plug = NicheDiscover()
        ctx = mock.Mock()
        ctx.account.raw = {}
        ctx.log = mock.Mock()
        stats = {"ai_account": 0}
        fake = {
            "description": "opis", "audience": "aud",
            "keywords": ["job search"],  # lustro #jobsearch → odpadnie
            "hashtags": ["#generated"],
        }
        with mock.patch("v2.plugins.impl.niche_discover.AiVision") as AV:
            AV.return_value.run.return_value = fake
            result = plug._finalize_account(
                ctx, {}, {}, None, "", {},
                "own", "own", "me",
                {"keywords_count": 8, "hashtags_count": 8},
                stats, observed_hashtags=["#jobsearch", "#cv"],
            )
        self.assertEqual(result["hashtags"][:2], ["#jobsearch", "#cv"])
        self.assertIn("#generated", result["hashtags"])


class ScrcpyMaxSizeTests(unittest.TestCase):
    def test_phone_a_and_cli_default_900(self):
        import inspect
        from v2 import cli as cli_mod
        cfg = json.loads(Path(
            r"c:\Users\mz\Desktop\prog\bot\bot-czerwiec\bot\v2"
            r"\config\devices\phone_A.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(cfg["scrcpy"]["max_size"], 900)
        src = inspect.getsource(cli_mod.build_context)
        self.assertIn("max_size", src)
        self.assertIn("900", src)


class FrameSourceTests(unittest.TestCase):
    def test_scale_crop_to_image(self):
        from v2.plugins.impl.niche_discover import _scale_crop_to_image
        # urządzenie 1080×2340 → kadr scrcpy 415×900 (≈ max_size 900)
        crop = _scale_crop_to_image(
            (0, 234, 1080, 1170), (1080, 2340), (415, 900))
        self.assertEqual(crop[0], 0)
        self.assertEqual(crop[2], 415)
        self.assertAlmostEqual(crop[1] / 900, 234 / 2340, places=2)

    def test_capture_screen_prefers_scrcpy(self):
        try:
            import numpy as np
            import cv2
        except ImportError:
            self.skipTest("numpy/cv2 required")
        from v2.plugins.impl import niche_discover as nd

        frame = np.zeros((90, 45, 3), dtype=np.uint8)
        frame[:] = (10, 20, 30)
        fb = mock.Mock()
        fb.is_live.return_value = True
        fb.get_frame.return_value = frame
        ctx = mock.Mock()
        ctx.fb = fb
        ctx.device.screenshot.side_effect = AssertionError("nie wołaj HTTP")
        ctx.log = mock.Mock()

        raw, source = nd._capture_screen_png(ctx)
        self.assertEqual(source, "scrcpy")
        self.assertIsNotNone(raw)
        ctx.device.screenshot.assert_not_called()
        # PNG da się zdekodować
        arr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(arr)
        self.assertEqual(arr.shape[:2], (90, 45))

    def test_capture_screen_falls_back_to_screenshot(self):
        try:
            from PIL import Image
            import io
        except ImportError:
            self.skipTest("PIL required")
        from v2.plugins.impl import niche_discover as nd

        img = Image.new("RGB", (20, 40), (1, 2, 3))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()

        ctx = mock.Mock()
        ctx.fb = None
        ctx.device.screenshot.return_value = png
        ctx.log = mock.Mock()

        raw, source = nd._capture_screen_png(ctx)
        self.assertEqual(source, "screenshot")
        self.assertEqual(raw, png)


if __name__ == "__main__":
    unittest.main()
