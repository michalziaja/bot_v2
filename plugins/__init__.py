"""Warstwa pluginów (jobs) — katalog, schematy ustawień, kolejka zadań.

`catalog` opisuje WSZYSTKIE pluginy — także te bez implementacji — bo panel
musi pokazać komplet ustawień, zanim powstanie kod. Panel czyta `catalog.PLUGINS`
i generuje z niego formularze; `store` zapisuje wartości do configu konta
(jako deltę) i kolejkę zadań do osobnego pliku.

Wykonanie: `base` (kontrakt + Budget), `registry` (co ma implementację),
`runner` (JobRunner), `impl/` (logika). Specyfikacja pozostałych pluginów
i kolejność prac — ROADMAP.md.
"""
from v2.plugins.catalog import PLUGINS, get_plugin, plugin_ids, all_defaults  # noqa: F401
