"""Testy warstwy wykonawczej pluginów: Budget, chance, JobRunner, reels_niche.

Wszystko na atrapach — zero telefonu. Sprawdzamy to, co realnie może się
zepsuć: kiedy pętla przestaje pracować, czy telefon zawsze wraca na ekran
wyjściowy, i czy wyłączona akcja faktycznie nie jest wołana.
"""
from __future__ import annotations

import json
import random
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.chance import probability, roll
from v2.plugins.base import Budget, PluginResult, draw_range, stop_path_for
from v2.runner.step import StepResult


# ============================================================
# atrapy
# ============================================================
class FakeNavigator:
    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def ensure(self, target, max_steps=6):
        self.calls.append(target)
        return target not in self.fail_on

    def identify(self):
        class S:
            top, overlay, package = "reels_feed", None, None
        return S()


class FakeDeviceStub:
    """Minimalne urządzenie — pluginy wołają na nim BACK i czekanie na UI."""
    def __init__(self):
        self.backs = 0

    def press_back(self):
        self.backs += 1
        return True

    def hierarchy(self, force=False):
        return {"properties": {}, "children": []}

    def wait_for_stable_ui(self, **kw):
        return True


class FakeHumanizer:
    """Pauzy w testach są natychmiastowe; `sleep` notuje CZAS LUDZKI."""
    def __init__(self):
        self.slept = []

    def pause(self, kind, why=""):
        return 0.0

    def sleep(self, seconds, why="", **kwargs):
        self.slept.append(seconds)
        return 0.0

    def scale(self, seconds):
        return seconds

    def draw(self, kind):
        return 0.0

    def range_for(self, kind):
        return (0.0, 0.0)


class FakeCtx:
    def __init__(self, navigator=None):
        self.navigator = navigator or FakeNavigator()
        self.session_id = "test-session"
        self.scratch = {}
        self.logs = []
        self.device = FakeDeviceStub()
        self.humanizer = FakeHumanizer()

    def log(self, kind, **payload):
        self.logs.append((kind, payload))

    def kinds(self):
        return [k for k, _ in self.logs]


class FakeStep:
    """Krok zwracający zaplanowane wyniki; zapisuje otrzymane params."""
    def __init__(self, results, recorder, name):
        self.results = list(results)
        self.recorder = recorder
        self.name = name

    def run(self, ctx, params):
        self.recorder.append((self.name, params))
        if not self.results:
            return StepResult.ok()
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class ScratchStep:
    """Atrapa kroku, który zapisuje coś do `ctx.scratch`.

    Potrzebna, bo plugin czyści scratch na starcie KAŻDEJ rolki — wynik AI
    czy metadane rolki muszą więc powstawać wewnątrz kroku, nie przed pętlą.
    """
    def __init__(self, updates: dict, recorder, name, result=None):
        self.updates = updates
        self.recorder = recorder
        self.name = name
        self.result = result or StepResult.ok()

    def run(self, ctx, params):
        self.recorder.append((self.name, params))
        ctx.scratch.update(self.updates)
        return self.result


def fake_registry(spec: dict, recorder: list):
    """Podstawiany `steps.get` — spec: {nazwa: [wyniki] | ScratchStep}.

    Instancja kroku jest CACHE'OWANA per nazwa. Bez tego każde wywołanie
    dostawało świeżą kopię listy wyników i zawsze zwracało pierwszy element —
    scenariusze typu „pierwsza próba nieudana, druga udana" były nietestowalne.
    """
    made: dict = {}

    def get(name):
        if name not in spec:
            raise KeyError(name)
        if name not in made:
            entry = spec[name]
            if isinstance(entry, ScratchStep):
                entry.recorder = recorder
                entry.name = name
                made[name] = entry
            else:
                made[name] = FakeStep(entry, recorder, name)
        return lambda: made[name]
    return get


# ============================================================
class BudgetTests(unittest.TestCase):
    def test_items_exhaustion(self):
        b = Budget(max_items=2)
        self.assertIsNone(b.stop_reason())
        b.tick(); b.tick()
        self.assertEqual(b.stop_reason(), "items")

    def test_duration_exhaustion(self):
        b = Budget(max_duration_s=0.01)
        time.sleep(0.02)
        self.assertEqual(b.stop_reason(), "duration")

    def test_no_limits_runs_forever(self):
        b = Budget()
        b.tick(); b.tick()
        self.assertIsNone(b.stop_reason())

    def test_stop_file_beats_everything(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "STOP"
            b = Budget(max_items=100, stop_path=p)
            self.assertIsNone(b.stop_reason())
            p.write_text("")
            self.assertEqual(b.stop_reason(), "user")

    def test_from_settings_draws_range(self):
        s = {"budget": {"max_items": {"min": 5, "max": 9}, "max_duration_min": 3}}
        for seed in range(20):
            b = Budget.from_settings(s, rng=random.Random(seed))
            self.assertGreaterEqual(b.max_items, 5)
            self.assertLessEqual(b.max_items, 9)
        self.assertEqual(b.max_duration_s, 180)

    def test_task_budget_overrides_settings(self):
        s = {"budget": {"max_items": {"min": 5, "max": 9}, "max_duration_min": 30}}
        b = Budget.from_settings(s, {"max_items": 3, "max_duration_min": 6})
        self.assertEqual(b.max_items, 3)
        self.assertEqual(b.max_duration_s, 360)

    def test_cli_override_wins_over_everything(self):
        s = {"budget": {"max_items": {"min": 5, "max": 9}}}
        b = Budget.from_settings(s, {"max_items": 3}, max_items_override=1)
        self.assertEqual(b.max_items, 1)

    def test_draw_range_handles_scalar_and_none(self):
        self.assertEqual(draw_range(7), 7)
        self.assertEqual(draw_range(None, default=4), 4)
        v = draw_range({"min": 2, "max": 2})
        self.assertEqual(v, 2)


class ChanceTests(unittest.TestCase):
    def test_missing_chance_always_passes(self):
        self.assertEqual(probability(None)[0], 1.0)
        self.assertEqual(probability({})[0], 1.0)

    def test_new_format(self):
        self.assertEqual(probability({"mode": "p", "value": 0.4})[0], 0.4)

    def test_legacy_format(self):
        self.assertEqual(probability({"p": 0.4})[0], 0.4)

    def test_formula_both_formats(self):
        vals = {"score": 70}
        self.assertAlmostEqual(probability({"mode": "formula", "value": "score/100"}, vals)[0], 0.7)
        self.assertAlmostEqual(probability({"formula": "score/100"}, vals)[0], 0.7)

    def test_unknown_formula_does_not_crash(self):
        p, how = probability({"mode": "formula", "value": "rm -rf /"}, {})
        self.assertEqual(p, 1.0)
        self.assertIn("unknown_formula", how)

    def test_probability_is_clamped(self):
        self.assertEqual(probability({"mode": "p", "value": 5})[0], 1.0)
        self.assertEqual(probability({"mode": "p", "value": -2})[0], 0.0)

    def test_roll_is_reproducible_with_seed(self):
        c = {"mode": "p", "value": 0.5}
        a = [roll(c, rng=random.Random(1))["pass"] for _ in range(5)]
        b = [roll(c, rng=random.Random(1))["pass"] for _ in range(5)]
        self.assertEqual(a, b)

    def test_p_zero_never_passes(self):
        self.assertFalse(roll({"mode": "p", "value": 0})["pass"])


class PluginResultTests(unittest.TestCase):
    def test_done_with_error_is_failed(self):
        """stopped_by=error przez done() → status failed (nie OK w panelu)."""
        b = Budget(max_items=3)
        b.tick()
        res = PluginResult.done(b, "error", watched=1)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.stopped_by, "error")
        self.assertEqual(res.items_done, 1)
        self.assertEqual(res.stats.get("watched"), 1)

    def test_done_items_stays_ok(self):
        res = PluginResult.done(Budget(max_items=1), "items")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.stopped_by, "items")


# ============================================================
class JobRunnerTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins import runner as runner_mod
        from v2.plugins import registry as plugin_registry
        self.mod = runner_mod
        self.plugin_registry = plugin_registry
        self._tmp = TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self.recorder = []

        # kroki systemowe (prolog/epilog) — atrapy
        # stats_snapshot jest też w prologu (po HOME), nie tylko w EPILOGUE_STEPS
        self._orig_step_get = runner_mod.step_registry.get
        sys_steps = {n: [] for n, _ in
                     runner_mod.PROLOGUE_STEPS + runner_mod.EPILOGUE_STEPS}
        sys_steps.setdefault("system.stats_snapshot", [])
        runner_mod.step_registry.get = fake_registry(sys_steps, self.recorder)

        # katalog i ustawienia — atrapy
        self._orig_get_plugin = runner_mod.get_plugin
        self._orig_resolve = runner_mod.store.resolve
        self._orig_read_queue = runner_mod.store.read_queue
        runner_mod.get_plugin = lambda pid: {
            "label": pid, "entry_screen": "reels_feed", "exit_screen": "home_feed"}

        self._orig_is_impl = plugin_registry.is_implemented
        self._orig_plugin_get = plugin_registry.get

    def tearDown(self):
        self.mod.step_registry.get = self._orig_step_get
        self.mod.get_plugin = self._orig_get_plugin
        self.mod.store.resolve = self._orig_resolve
        self.mod.store.read_queue = self._orig_read_queue
        self.plugin_registry.is_implemented = self._orig_is_impl
        self.plugin_registry.get = self._orig_plugin_get
        self._tmp.cleanup()

    def _wire(self, tasks, plugin_run, settings=None, on_task_error="continue"):
        self.mod.store.read_queue = lambda acc, name=None: {
            "queue": tasks, "between_tasks_s": {"min": 0, "max": 0},
            "on_task_error": on_task_error}
        self.mod.store.resolve = lambda acc, pid, ov=None: (
            settings or {"enabled": True, "budget": {"max_items": 1}})

        class P:
            id = "p"
            def run(self, ctx, s, budget):
                return plugin_run(ctx, s, budget)

        self.plugin_registry.is_implemented = lambda pid: True
        self.plugin_registry.get = lambda pid: P
        return P

    def _run(self, tasks, plugin_run, **kw):
        ctx = FakeCtx()
        self._wire(tasks, plugin_run, **kw)
        r = self.mod.JobRunner(ctx=ctx, data_root=self.data_root)
        return ctx, r.run("acc1")

    def test_prologue_and_epilogue_wrap_tasks(self):
        calls = []
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": True}],
            lambda c, s, b: (calls.append("plugin"), PluginResult.done(b, "items"))[1])
        names = [n for n, _ in self.recorder]
        # prolog: wake → unlock → MAC → IG → stats_start;
        # epilog: stats_end → close → restore MAC → sleep
        self.assertEqual(names[:5], ["system.wake", "system.unlock",
                                     "system.apply_clone_identity",
                                     "system.ensure_ig_running",
                                     "system.stats_snapshot"])
        self.assertEqual(names[-4:], ["system.stats_snapshot",
                                      "system.close_ig",
                                      "system.restore_clone_identity",
                                      "system.sleep_screen"])
        # params when=session_start / session_end — flaga w configu filtruje skip
        self.assertEqual(self.recorder[4][1].get("when"), "session_start")
        self.assertEqual(self.recorder[-4][1].get("when"), "session_end")
        self.assertEqual(calls, ["plugin"])
        self.assertTrue(summary["ok"])

    def test_empty_queue_skips_prologue(self):
        ctx, summary = self._run([], lambda c, s, b: PluginResult.done(b, "items"))
        self.assertEqual(self.recorder, [])
        self.assertFalse(summary["ran_prologue"])

    def test_exit_screen_ensured_even_when_plugin_raises(self):
        """Bez tego kolejne zadanie startuje w sheecie i tapie w losowe miejsca."""
        nav = FakeNavigator()
        ctx = FakeCtx(nav)
        def boom(c, s, b):
            raise RuntimeError("plugin padł")
        self._wire([{"id": "t1", "plugin": "p", "enabled": True}], boom)
        summary = self.mod.JobRunner(ctx=ctx, data_root=self.data_root).run("acc1")
        self.assertIn("home_feed", nav.calls)
        self.assertEqual(summary["tasks_failed"], 1)

    def test_on_task_error_abort_stops_queue(self):
        seen = []
        def run(c, s, b):
            seen.append(1)
            raise RuntimeError("x")
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": True},
             {"id": "t2", "plugin": "p", "enabled": True}],
            run, on_task_error="abort")
        self.assertEqual(len(seen), 1)
        self.assertTrue(summary["aborted"])

    def test_on_task_error_continue_runs_next(self):
        seen = []
        def run(c, s, b):
            seen.append(1)
            if len(seen) == 1:
                raise RuntimeError("x")
            return PluginResult.done(b, "items")
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": True},
             {"id": "t2", "plugin": "p", "enabled": True}], run)
        self.assertEqual(len(seen), 2)
        self.assertEqual(summary["tasks_failed"], 1)
        self.assertEqual(summary["tasks_ok"], 1)

    def test_disabled_task_is_not_run(self):
        seen = []
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": False}],
            lambda c, s, b: (seen.append(1), PluginResult.done(b, "items"))[1])
        self.assertEqual(seen, [])

    def test_plugin_disabled_for_account_is_skipped(self):
        seen = []
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": True}],
            lambda c, s, b: (seen.append(1), PluginResult.done(b, "items"))[1],
            settings={"enabled": False, "budget": {}})
        self.assertEqual(seen, [])
        self.assertEqual(summary["tasks_skipped"], 1)

    def test_daily_limit_aborts_whole_queue(self):
        seen = []
        def run(c, s, b):
            seen.append(1)
            return PluginResult.done(b, "limit_daily")
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": True},
             {"id": "t2", "plugin": "p", "enabled": True}], run)
        self.assertEqual(len(seen), 1)
        self.assertTrue(summary["aborted"])

    def test_checkpoint_written_after_each_task(self):
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": True}],
            lambda c, s, b: (b.tick(), b.tick(), PluginResult.done(b, "items"))[2])
        p = self.data_root / "accounts" / "acc1" / "queue_state.json"
        self.assertTrue(p.exists())
        state = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(state["tasks"][0]["items_done"], 2)

    def test_entry_screen_failure_marks_task_failed(self):
        nav = FakeNavigator(fail_on={"reels_feed"})
        ctx = FakeCtx(nav)
        seen = []
        self._wire([{"id": "t1", "plugin": "p", "enabled": True}],
                   lambda c, s, b: (seen.append(1), PluginResult.done(b, "items"))[1])
        summary = self.mod.JobRunner(ctx=ctx, data_root=self.data_root).run("acc1")
        self.assertEqual(seen, [])
        self.assertEqual(summary["tasks_failed"], 1)

    def test_stop_file_prevents_further_tasks(self):
        stop = stop_path_for(self.data_root, "acc1")
        stop.parent.mkdir(parents=True, exist_ok=True)
        seen = []
        def run(c, s, b):
            seen.append(1)
            stop.write_text("")            # user naciska Stop w trakcie 1. zadania
            return PluginResult.done(b, "user")
        ctx, summary = self._run(
            [{"id": "t1", "plugin": "p", "enabled": True},
             {"id": "t2", "plugin": "p", "enabled": True}], run)
        self.assertEqual(len(seen), 1)
        self.assertTrue(summary["aborted"])

    def test_only_filter_runs_single_task(self):
        seen = []
        ctx = FakeCtx()
        self._wire([{"id": "t1", "plugin": "p", "enabled": True},
                    {"id": "t2", "plugin": "p", "enabled": True}],
                   lambda c, s, b: (seen.append(s), PluginResult.done(b, "items"))[1])
        self.mod.JobRunner(ctx=ctx, data_root=self.data_root, only="t2").run("acc1")
        self.assertEqual(len(seen), 1)

    def test_only_filter_accepts_comma_separated_ids(self):
        """Scheduler wybiera losowy PODZBIÓR zadań ('scenariusz') — `only`
        musi umieć więcej niż jedno id naraz."""
        seen = []
        ctx = FakeCtx()
        self._wire([{"id": "t1", "plugin": "p", "enabled": True},
                    {"id": "t2", "plugin": "p", "enabled": True},
                    {"id": "t3", "plugin": "p", "enabled": True}],
                   lambda c, s, b: (seen.append(s), PluginResult.done(b, "items"))[1])
        self.mod.JobRunner(ctx=ctx, data_root=self.data_root, only="t1,t3").run("acc1")
        self.assertEqual(len(seen), 2)

    def test_queue_name_reads_named_scenario(self):
        """Scenariusz z Harmonogramu — jedno- albo wielozadaniowy, czytany
        przez store.read_queue z podaną nazwą (bez osobnej ścieżki 'akcja')."""
        seen = []
        ctx = FakeCtx()
        self._wire([{"id": "t1", "plugin": "p", "enabled": True}],
                   lambda c, s, b: (seen.append(s), PluginResult.done(b, "items"))[1])
        read_queue_calls = []
        orig = self.mod.store.read_queue
        self.mod.store.read_queue = lambda *a, **k: (read_queue_calls.append(k.get("name")), orig(*a, **k))[1]

        summary = self.mod.JobRunner(ctx=ctx, data_root=self.data_root,
                                     queue_name="grzanie").run("acc1")
        self.assertEqual(len(seen), 1)
        self.assertEqual(read_queue_calls, ["grzanie"])
        self.assertEqual(summary["queue_name"], "grzanie")


# ============================================================
class ReelsNichePluginTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins.impl import reels_niche as mod
        self.mod = mod
        self.recorder = []
        self._orig_get = mod.steps.get

    def tearDown(self):
        self.mod.steps.get = self._orig_get

    def _run(self, spec, settings, budget=None, ctx=None):
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = ctx or FakeCtx()
        budget = budget or Budget(max_items=1)
        res = self.mod.ReelsNiche().run(ctx, settings, budget)
        return ctx, res, [n for n, _ in self.recorder]

    BASE = {
        "enabled": True,
        "gate": {"min_likes": 10, "min_comments": 1, "on_unknown": "skip"},
        "ai": {"enabled": False},
        "like_reel": {"enabled": False},
        "likers": {"harvest": {"enabled": False}},
        "comments": {"harvest": {"enabled": False}, "like_comments": {"enabled": False}},
    }

    def test_gate_skip_does_not_call_ai_or_like(self):
        """Odsiew na licznikach ma oszczędzać koszt AI — jeśli mimo skipa
        wołalibyśmy analizę, bramka nie miałaby sensu."""
        s = {**self.BASE, "ai": {"enabled": True, "frames": 6},
             "like_reel": {"enabled": True}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.skipped("gate_failed")],
                "reels.analyze_frames_ai": [], "reels.like": []}
        ctx, res, names = self._run(spec, s)
        self.assertNotIn("reels.analyze_frames_ai", names)
        self.assertNotIn("reels.like", names)
        self.assertEqual(res.stats["gate_skipped"], 1)

    def test_like_disabled_never_calls_like_step(self):
        """Kluczowe dla pierwszego przebiegu na żywo — bot ma nie zostawić
        żadnego śladu na koncie."""
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()], "reels.like": []}
        ctx, res, names = self._run(spec, self.BASE)
        self.assertNotIn("reels.like", names)
        self.assertEqual(res.stats["liked"], 0)

    def test_like_enabled_calls_like_step(self):
        s = {**self.BASE, "like_reel": {"enabled": True,
                                        "chance": {"mode": "p", "value": 1}}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()], "reels.like": [StepResult.ok()]}
        ctx, res, names = self._run(spec, s)
        self.assertIn("reels.like", names)
        self.assertEqual(res.stats["liked"], 1)

    def test_budget_limits_iterations(self):
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok() for _ in range(10)]}
        ctx, res, names = self._run(spec, self.BASE, budget=Budget(max_items=3))
        self.assertEqual(names.count("reels.watch"), 3)
        self.assertEqual(res.items_done, 3)
        self.assertEqual(res.stopped_by, "items")

    def test_no_swipe_after_last_reel(self):
        """Ostatnia rolka nie powinna kończyć się swipe'em — to zbędny gest
        tuż przed wyjściem z ekranu."""
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok(), StepResult.ok()]}
        ctx, res, names = self._run(spec, self.BASE, budget=Budget(max_items=2))
        self.assertEqual(names.count("reels.swipe_next"), 1)

    def test_harvest_returns_to_feed_even_on_exception(self):
        nav = FakeNavigator()
        ctx = FakeCtx(nav)
        s = {**self.BASE, "likers": {"harvest": {"enabled": True,
                                                 "collect": {"min": 5, "max": 5}}}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()],
                "reels.harvest_likers": [RuntimeError("sheet padł")]}
        ctx, res, names = self._run(spec, s, ctx=ctx)
        self.assertIn("reels_feed", nav.calls)

    def _ai_spec(self, ai_result, extra=None):
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()],
                "reels.read_caption_comments": [],
                "reels.analyze_frames_ai": ScratchStep({"reel_ai": ai_result},
                                                       self.recorder, "x")}
        spec.update(extra or {})
        return spec

    def test_harvest_respects_min_score_threshold(self):
        s = {**self.BASE,
             "ai": {"enabled": True, "frames": 6, "mode": "niche_match",
                    "min_niche_score": 0, "allow_verdict": ["match", "partial"]},
             "likers": {"harvest": {"enabled": True, "collect": 5},
                        "min_niche_score_to_harvest": 70}}
        spec = self._ai_spec({"niche_score": 40, "verdict": "match"},
                             {"reels.harvest_likers": [StepResult.ok(saved=5)]})
        ctx, res, names = self._run(spec, s)
        self.assertNotIn("reels.harvest_likers", names)   # 40 < 70

    def test_harvest_runs_when_score_above_threshold(self):
        s = {**self.BASE,
             "ai": {"enabled": True, "frames": 6, "mode": "niche_match",
                    "min_niche_score": 0, "allow_verdict": ["match"]},
             "likers": {"harvest": {"enabled": True, "collect": 5},
                        "min_niche_score_to_harvest": 70}}
        spec = self._ai_spec({"niche_score": 85, "verdict": "match"},
                             {"reels.harvest_likers": [StepResult.ok(saved=5)]})
        ctx, res, names = self._run(spec, s)
        self.assertIn("reels.harvest_likers", names)
        self.assertEqual(res.stats["likers_saved"], 5)

    def test_ai_reject_skips_rest_of_reel(self):
        s = {**self.BASE,
             "ai": {"enabled": True, "frames": 6, "mode": "niche_match",
                    "min_niche_score": 80, "allow_verdict": ["match"]},
             "like_reel": {"enabled": True},
             "likers": {"harvest": {"enabled": True, "collect": 5}}}
        spec = self._ai_spec({"niche_score": 20, "verdict": "off"},
                             {"reels.like": [], "reels.harvest_likers": []})
        ctx, res, names = self._run(spec, s)
        self.assertNotIn("reels.like", names)
        self.assertNotIn("reels.harvest_likers", names)
        self.assertEqual(res.stats["ai_rejected"], 1)

    def test_exclude_only_passes_without_niche_match(self):
        """Konto ogólne: niski wynik zgodności nie blokuje akcji — liczy się
        tylko to, czy treść nie wpadła w wykluczenia."""
        s = {**self.BASE,
             "ai": {"enabled": True, "frames": 6, "mode": "exclude_only"},
             "like_reel": {"enabled": True, "chance": {"mode": "p", "value": 1}}}
        spec = self._ai_spec({"niche_score": 5, "verdict": "partial"},
                             {"reels.like": [StepResult.ok()]})
        ctx, res, names = self._run(spec, s)
        self.assertIn("reels.like", names)

    def test_exclude_only_still_rejects_excluded(self):
        s = {**self.BASE,
             "ai": {"enabled": True, "frames": 6, "mode": "exclude_only"},
             "like_reel": {"enabled": True, "chance": {"mode": "p", "value": 1}}}
        spec = self._ai_spec({"niche_score": 90, "verdict": "off"},
                             {"reels.like": []})
        ctx, res, names = self._run(spec, s)
        self.assertNotIn("reels.like", names)
        self.assertEqual(res.stats["ai_rejected"], 1)

    def test_blocked_author_is_skipped(self):
        s = {**self.BASE, "gate": {**self.BASE["gate"],
                                   "skip_authors": ["@Konkurencja"]}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": ScratchStep({"reel": {"author": "konkurencja"}},
                                           self.recorder, "x"),
                "reels.harvest_likers": []}
        ctx, res, names = self._run(spec, s)
        self.assertEqual(res.stats["gate_skipped"], 1)
        self.assertNotIn("reels.harvest_likers", names)

    # --- interakcja „na gorąco" (E4) ---------------------------------
    def _hot_settings(self, **hot):
        return {**self.BASE,
                "likers": {"harvest": {"enabled": True, "collect": 5},
                           "hot_interact": {"enabled": True, "count": 2,
                                            "prefer_ring": True, **hot}}}

    def _hot_spec(self, opens, visit_results):
        """Rejestr dla ścieżki: harvest → open_liker_profile × N → profile.visit.

        Jedna atrapa dla obu pluginów — rejestr kroków jest wspólny na proces,
        a `run_one` sięga do tego samego, co pętla rolek.
        """
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()],
                "reels.harvest_likers": [StepResult.ok(saved=5)],
                "reels.open_liker_profile": opens,
                "profile.visit": visit_results,
                "profile.exit": []}
        self.mod.steps.get = fake_registry(spec, self.recorder)
        return spec

    @staticmethod
    def _sheet_nav():
        """Nawigator, który widzi otwartą listę lajkujących.

        Domyślna atrapa mówi „reels_feed", a plugin po każdym profilu sprawdza,
        czy wrócił na listę — inaczej dalsze tapy szłyby w feed rolek.
        """
        nav = FakeNavigator()
        nav.identify = lambda: type("S", (), {"top": "reel_likers_list",
                                              "overlay": None})()
        return nav

    def _run_hot(self, s, opens, visit_results, nav=None):
        self._hot_spec(opens, visit_results)
        ctx = FakeCtx(nav or self._sheet_nav())
        res = self.mod.ReelsNiche().run(ctx, s, Budget(max_items=1))
        return ctx, res, [n for n, _ in self.recorder]

    def test_hot_interact_visits_profiles_from_sheet(self):
        ctx, res, names = self._run_hot(
            self._hot_settings(),
            opens=[StepResult.ok(username="a"), StepResult.ok(username="b")],
            visit_results=[StepResult.ok(username="a", action="follow", followed=True),
                           StepResult.ok(username="b", action="skip")])
        self.assertEqual(names.count("reels.open_liker_profile"), 2)
        self.assertEqual(names.count("profile.visit"), 2)
        self.assertEqual(res.stats["hot_visited"], 2)
        self.assertEqual(res.stats["hot_followed"], 1)

    def test_hot_interact_stops_when_list_exhausted(self):
        """Lista lajkujących bywa krótsza niż `count` — brak kandydatów kończy
        pętlę, zamiast tapać w to samo miejsce w kółko."""
        ctx, res, names = self._run_hot(
            self._hot_settings(count=3),
            opens=[StepResult.ok(username="a"), StepResult.skipped("no_candidates")],
            visit_results=[StepResult.ok(username="a", action="skip")])
        self.assertEqual(names.count("profile.visit"), 1)
        self.assertEqual(res.stats["hot_visited"], 1)

    def test_hot_interact_stops_when_sheet_is_lost(self):
        """Po powrocie z profilu musimy być z powrotem na liście. Jeśli nie —
        kolejny tap poszedłby w feed rolek, więc pętla ma się zatrzymać."""
        ctx, res, names = self._run_hot(
            self._hot_settings(count=3),
            opens=[StepResult.ok(username="a"), StepResult.ok(username="b")],
            visit_results=[StepResult.ok(username="a", action="skip")],
            nav=FakeNavigator())            # widzi reels_feed, nie listę
        self.assertEqual(names.count("reels.open_liker_profile"), 1)
        self.assertIn("reels_niche.hot_interact_lost_sheet", ctx.kinds())

    def test_hot_interact_daily_limit_stops_plugin(self):
        ctx, res, names = self._run_hot(
            self._hot_settings(),
            opens=[StepResult.ok(username="a"), StepResult.ok(username="b")],
            visit_results=[StepResult.skipped("follow_daily_exceeded (100/100)",
                                              skip_until="session.end")])
        self.assertEqual(res.stopped_by, "limit_daily")
        self.assertEqual(names.count("profile.visit"), 1)

    def test_hot_interact_keeps_sheet_open_for_harvest(self):
        """Krok zbierający normalnie zamyka listę w swoim `finally` — przy
        interakcji musi ją zostawić otwartą, inaczej klikamy w feed rolek."""
        self._hot_spec([StepResult.skipped("no_candidates")], [])
        ctx = FakeCtx()
        self.mod.ReelsNiche().run(ctx, self._hot_settings(), Budget(max_items=1))
        params = dict(self.recorder)["reels.harvest_likers"]
        self.assertTrue(params["keep_sheet_open"])

    def test_hot_interact_returns_to_feed_even_on_exception(self):
        nav = FakeNavigator()
        ctx, res, names = self._run_hot(
            self._hot_settings(),
            opens=[RuntimeError("sheet zniknął")],
            visit_results=[], nav=nav)
        self.assertIn("reels_feed", nav.calls)

    def test_like_comments_runs_with_open_sheet(self):
        """Regresja: lajkowanie komentarzy wołane po zamknięciu sheetu nie
        miało w co kliknąć i zawsze raportowało brak kandydatów."""
        s = {**self.BASE,
             "comments": {"harvest": {"enabled": True, "collect": 5},
                          "like_comments": {"enabled": True, "count": 1}}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()],
                "reels.harvest_commenters": [StepResult.ok(saved=5)],
                "reels.like_comment": [StepResult.ok()]}
        ctx, res, names = self._run(spec, s)
        params = dict(self.recorder)["reels.harvest_commenters"]
        self.assertTrue(params["keep_sheet_open"])
        self.assertEqual(res.stats["comments_liked"], 1)

    def test_hot_interact_disabled_never_opens_profile(self):
        s = {**self.BASE, "likers": {"harvest": {"enabled": True, "collect": 5},
                                     "hot_interact": {"enabled": False}}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()],
                "reels.harvest_likers": [StepResult.ok(saved=5)],
                "reels.open_liker_profile": []}
        ctx, res, names = self._run(spec, s)
        self.assertNotIn("reels.open_liker_profile", names)
        self.assertFalse(dict(self.recorder)["reels.harvest_likers"]["keep_sheet_open"])

    def test_same_reel_is_not_processed_twice(self):
        """Objaw z realnego przebiegu: swipe nie przewinął, a bot otworzył
        komentarze tej samej rolki dwa razy pod rząd."""
        s = {**self.BASE,
             "comments": {"harvest": {"enabled": True, "collect": 5},
                          "like_comments": {"enabled": False}}}
        same = {"reel": {"author": "ten_sam", "marker": "a:ten_sam|l:100"}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": ScratchStep(same, self.recorder, "x"),
                "reels.harvest_commenters": [StepResult.ok(saved=5),
                                             StepResult.ok(saved=5)]}
        ctx, res, names = self._run(spec, s, budget=Budget(max_items=2))
        self.assertEqual(names.count("reels.watch"), 2)
        self.assertEqual(names.count("reels.harvest_commenters"), 1)
        self.assertEqual(res.stats["repeat_skipped"], 1)
        self.assertIn("reels_niche.same_reel", ctx.kinds())

    def test_different_reels_are_both_processed(self):
        """Kontrola dla poprzedniego: różne odciski = obie rolki obsłużone."""
        s = {**self.BASE,
             "comments": {"harvest": {"enabled": True, "collect": 5},
                          "like_comments": {"enabled": False}}}
        markers = iter([{"reel": {"author": "a", "marker": "a:a|l:1"}},
                        {"reel": {"author": "b", "marker": "a:b|l:2"}}])

        class Rotating:
            def run(_self, c, p):
                self.recorder.append(("reels.watch", p))
                c.scratch.update(next(markers))
                return StepResult.ok()

        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": Rotating(),
                "reels.harvest_commenters": [StepResult.ok(saved=5),
                                             StepResult.ok(saved=5)]}
        self.mod.steps.get = lambda name: (
            (lambda: spec[name]) if name == "reels.watch"
            else fake_registry(spec, self.recorder)(name))
        ctx = FakeCtx()
        res = self.mod.ReelsNiche().run(ctx, s, Budget(max_items=2))
        names = [n for n, _ in self.recorder]
        self.assertEqual(names.count("reels.harvest_commenters"), 2)
        self.assertEqual(res.stats["repeat_skipped"], 0)

    def test_single_swipe_failure_is_tolerated(self):
        """Jedno nieudane przewinięcie bywa chwilowe — nie kończy zadania."""
        spec = {"reels.open": [], "reels.watch": [StepResult.ok()] * 3,
                "reels.swipe_next": [StepResult.failed("nie potwierdzono"),
                                     StepResult.ok()]}
        ctx, res, names = self._run(spec, self.BASE, budget=Budget(max_items=3))
        self.assertEqual(names.count("reels.watch"), 3)
        self.assertEqual(res.stopped_by, "items")

    def test_two_swipe_failures_stop_the_plugin(self):
        spec = {"reels.open": [], "reels.watch": [StepResult.ok()] * 5,
                "reels.swipe_next": [StepResult.failed("x"),
                                     StepResult.failed("x")]}
        ctx, res, names = self._run(spec, self.BASE, budget=Budget(max_items=5))
        self.assertEqual(res.stopped_by, "error")
        # stopped_by=error → status failed (kolejka/panel nie kłamią „OK”)
        self.assertEqual(res.status, "failed")
        self.assertLess(names.count("reels.watch"), 5)

    def test_step_with_requires_screen_is_routed_first(self):
        """Zabłąkany BACK potrafi wyrzucić bota z rolek. Krok deklarujący
        ekran wejściowy musi wtedy najpierw tam dojechać, a nie wykonać się
        na stronie głównej i „nic nie znaleźć"."""
        nav = FakeNavigator()
        nav.identify = lambda: type("S", (), {"top": "home_feed", "overlay": None})()
        ctx = FakeCtx(nav)

        class NeedsReels:
            requires_screen = "reels_feed"
            def run(_s, c, p):
                self.recorder.append(("reels.harvest_likers", p))
                return StepResult.ok(saved=1)

        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": [StepResult.ok()]}
        base = fake_registry(spec, self.recorder)
        self.mod.steps.get = lambda n: (
            (lambda: NeedsReels()) if n == "reels.harvest_likers" else base(n))
        s = {**self.BASE, "likers": {"harvest": {"enabled": True, "collect": 3}}}
        self.mod.ReelsNiche().run(ctx, s, Budget(max_items=1))
        self.assertIn("reels_feed", nav.calls)

    def test_step_fails_when_screen_unreachable(self):
        nav = FakeNavigator(fail_on={"reels_feed"})
        nav.identify = lambda: type("S", (), {"top": "home_feed", "overlay": None})()
        ctx = FakeCtx(nav)
        ran = []

        class NeedsReels:
            requires_screen = "reels_feed"
            def run(_s, c, p):
                ran.append(1)
                return StepResult.ok()

        self.mod.steps.get = lambda n: (lambda: NeedsReels())
        res = self.mod.ReelsNiche().run(ctx, self.BASE, Budget(max_items=1))
        self.assertEqual(ran, [])                     # krok nie wykonał się
        self.assertIn("reels_niche.wrong_screen", ctx.kinds())

    def test_open_failure_returns_failed(self):
        spec = {"reels.open": [StepResult.failed("brak kotwicy")],
                "reels.watch": []}
        ctx, res, names = self._run(spec, self.BASE)
        self.assertEqual(res.status, "failed")
        self.assertNotIn("reels.watch", names)


if __name__ == "__main__":
    unittest.main()
