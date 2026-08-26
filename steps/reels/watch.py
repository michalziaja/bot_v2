"""reels.watch — klatki OD RAZU, potem filtr i opis.

Design (engagement / nisza):
  1. KLATKI startują natychmiast w tle (N zrzutów, odstęp `frame_interval_s`),
     o ile `use_frames=true` (domyślnie).
  2. Równolegle META/GATE (dump hierarchii) — reklama / min_likes / max_likes / komentarze.
  3. Gate fail → cancel klatek, SKIP (bez composite / opisu).
  4. Gate OK → dokończ klatki → composite + opis równolegle → AI w pluginie.
  5. `use_frames=false` — tylko gate (+ opcjonalnie opis); bez capture/composite.
  6. Legacy: `use_watch_duration=true` — rozkład na czas oglądania podany
     w parametrze `watch_reel` (biznesowy parametr pluginu, nie humanizer).

Params:
  frames:             int   — ile klatek (default 2)
  frame_interval_s:   float — pauza między klatkami (default 0.5)
  use_frames:         bool  — zbieraj klatki / buduj composite (default True)
  use_watch_duration: bool  — True = stary tryb watch_reel (default False)
  read_caption:       bool  — opis równolegle ze sklejaniem composite
  gate / skip_until / save_frames / open_comments / top_k
"""
from __future__ import annotations
import threading
import time
from pathlib import Path

from v2.core.composite import build_composite_grid
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels._reel_meta import read_current_reel


@register
class WatchReel:
    name = "reels.watch"
    requires_screen = "reels_feed"

    def run(self, ctx, params):
        # frames=0 + brak use_frames → bez klatek (content_search: tylko gate).
        # Stary max(1, frames) zamieniał 0→1 i logował „Klatki” mimo frames:0.
        raw_frames_n = int(params.get("frames", 2))
        if "use_frames" in params:
            use_frames = bool(params.get("use_frames"))
        else:
            use_frames = raw_frames_n > 0
        n_frames = max(1, raw_frames_n) if use_frames else 0
        gate = params.get("gate") or {}
        skip_target = params.get("skip_until", "reels.swipe_next")
        use_duration = bool(params.get("use_watch_duration", False))
        interval_s = float(params.get("frame_interval_s", 0.5))
        if interval_s < 0:
            interval_s = 0.0
        # Prefix logów: content_search nie powinien wyglądać jak reels_niche
        log_prefix = params.get("log_prefix")

        frames_dir = ctx.events.file_path.parent / "frames" / ctx.session_id
        frames_dir.mkdir(parents=True, exist_ok=True)

        t_start = time.monotonic()
        stream = ctx.fb if (ctx.fb and ctx.fb.codec == "h264") else None

        def _grab_one():
            """Jedna klatka: żywy bufor, a gdy zamarł — świeży screenshot."""
            if stream is not None and stream.is_live():
                frame = stream.get_frame()
                if frame is not None:
                    return frame
            import cv2, numpy as np
            png = ctx.device.screenshot()
            return cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)

        def _say(msg: str) -> None:
            from v2.plugins.reel_log import say
            say(msg, prefix=log_prefix) if log_prefix is not None else say(msg)

        # -------- 1) KLATKI od razu w tle (nie czekamy na dump) --------
        raw_box: list = []
        lock = threading.Lock()
        cancel = threading.Event()
        capture_thread = None

        if use_frames:
            if use_duration:
                # Czas oglądania to biznesowy parametr pluginu, nie pauza ludzka.
                watch_cfg = params.get("watch_reel") or {"min": 2, "max": 3}
                duration = ctx.humanizer.scale(
                    ctx.humanizer._rng.uniform(float(watch_cfg.get("min", 2)),
                                               float(watch_cfg.get("max", 3))))
                capture_thread = threading.Thread(
                    target=_capture_spread_into,
                    args=(ctx, _grab_one, raw_box, lock, cancel,
                          n_frames, duration, t_start),
                    daemon=True)
            else:
                capture_thread = threading.Thread(
                    target=_capture_interval_into,
                    args=(ctx, _grab_one, raw_box, lock, cancel,
                          n_frames, interval_s),
                    daemon=True)
            capture_thread.start()

        # -------- 2) równolegle: meta + bramka (log OD RAZU = telefon) --------
        meta_start = read_current_reel(ctx.device)
        from v2.plugins.reel_log import watch_line, gate_ok_line, fmt_count
        # Komunikat tuż po odczycie autora, PRZED gate (klatki lecą albo tryb bez nich).
        if use_frames:
            if use_duration:
                _say(watch_line(
                    author=meta_start.get("author"),
                    seconds=duration,
                    likes=meta_start.get("likes"),
                    comments=meta_start.get("comments"),
                    frames=n_frames,
                ))
            else:
                _say(watch_line(
                    author=meta_start.get("author"),
                    seconds=None,
                    interval_s=interval_s,
                    likes=meta_start.get("likes"),
                    comments=meta_start.get("comments"),
                    frames=n_frames,
                ))
        else:
            # Tylko opis / gate — bez zbierania klatek
            _say(watch_line(
                author=meta_start.get("author"),
                seconds=None,
                likes=meta_start.get("likes"),
                comments=meta_start.get("comments"),
                frames=0,
                caption_only=True,
            ))
        ctx.log("reels.watch.start", frames=n_frames if use_frames else 0,
                use_frames=use_frames,
                interval_s=round(interval_s, 2) if (use_frames and not use_duration) else None,
                use_watch_duration=use_duration if use_frames else False,
                author=meta_start.get("author"))

        reason = None
        gate_detail: dict = {}
        if meta_start.get("is_ad"):
            reason = "ad_detected"
        elif gate:
            gate_detail = _eval_early_gate(meta_start, gate)
            if not gate_detail["pass"]:
                reason = "gate_failed"

        if reason:
            # Gate fail — porzuć klatki, nie buduj composite / nie czytaj opisu
            cancel.set()
            if capture_thread is not None:
                capture_thread.join(timeout=2.0)
            ctx.scratch["reel"] = {
                "author":  meta_start.get("author"),
                "likes":   meta_start.get("likes"),
                "comments": meta_start.get("comments"),
                "is_ad":   meta_start.get("is_ad", False),
                "marker":  meta_start.get("marker") or meta_start.get("author"),
            }
            ctx.scratch["reel_composite"] = None
            ctx.scratch.pop("reel_ai", None)
            author = meta_start.get("author") or "?"
            if reason == "ad_detected":
                _say(f"Reklama (@{author}) — pomijam")
            else:
                why = gate_detail.get("why") or "filtr"
                _say(f"Filtr nie OK (@{author}, {fmt_count(meta_start.get('likes'))} "
                     f"lajkow): {why}")
            ctx.log("reels.watch.short_circuit", reason=reason,
                    gate_why=gate_detail.get("why"), author=author)
            payload = {"author": author, "skip_until": skip_target}
            if gate_detail:
                payload["gate_why"] = gate_detail.get("why")
            return StepResult.skipped(reason, **payload)

        # -------- 3) gate OK — od razu log, potem dokończ klatki --------
        _say(gate_ok_line(
            author=meta_start.get("author"),
            likes=meta_start.get("likes"),
            comments=meta_start.get("comments"),
        ))

        raw_frames: list = []
        if use_frames and capture_thread is not None:
            join_timeout = (
                max(duration + 2.0, 5.0) if use_duration
                else max(n_frames * (interval_s + 0.5) + 3.0, 5.0)
            )
            capture_thread.join(timeout=join_timeout)
            cancel.set()

            with lock:
                raw_frames = list(raw_box)

            if not raw_frames:
                fallback = _grab_single_frame(ctx)
                if fallback is not None:
                    raw_frames.append(fallback)
                    ctx.log("reels.watch.frames_fallback")

        stamp = int(time.time() * 1000)
        composite_path: str | None = None
        saved_frames = 0

        ctx.scratch["reel"] = {
            "author":   meta_start.get("author"),
            "likes":    meta_start.get("likes"),
            "comments": meta_start.get("comments"),
            "reposts":  meta_start.get("reposts"),
            "saves":    meta_start.get("saves"),
            "is_ad":    False,
            "marker":   meta_start.get("marker"),
        }
        ctx.scratch.pop("reel_ai", None)

        # -------- 4) composite (CPU) + opis (UI) równolegle --------
        caption_info: dict = {}
        if use_frames and raw_frames:
            out_path = frames_dir / f"reel_{stamp}.jpg"
            hold: dict = {"path": None, "saved": 0, "error": None}

            def _compose_job():
                try:
                    if params.get("save_frames"):
                        hold["saved"] = _save_frames(raw_frames, frames_dir, stamp)
                    hold["path"] = build_composite_grid(
                        raw_frames, out_path=out_path, cols=2)
                except Exception as exc:
                    hold["error"] = str(exc)

            compose_thread = threading.Thread(target=_compose_job, daemon=True)
            compose_thread.start()

            if params.get("read_caption"):
                from v2.steps.reels.read_caption_comments import capture_reel_caption
                caption_info = capture_reel_caption(
                    ctx,
                    open_comments=bool(params.get("open_comments")),
                    top_k=int(params.get("top_k", 5)),
                )
                ctx.scratch["reel_caption_from_watch"] = True

            compose_thread.join(timeout=45.0)
            composite_path = hold.get("path")
            saved_frames = int(hold.get("saved") or 0)
            if hold.get("error"):
                ctx.log("reels.watch.compose_error", error=hold["error"])
        elif params.get("read_caption"):
            # Bez klatek: sam opis (text-only AI)
            from v2.steps.reels.read_caption_comments import capture_reel_caption
            caption_info = capture_reel_caption(
                ctx,
                open_comments=bool(params.get("open_comments")),
                top_k=int(params.get("top_k", 5)),
            )
            ctx.scratch["reel_caption_from_watch"] = True

        ctx.scratch["reel_composite"] = composite_path
        elapsed = round(time.monotonic() - t_start, 2)

        ctx.log("reels.watch.end", watched_s=elapsed, frames=len(raw_frames),
                use_frames=use_frames,
                author=ctx.scratch["reel"].get("author"),
                composite=composite_path is not None,
                caption_chars=len(caption_info.get("caption") or ""),
                reel_slipped=bool(caption_info.get("reel_slipped")),
                saved_frames=saved_frames)
        return StepResult.ok(
            author=ctx.scratch["reel"].get("author"),
            likes=ctx.scratch["reel"].get("likes"),
            comments=ctx.scratch["reel"].get("comments"),
            watched_s=elapsed,
            frames=len(raw_frames),
            use_frames=use_frames,
            composite=bool(composite_path),
            caption_found=bool(caption_info.get("caption")),
            reel_slipped=bool(caption_info.get("reel_slipped")),
        )


# ============================================================
def _capture_interval_into(ctx, grab_one, raw_box: list, lock: threading.Lock,
                           cancel: threading.Event, n_frames: int,
                           interval_s: float) -> None:
    """Wątek: N klatek z odstępem — pierwsza OD RAZU."""
    for i in range(n_frames):
        if cancel.is_set():
            return
        if i > 0 and interval_s > 0:
            if cancel.wait(timeout=interval_s):
                return
        if cancel.is_set():
            return
        try:
            frame = grab_one()
        except Exception as e:
            ctx.log("reels.watch.frame_error", i=i, error=str(e))
            continue
        if frame is not None:
            with lock:
                raw_box.append(frame)


def _capture_spread_into(ctx, grab_one, raw_box: list, lock: threading.Lock,
                         cancel: threading.Event, n_frames: int,
                         duration: float, t_start: float) -> None:
    """Legacy: rozłóż klatki równomiernie na `duration`."""
    interval = duration / max(n_frames, 1)
    for i in range(n_frames):
        if cancel.is_set():
            return
        target_t = t_start + i * interval
        wait = target_t - time.monotonic()
        if wait > 0 and cancel.wait(timeout=wait):
            return
        if cancel.is_set():
            return
        try:
            frame = grab_one()
        except Exception as e:
            ctx.log("reels.watch.frame_error", i=i, error=str(e))
            continue
        if frame is not None:
            with lock:
                raw_box.append(frame)


def _grab_single_frame(ctx):
    """Jedna klatka awaryjnie — z bufora scrcpy, a gdy go nie ma, screenshotem."""
    try:
        if ctx.fb and ctx.fb.is_live():
            frame = ctx.fb.get_frame()
            if frame is not None:
                return frame
        import cv2, numpy as np
        png = ctx.device.screenshot()
        return cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception as exc:
        ctx.log("reels.watch.fallback_failed", error=str(exc))
        return None


def _save_frames(frames: list, frames_dir: Path, stamp: int) -> int:
    """Zapisz pojedyncze klatki obok composite — do ręcznej weryfikacji."""
    try:
        import cv2
    except ImportError:
        return 0
    saved = 0
    for i, frame in enumerate(frames, start=1):
        try:
            path = frames_dir / f"reel_{stamp}_f{i:02d}.jpg"
            if cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                saved += 1
        except Exception:
            continue
    return saved


def _eval_early_gate(reel: dict, gate: dict) -> dict:
    """Wczesna bramka liczników — min/max lajki + min komentarze.

    `max_likes` musi być w `gate` (przekazywane z `gate_params` pluginów);
    brak klucza = brak górnego limitu. `likes_unknown` dodajemy raz.
    """
    on_unknown = gate.get("on_unknown", "skip")
    reasons = []
    # Lajki: min i max w jednym przejściu (bez podwójnego likes_unknown)
    if "min_likes" in gate or "max_likes" in gate:
        v = reel.get("likes")
        if v is None:
            if on_unknown == "skip":
                reasons.append("likes_unknown")
        else:
            # Porównania numeryczne — meta zwraca int; gate z JSON też int
            if "min_likes" in gate and v < gate["min_likes"]:
                reasons.append(f"likes<{gate['min_likes']}")
            if "max_likes" in gate and v > gate["max_likes"]:
                reasons.append(f"likes>{gate['max_likes']}")
    if "min_comments" in gate:
        v = reel.get("comments")
        if v is None:
            if on_unknown == "skip":
                reasons.append("comments_unknown")
        elif v < gate["min_comments"]:
            reasons.append(f"comments<{gate['min_comments']}")
    return {"pass": not reasons, "why": ",".join(reasons) or "ok"}
