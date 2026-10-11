"""Rejestr implementacji pluginów — nazwa → klasa.

Ten sam wzorzec co `v2/steps/registry.py`, żeby nie wprowadzać drugiego stylu.

Rejestr trzyma tylko pluginy MAJĄCE logikę. Katalog (`v2/plugins/catalog.py`)
opisuje wszystkie — także te jeszcze nienapisane — bo dashboard musi pokazać
komplet ustawień, zanim powstanie kod. `available()` mówi, które da się
faktycznie uruchomić.
"""
from __future__ import annotations

from typing import Type

_PLUGINS: dict[str, Type] = {}


def register(cls):
    """Dekorator. Wymaga pola klasowego `id`."""
    pid = getattr(cls, "id", None)
    if not pid:
        raise ValueError(f"{cls.__name__}: klasa musi mieć atrybut klasowy `id`")
    if pid in _PLUGINS:
        raise ValueError(f"duplikat rejestracji pluginu: {pid}")
    _PLUGINS[pid] = cls
    return cls


def get(plugin_id: str):
    if plugin_id not in _PLUGINS:
        raise KeyError(
            f"plugin '{plugin_id}' nie ma jeszcze implementacji "
            f"(zaimplementowane: {sorted(_PLUGINS) or 'brak'})")
    return _PLUGINS[plugin_id]


def available() -> list[str]:
    return sorted(_PLUGINS)


def is_implemented(plugin_id: str) -> bool:
    return plugin_id in _PLUGINS


def clear():
    """Do testów."""
    _PLUGINS.clear()


def load_all():
    """Import modułów implementacji, żeby dekoratory się wykonały."""
    import v2.plugins.impl.content_search     # noqa: F401
    import v2.plugins.impl.feed_browser       # noqa: F401
    import v2.plugins.impl.mass_stories       # noqa: F401
    import v2.plugins.impl.niche_discover     # noqa: F401
    import v2.plugins.impl.profile_interact   # noqa: F401
    import v2.plugins.impl.reels_niche        # noqa: F401
    import v2.plugins.impl.reels_warmup       # noqa: F401
    import v2.plugins.impl.target_scraper     # noqa: F401
    import v2.plugins.impl.publish_media      # noqa: F401
