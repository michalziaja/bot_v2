"""Testy katalogu pluginów i warstwy zapisu ustawień/kolejki.

Katalog jest deklaratywny (bez logiki wykonania), więc testujemy to, co może
się realnie zepsuć: spójność schematów, poprawność merge'a trójstopniowego
i walidację kolejki.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from v2.plugins.catalog import PLUGINS, all_defaults, get_plugin, plugin_ids
from v2.plugins import store


KNOWN_TYPES = {
    "bool", "num", "text", "textarea", "select", "range", "percent",
    "tags", "lines", "multiselect", "chance", "json", "hour_caps",
    "likes_range", "like_decision",
}


LEAF_SHAPES = ({"min", "max"}, {"mode", "value"})


def _walk(d, prefix=()):
    """Wszystkie ścieżki liści w słowniku defaultów.

    `range` i `chance` są liśćmi mimo że to słowniki — odpowiada im JEDNA
    kontrolka w formularzu, nie osobne pole na klucz.
    """
    for k, v in d.items():
        p = (*prefix, k)
        if isinstance(v, dict) and v and set(v) not in LEAF_SHAPES:
            yield from _walk(v, p)
        else:
            yield p


def _field_paths(f: dict) -> list[tuple]:
    """Ścieżki pokrywane przez pole (likes_range obejmuje też max_key)."""
    if f.get("ux_only"):
        return []
    paths = [tuple(f["key"])]
    if f.get("type") == "likes_range" and f.get("max_key"):
        paths.append(tuple(f["max_key"]))
    return paths


class TestCatalogIntegrity(unittest.TestCase):
    def test_ids_unique(self):
        ids = plugin_ids()
        self.assertEqual(len(ids), len(set(ids)), "duplikat id pluginu")

    def test_required_meta(self):
        for p in PLUGINS:
            for key in ("id", "label", "group", "status", "entry_screen",
                        "exit_screen", "desc", "sections", "defaults"):
                self.assertIn(key, p, f"{p.get('id')}: brak pola {key}")
            self.assertIn(p["status"], ("ready", "partial", "planned"))

    def test_field_types_known(self):
        """Nieznany typ pola = puste miejsce w formularzu zamiast kontrolki."""
        for p in PLUGINS:
            for s in p["sections"]:
                for f in s["fields"]:
                    self.assertIn(f["type"], KNOWN_TYPES,
                                  f"{p['id']}.{'.'.join(f['key'])}: nieznany typ {f['type']}")

    def test_every_field_has_label_and_key(self):
        for p in PLUGINS:
            for s in p["sections"]:
                for f in s["fields"]:
                    self.assertTrue(f.get("label"), f"{p['id']}: pole bez label")
                    self.assertTrue(f.get("key"), f"{p['id']}: pole bez key")

    def test_no_duplicate_field_paths(self):
        """Dwa pola o tej samej ścieżce = jedno nadpisuje drugie przy zapisie."""
        for p in PLUGINS:
            seen = set()
            for s in p["sections"]:
                for f in s["fields"]:
                    if f.get("ux_only"):
                        continue
                    for path in _field_paths(f):
                        self.assertNotIn(path, seen,
                                         f"{p['id']}: zduplikowana ścieżka {path}")
                        seen.add(path)

    def test_every_field_has_default(self):
        """Pole bez wartości domyślnej renderuje się puste i po zapisie
        wstawia null do configu. Pola ux_only mapują się na inne klucze."""
        for p in PLUGINS:
            defaults = p["defaults"]
            for s in p["sections"]:
                for f in s["fields"]:
                    if f.get("ux_only"):
                        continue
                    for path in _field_paths(f):
                        cur = defaults
                        for k in path:
                            self.assertIsInstance(
                                cur, dict,
                                f"{p['id']}: brak defaultu dla {'.'.join(path)}")
                            self.assertIn(
                                k, cur,
                                f"{p['id']}: brak defaultu dla {'.'.join(path)}")
                            cur = cur[k]

    def test_every_default_has_field(self):
        """Odwrotnie: wartość w defaults bez pola w formularzu jest
        nieedytowalna z panelu — czyli zapomniana."""
        for p in PLUGINS:
            field_paths = set()
            for s in p["sections"]:
                for f in s["fields"]:
                    field_paths.update(_field_paths(f))
            # like_decision pokrywa ai.enabled + chance — dolicz te ścieżki
            for s in p["sections"]:
                for f in s["fields"]:
                    if f.get("type") != "like_decision":
                        continue
                    field_paths.add(tuple(f["ai_key"]))
                    field_paths.add(tuple(f["chance_key"]))
            for path in _walk(p["defaults"]):
                covered = any(path[:len(fp)] == fp for fp in field_paths)
                self.assertTrue(covered,
                                f"{p['id']}: default {'.'.join(path)} bez pola w formularzu")

    def test_multiselect_defaults_are_subset_of_options(self):
        for p in PLUGINS:
            for s in p["sections"]:
                for f in s["fields"]:
                    if f["type"] != "multiselect":
                        continue
                    cur = p["defaults"]
                    for k in f["key"]:
                        cur = cur[k]
                    self.assertTrue(set(cur) <= set(f["options"]),
                                    f"{p['id']}.{'.'.join(f['key'])}: default spoza options")

    def test_select_defaults_are_valid_options(self):
        for p in PLUGINS:
            for s in p["sections"]:
                for f in s["fields"]:
                    if f["type"] != "select":
                        continue
                    cur = p["defaults"]
                    for k in f["key"]:
                        cur = cur[k]
                    self.assertIn(cur, f["options"],
                                  f"{p['id']}.{'.'.join(f['key'])}: default spoza options")

    def test_chance_defaults_have_explicit_mode(self):
        """`chance` musi mieć jawny `mode`, nie wybór klucza (p vs formula).

        Przy wariancie z wyborem klucza zmiana trybu w panelu zostawiała po
        głębokim scaleniu OBA klucze naraz i nie dało się stwierdzić, który
        obowiązuje.
        """
        for p in PLUGINS:
            for s in p["sections"]:
                for f in s["fields"]:
                    if f["type"] != "chance":
                        continue
                    cur = p["defaults"]
                    for k in f["key"]:
                        cur = cur[k]
                    self.assertEqual(set(cur), {"mode", "value"},
                                     f"{p['id']}.{'.'.join(f['key'])}: zły kształt chance")
                    self.assertIn(cur["mode"], ("p", "formula"))

    def test_content_search_ai_inputs_visible_like_comments_hidden(self):
        """AI wejścia widoczne przy like_decision; like_comments nadal ukryte."""
        from v2.plugins.catalog import get_plugin
        pl = get_plugin("content_search")
        by_key = {}
        for sec in pl["sections"]:
            for f in sec.get("fields") or []:
                by_key[tuple(f["key"])] = f
        for key in (("ai", "use_frames"), ("ai", "read_caption"),
                    ("ai", "read_top_comments"), ("ai", "frames")):
            self.assertIn(key, by_key, f"brak pola {key}")
            self.assertFalse(by_key[key].get("hidden"),
                             f"{key} nie powinno być hidden")
        # enabled steruje like_decision — ukryte w formularzu
        self.assertTrue(by_key[("ai", "enabled")].get("hidden"))
        # nadal niepodpięte
        self.assertTrue(by_key[("comments", "like_comments", "enabled")].get("hidden"))
        self.assertTrue(by_key[("gate", "skip_authors")].get("hidden"))

    def test_chance_mode_switch_does_not_leave_stale_value(self):
        """Zmiana trybu przez merge zastępuje komplet, nie dokleja."""
        base = {"chance": {"mode": "p", "value": 0.5}}
        out = store.deep_merge(base, {"chance": {"mode": "formula",
                                                 "value": "score/100"}})
        self.assertEqual(out["chance"], {"mode": "formula", "value": "score/100"})

    def test_ranges_have_min_le_max(self):
        for p in PLUGINS:
            for s in p["sections"]:
                for f in s["fields"]:
                    if f["type"] != "range":
                        continue
                    cur = p["defaults"]
                    for k in f["key"]:
                        cur = cur[k]
                    self.assertLessEqual(cur["min"], cur["max"],
                                         f"{p['id']}.{'.'.join(f['key'])}: min > max")

    def test_all_plugins_disabled_by_default(self):
        """Nowe konto nie może wystartować z włączonymi akcjami."""
        for pid, d in all_defaults().items():
            self.assertFalse(d["enabled"], f"{pid}: włączony domyślnie")

    def test_get_plugin_unknown_raises(self):
        with self.assertRaises(KeyError):
            get_plugin("nie_ma_takiego")


class TestDeepMerge(unittest.TestCase):
    def test_override_wins(self):
        out = store.deep_merge({"a": 1, "b": {"c": 2}}, {"b": {"c": 9}})
        self.assertEqual(out, {"a": 1, "b": {"c": 9}})

    def test_missing_keys_preserved(self):
        out = store.deep_merge({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": 9}})
        self.assertEqual(out["b"]["d"], 3)

    def test_lists_replaced_not_concatenated(self):
        """allow_verdict: ["match"] ma znaczyć dokładnie to, a nie dopisanie."""
        out = store.deep_merge({"v": ["match", "partial"]}, {"v": ["match"]})
        self.assertEqual(out["v"], ["match"])

    def test_source_not_mutated(self):
        base = {"a": {"b": 1}}
        store.deep_merge(base, {"a": {"b": 2}})
        self.assertEqual(base["a"]["b"], 1)

    def test_null_does_not_override_default(self):
        """Panel bywa zapisuje value:null — null = brak klucza, default zostaje."""
        base = {"chance": {"mode": "p", "value": 0.15}}
        out = store.deep_merge(base, {"chance": {"value": None}})
        self.assertEqual(out["chance"]["value"], 0.15)
        self.assertEqual(out["chance"]["mode"], "p")

    def test_null_scalar_skipped(self):
        out = store.deep_merge({"enabled": True, "x": 1}, {"enabled": None, "x": 2})
        self.assertTrue(out["enabled"])
        self.assertEqual(out["x"], 2)


class TestSettingsAndQueue(unittest.TestCase):
    """Zapis/odczyt na tymczasowym drzewie configu."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "accounts").mkdir()
        (root / "devices").mkdir()
        (root / "accounts" / "acc1.json").write_text(json.dumps({
            "meta": {"account_id": "acc1", "ig_username": "u"},
            "device": {"device_alias": "dev1", "app_clone_package": "com.instagram.android"},
            "humanizer": {"profile_name": "default"},
        }), encoding="utf-8")
        (root / "devices" / "dev1.json").write_text(json.dumps({
            "adb_id": "x", "resolution": [1080, 2340],
            "uiautodev": {"base_url": "http://127.0.0.1:20242"},
        }), encoding="utf-8")
        self._orig_root = store.CONFIG_ROOT
        store.CONFIG_ROOT = root

    def tearDown(self):
        store.CONFIG_ROOT = self._orig_root
        self._tmp.cleanup()

    def test_read_settings_fills_defaults(self):
        s = store.read_settings("acc1")
        self.assertEqual(set(s), set(plugin_ids()))
        self.assertEqual(s["reels_niche"]["ai"]["frames"], 2)
        self.assertEqual(s["reels_niche"]["ai"]["frame_interval_s"], 0.5)
        self.assertTrue(s["reels_niche"]["ai"]["use_frames"])
        self.assertTrue(s["reels_niche"]["ai"]["read_caption"])
        # content_search: AI off by default, ale flagi wejść jak w reels_niche
        cs_ai = s["content_search"]["ai"]
        self.assertFalse(cs_ai["enabled"])
        self.assertTrue(cs_ai["use_frames"])
        self.assertTrue(cs_ai["read_caption"])
        self.assertFalse(cs_ai["read_top_comments"])
        self.assertEqual(cs_ai["frames"], 2)

    def test_write_settings_stores_only_delta(self):
        """Config nie ma puchnąć o komplet defaultów przy każdym zapisie."""
        store.write_settings("acc1", {"reels_niche": {"enabled": True,
                                                      "gate": {"min_likes": 250}}})
        raw = json.loads((store.CONFIG_ROOT / "accounts" / "acc1.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(raw["plugins"], {"reels_niche": {"enabled": True,
                                                          "gate": {"min_likes": 250}}})
        s = store.read_settings("acc1")
        self.assertEqual(s["reels_niche"]["gate"]["min_likes"], 250)
        self.assertEqual(s["reels_niche"]["gate"]["min_comments"], 35)  # z defaultów

    def test_write_settings_drops_full_payload_to_delta(self):
        """Panel wysyła komplet pól — w pliku ma zostać sama różnica."""
        full = store.read_settings("acc1")["reels_niche"]
        full["gate"]["min_likes"] = 250
        store.write_settings("acc1", {"reels_niche": full})
        raw = json.loads((store.CONFIG_ROOT / "accounts" / "acc1.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(raw["plugins"]["reels_niche"], {"gate": {"min_likes": 250}})

    def test_write_settings_removes_entry_when_all_defaults(self):
        store.write_settings("acc1", {"reels_niche": {"gate": {"min_likes": 250}}})
        back = store.read_settings("acc1")["reels_niche"]
        store.write_settings("acc1", {"reels_niche": {**back, "gate":
                             {**back["gate"], "min_likes": 100}}})
        raw = json.loads((store.CONFIG_ROOT / "accounts" / "acc1.json")
                         .read_text(encoding="utf-8"))
        self.assertNotIn("reels_niche", raw["plugins"])

    def test_stale_key_from_removed_field_is_dropped(self):
        """Pole wycofane z katalogu (np. dawne `excluded_kills`) nie może
        wracać z pliku — formularz go nie pokazuje, więc nie dałoby się go
        usunąć z panelu."""
        path = store.CONFIG_ROOT / "accounts" / "acc1.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["plugins"] = {"profile_interact": {"grid_ai": {
            "excluded_kills": True, "min_niche_tiles": 4}}}
        path.write_text(json.dumps(cfg), encoding="utf-8")

        s = store.read_settings("acc1")["profile_interact"]["grid_ai"]
        self.assertNotIn("excluded_kills", s)
        self.assertEqual(s["min_niche_tiles"], 4)   # znany klucz zachowany

    def test_reshaped_value_falls_back_to_default(self):
        """Stary kształt `chance` ({"p": …}) nie może się zlepić z nowym
        ({"mode","value"}) — inaczej nie wiadomo, który wariant obowiązuje."""
        path = store.CONFIG_ROOT / "accounts" / "acc1.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["plugins"] = {"profile_interact": {"follow": {"chance": {"p": 0.2}}}}
        path.write_text(json.dumps(cfg), encoding="utf-8")

        chance = store.read_settings("acc1")["profile_interact"]["follow"]["chance"]
        self.assertEqual(set(chance), {"mode", "value"})
        self.assertNotIn("p", chance)

    def test_write_settings_rejects_unknown_plugin(self):
        with self.assertRaises(ValueError):
            store.write_settings("acc1", {"nie_ma": {}})

    def test_resolve_three_levels(self):
        store.write_settings("acc1", {"reels_niche": {"gate": {"min_likes": 250}}})
        r = store.resolve("acc1", "reels_niche", {"gate": {"min_likes": 400}})
        self.assertEqual(r["gate"]["min_likes"], 400)     # override
        self.assertEqual(r["gate"]["min_comments"], 35)   # default
        r2 = store.resolve("acc1", "reels_niche")
        self.assertEqual(r2["gate"]["min_likes"], 250)    # config konta

    def test_queue_roundtrip_and_id_assignment(self):
        store.write_queue("acc1", {"queue": [
            {"plugin": "feed_browser"},
            {"plugin": "reels_niche", "id": "moje_id"},
        ]})
        q = store.read_queue("acc1")
        self.assertEqual(len(q["queue"]), 2)
        self.assertTrue(q["queue"][0]["id"])              # nadane automatycznie
        self.assertEqual(q["queue"][1]["id"], "moje_id")  # zachowane

    def test_queue_rejects_unknown_plugin(self):
        with self.assertRaises(ValueError):
            store.write_queue("acc1", {"queue": [{"plugin": "nie_ma"}]})

    def test_queue_rejects_non_dict_overrides(self):
        with self.assertRaises(ValueError):
            store.write_queue("acc1", {"queue": [
                {"plugin": "feed_browser", "overrides": "nie_jest_obiektem"}]})

    def test_missing_queue_file_returns_empty(self):
        q = store.read_queue("acc1")
        self.assertEqual(q["queue"], [])
        self.assertEqual(q["on_task_error"], "continue")

    def test_resolve_queue_applies_task_budget(self):
        store.write_queue("acc1", {"queue": [
            {"plugin": "feed_browser", "budget": {"max_duration_min": 6}}]})
        r = store.resolve_queue("acc1")
        self.assertEqual(r[0]["resolved"]["budget"]["max_duration_min"], 6)
        # reszta budżetu z defaultów, nie skasowana przez częściowy override
        self.assertIn("max_items", r[0]["resolved"]["budget"])

    def test_resolve_queue_flags_disabled_plugin(self):
        store.write_queue("acc1", {"queue": [{"plugin": "feed_browser"}]})
        r = store.resolve_queue("acc1")
        self.assertFalse(r[0]["plugin_enabled_for_account"])

    # ---- nazwane scenariusze (presety kolejki) ----
    def test_no_presets_by_default(self):
        """`default` jest zawsze na liście — to zarezerwowana nazwa domyślnego
        scenariusza, nie osobny byt "kolejka" (scalone 2026-08-03)."""
        self.assertEqual(store.list_queue_presets("acc1"), [store.DEFAULT_SCENARIO])

    def test_named_preset_is_independent_from_default_queue(self):
        store.write_queue("acc1", {"queue": [{"plugin": "feed_browser"}]})
        store.write_queue("acc1", {"queue": [{"plugin": "reels_niche"},
                                              {"plugin": "mass_stories"}]}, name="grzanie")
        default_q = store.read_queue("acc1")
        preset_q = store.read_queue("acc1", name="grzanie")
        self.assertEqual(len(default_q["queue"]), 1)
        self.assertEqual(len(preset_q["queue"]), 2)

    def test_named_preset_appears_in_list(self):
        store.write_queue("acc1", {"queue": []}, name="grzanie")
        store.write_queue("acc1", {"queue": []}, name="zaangazowanie")
        self.assertEqual(store.list_queue_presets("acc1"),
                         [store.DEFAULT_SCENARIO, "grzanie", "zaangazowanie"])

    def test_preset_does_not_leak_into_account_list(self):
        """Presety mieszkają w podkatalogu — inaczej `glob('*.json')` na
        katalogu accounts/ pokazałby je w panelu jako fałszywe konta."""
        store.write_queue("acc1", {"queue": []}, name="grzanie")
        top_level = sorted(p.name for p in (store.CONFIG_ROOT / "accounts").glob("*.json"))
        self.assertNotIn("acc1.queue.grzanie.json", top_level)

    def test_missing_preset_returns_empty_queue_not_error(self):
        q = store.read_queue("acc1", name="nieistniejacy")
        self.assertEqual(q["queue"], [])

    def test_delete_preset(self):
        store.write_queue("acc1", {"queue": []}, name="grzanie")
        store.delete_queue_preset("acc1", "grzanie")
        self.assertEqual(store.list_queue_presets("acc1"), [store.DEFAULT_SCENARIO])

    def test_delete_missing_preset_raises(self):
        with self.assertRaises(FileNotFoundError):
            store.delete_queue_preset("acc1", "nieistniejacy")

    def test_delete_default_scenario_is_refused(self):
        """`default` jest zarezerwowany — musi zawsze istnieć coś, co
        odpali sidebar/scheduler bez podanej nazwy."""
        with self.assertRaises(ValueError):
            store.delete_queue_preset("acc1", store.DEFAULT_SCENARIO)

    def test_legacy_default_queue_file_is_migrated_on_first_read(self):
        """Sprzed scalenia 2026-08-03: domyślna kolejka mieszkała w
        `<alias>.queue.json`, plik-rodzeństwo obok configu konta. Pierwszy
        odczyt po aktualizacji ma ją przenieść do `<alias>.queues/default.json`
        bez utraty danych, zamiast cichego porzucenia istniejących kont."""
        legacy = store.CONFIG_ROOT / "accounts" / "acc1.queue.json"
        legacy.write_text(json.dumps({"queue": [{"plugin": "feed_browser"}],
                                      "on_task_error": "abort"}), encoding="utf-8")

        q = store.read_queue("acc1")   # bez name= — dawna ścieżka "domyślnej"
        self.assertEqual(len(q["queue"]), 1)
        self.assertEqual(q["on_task_error"], "abort")
        self.assertFalse(legacy.exists())   # przeniesiona, nie skopiowana
        self.assertTrue((store.CONFIG_ROOT / "accounts" / "acc1.queues"
                         / "default.json").exists())
        self.assertIn(store.DEFAULT_SCENARIO, store.list_queue_presets("acc1"))

    def test_resolve_queue_accepts_preset_name(self):
        store.write_queue("acc1", {"queue": [{"plugin": "feed_browser"}]}, name="grzanie")
        r = store.resolve_queue("acc1", name="grzanie")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["plugin"], "feed_browser")


if __name__ == "__main__":
    unittest.main()
