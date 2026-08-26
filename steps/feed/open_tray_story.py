"""feed.open_tray_story — otwórz relację z tacki na stronie głównej.

Kandydatów rozpoznajemy po `content-desc` awatara, który w tej wersji IG ma
postać:

    "Relacja użytkownika agagrzelakk, 1 z 21, Nie wyświetlono."

Niesie komplet potrzebnych informacji: nick, pozycję w tacce i to, czy relacja
była już oglądana. Stary bot zgadywał po prefiksach tekstu — tu mamy jedno
źródło i jeden parser.

Własne konto pomijamy dwiema drogami naraz (nick z configu i podpis
„Twoja relacja"), bo wejście we własną relację to nie tylko zmarnowany cykl —
z tego ekranu wychodzi się inaczej niż z cudzej relacji.
"""
from __future__ import annotations

import random
import re

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system._ui_helpers import settle_for_animation

AVATAR_ID = "com.instagram.android:id/avatar_image_view"
USERNAME_ID = "com.instagram.android:id/username"

# "Relacja użytkownika <nick>, <i> z <n>, <stan>."
DESC_RE = re.compile(
    r"(?:relacja użytkownika|story by|\bstory\b)\s+([\w.\-_]+)"
    r"(?:\s*,\s*(\d+)\s*(?:z|of)\s*(\d+))?"
    r"(?:\s*,\s*(.+?))?\.?$",
    re.IGNORECASE)

OWN_LABELS = ("twoja relacja", "your story", "dodaj do relacji", "add to your story")
UNSEEN_HINTS = ("nie wyświetlono", "nie wyswietlono", "not seen", "not viewed",
                "unseen", "nowe")

SCRATCH_SEEN = "tray_opened"      # nicki otwarte w tej sesji przeglądania


@register
class OpenTrayStory:
    name = "feed.open_tray_story"
    requires_screen = "home_feed"

    def run(self, ctx, params):
        rng = random.Random()
        prefer_unseen = bool(params.get("prefer_unseen", True))
        own = _own_username(ctx)

        used = ctx.scratch.setdefault(SCRATCH_SEEN, set())
        rows, cands = self._look(ctx, own, used)

        # Tacka jest tylko na samej górze feedu — kilka przewinięć wystarczy,
        # żeby zniknęła. Zanim uznamy, że nie ma kogo oglądać, wracamy na górę
        # tak, jak zrobiłby to człowiek: paroma szybkimi machnięciami w dół.
        if not cands and params.get("scroll_to_top", True):
            for _ in range(int(params.get("scroll_to_top_swipes", 3))):
                _swipe_to_top(ctx)
            # Po scrollu feedu wystarczy stabilizacja animacji; kolejny _look()
            # i tak robi własny dump hierarchii.
            settle_for_animation(ctx, max_wait_s=1.5, fallback_wait_s=1.0)
            rows, cands = self._look(ctx, own, used)
            ctx.log("feed.open_tray_story.scrolled_to_top", found=len(cands))

        if not cands:
            ctx.log("feed.open_tray_story.no_candidates", in_tray=len(rows),
                    already_used=len(used))
            return StepResult.skipped("no_candidates", in_tray=len(rows))

        if prefer_unseen:
            # nieobejrzane najpierw; przy równych zostaje kolejność z tacki,
            # czyli ta, którą narzucił Instagram
            cands.sort(key=lambda r: r["seen"] is True)

        target = cands[0]
        used.add(target["username"])
        ctx.ht.tap(*target["center"], pause_kind="tap")
        ctx.humanizer.pause("between_steps")

        if not ctx.navigator.ensure("story_viewer", max_steps=2):
            ctx.log("feed.open_tray_story.no_viewer", username=target["username"])
            ctx.navigator.ensure("home_feed", max_steps=3)
            return StepResult.failed(f"tap w '{target['username']}' "
                                     f"nie otworzył relacji")

        ctx.log("feed.open_tray_story.done", username=target["username"],
                seen=target["seen"], position=target["position"])
        return StepResult.ok(username=target["username"], seen=target["seen"],
                             position=target["position"])


    def _look(self, ctx, own: str, used: set):
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        rows = collect_tray(flat, own_username=own)
        return rows, [r for r in rows if r["username"] not in used]


# ============================================================
def _swipe_to_top(ctx) -> None:
    w, h = ctx.account.device["_hardware"]["resolution"]
    ctx.ht.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.80),
                 duration_ms=280, pause_kind="tap")


def collect_tray(flat: list[dict], own_username: str = "") -> list[dict]:
    """Kandydaci z tacki: `{username, seen, position, center}`, bez własnego konta.

    `seen` jest trójwartościowe — `None` gdy opis nie niósł stanu; wtedy nie
    udajemy, że wiemy, i kolejność zostaje taka, jaką dał Instagram.
    """
    own = (own_username or "").strip().lstrip("@").lower()
    own_by_label = _own_avatar_ys(flat)

    out = []
    for e in flat:
        if (e.get("resource-id") or "") != AVATAR_ID:
            continue
        desc = (e.get("content-desc") or "").strip()
        m = DESC_RE.search(desc)
        if not m:
            continue
        username = (m.group(1) or "").strip()
        if not username or (own and username.lower() == own):
            continue

        c = _center(e.get("bounds"))
        if not c or any(abs(c[1] - y) < 60 and abs(c[0] - x) < 60
                        for x, y in own_by_label):
            continue

        state = (m.group(4) or "").strip().lower()
        seen = None
        if state:
            seen = not any(h in state for h in UNSEEN_HINTS)
        out.append({"username": username, "seen": seen,
                    "position": int(m.group(2)) if m.group(2) else None,
                    "center": c})
    return out


def _own_avatar_ys(flat: list[dict]) -> list[tuple[int, int]]:
    """Środki kafelków podpisanych „Twoja relacja" — druga droga odsiania siebie,
    na wypadek gdyby nick w configu nie zgadzał się z tym w aplikacji."""
    out = []
    for e in flat:
        text = (e.get("text") or "").strip().lower()
        desc = (e.get("content-desc") or "").strip().lower()
        if text in OWN_LABELS or desc in OWN_LABELS:
            c = _center(e.get("bounds"))
            if c:
                # podpis jest POD awatarem — cofamy się do jego środka
                out.append((c[0], c[1] - 200))
    return out


def _own_username(ctx) -> str:
    try:
        return (ctx.account.raw.get("meta") or {}).get("ig_username") or ""
    except Exception:
        return ""


def _center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None
