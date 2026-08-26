"""Unit: search.pick_result — exact match vs cichy fallback na zły profil."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from v2.steps.search.pick_result import SearchPickResult, _normalize_username


class FakeNav:
    def __init__(self, top="search_input_active"):
        self._top = top

    def identify(self):
        s = MagicMock()
        s.top = self._top
        return s

    def ensure(self, _name, max_steps=3):
        return True


def _node(rid: str, text: str, bounds="[10,100][200,160]"):
    return {"resource-id": rid, "text": text, "bounds": bounds}


class TestSearchPickResult(unittest.TestCase):
    def test_normalize_strips_at(self):
        self.assertEqual(_normalize_username("@Foo_Bar"), "foo_bar")

    def test_prefer_exact_fails_when_missing(self):
        """Live bug: brak ___anji_i → NIE wolno brać ishioanji."""
        flat = [
            _node("com.instagram.android:id/row_search_user_username", "ishioanji"),
            _node("com.instagram.android:id/row_search_user_username", "other_user"),
        ]
        ctx = MagicMock()
        ctx.navigator = FakeNav()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()

        with patch("v2.steps.search.pick_result.flatten_hierarchy", return_value=flat):
            r = SearchPickResult().run(ctx, {
                "username": "___anji_i", "prefer_exact": True,
            })
        self.assertEqual(r.status, "failed")
        self.assertIn("___anji_i", r.error or "")
        ctx.ht.tap.assert_not_called()

    def test_prefer_exact_taps_matching_row(self):
        flat = [
            _node("com.instagram.android:id/row_search_user_username", "ishioanji",
                  "[10,100][200,160]"),
            _node("com.instagram.android:id/row_search_user_username", "___anji_i",
                  "[10,200][200,260]"),
        ]
        ctx = MagicMock()
        ctx.navigator = FakeNav()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()

        with patch("v2.steps.search.pick_result.flatten_hierarchy", return_value=flat):
            r = SearchPickResult().run(ctx, {
                "username": "@___anji_i", "prefer_exact": True,
            })
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.payload.get("username"), "___anji_i")
        # Tap w środek drugiego wiersza (Y ~230)
        xy = ctx.ht.tap.call_args[0]
        self.assertEqual(xy[1], 230)

    def test_without_username_picks_first(self):
        flat = [
            _node("com.instagram.android:id/row_search_user_username", "first_u"),
            _node("com.instagram.android:id/row_search_user_username", "second_u"),
        ]
        ctx = MagicMock()
        ctx.navigator = FakeNav()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()

        with patch("v2.steps.search.pick_result.flatten_hierarchy", return_value=flat):
            r = SearchPickResult().run(ctx, {"prefer_exact": True})
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.payload.get("username"), "first_u")


if __name__ == "__main__":
    unittest.main()
