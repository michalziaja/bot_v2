"""publish.send_intent — ACTION_SEND do konkretnego handlera klona.

Live (technature699, IG 444): `am start -p pakiet` otwiera systemowy resolver
(Feed / Messages / Reels / Stories) — nawet przy jednym pakiecie. Trafiamy
w komponent:

  post  → …ShareHandlerActivity
  story → …StoryShareHandlerActivity

URI: content:// MediaStore (nie file://, nie DCIM). Grant READ.
"""
from __future__ import annotations

from v2.core.screen_map import flatten_hierarchy
from v2.data.publish_store import phone_path_for
from v2.runner.step import StepResult
from v2.steps.publish._ui import identify_creator
from v2.steps.registry import register

# Komponenty z `cmd package query-activities` na phone_A (2026-09-05).
COMP_FEED = "com.instagram.share.handleractivity.ShareHandlerActivity"
COMP_STORY = "com.instagram.share.handleractivity.StoryShareHandlerActivity"

# Te ścieżki = „weź z galerii” — zakaz.
_REFUSE = ("/DCIM/", "/Download/", "/downloads/", "/Recents")


def _canonical(item: dict) -> str:
    files = item.get("files") or [{}]
    return (files[0] or {}).get("canonical_name") or ""


def _mime(item: dict) -> str:
    files = item.get("files") or [{}]
    return (files[0] or {}).get("mime") or "image/jpeg"


def handler_component(kind: str) -> str:
    """Activity share dla post vs relacja — ten sam pakiet, inny alias."""
    return COMP_STORY if kind == "story" else COMP_FEED


def refuse_path(path: str) -> bool:
    p = path or ""
    return any(x in p for x in _REFUSE)


def resolve_stream_uri(item: dict, account: str) -> tuple[str, str]:
    """(uri, phone_path). Prefer content:// z pusha, nie file://."""
    path = item.get("phone_path") or ""
    name = _canonical(item)
    if not path and name:
        path = phone_path_for(account, item.get("kind") or "post", name)
    uri = (item.get("content_uri") or "").strip()
    if uri.startswith("content://"):
        return uri, path
    # file:// bywa odrzucany / odpala resolver — zostawiamy jako last resort
    if uri.startswith("file://"):
        return uri, path
    if path:
        return f"file://{path}", path
    return "", path


def send_to_clone(ctx, item: dict) -> bool:
    """ACTION_SEND do pakietu klona + konkretny handler. False = nie ruszaj Recents."""
    pkg = ctx.ig_package
    account = ""
    acc = getattr(ctx, "account", None)
    if acc is not None:
        account = str(getattr(acc, "account_id", "") or item.get("account") or "")
    else:
        account = str(item.get("account") or "")
    uri, path = resolve_stream_uri(item, account)
    name = _canonical(item)
    if not pkg:
        return False
    if path and refuse_path(path):
        ctx.log("publish_media.refuse_dcim", path=path)
        return False
    if not uri and not path:
        return False
    kind = item.get("kind") or "post"
    comp = handler_component(kind)
    mime = _mime(item)
    # -n pakiet/komponent omija resolver (live: -p sam nie wystarcza).
    cmd = (
        f"am start -a android.intent.action.SEND -t {mime} "
        f"--eu android.intent.extra.STREAM {uri} "
        f"-n {pkg}/{comp} "
        f"--grant-read-uri-permission -f 0x10000001"
    )
    ctx.log("publish_media.intent", cmd=cmd, package=pkg, path=path,
            name=name, component=comp)
    try:
        ctx.device.shell(cmd)
    except Exception as e:
        ctx.log("publish_media.intent_fail", error=str(e))
        return False
    # ShareHandler nie wstaje w tej samej klatce — daj IG chwilę.
    if hasattr(ctx.device, "wait_for_stable_ui"):
        try:
            ctx.device.wait_for_stable_ui(max_wait_s=3.0)
        except Exception:
            pass
    # Klon OnePlus: czarny spinner = intent nie wstał, oddaj CTA pierwszego posta.
    try:
        h = ctx.device.hierarchy(force=True)
        screen = identify_creator(flatten_hierarchy(h))
    except Exception:
        return True
    if screen == "share_loading":
        ctx.log("publish_media.intent_spinner")
        return False
    return True


@register
class PublishSendIntent:
    name = "publish.send_intent"
    requires_screen = None

    def run(self, ctx, params):
        item = params.get("item") or {}
        if not send_to_clone(ctx, item):
            return StepResult.failed("intent nie wystartował")
        if hasattr(ctx.device, "wait_for_stable_ui"):
            try:
                ctx.device.wait_for_stable_ui(max_wait_s=3.0)
            except Exception:
                pass
        return StepResult.ok(kind=item.get("kind"),
                             name=_canonical(item))
