"""Testy magazynu klucza API oraz nadpisywania promptu / rygoru AI."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.config import secrets
from v2.ai.vision import _apply_opts, _strictness_hint, _STRICTNESS_HINTS


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig_path = secrets.SECRETS_PATH
        secrets.SECRETS_PATH = Path(self._tmp.name) / "secrets.json"
        self._orig_env = os.environ.pop(secrets.ENV_VAR, None)
        self._orig_ws = os.environ.pop(secrets.ENV_WORKSPACE, None)

    def tearDown(self):
        secrets.SECRETS_PATH = self._orig_path
        os.environ.pop(secrets.ENV_VAR, None)
        os.environ.pop(secrets.ENV_WORKSPACE, None)
        if self._orig_env is not None:
            os.environ[secrets.ENV_VAR] = self._orig_env
        if self._orig_ws is not None:
            os.environ[secrets.ENV_WORKSPACE] = self._orig_ws
        self._tmp.cleanup()

    def test_missing_file_means_no_key(self):
        self.assertIsNone(secrets.get_api_key())
        self.assertFalse(secrets.status()["configured"])

    def test_roundtrip(self):
        secrets.set_api_key("sk-ant-abcdefghijklmnop")
        self.assertEqual(secrets.get_api_key(), "sk-ant-abcdefghijklmnop")
        st = secrets.status()
        self.assertTrue(st["configured"])
        self.assertEqual(st["source"], "file")

    def test_env_wins_over_file(self):
        secrets.set_api_key("sk-ant-from-file-xxxx")
        os.environ[secrets.ENV_VAR] = "sk-ant-from-env-yyyy"
        self.assertEqual(secrets.get_api_key(), "sk-ant-from-env-yyyy")
        st = secrets.status()
        self.assertEqual(st["source"], "env")
        self.assertTrue(st["env_overrides_file"])

    def test_clear(self):
        secrets.set_api_key("sk-ant-abcdefghijklmnop")
        secrets.set_api_key("")
        self.assertIsNone(secrets.get_api_key())

    def test_whitespace_only_is_not_a_key(self):
        secrets.set_api_key("   ")
        self.assertIsNone(secrets.get_api_key())

    def test_mask_never_reveals_middle(self):
        key = "sk-ant-VERYSECRETMIDDLE1234"
        m = secrets.mask(key)
        self.assertNotIn("VERYSECRETMIDDLE", m)
        self.assertTrue(m.startswith("sk-ant-"))
        self.assertTrue(m.endswith("1234"))

    def test_status_never_contains_raw_key(self):
        secrets.set_api_key("sk-ant-VERYSECRETMIDDLE1234")
        self.assertNotIn("VERYSECRETMIDDLE", str(secrets.status()))

    def test_filter_proxies_roundtrip(self):
        secrets.set_filter_proxies({
            "webshare": "http://u:p@host:80/",
            "rayobyte": "http://a:b@other:8000",
        })
        proxies = secrets.get_filter_proxies()
        self.assertEqual(set(proxies), {"webshare", "rayobyte"})
        st = secrets.filter_proxies_status()
        self.assertTrue(st["configured"])
        self.assertEqual(sorted(st["names"]), ["rayobyte", "webshare"])
        # Status nie ujawnia URL
        self.assertNotIn("u:p", str(st))

    def test_filter_proxies_for_edit_and_parse(self):
        secrets.set_filter_proxies({
            "webshare": "http://u:p@h:80/",
            "mars": ["http://a:b@x:1", "http://c:d@x:2"],
        })
        edited = secrets.filter_proxies_for_edit()
        self.assertEqual(edited["webshare"], "http://u:p@h:80/")
        self.assertEqual(edited["mars"], "http://a:b@x:1\nhttp://c:d@x:2")
        self.assertEqual(secrets.parse_filter_proxy_text(""), None)
        self.assertEqual(
            secrets.parse_filter_proxy_text("http://u:p@h/\n\nhttp://a:b@x/"),
            ["http://u:p@h/", "http://a:b@x/"])

    def test_filter_proxies_clear(self):
        secrets.set_filter_proxies({"webshare": "http://u:p@h/"})
        secrets.set_filter_proxies(None)
        self.assertEqual(secrets.get_filter_proxies(), {})

    def test_workspace_id_roundtrip_and_env_wins(self):
        """Identity-linked key: workspace z pliku / env."""
        self.assertIsNone(secrets.get_workspace_id())
        secrets.set_workspace_id("wrkspc_fileid")
        self.assertEqual(secrets.get_workspace_id(), "wrkspc_fileid")
        self.assertEqual(secrets.status()["workspace_source"], "file")
        os.environ[secrets.ENV_WORKSPACE] = "wrkspc_envid"
        self.assertEqual(secrets.get_workspace_id(), "wrkspc_envid")
        self.assertEqual(secrets.status()["workspace_source"], "env")
        self.assertTrue(secrets.status()["workspace_env_overrides_file"])
        secrets.set_workspace_id("")
        os.environ.pop(secrets.ENV_WORKSPACE, None)
        self.assertIsNone(secrets.get_workspace_id())


class PromptOptionsTests(unittest.TestCase):
    BASE = {"system": "ZWRACAJ JSON", "user": "Nisza: {opis}", "max_tokens": 400}

    def test_no_override_keeps_prompt(self):
        self.assertEqual(_apply_opts(self.BASE, {}), self.BASE)

    def test_blank_override_keeps_prompt(self):
        self.assertEqual(_apply_opts(self.BASE, {"prompt_override": "   "}), self.BASE)

    def test_override_replaces_user_section(self):
        out = _apply_opts(self.BASE, {"prompt_override": "Mój prompt {opis}"})
        self.assertEqual(out["user"], "Mój prompt {opis}")

    def test_override_cannot_touch_system_section(self):
        """System niesie kontrakt formatu JSON — własny prompt nie może go
        nadpisać, bo zepsułby parsowanie odpowiedzi i wywalił cały krok."""
        out = _apply_opts(self.BASE, {"prompt_override": "cokolwiek"})
        self.assertEqual(out["system"], self.BASE["system"])

    def test_strictness_levels_differ(self):
        hints = {lvl: _strictness_hint(lvl) for lvl in _STRICTNESS_HINTS}
        self.assertEqual(len(set(hints.values())), len(_STRICTNESS_HINTS))

    def test_unknown_strictness_falls_back_to_balanced(self):
        self.assertEqual(_strictness_hint("nonsens"), _STRICTNESS_HINTS["balanced"])
        self.assertEqual(_strictness_hint(None), _STRICTNESS_HINTS["balanced"])

    def test_prompts_expose_strictness_placeholder(self):
        """Pole `strictness` byłoby martwe, gdyby prompt go nie używał."""
        import yaml
        for name in ("reel_niche", "profile_grid"):
            p = ROOT / "ai" / "prompts" / f"{name}.yml"
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            self.assertIn("{strictness_hint}", data["user"], f"{name}.yml")


if __name__ == "__main__":
    unittest.main()
