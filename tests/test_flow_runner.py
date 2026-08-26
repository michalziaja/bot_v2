"""Testy FlowRunner + registry — parsowanie YAML + dispatch bez telefonu."""
from __future__ import annotations
import tempfile, shutil, unittest
from pathlib import Path
from unittest.mock import MagicMock

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.runner.flow_runner import _unpack, FlowRunner
from v2.runner.step        import StepResult
from v2.steps              import registry


class UnpackTests(unittest.TestCase):
    def test_scalar_step(self):
        n, p, oe = _unpack("system.wake")
        self.assertEqual((n, p, oe), ("system.wake", {}, "abort"))

    def test_dict_step_with_params(self):
        n, p, oe = _unpack({"system.ensure_screen": {"target": "home_feed"}})
        self.assertEqual(n, "system.ensure_screen")
        self.assertEqual(p, {"target": "home_feed"})
        self.assertEqual(oe, "abort")

    def test_on_error_extracted(self):
        n, p, oe = _unpack({"system.foo": {"x": 1, "on_error": "continue"}})
        self.assertEqual(p, {"x": 1})
        self.assertEqual(oe, "continue")


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._backup = dict(registry._STEPS)
        registry.clear()

    def tearDown(self):
        registry.clear()
        registry._STEPS.update(self._backup)

    def test_register_and_get(self):
        @registry.register
        class Dummy:
            name = "test.dummy"
            requires_screen = None
            def run(self, ctx, params): return StepResult.ok()
        self.assertIn("test.dummy", registry.known())
        cls = registry.get("test.dummy")
        self.assertIs(cls, Dummy)

    def test_duplicate_registration_raises(self):
        @registry.register
        class A:
            name = "dup"
            requires_screen = None
            def run(self, ctx, params): return StepResult.ok()
        with self.assertRaises(ValueError):
            @registry.register
            class B:
                name = "dup"
                requires_screen = None
                def run(self, ctx, params): return StepResult.ok()

    def test_missing_name_raises(self):
        with self.assertRaises(ValueError):
            @registry.register
            class NoName:
                requires_screen = None
                def run(self, ctx, params): return StepResult.ok()


class FlowRunnerTests(unittest.TestCase):
    def setUp(self):
        self._backup = dict(registry._STEPS)
        registry.clear()
        self.tmp_flows = Path(tempfile.mkdtemp())

    def tearDown(self):
        registry.clear()
        registry._STEPS.update(self._backup)
        shutil.rmtree(self.tmp_flows)

    def _fake_ctx(self):
        ctx = MagicMock()
        ctx.humanizer.pause.return_value = 0.0
        # navigator.identify().top zawsze zwróci None (żeby requires_screen=None nie kolidował)
        ctx.navigator.identify.return_value = MagicMock(top=None)
        # events.log = no-op
        return ctx

    def test_scalar_and_dict_steps_run(self):
        run_calls = []
        @registry.register
        class A:
            name = "test.a"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append(("a", params)); return StepResult.ok()
        @registry.register
        class B:
            name = "test.b"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append(("b", params)); return StepResult.ok()

        # napisz mini flow do temp katalogu (patchujemy FLOWS_DIR)
        (self.tmp_flows / "mini.yml").write_text(
            "name: mini\nsteps:\n  - test.a\n  - test.b: {x: 1}\n", encoding="utf-8")

        import v2.runner.flow_runner as fr
        old = fr.FLOWS_DIR; fr.FLOWS_DIR = self.tmp_flows
        try:
            runner = FlowRunner(ctx=self._fake_ctx())
            summary = runner.run("mini")
        finally:
            fr.FLOWS_DIR = old

        self.assertEqual(summary["steps_total"], 2)
        self.assertEqual(summary["steps_ok"], 2)
        self.assertEqual(run_calls, [("a", {}), ("b", {"x": 1})])

    def test_failed_step_aborts_by_default(self):
        run_calls = []
        @registry.register
        class Fail:
            name = "test.fail"; requires_screen = None
            def run(self, ctx, params): return StepResult.failed("boom")
        @registry.register
        class After:
            name = "test.after"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("after"); return StepResult.ok()

        (self.tmp_flows / "f.yml").write_text(
            "name: f\nsteps:\n  - test.fail\n  - test.after\n", encoding="utf-8")

        import v2.runner.flow_runner as fr
        old = fr.FLOWS_DIR; fr.FLOWS_DIR = self.tmp_flows
        try:
            summary = FlowRunner(ctx=self._fake_ctx()).run("f")
        finally:
            fr.FLOWS_DIR = old

        self.assertFalse(summary["ok"])
        self.assertNotIn("after", run_calls)   # abort zablokował drugi krok

    def test_skip_until_fast_forwards(self):
        """Krok który zwraca skipped z skip_until="reels.swipe_next" powoduje
        że runner pomija wszystko do najbliższego kroku o tej nazwie."""
        run_calls = []
        @registry.register
        class Ad:
            name = "reels.watch"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("watch")
                return StepResult.skipped("ad_detected", skip_until="reels.swipe_next")
        @registry.register
        class Ai:
            name = "reels.analyze_frames_ai"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("ai"); return StepResult.ok()
        @registry.register
        class Like:
            name = "reels.like"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("like"); return StepResult.ok()
        @registry.register
        class Harvest:
            name = "reels.harvest_likers"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("harvest"); return StepResult.ok()
        @registry.register
        class Next:
            name = "reels.swipe_next"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("next"); return StepResult.ok()
        @registry.register
        class Watch2:
            name = "reels.watch_second"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("watch2"); return StepResult.ok()

        (self.tmp_flows / "ad.yml").write_text(
            "name: ad\nsteps:\n"
            "  - reels.watch\n"
            "  - reels.analyze_frames_ai\n"
            "  - reels.like\n"
            "  - reels.harvest_likers\n"
            "  - reels.swipe_next\n"
            "  - reels.watch_second\n", encoding="utf-8")

        import v2.runner.flow_runner as fr
        old = fr.FLOWS_DIR; fr.FLOWS_DIR = self.tmp_flows
        try:
            summary = FlowRunner(ctx=self._fake_ctx()).run("ad")
        finally:
            fr.FLOWS_DIR = old

        # watch (ad) → skip_until swipe_next → pominięte: ai, like, harvest
        # potem swipe_next wykonany, potem watch_second
        self.assertEqual(run_calls, ["watch", "next", "watch2"])
        self.assertTrue(summary["ok"])

    def test_skip_until_no_target_ends_flow(self):
        """Jeśli w flow nie ma kroku o nazwie skip_until, runner kończy flow."""
        run_calls = []
        @registry.register
        class Ad:
            name = "reels.watch"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("watch")
                return StepResult.skipped("ad_detected", skip_until="reels.swipe_next")
        @registry.register
        class Never:
            name = "reels.like"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("like"); return StepResult.ok()

        (self.tmp_flows / "ad2.yml").write_text(
            "name: ad2\nsteps:\n  - reels.watch\n  - reels.like\n", encoding="utf-8")

        import v2.runner.flow_runner as fr
        old = fr.FLOWS_DIR; fr.FLOWS_DIR = self.tmp_flows
        try:
            FlowRunner(ctx=self._fake_ctx()).run("ad2")
        finally:
            fr.FLOWS_DIR = old

        # like nie został wywołany bo nie ma swipe_next
        self.assertEqual(run_calls, ["watch"])

    def test_on_error_continue_lets_flow_finish(self):
        run_calls = []
        @registry.register
        class Fail:
            name = "test.fail"; requires_screen = None
            def run(self, ctx, params): return StepResult.failed("boom")
        @registry.register
        class After:
            name = "test.after"; requires_screen = None
            def run(self, ctx, params):
                run_calls.append("after"); return StepResult.ok()

        (self.tmp_flows / "f.yml").write_text(
            "name: f\nsteps:\n  - test.fail: {on_error: continue}\n  - test.after\n",
            encoding="utf-8")

        import v2.runner.flow_runner as fr
        old = fr.FLOWS_DIR; fr.FLOWS_DIR = self.tmp_flows
        try:
            summary = FlowRunner(ctx=self._fake_ctx()).run("f")
        finally:
            fr.FLOWS_DIR = old

        # runner nie abortował — after się wykonał
        self.assertIn("after", run_calls)


if __name__ == "__main__":
    unittest.main()
