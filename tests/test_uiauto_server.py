"""Testy wyboru interpretera dla uiautodev (bez startu serwera)."""
from __future__ import annotations
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.uiauto_server import resolve_uiauto_python


class ResolveUiautoPythonTests(unittest.TestCase):
    def test_prefers_venv_when_it_has_package(self):
        venv_py = ROOT / ".venv" / "bin" / "python"
        if not venv_py.is_file():
            self.skipTest("brak .venv/bin/python w tym checkoutcie")
        with patch("v2.core.uiauto_server._has_module",
                   side_effect=lambda p, m: Path(p) == venv_py):
            self.assertEqual(resolve_uiauto_python(), venv_py)

    def test_falls_back_to_sys_executable(self):
        with patch("v2.core.uiauto_server._has_module",
                   side_effect=lambda p, m: Path(p) == Path(sys.executable)):
            self.assertEqual(resolve_uiauto_python(), Path(sys.executable))


if __name__ == "__main__":
    unittest.main()
