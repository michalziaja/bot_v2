"""publish_media — publikacja z kolejki Treści do klona IG.

Kontrakt: TYLKO pozycje `ctx.account`. Intent ACTION_SEND idzie do
konkretnego handlera pakietu klona (ShareHandler / StoryShareHandler),
nigdy „najnowsze z DCIM”. Resztę ekranów creatora jedzie uiauto
(z dumpów live technature699). Fallback szuka albumu bot_v2/alias —
nie Recents.
"""
from __future__ import annotations

import os
from pathlib import Path

from v2.data.publish_store import PublishStore, push_to_phone
from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult
from v2.plugins.progress_log import announce_progress, sync_plugin_stats
from v2.plugins.registry import register
from v2.steps import registry as steps
from v2.steps.publish.fallback_album import (
    fallback_pick_canonical,
    is_first_post_account,
    open_creator_gallery,
)
from v2.steps.publish.send_intent import send_to_clone

HOME = "home_feed"
LOG_PREFIX = "publish_media"


def _say(msg: str) -> None:
    """Linia operatora — ten sam prefix co announce_progress."""
    print(f"  [{LOG_PREFIX}] {msg}", flush=True)


def _data_root(ctx) -> Path:
    ev = getattr(ctx, "events", None)
    if ev is not None and getattr(ev, "data_root", None):
        return Path(ev.data_root)
    ss = getattr(ctx, "session_stats", None)
    if ss is not None and getattr(ss, "data_root", None):
        return Path(ss.data_root)
    raise RuntimeError("brak data_root w ctx (events / session_stats)")


def _account_id(ctx) -> str:
    acc = getattr(ctx, "account", None)
    aid = getattr(acc, "account_id", None)
    if not aid:
        raise RuntimeError("ctx.account.account_id jest wymagane")
    return str(aid)


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="publish_media")


def _canonical(item: dict) -> str:
    files = item.get("files") or [{}]
    return (files[0] or {}).get("canonical_name") or ""


def _selected_ids(s: dict) -> list[str]:
    """Id z kroku Runów — `item_ids` albo pojedyncze `item_id`. Puste = brak wyboru."""
    raw = s.get("item_ids")
    if raw is None:
        raw = s.get("item_id")
    if isinstance(raw, str):
        one = raw.strip()
        return [one] if one else []
    if isinstance(raw, (list, tuple)):
        out = []
        for x in raw:
            v = str(x or "").strip()
            if v and v not in out:
                out.append(v)
        return out
    return []


def _items_for_run(store: PublishStore, s: dict) -> list[dict] | None:
    """Wybrane rekordy tego aliasu. None = operator nic nie zaznaczył.

    Nie wołamy `pending_for_plugin` — zakaz „weź następny ready”.
    Brak rekordu / cudze konto = pomijamy (nie podstawiamy sąsiada).
    """
    ids = _selected_ids(s)
    if not ids:
        return None
    out = []
    for iid in ids:
        rec = store.get(iid)
        if not rec:
            continue
        if rec.get("account") and rec.get("account") != store.account_id:
            continue
        out.append(rec)
    return out


def _stop_before_share(s: dict) -> bool:
    """Ustawienie pluginu albo env — live bez tapu Udostępnij / Twoja relacja."""
    if s.get("stop_before_share"):
        return True
    env = (os.environ.get("BOT_PUBLISH_STOP_BEFORE_SHARE") or "").strip().lower()
    return env in ("1", "true", "yes")


def confirm_share(ctx, item: dict, *, stop_before_share: bool = False) -> bool:
    """Ekrany po intencie — drive_creator (nie surowy tap „Udostępnij”)."""
    res = _step(ctx, "publish.drive_creator", {
        "item": item,
        "stop_before_share": stop_before_share,
    })
    return res.status == "ok"


@register
class PublishMedia:
    id = "publish_media"
    entry_screen = HOME
    exit_screen = HOME

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        stats = {"published": 0, "failed": 0, "pushed": 0, "skipped": 0,
                 "dry_ok": 0}
        account = _account_id(ctx)
        root = _data_root(ctx)
        store = PublishStore(account, root)
        # Tylko pliki zaznaczone w kroku Runów — nie „następny ready”.
        stop_before = _stop_before_share(s)
        picked = _items_for_run(store, s)
        if picked is None:
            _say("Nie wybrano pliku w Runach — nic nie publikuję.")
            announce_progress(
                ctx, _say, msg="Brak wyboru pliku",
                phase="publish", current=0, total=0, label="Treści")
            return PluginResult.failed(
                "nie wybrano pliku w Runach — zaznacz pozycję z Treści",
                budget, **stats)
        if not picked:
            _say("Wybrane id nie ma w kolejce tego konta.")
            return PluginResult.failed(
                "wybrane pliki nie są w kolejce tego konta",
                budget, **stats)
        items = picked
        total = min(len(items), budget.max_items or len(items))

        ig = _step(ctx, "system.ensure_ig_running", {})
        if ig.status == "failed":
            return PluginResult.failed(
                f"ensure_ig_running: {ig.error}", budget, **stats)

        stopped_by = "items"
        for item in items:
            reason = budget.stop_reason()
            if reason:
                stopped_by = reason
                break
            cur = budget.items_done + 1
            name = _canonical(item)
            announce_progress(
                ctx, _say,
                msg=f"Publikuję {name} {cur}/{total or '?'}",
                phase="publish", current=cur, total=total or None,
                label="Treści", detail=name)
            try:
                store.update(item["id"], status="publishing", error=None)
            except Exception:
                pass

            # Zawsze push, potem creator — ręczny Push z Treści nie jest ścieżką.
            blob = store.blob_path(item)
            serial = getattr(ctx.device, "serial", None)
            if blob is not None and serial:
                try:
                    pushed = push_to_phone(
                        {**item, "account": account},
                        serial=serial, blob_path=blob)
                    item = store.update(
                        item["id"], status="on_device",
                        phone_path=pushed["phone_path"],
                        content_uri=pushed.get("content_uri"),
                        error=None)
                    stats["pushed"] += 1
                except Exception as e:
                    store.update(item["id"], status="failed", error=str(e))
                    stats["failed"] += 1
                    budget.tick()
                    continue
            elif not item.get("phone_path"):
                store.update(item["id"], status="failed",
                             error="brak bloba na PC i brak phone_path")
                stats["failed"] += 1
                budget.tick()
                continue

            ok = False
            if send_to_clone(ctx, item):
                ok = confirm_share(ctx, item, stop_before_share=stop_before)
                if not ok and fallback_pick_canonical(ctx, item):
                    ok = confirm_share(ctx, item, stop_before_share=stop_before)
            if not ok and fallback_pick_canonical(ctx, item):
                ok = confirm_share(ctx, item, stop_before_share=stop_before)
            # SHARE na OnePlus wisi — galeria: pierwszy post XOR zwykły plus.
            if not ok and open_creator_gallery(
                    ctx, item, first_post=is_first_post_account(ctx, store)):
                ok = confirm_share(ctx, item, stop_before_share=stop_before)
            if ok and stop_before:
                # Plik na telefonie, creator otwarty — bez tapu publikacji.
                store.update(item["id"], status="on_device", error=None)
                stats["dry_ok"] += 1
                ctx.log("publish_media.dry_ok", id=item["id"], name=name)
            elif ok:
                store.update(item["id"], status="published", error=None)
                stats["published"] += 1
                ctx.log("publish_media.ok", id=item["id"], name=name)
            else:
                store.update(item["id"], status="failed",
                             error="intent/confirm nie wstali")
                stats["failed"] += 1
            budget.tick()
            sync_plugin_stats(
                ctx, stats,
                items_done=budget.items_done,
                max_items=budget.max_items)

        ctx.log("publish_media.done", stopped_by=stopped_by, **stats)
        return PluginResult.done(budget, stopped_by, **stats)
