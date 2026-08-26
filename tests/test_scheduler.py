"""Testy harmonogramu: wycena czasu, wzorzec cyklu, egzekucja.

Wszystko bez telefonu — to czysta logika czasu i plików. Konta i urządzenia
są budowane w tmpdirze, więc testy nie zależą od stanu `v2/config`.
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.plugins.store import DEFAULT_SCENARIO
from v2.scheduler import device_planner as dp
from v2.scheduler import state as sched_state
from v2.scheduler import tick as tick_mod
from v2.scheduler.estimator import (DEFAULT_TASK_CAP_MIN, PROLOGUE_EPILOGUE_S,
                                    estimate_tasks)
from v2.scheduler.planner import dates_ahead, min_to_hhmm, pick_scenario


def _row(plugin, minutes, *, enabled=True, for_account=True):
    return {"id": plugin, "plugin": plugin, "label": plugin, "enabled": enabled,
            "plugin_enabled_for_account": for_account,
            "resolved": {"budget": {"max_duration_min": minutes} if minutes else {}}}


class PickScenarioTests(unittest.TestCase):
    def test_empty_list_means_default(self):
        self.assertEqual(pick_scenario({}, random.Random(1)), DEFAULT_SCENARIO)

    def test_picks_from_scenarios(self):
        cfg = {"scenarios": ["grzanie", "zaangazowanie"]}
        self.assertIn(pick_scenario(cfg, random.Random(1)), cfg["scenarios"])


class DatesAheadTests(unittest.TestCase):
    def test_includes_start_date(self):
        self.assertEqual(dates_ahead("2026-08-02", 1), ["2026-08-02"])

    def test_consecutive_dates(self):
        self.assertEqual(dates_ahead("2026-08-30", 4),
                         ["2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02"])


class EstimatorTests(unittest.TestCase):
    def test_sums_task_limits_gaps_overhead_and_buffer(self):
        est = estimate_tasks([_row("a", 10), _row("b", 20)],
                             {"min": 30, "max": 60}, speed=1.0, safety=0.10)
        self.assertEqual(est["breakdown"]["tasks_s"], 30 * 60)
        self.assertEqual(est["breakdown"]["gaps_s"], 60)
        self.assertEqual(est["breakdown"]["overhead_s"], PROLOGUE_EPILOGUE_S)
        base = 30 * 60 + 60 + PROLOGUE_EPILOGUE_S
        self.assertEqual(est["total_s"], round(base * 1.10))

    def test_disabled_task_costs_nothing(self):
        est = estimate_tasks([_row("a", 10), _row("b", 20, enabled=False)], {})
        self.assertEqual(est["task_count"], 1)

    def test_plugin_disabled_for_account_is_skipped_with_warning(self):
        est = estimate_tasks([_row("a", 10, for_account=False)], {})
        self.assertEqual(est["task_count"], 0)
        self.assertTrue(any("wyłączony dla konta" in w for w in est["warnings"]))

    def test_missing_time_limit_falls_back_and_warns(self):
        est = estimate_tasks([_row("a", None)], {})
        self.assertEqual(est["breakdown"]["tasks_s"], DEFAULT_TASK_CAP_MIN * 60)
        self.assertTrue(any("brak max_duration_min" in w for w in est["warnings"]))
        self.assertTrue(est["tasks"][0]["assumed"])

    def test_speed_shortens_gaps_but_not_task_limits(self):
        slow = estimate_tasks([_row("a", 10), _row("b", 10)], {"max": 60}, speed=1.0)
        fast = estimate_tasks([_row("a", 10), _row("b", 10)], {"max": 60}, speed=4.0)
        self.assertEqual(slow["breakdown"]["tasks_s"], fast["breakdown"]["tasks_s"])
        self.assertLess(fast["breakdown"]["gaps_s"], slow["breakdown"]["gaps_s"])

    def test_empty_queue_warns_instead_of_silently_zero(self):
        est = estimate_tasks([], {})
        self.assertEqual(est["total_s"], 0)
        self.assertTrue(any("brak włączonych zadań" in w for w in est["warnings"]))


class CycleIndexTests(unittest.TestCase):
    def test_seven_day_cycle_follows_weekday(self):
        # 2026-08-03 = poniedziałek
        self.assertEqual(dp.cycle_index("2026-08-03", 7), 0)
        self.assertEqual(dp.cycle_index("2026-08-09", 7), 6)

    def test_three_day_cycle_is_modulo(self):
        a = dp.cycle_index("2026-08-03", 3)
        b = dp.cycle_index("2026-08-06", 3)
        self.assertEqual(a, b)


class _PlannerCase(unittest.TestCase):
    """Wspólny tmpdir: jedno urządzenie, konta o zadanych czasach sesji."""

    ACCOUNTS = {"acc1": 3600, "acc2": 3600}
    SESSIONS = {"min": 1, "max": 1}
    HOURS = [9, 22]
    CYCLE_DAYS = 1

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.config_root, self.data_root = root / "config", root / "data"
        (self.config_root / "accounts").mkdir(parents=True)
        (self.config_root / "devices").mkdir(parents=True)
        (self.config_root / "devices" / "phone_A.json").write_text("{}", encoding="utf-8")
        for acc in self.ACCOUNTS:
            (self.config_root / "accounts" / f"{acc}.json").write_text(json.dumps({
                "device": {"device_alias": "phone_A"},
                "schedule": {"enabled": True,
                             "sessions_per_day": self.SESSIONS,
                             "scenarios": ["default"]},
            }), encoding="utf-8")
        # godziny działania i długość cyklu są odgórne — nie w configu konta
        sched_state.update(self.data_root, active_hours=self.HOURS,
                           cycle_days=self.CYCLE_DAYS)
        self.date = "2026-08-08"

    def tearDown(self):
        self._tmp.cleanup()

    def est(self, account, scenario, override):
        return {"total_s": self.ACCOUNTS[account], "breakdown": {},
                "task_count": 2, "warnings": []}

    def build(self, seed=1):
        return dp.build_cycle("phone_A", self.data_root, self.config_root,
                              random.Random(seed), estimate_fn=self.est)

    def day0(self, cycle=None):
        cycle = cycle or self.build()
        return cycle["days"][0]


class DevicePlanTests(_PlannerCase):
    ACCOUNTS = {"acc1": 3600, "acc2": 5400, "acc3": 2400}
    SESSIONS = {"min": 2, "max": 2}

    def test_all_accounts_land_on_one_device_lane(self):
        day = self.day0()
        self.assertEqual({b["account"] for b in day["blocks"]}, set(self.ACCOUNTS))

    def test_blocks_never_overlap(self):
        for seed in range(15):
            day = self.day0(self.build(seed))
            self.assertEqual(day["collisions"], [], f"seed={seed}")

    def test_blocks_respect_global_active_hours(self):
        """Okno jest odgórne — ustawienia konta nie mają tu nic do rzeczy."""
        day = self.day0()
        for b in day["blocks"]:
            self.assertGreaterEqual(dp.block_start_min(b), self.HOURS[0] * 60)
            self.assertLessEqual(dp.block_end_min(b), self.HOURS[1] * 60)

    def test_account_active_hours_are_ignored(self):
        # wąskie okno w koncie NIE zawęża planu — rządzą ramy globalne
        for acc in self.ACCOUNTS:
            p = self.config_root / "accounts" / f"{acc}.json"
            cfg = json.loads(p.read_text(encoding="utf-8"))
            cfg["schedule"]["active_hours"] = [12, 13]
            p.write_text(json.dumps(cfg), encoding="utf-8")
        day = self.day0()
        starts = [dp.block_start_min(b) for b in day["blocks"]]
        self.assertTrue(any(s < 12 * 60 or s >= 13 * 60 for s in starts)
                        or len(starts) <= 1)

    def test_block_carries_its_duration(self):
        day = self.day0()
        for b in day["blocks"]:
            self.assertEqual(b["duration_s"], self.ACCOUNTS[b["account"]])

    def test_cycle_is_written_to_device_directory(self):
        self.build()
        self.assertTrue((self.data_root / "devices" / "phone_A" / "cycle.json").exists())

    def test_reload_reuses_existing_cycle(self):
        c1 = self.build()
        c2 = dp.load_or_build_cycle("phone_A", self.data_root, self.config_root,
                                    random.Random(999), estimate_fn=self.est)
        self.assertEqual([b["id"] for b in c1["days"][0]["blocks"]],
                         [b["id"] for b in c2["days"][0]["blocks"]])

    def test_changing_global_hours_rebuilds_cycle(self):
        self.build()
        sched_state.update(self.data_root, active_hours=[10, 20])
        c2 = dp.load_or_build_cycle("phone_A", self.data_root, self.config_root,
                                    random.Random(1), estimate_fn=self.est)
        # stare godziny z pliku nie mogą zostać — inaczej panel pokazywałby
        # wzorzec ułożony pod inne okno niż to, które właśnie ustawiono
        self.assertEqual(c2["active_hours"], [10, 20])
        self.assertEqual(c2["window"]["from_min"], 10 * 60)
        self.assertEqual(c2["window"]["to_min"], 20 * 60)

    def test_three_day_cycle_has_three_days(self):
        sched_state.update(self.data_root, cycle_days=3)
        cycle = dp.build_cycle("phone_A", self.data_root, self.config_root,
                               random.Random(1), estimate_fn=self.est)
        self.assertEqual(len(cycle["days"]), 3)
        self.assertEqual([d["index"] for d in cycle["days"]], [0, 1, 2])

    def test_starts_are_not_round_hours(self):
        starts = [b["start"] for b in self.day0(self.build(5))["blocks"]]
        self.assertTrue(any(not s.endswith(":00") for s in starts))

    def test_load_pct_is_reported(self):
        self.assertGreater(self.day0()["load_pct"], 0)


class OverloadedDayTests(_PlannerCase):
    ACCOUNTS = {"acc1": 3600, "acc2": 3600, "acc3": 3600}
    SESSIONS = {"min": 3, "max": 3}
    HOURS = [9, 12]

    def test_excess_sessions_are_dropped_with_reason(self):
        day = self.day0()
        self.assertTrue(day["dropped"])
        self.assertTrue(all(d["reason"] for d in day["dropped"]))

    def test_placed_blocks_still_do_not_collide(self):
        self.assertEqual(self.day0()["collisions"], [])


class BlockEditingTests(_PlannerCase):
    SESSIONS = {"min": 1, "max": 1}

    def test_move_rejects_collision(self):
        day = self.day0()
        a, b = day["blocks"]
        with self.assertRaises(dp.PlanError):
            dp.move_block("phone_A", self.data_root, self.config_root, 0,
                          b["id"], a["start"])

    def test_move_with_force_records_the_collision(self):
        day = self.day0()
        a, b = day["blocks"]
        out = dp.move_block("phone_A", self.data_root, self.config_root, 0,
                            b["id"], a["start"], force=True)
        self.assertTrue(out["days"][0]["collisions"])

    def test_moved_block_is_locked(self):
        day = self.day0()
        b = day["blocks"][0]
        target = min_to_hhmm(dp.block_start_min(b) + 15)
        out = dp.move_block("phone_A", self.data_root, self.config_root, 0,
                            b["id"], target)
        self.assertTrue(next(x for x in out["days"][0]["blocks"]
                             if x["id"] == b["id"])["locked"])

    def test_regeneration_keeps_locked_blocks(self):
        day = self.day0()
        b = day["blocks"][0]
        dp.move_block("phone_A", self.data_root, self.config_root, 0,
                      b["id"], min_to_hhmm(dp.block_start_min(b) + 15))
        again = dp.build_cycle("phone_A", self.data_root, self.config_root,
                               random.Random(42), estimate_fn=self.est)
        self.assertIn(b["id"], [x["id"] for x in again["days"][0]["blocks"]])

    def test_autospread_resolves_all_collisions(self):
        day = self.day0()
        a, b = day["blocks"]
        dp.move_block("phone_A", self.data_root, self.config_root, 0,
                      b["id"], a["start"], force=True)
        out = dp.autospread("phone_A", self.data_root, self.config_root, 0)
        self.assertEqual(out["days"][0]["collisions"], [])

    def test_queue_override_changes_duration_only_for_this_block(self):
        day = self.day0()
        b, other = day["blocks"][0], day["blocks"][1]
        long_est = lambda a, s, o: {"total_s": 9999, "breakdown": {}, "task_count": 1,
                                    "warnings": []}
        out = dp.set_block_queue("phone_A", self.data_root, self.config_root, 0,
                                 b["id"], queue_override={"queue": []},
                                 estimate_fn=long_est)
        edited = next(x for x in out["days"][0]["blocks"] if x["id"] == b["id"])
        untouched = next(x for x in out["days"][0]["blocks"] if x["id"] == other["id"])
        self.assertEqual(edited["duration_s"], 9999)
        self.assertEqual(untouched["duration_s"], other["duration_s"])

    def test_delete_removes_block(self):
        day = self.day0()
        b = day["blocks"][0]
        out = dp.delete_block("phone_A", self.data_root, self.config_root, 0, b["id"])
        self.assertNotIn(b["id"], [x["id"] for x in out["days"][0]["blocks"]])

    def test_missing_block_raises(self):
        self.build()
        with self.assertRaises(dp.PlanError):
            dp.delete_block("phone_A", self.data_root, self.config_root, 0, "nope")

    def test_manual_add_is_locked_and_counts_against_sessions(self):
        """Ręczny slot przeżywa regenerację i odejmuje się od sessions_per_day."""
        self.build()
        # jedno konto, 2 sesje/dzień — ręczne dodanie jednej → auto dokłada 1
        for acc in self.ACCOUNTS:
            p = self.config_root / "accounts" / f"{acc}.json"
            cfg = json.loads(p.read_text(encoding="utf-8"))
            cfg["schedule"]["sessions_per_day"] = {"min": 2, "max": 2}
            # zostaw tylko jedno konto w planie, żeby liczyć prosto
            cfg["schedule"]["enabled"] = acc == "acc1"
            p.write_text(json.dumps(cfg), encoding="utf-8")
        # świeży cykl tylko z acc1
        cycle = dp.build_cycle("phone_A", self.data_root, self.config_root,
                               random.Random(1), estimate_fn=self.est)
        # wyczyść auto-bloki, dodaj jeden ręczny
        cycle["days"][0]["blocks"] = []
        dp._write_json(dp.cycle_path("phone_A", self.data_root), cycle)
        out = dp.add_block("phone_A", self.data_root, self.config_root, 0,
                           "acc1", "default", "11:00", estimate_fn=self.est)
        manual_id = out["days"][0]["blocks"][0]["id"]
        self.assertTrue(out["days"][0]["blocks"][0]["locked"])
        again = dp.build_cycle("phone_A", self.data_root, self.config_root,
                               random.Random(7), estimate_fn=self.est)
        day = again["days"][0]
        acc1 = [b for b in day["blocks"] if b["account"] == "acc1"]
        self.assertIn(manual_id, [b["id"] for b in acc1])
        # 2 docelowe − 1 locked = 1 auto → łącznie 2
        self.assertEqual(len(acc1), 2)

    def test_resync_preserves_untouched_history(self):
        """Edycja innego bloku nie kasuje historii done/missed."""
        self.build()
        plan = dp.materialize("phone_A", self.data_root, self.config_root,
                              self.date, estimate_fn=self.est)
        a, b = plan["blocks"][0], plan["blocks"][1]
        old_start = a["start"]
        dp.mark_block("phone_A", self.data_root, self.date, a["id"], "done",
                      ended_at="2026-08-08T10:00:00")
        # przesuń INNY blok we wzorcu
        target = min_to_hhmm(dp.block_start_min(b) + 20)
        dp.move_block("phone_A", self.data_root, self.config_root, 0,
                      b["id"], target)
        # resync na datę planu (move celuje w today — tu wołamy wprost)
        dp.resync_date("phone_A", self.data_root, self.config_root,
                       date=self.date, force_ids={b["id"]})
        reloaded = dp.read_plan("phone_A", self.data_root, self.date)
        hist = next(x for x in reloaded["blocks"] if x["id"] == a["id"])
        self.assertEqual(hist["status"], "done")
        self.assertEqual(hist["start"], old_start)
        moved = next(x for x in reloaded["blocks"] if x["id"] == b["id"])
        self.assertEqual(moved["start"], target)

    def test_force_resync_updates_missed_and_revives(self):
        """Jawne przesunięcie missed → nowe pola ze wzorca + status planned."""
        self.build()
        plan = dp.materialize("phone_A", self.data_root, self.config_root,
                              self.date, estimate_fn=self.est)
        b = plan["blocks"][0]
        dp.mark_block("phone_A", self.data_root, self.date, b["id"], "missed")
        target = min_to_hhmm(dp.block_start_min(b) + 30)
        dp.move_block("phone_A", self.data_root, self.config_root, 0,
                      b["id"], target)
        dp.resync_date("phone_A", self.data_root, self.config_root,
                       date=self.date, force_ids={b["id"]})
        reloaded = dp.read_plan("phone_A", self.data_root, self.date)
        edited = next(x for x in reloaded["blocks"] if x["id"] == b["id"])
        self.assertEqual(edited["start"], target)
        self.assertEqual(edited["status"], "planned")
        self.assertIsNone(edited.get("started_at"))

    def test_force_resync_keeps_done_status_but_applies_fields(self):
        """done zostaje done (historia), ale start/scenariusz idą ze wzorca."""
        self.build()
        plan = dp.materialize("phone_A", self.data_root, self.config_root,
                              self.date, estimate_fn=self.est)
        b = plan["blocks"][0]
        dp.mark_block("phone_A", self.data_root, self.date, b["id"], "done",
                      started_at="t0", ended_at="t1")
        target = min_to_hhmm(dp.block_start_min(b) + 45)
        dp.move_block("phone_A", self.data_root, self.config_root, 0,
                      b["id"], target)
        dp.resync_date("phone_A", self.data_root, self.config_root,
                       date=self.date, force_ids={b["id"]})
        edited = next(x for x in dp.read_plan("phone_A", self.data_root, self.date)["blocks"]
                      if x["id"] == b["id"])
        self.assertEqual(edited["start"], target)
        self.assertEqual(edited["status"], "done")
        self.assertEqual(edited["started_at"], "t0")

    def test_force_resync_never_touches_running(self):
        self.build()
        plan = dp.materialize("phone_A", self.data_root, self.config_root,
                              self.date, estimate_fn=self.est)
        b = plan["blocks"][0]
        old_start = b["start"]
        dp.mark_block("phone_A", self.data_root, self.date, b["id"], "running",
                      started_at="now")
        target = min_to_hhmm(dp.block_start_min(b) + 60)
        dp.move_block("phone_A", self.data_root, self.config_root, 0,
                      b["id"], target)  # wzorzec się zmienia
        dp.resync_date("phone_A", self.data_root, self.config_root,
                       date=self.date, force_ids={b["id"]})
        edited = next(x for x in dp.read_plan("phone_A", self.data_root, self.date)["blocks"]
                      if x["id"] == b["id"])
        self.assertEqual(edited["start"], old_start)
        self.assertEqual(edited["status"], "running")


class MaterializeTests(_PlannerCase):
    def test_materialize_copies_template_with_planned_status(self):
        cycle = self.build()
        plan = dp.materialize("phone_A", self.data_root, self.config_root,
                              self.date, estimate_fn=self.est)
        self.assertEqual(plan["date"], self.date)
        self.assertTrue(all(b["status"] == "planned" for b in plan["blocks"]))
        self.assertEqual(len(plan["blocks"]), len(cycle["days"][0]["blocks"]))

    def test_materialize_is_idempotent(self):
        p1 = dp.materialize("phone_A", self.data_root, self.config_root,
                            self.date, estimate_fn=self.est)
        p2 = dp.materialize("phone_A", self.data_root, self.config_root,
                            self.date, estimate_fn=self.est)
        self.assertEqual(p1["blocks"][0]["id"], p2["blocks"][0]["id"])


class TickTests(_PlannerCase):
    SESSIONS = {"min": 1, "max": 1}

    def _start_collector(self):
        started = []
        return started, lambda acc, scenario, max_s=None: started.append((acc, scenario, max_s))

    def _materialize(self):
        self.build()
        return dp.materialize("phone_A", self.data_root, self.config_root,
                              self.date, estimate_fn=self.est)

    def test_master_switch_off_starts_nothing(self):
        plan = self._materialize()
        started, start = self._start_collector()
        hh, mm = map(int, plan["blocks"][0]["start"].split(":"))
        tick_mod.tick_once(self.data_root, self.config_root, lambda: False, start,
                           now=datetime(2026, 8, 8, hh, mm))
        self.assertEqual(started, [])

    def test_due_block_starts_with_its_time_window(self):
        plan = self._materialize()
        sched_state.set_enabled(self.data_root, True)
        block = plan["blocks"][0]
        hh, mm = map(int, block["start"].split(":"))
        started, start = self._start_collector()
        actions = tick_mod.tick_once(self.data_root, self.config_root, lambda: False,
                                     start, now=datetime(2026, 8, 8, hh, mm))
        self.assertEqual([a["result"] for a in actions], ["started"])
        self.assertEqual(started[0][0], block["account"])
        self.assertAlmostEqual(started[0][2], block["duration_s"], delta=60)

    def test_late_start_gets_a_shorter_window(self):
        from datetime import timedelta
        plan = self._materialize()
        sched_state.set_enabled(self.data_root, True)
        block = plan["blocks"][0]
        hh, mm = map(int, block["start"].split(":"))
        on_time = datetime(2026, 8, 8, hh, mm)

        started, start = self._start_collector()
        tick_mod.tick_once(self.data_root, self.config_root, lambda: False, start,
                           now=on_time)
        full = started[0][2]

        dp.mark_block("phone_A", self.data_root, self.date, block["id"], "planned")
        started2, start2 = self._start_collector()
        tick_mod.tick_once(self.data_root, self.config_root, lambda: False, start2,
                           now=on_time + timedelta(minutes=4))
        self.assertLess(started2[0][2], full)

    def test_busy_device_does_not_start_and_keeps_block_planned(self):
        plan = self._materialize()
        sched_state.set_enabled(self.data_root, True)
        block = plan["blocks"][0]
        hh, mm = map(int, block["start"].split(":"))
        started, start = self._start_collector()
        actions = tick_mod.tick_once(self.data_root, self.config_root, lambda: True,
                                     start, now=datetime(2026, 8, 8, hh, mm))
        self.assertIn("device_busy", [a["result"] for a in actions])
        self.assertEqual(started, [])
        reloaded = dp.read_plan("phone_A", self.data_root, self.date)
        self.assertEqual(reloaded["blocks"][0]["status"], "planned")

    def test_expired_block_is_marked_missed_not_dropped_silently(self):
        self._materialize()
        sched_state.set_enabled(self.data_root, True)
        started, start = self._start_collector()
        actions = tick_mod.tick_once(self.data_root, self.config_root, lambda: False,
                                     start, now=datetime(2026, 8, 8, 23, 55))
        self.assertTrue(actions)
        self.assertTrue(all(a["result"] == "missed" for a in actions))
        reloaded = dp.read_plan("phone_A", self.data_root, self.date)
        self.assertTrue(all(b["status"] == "missed" for b in reloaded["blocks"]))

    def test_block_is_not_started_before_its_time(self):
        plan = self._materialize()
        sched_state.set_enabled(self.data_root, True)
        first = min(dp.block_start_min(b) for b in plan["blocks"])
        started, start = self._start_collector()
        tick_mod.tick_once(self.data_root, self.config_root, lambda: False, start,
                           now=datetime(2026, 8, 8, (first - 30) // 60, (first - 30) % 60))
        self.assertEqual(started, [])

    def test_running_block_is_closed_when_its_window_ends(self):
        plan = self._materialize()
        block = plan["blocks"][0]
        dp.mark_block("phone_A", self.data_root, self.date, block["id"], "running")
        closed = tick_mod.finish_running_blocks(self.data_root, self.config_root,
                                                now=datetime(2026, 8, 8, 23, 59))
        self.assertEqual([c["block"] for c in closed], [block["id"]])


class TimelineHygieneTests(_PlannerCase):
    """Orphan'y usuniętych kont, bloki poza oknem, clear osi."""

    ACCOUNTS = {"alive": 1800, "ghost": 1800}
    SESSIONS = {"min": 1, "max": 1}
    HOURS = [9, 22]

    def test_scrub_removes_blocks_for_deleted_accounts(self):
        cycle = self.build()
        # zasymuluj usunięcie konta: bloki zostają, plik config znika
        (self.config_root / "accounts" / "ghost.json").unlink()
        scrub = dp.scrub_orphan_blocks("phone_A", self.data_root, self.config_root)
        self.assertGreater(scrub["removed_cycle"], 0)
        cycle = dp.read_cycle("phone_A", self.data_root)
        accounts = {b["account"] for d in cycle["days"] for b in d["blocks"]}
        self.assertNotIn("ghost", accounts)
        self.assertIn("alive", accounts)

    def test_purge_on_delete_cleans_cycle(self):
        self.build()
        (self.config_root / "accounts" / "ghost.json").unlink()
        out = dp.purge_account_from_plans("ghost", self.data_root, self.config_root)
        self.assertGreater(out["removed_cycle"], 0)
        cycle = dp.read_cycle("phone_A", self.data_root)
        self.assertFalse(any(b["account"] == "ghost"
                             for d in cycle["days"] for b in d["blocks"]))

    def test_locked_outside_window_does_not_block_sessions(self):
        """technature-case: locked o 05:14 przy oknie 9–22 nie zjada puli."""
        cycle = self.build()
        # zostaw tylko locked poza oknem dla alive
        cycle["days"][0]["blocks"] = [{
            "id": "bout", "account": "alive", "scenario": "default",
            "start": "05:14", "duration_s": 1800, "gap_s": 300,
            "est": {}, "queue_override": None, "locked": True,
        }]
        # wyłącz ghost, żeby liczyć prosto
        p = self.config_root / "accounts" / "ghost.json"
        cfg = json.loads(p.read_text(encoding="utf-8"))
        cfg["schedule"]["enabled"] = False
        p.write_text(json.dumps(cfg), encoding="utf-8")
        dp._write_json(dp.cycle_path("phone_A", self.data_root), cycle)
        again = dp.build_cycle("phone_A", self.data_root, self.config_root,
                               random.Random(3), estimate_fn=self.est)
        alive = [b for b in again["days"][0]["blocks"] if b["account"] == "alive"]
        self.assertEqual(len(alive), 1)
        self.assertTrue(9 * 60 <= dp.block_start_min(alive[0]) < 22 * 60)

    def test_load_rebuilds_after_orphans(self):
        self.build()
        (self.config_root / "accounts" / "ghost.json").unlink()
        # zostaw orphan w pliku (scrub w load go usunie i przebuduje)
        loaded = dp.load_or_build_cycle("phone_A", self.data_root, self.config_root,
                                        random.Random(1), estimate_fn=self.est)
        accounts = {b["account"] for d in loaded["days"] for b in d["blocks"]}
        self.assertNotIn("ghost", accounts)
        self.assertIn("alive", accounts)

    def test_clear_device_timeline_removes_cycle_and_schedule(self):
        self.build()
        dp.materialize("phone_A", self.data_root, self.config_root, self.date,
                       estimate_fn=self.est)
        self.assertTrue(dp.cycle_path("phone_A", self.data_root).exists())
        self.assertTrue(dp.plan_path("phone_A", self.data_root, self.date).exists())
        out = dp.clear_device_timeline("phone_A", self.data_root)
        self.assertTrue(out["removed"]["cycle"])
        self.assertGreaterEqual(out["removed"]["schedule_files"], 1)
        self.assertIsNone(dp.read_cycle("phone_A", self.data_root))
        self.assertIsNone(dp.read_plan("phone_A", self.data_root, self.date))


class MasterSwitchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_to_off(self):
        self.assertFalse(sched_state.is_enabled(self.data_root))

    def test_defaults_include_hours_and_cycle(self):
        s = sched_state.read(self.data_root)
        self.assertEqual(s["active_hours"], [9, 22])
        self.assertEqual(s["cycle_days"], 1)

    def test_toggle_persists(self):
        sched_state.set_enabled(self.data_root, True)
        self.assertTrue(sched_state.is_enabled(self.data_root))
        sched_state.set_enabled(self.data_root, False)
        self.assertFalse(sched_state.is_enabled(self.data_root))

    def test_corrupt_file_reads_as_off(self):
        (self.data_root / "scheduler.json").write_text("{[", encoding="utf-8")
        self.assertFalse(sched_state.is_enabled(self.data_root))

    def test_invalid_cycle_falls_back(self):
        sched_state.update(self.data_root, cycle_days=5)
        self.assertEqual(sched_state.cycle_days(self.data_root), 1)


if __name__ == "__main__":
    unittest.main()
