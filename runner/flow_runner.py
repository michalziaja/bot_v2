"""FlowRunner — ładuje flow YAML, iteruje kroki, loguje wyniki.

Cienki: nie zna semantyki żadnego pluginu. Silnik decyzyjny (gate/match/chance)
siedzi w pluginach — patrz ARCHITEKTURA.md §5.

Obsługiwana gramatyka YAML:
  name: <flow_id>
  description: <str>
  steps:
    - system.wake                          # krok bez params
    - system.ensure_screen:                # krok z params (dict inline lub multiline)
        target: home_feed
    - system.stats_snapshot:
        when: session_start
        on_error: continue                 # abort | continue | skip
"""
from __future__ import annotations
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import yaml

from v2.runner.context import Context
from v2.runner.step    import StepResult
from v2.steps import registry


FLOWS_DIR = Path(__file__).resolve().parent.parent / "flows"


class FlowError(Exception): pass


@dataclass
class FlowRunner:
    ctx: Context

    def load_flow(self, flow_id: str) -> dict:
        p = FLOWS_DIR / f"{flow_id}.yml"
        if not p.exists():
            raise FlowError(f"flow nie istnieje: {p}")
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    def run(self, flow_id: str) -> dict:
        flow = self.load_flow(flow_id)
        self.ctx.log("session.start",
                     flow=flow_id,
                     account=self.ctx.account.account_id,
                     session_id=self.ctx.session_id,
                     ig_package=self.ctx.ig_package)

        results = []
        failed  = False
        steps_list = flow.get("steps") or []
        i = 0
        while i < len(steps_list):
            raw = steps_list[i]
            step_name, params, on_error = _unpack(raw)
            self._banner(i + 1, step_name, params)

            try:
                cls = registry.get(step_name)
            except KeyError as e:
                self.ctx.log("flow.step.unknown", step=step_name)
                print(f"   ! nieznany krok: {step_name}")
                if on_error == "abort":
                    failed = True; break
                i += 1; continue

            step = cls()

            # requires_screen check
            req = getattr(step, "requires_screen", None)
            if req:
                got = self.ctx.navigator.identify().top
                if got != req:
                    print(f"   ! wymagany ekran: {req}, aktualnie: {got} → ensure()")
                    if not self.ctx.navigator.ensure(req):
                        res = StepResult.failed(f"nie doprowadzono do {req}")
                        self._log_result(step_name, res)
                        results.append((step_name, res))
                        if on_error == "abort": failed = True; break
                        i += 1; continue

            # humanizer pre-pause — jednolita z stepcall (pauza między krokami atomowymi)
            self.ctx.humanizer.pause("between_steps", why=f"flow_runner:{step_name}")

            t0 = time.monotonic()
            try:
                res = step.run(self.ctx, params)
            except Exception as e:
                res = StepResult.failed(f"exception: {e}\n{traceback.format_exc()}")
            res.duration_ms = int((time.monotonic() - t0) * 1000)

            self._log_result(step_name, res)
            results.append((step_name, res))

            if res.status == "failed" and on_error == "abort":
                print(f"   STOP — on_error=abort")
                failed = True
                break

            # fast-forward — plugin poprosił o pominięcie kroków aż do wskazanego
            skip_target = (res.payload or {}).get("skip_until")
            if skip_target:
                jumped = False
                for j in range(i + 1, len(steps_list)):
                    other_name, _, _ = _unpack(steps_list[j])
                    if other_name == skip_target:
                        skipped_count = j - i - 1
                        if skipped_count > 0:
                            print(f"   → fast-forward do {skip_target} "
                                  f"(pomijam {skipped_count} kroków)")
                            self.ctx.log("flow.fast_forward",
                                         from_step=step_name, to=skip_target,
                                         skipped=skipped_count)
                        i = j
                        jumped = True
                        break
                if not jumped:
                    print(f"   → fast-forward: brak {skip_target}, koniec flow")
                    self.ctx.log("flow.fast_forward_end",
                                 from_step=step_name, target=skip_target)
                    break
                continue

            i += 1

        summary = {
            "flow": flow_id,
            "ok": not failed,
            "steps_total": len(results),
            "steps_ok":     sum(1 for _, r in results if r.status == "ok"),
            "steps_skip":   sum(1 for _, r in results if r.status == "skipped"),
            "steps_failed": sum(1 for _, r in results if r.status == "failed"),
        }
        self.ctx.log("session.end", **summary)
        return summary

    # ---- pretty print ----
    def _banner(self, n, name, params):
        p = "" if not params else f"  params={params}"
        print()
        print(f"── KROK {n} — {name}{p}")

    def _log_result(self, name, res: StepResult):
        color = {"ok": "OK  ", "skipped": "SKIP", "failed": "FAIL",
                 "aborted_by_gate": "GATE"}.get(res.status, "??  ")
        payload = res.payload or {}
        # Na stdout: status + reason/error. Pełny payload (np. likes/comments
        # rolki) jest w evencie `step` — drugi dump dublował te same liczby.
        reason  = payload.get("reason")
        rline   = f" ({reason})" if reason else ""
        errline = f"  error={res.error}" if res.error else ""
        print(f"   {color} {name}{rline}  {res.duration_ms}ms{errline}")
        self.ctx.log("step",
                     step=name, status=res.status,
                     payload=res.payload, error=res.error,
                     duration_ms=res.duration_ms)


def _unpack(raw) -> tuple[str, dict, str]:
    """Rozbierz element listy `steps:` na (name, params, on_error).
    - "system.wake"                                 → ("system.wake", {}, "abort")
    - {"system.ensure_screen": {"target": "..."}}   → ("system.ensure_screen", {...}, "abort")
    - {"system.foo": {..., "on_error": "continue"}} → ("system.foo", {bez on_error}, "continue")
    """
    if isinstance(raw, str):
        return raw, {}, "abort"
    if isinstance(raw, dict) and len(raw) == 1:
        name, params = next(iter(raw.items()))
        params = dict(params) if isinstance(params, dict) else {}
        on_error = params.pop("on_error", "abort")
        return name, params, on_error
    raise FlowError(f"nie umiem sparsować kroku: {raw!r}")
