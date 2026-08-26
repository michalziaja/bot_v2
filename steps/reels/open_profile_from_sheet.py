"""reels.open_liker_profile / reels.open_commenter_profile — wejście na profil
z otwartego sheetu rolki.

Krok zakłada, że sheet jest JUŻ OTWARTY (otwiera go plugin przed pętlą
interakcji) i po sobie go nie zamyka — zamknięcie należy do wołającego,
bo między kolejnymi profilami wracamy do tej samej listy.

Tapujemy w NICK (z biasem w prawo od awatara), nie w awatar. Tap w awatar /
hitbox ringu otwiera relację, a nie profil — live 14:06: `prefer_ring`
wybierał właśnie takich, a środek krótkiego nicku lądował w story.

`baseline_screen` ustawiamy przed tapem, żeby `profile.exit` wiedział, dokąd
wracać — bez tego wychodzi na `home_feed` i gubi listę.

Live 14:23 (`landed_reels_feed`): animacja sheet→profil miga rolką pod spodem;
poll łapał `reels_feed`, wołał BACK (wychodził z właśnie otwartego profilu)
i raportował `open_failed`, mimo że profil był na ekranie.
"""
from __future__ import annotations

import time

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels import _sheet

SCRATCH_USED = "hot_used"       # nicki odwiedzone z bieżącego sheetu

# Po tapie UI animuje przejście — natychmiastowy identify wciąż widzi listę
# albo łapie story. Pollujemy krótko zamiast jednego odczytu.
_PROFILE_WAIT_S = 2.4
_PROFILE_POLL_S = 0.30
# Ile kolejnych odczytów `reels_feed` zanim uznamy sheet za zamknięty.
# Jeden hit to często mignięcie feedu pod animacją wejścia na profil.
_REELS_FEED_CONFIRM = 3


class _OpenProfileFromSheet:
    """Wspólna logika; podklasy różnią się tylko listą ID nicku i ekranem bazowym."""
    username_ids: tuple = _sheet.LIKER_USERNAME_IDS
    baseline: str = "reel_likers_list"
    requires_screen = None      # sheet nie jest ekranem, na który da się „dojechać”

    def run(self, ctx, params):
        # `skip_private` NIE jest tutaj — profil prywatny odsiewa sanity check
        # w procedurze profilowej, która widzi nagłówek. Z poziomu listy nie da
        # się tego stwierdzić inaczej niż zgadując po ringu.
        prefer_ring  = bool(params.get("prefer_ring", True))
        require_ring = bool(params.get("require_ring", False))

        used = ctx.scratch.setdefault(SCRATCH_USED, set())

        # Krok zbierający kończy pracę swipe'em, więc lista potrafi jeszcze
        # jechać rozpędem. Dump zrobiony w tej chwili daje współrzędne, których
        # sekundę później już tam nie ma — tap ląduje obok wiersza i zamiast
        # profilu zostajemy na liście.
        ctx.device.wait_for_stable_ui(max_wait_s=1.5)

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        rows = _sheet.collect_rows(flat, self.username_ids)
        if not rows:
            return StepResult.skipped("no_rows")

        cands = _sheet.pick_candidates(rows, prefer_ring=prefer_ring,
                                       require_ring=require_ring, exclude=used)
        if not cands:
            # Rozróżnienie ważne przy diagnozie: „nikt nie ma relacji" wygląda
            # tak samo jak „nie umiemy wykryć relacji na tej wersji IG".
            if require_ring and all(r["ring"] is None for r in rows):
                ctx.log("reels.open_profile.ring_detection_unconfirmed",
                        rows=len(rows), baseline=self.baseline)
            return StepResult.skipped("no_candidates", rows=len(rows),
                                      used=len(used))

        target = cands[0]
        used.add(target["username"])

        ctx.scratch["baseline_screen"] = self.baseline
        # Wiemy, w kogo tapiemy — zapisujemy to jako drugą drogę na nick.
        # Parser nagłówka zależy od ID, które zmienia się między wersjami IG,
        # a bez nicku wpis w bazie odwiedzonych jest bezużyteczny i dedup
        # przestaje działać.
        ctx.scratch["expected_username"] = target["username"]

        landed, saw_profile = self._tap_until_profile(ctx, target)
        # Profil bywa spóźniony względem polla (animacja po mignięciu feedu).
        if landed != "profile_other":
            now = ctx.navigator.identify().top
            if now == "profile_other":
                landed, saw_profile = now, True

        if landed == "profile_other":
            ctx.log("reels.open_profile.done", username=target["username"],
                    ring=target["ring"], source=self.baseline)
            return StepResult.ok(username=target["username"], ring=target["ring"])

        # Tap nie otworzył profilu trwale. NIE wołamy `ensure(profile_other)` —
        # drabinka uznaje listę za ekran do wyjścia i BACK-iem wyprowadza nas
        # z sheetu.
        why = _fail_why(landed, self.baseline, saw_profile=saw_profile)
        xy = _sheet.nick_tap_xy(target)
        ctx.log("reels.open_profile.no_profile",
                username=target["username"], landed=landed, why=why,
                ring=target["ring"], xy=list(xy), baseline=self.baseline,
                saw_profile=saw_profile)

        # Po reels_feed / zgubionym sheetcie: przywróć listę, żeby kolejny
        # kandydat (hot) nie tapił w pustą rolkę. NIE BACK-uj gdy nagle
        # jesteśmy na profilu — to właśnie „otwarto i zgubiono" z live.
        top_now = ctx.navigator.identify().top
        if top_now == "profile_other":
            ctx.log("reels.open_profile.late_profile",
                    username=target["username"], after_landed=landed)
            return StepResult.ok(username=target["username"], ring=target["ring"])

        on_list = top_now == self.baseline
        if not on_list:
            on_list = self._restore_sheet(ctx)
            if not on_list:
                top_after = ctx.navigator.identify().top
                if top_after == "profile_other":
                    return StepResult.ok(
                        username=target["username"], ring=target["ring"])
                ctx.log("reels.open_profile.sheet_restore_failed",
                        username=target["username"], landed=landed,
                        top=top_after)
                # content_search: bez clips — gubi siatkę #; zostaw ekran,
                # hot i tak skończy na no_rows / lost_sheet.
                if ctx.scratch.get("content_search_active"):
                    return StepResult.skipped(
                        "open_failed", username=target["username"],
                        landed=landed, why=why, saw_profile=saw_profile)
                _sheet.return_to_reels(ctx)
                return StepResult.failed(
                    f"po tapie w '{target['username']}' zgubiliśmy listę "
                    f"(landed={landed})")

        return StepResult.skipped(
            "open_failed", username=target["username"],
            landed=landed, why=why, saw_profile=saw_profile)

    def _tap_until_profile(self, ctx, target: dict) -> tuple[str, bool]:
        """Tap w nick; przy story — BACK i retry; przy reels_feed — restore + 1×.

        Zwraca `(top, saw_profile)`. `saw_profile=True` gdy choć raz widzieliśmy
        `profile_other` w pollu (sukces albo „otwarto i zgubiono").
        """
        biases = ("safe", "right") if target.get("ring") is True else ("safe",)
        last_top = self.baseline
        saw_profile = False

        for i, bias in enumerate(biases):
            # Świeże współrzędne przy retry — lista mogła drgnąć po BACK ze story
            if i > 0:
                ctx.device.wait_for_stable_ui(max_wait_s=1.0)
                refreshed = self._refind(ctx, target["username"])
                if refreshed:
                    target = refreshed
            xy = _sheet.nick_tap_xy(target, bias=bias)
            ctx.ht.tap(*xy, pause_kind="tap")
            last_top, saw = _wait_open_result(ctx, self.baseline)
            saw_profile = saw_profile or saw
            if last_top == "profile_other":
                return last_top, True
            if last_top == "story_viewer":
                ctx.log("reels.open_profile.got_story",
                        username=target["username"], bias=bias, xy=list(xy))
                ctx.device.press_back()
                ctx.humanizer.pause("tap")
                ctx.device.wait_for_stable_ui(max_wait_s=1.2)
                last_top = ctx.navigator.identify().top
                # kolejny bias / koniec pętli
                continue
            # Pudło na liście przy ringowym wierszu — jeden retry dalej w prawo
            # (hitbox ringu / Obserwuj). Inne ekrany → koniec biasów.
            if last_top == self.baseline and i + 1 < len(biases):
                continue
            break

        # Sheet zniknął / złe lądowanie — przywróć listę i jeden ostatni tap
        # (nie spalaj nicka twardym failem bez retry).
        if last_top != "profile_other" and last_top != self.baseline:
            if self._restore_sheet(ctx):
                ctx.log("reels.open_profile.retry_after_restore",
                        username=target["username"], after=last_top)
                ctx.device.wait_for_stable_ui(max_wait_s=1.0)
                refreshed = self._refind(ctx, target["username"])
                if refreshed:
                    target = refreshed
                xy = _sheet.nick_tap_xy(target, bias="right")
                ctx.ht.tap(*xy, pause_kind="tap")
                last_top, saw = _wait_open_result(ctx, self.baseline)
                saw_profile = saw_profile or saw
                if last_top == "profile_other":
                    return last_top, True

        return last_top, saw_profile

    def _restore_sheet(self, ctx) -> bool:
        """Wróć na baseline (lista lajków / komentarzy) po zgubieniu sheetu.

        1) Już na liście → OK
        2) BACK raz — gdy profil/story/overlay zasłania listę (NIE gdy
           `reels_feed`: BACK z rolki nic nie przywraca, a bywa szkodliwy)
        3) Z rolki: ponownie tap w ikonę lajków/komentarzy (+ lekki expand)
        """
        top = ctx.navigator.identify().top
        if top == self.baseline:
            return True

        # Profil otworzył się „po terminie" polla — traktuj jako sukces ścieżki
        # wołającej (ona sprawdza top osobno); tu tylko nie zamykaj BACK-iem.
        if top == "profile_other":
            return False

        if top != "reels_feed":
            ctx.device.press_back()
            ctx.humanizer.pause("tap")
            ctx.device.wait_for_stable_ui(max_wait_s=1.0)
            top = ctx.navigator.identify().top
            if top == self.baseline:
                return True
            if top == "profile_other":
                # Byliśmy na profilu, BACK zszedł obok — nie ciągniemy dalej
                return False

        if top == "reels_feed" or top != self.baseline:
            opened = False
            try:
                if self.baseline == "reel_likers_list":
                    opened = _sheet.open_likers_sheet(ctx)
                elif self.baseline == "reel_comments_sheet":
                    opened = _sheet.open_comments_sheet(ctx)
            except Exception as e:
                # Hierarchy / atrapa testowa — nie walimy całego kroku
                ctx.log("reels.open_profile.reopen_error",
                        baseline=self.baseline, err=str(e)[:120])
                opened = False
            if opened:
                # Lekki expand — half-sheet bywa nierozpoznany / bez wierszy
                try:
                    _sheet.expand_sheet(ctx, pulls=1)
                except Exception:
                    pass
                ctx.device.wait_for_stable_ui(max_wait_s=1.2)
                time.sleep(0.35)
                top = ctx.navigator.identify().top
                if top == self.baseline:
                    ctx.log("reels.open_profile.sheet_restored",
                            baseline=self.baseline, how="reopen")
                    return True

        return ctx.navigator.identify().top == self.baseline

    def _refind(self, ctx, username: str) -> dict | None:
        """Po BACK ze story / restore znajdź ten sam nick (świeże bounds)."""
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        for row in _sheet.collect_rows(flat, self.username_ids):
            if row["username"] == username:
                return row
        return None


def _wait_open_result(ctx, baseline: str) -> tuple[str, bool]:
    """Poll identify aż profil / story / potwierdzony reels_feed / timeout.

    Zwraca `(top, saw_profile)`.

    Ważne (live 14:23): NIE kończ na pierwszym `reels_feed` — animacja
    sheet→profil potrafi mignąć rolką pod spodem; kolejne odczyty łapią
    `profile_other`. Gdy raz zobaczymy profil → sukces (nie pollujemy dalej
    aż do feedu).
    """
    deadline = time.monotonic() + _PROFILE_WAIT_S
    baseline_hits = 0
    reels_hits = 0
    saw_profile = False
    last = None
    while time.monotonic() < deadline:
        last = ctx.navigator.identify().top
        if last == "profile_other":
            # Goal 1: jakikolwiek profil w oknie polla = sukces
            return last, True
        if last == "story_viewer":
            return last, saw_profile

        if last == "reels_feed":
            reels_hits += 1
            baseline_hits = 0
            # Potwierdź kilkoma odczytami — jeden hit = często transient
            if reels_hits >= _REELS_FEED_CONFIRM:
                return last, saw_profile
            time.sleep(_PROFILE_POLL_S)
            continue

        reels_hits = 0

        # Zeszliśmy z listy na coś innego niż profil/feed — kończymy poll
        if last not in (baseline, None):
            return last, saw_profile

        if last == baseline:
            baseline_hits += 1
            if baseline_hits >= 3:
                return last, saw_profile
        else:
            baseline_hits = 0
        time.sleep(_PROFILE_POLL_S)

    # Po timeout: ostatnia szansa na spóźniony profil (animacja dogoniła)
    last = ctx.navigator.identify().top
    if last == "profile_other":
        return last, True
    return last or baseline, saw_profile


def _fail_why(landed: str | None, baseline: str, *,
              saw_profile: bool = False) -> str:
    """Krótki powód do logu ludzkiego / JSONL — nie sam `no_profile`."""
    # Profil był w pollu, a skończyliśmy poza nim (race / BACK) —
    # inne brzmienie niż „w ogóle nie otwarto".
    if saw_profile and landed != "profile_other":
        return "opened_then_lost"
    if landed == "story_viewer":
        return "opened_story"
    if landed == baseline:
        return "still_on_list"
    if landed == "reels_feed":
        return "landed_reels_feed"
    if not landed:
        return "unknown_screen"
    return f"landed_{landed}"


@register
class OpenLikerProfile(_OpenProfileFromSheet):
    name = "reels.open_liker_profile"
    username_ids = _sheet.LIKER_USERNAME_IDS
    baseline = "reel_likers_list"


@register
class OpenCommenterProfile(_OpenProfileFromSheet):
    name = "reels.open_commenter_profile"
    username_ids = _sheet.COMMENT_USERNAME_IDS
    baseline = "reel_comments_sheet"
