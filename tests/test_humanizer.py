"""Testy Humanizera — parser YAML, zakresy, deterministyczne losowanie z seedem."""
from __future__ import annotations
import time
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.humanizer import Humanizer, HumanizerError, _parse_simple_yaml


class YamlParserTests(unittest.TestCase):
    def test_flat_key_value(self):
        d = _parse_simple_yaml("foo: 1\nbar: hello\n")
        self.assertEqual(d, {"foo": 1, "bar": "hello"})

    def test_inline_dict(self):
        d = _parse_simple_yaml("tap: {min: 0.1, max: 0.5}\n")
        self.assertEqual(d["tap"], {"min": 0.1, "max": 0.5})

    def test_list(self):
        d = _parse_simple_yaml("hours: [9, 22]\n")
        self.assertEqual(d["hours"], [9, 22])

    def test_nested(self):
        text = "per_action:\n  tap: {min: 0.1, max: 0.5}\n  swipe: {min: 0.3, max: 0.8}\n"
        d = _parse_simple_yaml(text)
        self.assertEqual(d["per_action"]["tap"]["max"], 0.5)
        self.assertEqual(d["per_action"]["swipe"]["min"], 0.3)

    def test_comment_stripping(self):
        d = _parse_simple_yaml("# to jest komentarz\nfoo: 1 # inline też\n")
        self.assertEqual(d, {"foo": 1})


class HumanizerTests(unittest.TestCase):
    def setUp(self):
        self.h = Humanizer.load("default", seed=42)

    def test_builtin_profiles_exist(self):
        """Panel oferuje default / aggressive / conservative — wszystkie muszą się ładować."""
        for name in ("default", "aggressive", "conservative"):
            h = Humanizer.load(name, seed=1)
            lo, hi = h.range_for("tap")
            self.assertLess(lo, hi, name)

    def test_aggressive_shorter_than_default(self):
        d = Humanizer.load("default")
        a = Humanizer.load("aggressive")
        d_lo, d_hi = d.range_for("between_profiles")
        a_lo, a_hi = a.range_for("between_profiles")
        self.assertLess(a_lo, d_lo)
        self.assertLess(a_hi, d_hi)

    def test_conservative_longer_than_default(self):
        d = Humanizer.load("default")
        c = Humanizer.load("conservative")
        d_lo, d_hi = d.range_for("between_profiles")
        c_lo, c_hi = c.range_for("between_profiles")
        self.assertGreater(c_lo, d_lo)
        self.assertGreater(c_hi, d_hi)

    def test_range_lookup(self):
        lo, hi = self.h.range_for("tap")
        self.assertLess(lo, hi)
        self.assertAlmostEqual(lo, 0.15)
        self.assertAlmostEqual(hi, 0.4)

    def test_draw_in_range(self):
        for _ in range(50):
            v = self.h.draw("swipe")
            self.assertGreaterEqual(v, 0.3)
            self.assertLessEqual(v, 0.8)

    def test_fallback_to_between_steps(self):
        # nieznana akcja → używa 'between_steps'
        lo, hi = self.h.range_for("some_unknown_action")
        self.assertEqual((lo, hi), (0.6, 1.4))

    def test_missing_profile_raises(self):
        with self.assertRaises(HumanizerError):
            Humanizer.load("no_such_profile_xxx")

    def test_seeded_determinism(self):
        h1 = Humanizer.load("default", seed=123)
        h2 = Humanizer.load("default", seed=123)
        vals1 = [h1.draw("tap") for _ in range(10)]
        vals2 = [h2.draw("tap") for _ in range(10)]
        self.assertEqual(vals1, vals2)

    def test_overrides_merge(self):
        h = Humanizer.load("default", overrides={
            "per_action": {"tap": {"min": 5.0, "max": 10.0}}
        }, seed=1)
        lo, hi = h.range_for("tap")
        self.assertEqual((lo, hi), (5.0, 10.0))
        # inne akcje niezmienione
        self.assertEqual(h.range_for("swipe"), (0.3, 0.8))

    def test_pause_actually_sleeps_but_briefly(self):
        # sanity: pause zwraca > 0 i śpi krótko
        h = Humanizer.load("default", overrides={
            "per_action": {"tap": {"min": 0.01, "max": 0.02}}
        }, seed=1)
        t0 = time.monotonic()
        slept = h.pause("tap")
        dt = time.monotonic() - t0
        self.assertGreater(slept, 0)
        self.assertLess(dt, 0.5)  # generous


class SpeedTests(unittest.TestCase):
    """Globalna prędkość — jedno pokrętło nad wszystkimi pauzami."""

    def _h(self, speed=None):
        return Humanizer.load("default", overrides={
            "per_action": {"tap": {"min": 2.0, "max": 2.0}}}, seed=1, speed=speed)

    def test_default_speed_changes_nothing(self):
        self.assertEqual(self._h().scale(4.0), 4.0)

    def test_faster_shortens_pauses(self):
        self.assertEqual(self._h(2.0).scale(4.0), 2.0)

    def test_slower_lengthens_pauses(self):
        self.assertEqual(self._h(0.5).scale(4.0), 8.0)

    def test_ranges_stay_untouched(self):
        """Prędkość nie rusza zakresów w profilu — proporcje między rodzajami
        pauz mają zostać te same, zmienia się tylko tempo całości."""
        h = self._h(4.0)
        self.assertEqual(h.range_for("tap"), (2.0, 2.0))
        self.assertEqual(h.draw("tap"), 2.0)

    def test_pause_reports_real_elapsed_time(self):
        h = self._h(20.0)
        self.assertAlmostEqual(h.pause("tap"), 0.1, places=6)

    def test_speed_from_profile_is_used_when_not_given(self):
        h = Humanizer.load("default", overrides={"speed": 2.0}, seed=1)
        self.assertEqual(h.speed, 2.0)

    def test_explicit_speed_wins_over_profile(self):
        h = Humanizer.load("default", overrides={"speed": 2.0}, seed=1, speed=5.0)
        self.assertEqual(h.speed, 5.0)

    def test_nonsense_speed_falls_back_to_normal(self):
        """Literówka w configu nie może zatrzymać bota na godzinę ani zasypać
        telefonu gestami."""
        for bad in (0, -3, None, "szybko"):
            self.assertEqual(Humanizer(profile={}, speed=bad).speed, 1.0)

    def test_extreme_speed_is_clamped(self):
        self.assertEqual(Humanizer(profile={}, speed=1000).speed, 20.0)
        self.assertEqual(Humanizer(profile={}, speed=0.001).speed, 0.1)

    def test_sleep_scales_arbitrary_waits(self):
        """Wejście dla wszystkiego, co czeka „bo tak zrobiłby człowiek" —
        oglądanie rolki, czytanie opisu, przerwa między profilami."""
        h = self._h(10.0)
        t0 = time.monotonic()
        reported = h.sleep(1.0)
        dt = time.monotonic() - t0
        self.assertAlmostEqual(reported, 0.1, places=6)
        self.assertLess(dt, 0.5)


class PauseCallbackTests(unittest.TestCase):
    """Logowanie każdej pauzy przez opcjonalny `on_pause`."""

    def test_pause_emits_callback(self):
        calls = []
        h = Humanizer.load("default", overrides={
            "per_action": {"tap": {"min": 0.01, "max": 0.02}}
        }, seed=1)
        h.on_pause = lambda **kwargs: calls.append(kwargs)
        h.pause("tap", why="test")
        self.assertEqual(len(calls), 1)
        rec = calls[0]
        self.assertEqual(rec["pause_kind"], "tap")
        self.assertEqual(rec["why"], "test")
        self.assertEqual(rec["source"], "pause")
        self.assertEqual(rec["speed"], 1.0)
        self.assertEqual(rec["action_n"], 1)
        self.assertGreater(rec["drawn_s"], 0)
        self.assertGreater(rec["slept_s"], 0)
        self.assertEqual(rec["long_pause_added_s"], 0.0)

    def test_sleep_emits_callback_with_kind(self):
        calls = []
        h = Humanizer.load("default", seed=1)
        h.on_pause = lambda **kwargs: calls.append(kwargs)
        h.sleep(0.05, why="reason", kind="between_steps")
        self.assertEqual(len(calls), 1)
        rec = calls[0]
        self.assertEqual(rec["pause_kind"], "between_steps")
        self.assertEqual(rec["why"], "reason")
        self.assertEqual(rec["source"], "sleep")
        self.assertAlmostEqual(rec["drawn_s"], 0.05, places=6)
        self.assertAlmostEqual(rec["slept_s"], 0.05, places=6)

    def test_long_pause_callback_every_n_actions(self):
        calls = []
        h = Humanizer.load("default", overrides={
            "per_action": {"tap": {"min": 0.01, "max": 0.01}},
            "long_pause": {"every_n_actions": {"n": 2, "min": 1.0, "max": 1.0}}
        }, seed=1)
        h.on_pause = lambda **kwargs: calls.append(kwargs)
        h.pause("tap")          # action_n=1, bez long pause
        h.pause("tap")          # action_n=2, +1.0s
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["long_pause_added_s"], 0.0)
        self.assertEqual(calls[1]["long_pause_added_s"], 1.0)
        self.assertAlmostEqual(calls[1]["drawn_s"], 1.01, places=6)

    def test_no_callback_when_not_set(self):
        h = Humanizer.load("default", overrides={
            "per_action": {"tap": {"min": 0.01, "max": 0.02}}
        }, seed=1)
        # brak on_pause — nie powinno rzucić wyjątku
        h.pause("tap")


class ShouldLogPauseTests(unittest.TestCase):
    """Filtr mikro-pauz — panel/JSONL bez zalewu tap/swipe."""

    def test_micro_skipped_by_default(self):
        from v2.core.humanizer import should_log_pause
        self.assertFalse(should_log_pause("tap"))
        self.assertFalse(should_log_pause("swipe"))
        self.assertFalse(should_log_pause("between_steps"))

    def test_notable_kinds_logged(self):
        from v2.core.humanizer import should_log_pause
        self.assertTrue(should_log_pause("after_launch"))
        self.assertTrue(should_log_pause("before_sleep"))
        self.assertTrue(should_log_pause("between_profiles"))

    def test_long_pause_overrides_micro(self):
        from v2.core.humanizer import should_log_pause
        self.assertTrue(should_log_pause("tap", long_pause_added_s=1.5))

    def test_debug_logs_all(self):
        from v2.core.humanizer import should_log_pause
        self.assertTrue(should_log_pause("tap", debug=True))


if __name__ == "__main__":
    unittest.main()
