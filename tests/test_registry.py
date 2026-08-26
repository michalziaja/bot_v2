"""Pilnuje, że KAŻDY plik kroku z `@register` jest wciąż importowany przez
`steps.registry.load_all()`.

Bez tego nowy krok kompiluje się i przechodzi własne testy jednostkowe
(importowane bezpośrednio), ale w praniu `KeyError: nieznany krok` przy
pierwszym użyciu z CLI/JobRunnera — dokładnie to, co się stało z
`system.login_if_needed` (2026-08-02): działał w testach, crashował na żywo,
bo nikt go nie dopisał do `load_all_system_steps()`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps import registry

STEPS_DIR = ROOT / "steps"


def _modules_with_register() -> set[str]:
    """Dotted-path modułów (v2.steps.x.y) zawierających dekorator @register.

    Pliki zaczynające się od `_` to prywatne helpery współdzielone przez kroki
    (`_post.py`, `_header.py`, `_sheet.py`…), same nie definiują kroków.
    """
    out = set()
    for p in STEPS_DIR.rglob("*.py"):
        if p.name in ("__init__.py", "registry.py") or p.name.startswith("_"):
            continue
        if "@register" not in p.read_text(encoding="utf-8"):
            continue
        rel = p.relative_to(ROOT).with_suffix("")
        out.add("v2." + ".".join(rel.parts))
    return out


class RegistryCompletenessTests(unittest.TestCase):
    def test_every_step_file_is_reachable_from_load_all(self):
        # UWAGA: celowo BEZ registry.clear() — czyszczenie `_STEPS` nie cofa
        # importu z `sys.modules`. Gdy inny plik testowy w tej samej sesji
        # już zaimportował dany moduł kroku, `import` w `load_all()` staje
        # się no-opem (dekorator @register się nie odpala drugi raz) i test
        # zgłasza fałszywy brak, mimo że moduł jest osiągalny. `load_all()`
        # wołane na tym, co już jest zarejestrowane, jest bezpieczne —
        # ponowny import znanego modułu nigdy nie cofa wcześniejszej rejestracji.
        registry.load_all()
        loaded_modules = {cls.__module__ for cls in registry._STEPS.values()}
        expected = _modules_with_register()
        missing = expected - loaded_modules
        self.assertEqual(missing, set(),
                         f"kroki z @register nieosiągalne z load_all() — "
                         f"brak importu w steps/registry.py: {missing}")


if __name__ == "__main__":
    unittest.main()
