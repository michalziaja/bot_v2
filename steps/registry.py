"""Registry — nazwa kroku → klasa. Kroki rejestrują się przez `@register`."""
from __future__ import annotations
from typing import Type
from v2.runner.step import Step

_STEPS: dict[str, Type] = {}


def register(cls):
    """Dekorator. Wymaga że klasa ma pole klasowe `name`."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__}: klasa musi mieć atrybut klasowy `name`")
    if cls.name in _STEPS:
        raise ValueError(f"duplikat rejestracji kroku: {cls.name}")
    _STEPS[cls.name] = cls
    return cls


def get(name: str):
    if name not in _STEPS:
        raise KeyError(f"nieznany krok: {name} (znane: {sorted(_STEPS)})")
    return _STEPS[name]


def known() -> list[str]:
    return sorted(_STEPS)


def clear():
    """Do testów."""
    _STEPS.clear()


def load_all_system_steps():
    """Wygodny import — importuje moduły w steps/system żeby dekoratory się wykonały."""
    import v2.steps.system.wake              # noqa: F401
    import v2.steps.system.unlock            # noqa: F401
    import v2.steps.system.apply_clone_identity  # noqa: F401
    import v2.steps.system.ensure_ig_running # noqa: F401
    import v2.steps.system.login_if_needed   # noqa: F401
    import v2.steps.system.signup_if_needed  # noqa: F401
    import v2.steps.system.enter_verification_code_if_needed  # noqa: F401
    import v2.steps.system.ensure_screen     # noqa: F401
    import v2.steps.system.stats_snapshot    # noqa: F401
    import v2.steps.system.browse_feed       # noqa: F401
    import v2.steps.system.close_ig          # noqa: F401
    import v2.steps.system.restore_clone_identity  # noqa: F401
    import v2.steps.system.sleep_screen      # noqa: F401
    import v2.steps.system.dismiss_notifications  # noqa: F401
    import v2.steps.system.sweep_tabs        # noqa: F401


def load_all_reels_steps():
    import v2.steps.reels.open                  # noqa: F401
    import v2.steps.reels.watch                 # noqa: F401
    import v2.steps.reels.swipe_next            # noqa: F401
    import v2.steps.reels.like                  # noqa: F401
    import v2.steps.reels.read_caption_comments # noqa: F401
    import v2.steps.reels.harvest_likers        # noqa: F401
    import v2.steps.reels.harvest_commenters    # noqa: F401
    import v2.steps.reels.analyze_frames_ai     # noqa: F401
    import v2.steps.reels.like_comment          # noqa: F401
    import v2.steps.reels.open_profile_from_sheet  # noqa: F401


def load_all_search_steps():
    import v2.steps.search.open                 # noqa: F401
    import v2.steps.search.type_query           # noqa: F401
    import v2.steps.search.pick_result          # noqa: F401
    import v2.steps.search.switch_results_tab   # noqa: F401
    import v2.steps.search.collect_tag_variants # noqa: F401


def load_all_stories_steps():
    import v2.steps.stories.watch_segments      # noqa: F401
    import v2.steps.stories.next_user           # noqa: F401
    import v2.steps.stories.close               # noqa: F401
    import v2.steps.stories.pick_ring_from_list # noqa: F401


def load_all_list_steps():
    import v2.steps.lists.scroll_until_new    # noqa: F401
    import v2.steps.lists.harvest_usernames   # noqa: F401


def load_all_post_steps():
    import v2.steps.post.open_from_grid    # noqa: F401
    import v2.steps.post.like              # noqa: F401
    import v2.steps.post.open_engagement   # noqa: F401


def load_all_publish_steps():
    """Creator post/relacja — intent + uiauto (nie Recents)."""
    import v2.steps.publish.send_intent      # noqa: F401
    import v2.steps.publish.drive_creator    # noqa: F401
    import v2.steps.publish.fallback_album   # noqa: F401


def load_all_feed_steps():
    import v2.steps.feed.scroll            # noqa: F401
    import v2.steps.feed.open_tray_story   # noqa: F401


def load_all_profile_steps():
    import v2.steps.profile.exit                   # noqa: F401
    import v2.steps.profile.pick_from_store        # noqa: F401
    import v2.steps.profile.visit                  # noqa: F401
    import v2.steps.profile.visit_next_from_queue  # noqa: F401
    import v2.steps.profile.open_followers         # noqa: F401
    import v2.steps.profile.set_avatar             # noqa: F401
    import v2.steps.profile.set_bio                # noqa: F401


def load_all():
    load_all_system_steps()
    load_all_reels_steps()
    load_all_search_steps()
    load_all_profile_steps()
    load_all_stories_steps()
    load_all_feed_steps()
    load_all_list_steps()
    load_all_post_steps()
    load_all_publish_steps()
