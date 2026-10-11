"""Cennik Anthropic — koszt USD z tokenów IN/OUT."""
from __future__ import annotations

import unittest

from v2.ai.pricing import apply_costs, cost_usd, rates_for


class RatesForTests(unittest.TestCase):
    def test_haiku_45_prefix(self):
        # pełne ID z API — prefiks claude-haiku-4-5
        inn, out = rates_for("claude-haiku-4-5-20251001")
        self.assertEqual((inn, out), (1.0, 5.0))

    def test_stub_is_zero_priced(self):
        # stub nie idzie na fakturę, ale stawka jest „znana” (zero)
        inn, out = rates_for("stub")
        self.assertEqual((inn, out), (0.0, 0.0))
        usd, priced = cost_usd("stub", 999, 999)
        self.assertEqual(usd, 0.0)
        self.assertTrue(priced)


class CostUsdTests(unittest.TestCase):
    def test_haiku_45_one_million(self):
        # 1M IN × $1 + 1M OUT × $5
        usd, priced = cost_usd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        self.assertTrue(priced)
        self.assertAlmostEqual(usd, 6.0, places=5)

    def test_unknown_model_unpriced(self):
        usd, priced = cost_usd("gpt-mystery-9", 1000, 100)
        self.assertEqual(usd, 0.0)
        self.assertFalse(priced)


class ApplyCostsTests(unittest.TestCase):
    def test_sums_by_model_and_flags_unknown(self):
        # znany + nieznany → suma tylko ze znanego, flaga cost_unknown
        totals = {
            "by_model": {
                "claude-haiku-4-5": {
                    "tokens_in": 1_000_000, "tokens_out": 0, "calls": 1,
                },
                "mystery-x": {
                    "tokens_in": 10, "tokens_out": 10, "calls": 1,
                },
            },
        }
        apply_costs(totals)
        self.assertAlmostEqual(totals["by_model"]["claude-haiku-4-5"]["cost_usd"], 1.0)
        self.assertEqual(totals["by_model"]["mystery-x"]["cost_usd"], 0.0)
        self.assertAlmostEqual(totals["cost_usd"], 1.0)
        self.assertTrue(totals["cost_unknown"])


if __name__ == "__main__":
    unittest.main()



class RatesForTests(unittest.TestCase):
    def test_haiku_45_prefix(self):
        inn, out = rates_for("claude-haiku-4-5-20251001")
        self.assertEqual((inn, out), (1.0, 5.0))

    def test_stub_is_zero_priced(self):
        inn, out = rates_for("stub")
        self.assertEqual((inn, out), (0.0, 0.0))
        usd, priced = cost_usd("stub", 999, 999)
        self.assertEqual(usd, 0.0)
        self.assertTrue(priced)


class CostUsdTests(unittest.TestCase):
    def test_haiku_45_one_million(self):
        # 1M IN × $1 + 1M OUT × $5
        usd, priced = cost_usd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        self.assertTrue(priced)
        self.assertAlmostEqual(usd, 6.0, places=5)

    def test_unknown_model_unpriced(self):
        usd, priced = cost_usd("gpt-mystery-9", 1000, 100)
        self.assertEqual(usd, 0.0)
        self.assertFalse(priced)


class ApplyCostsTests(unittest.TestCase):
    def test_sums_by_model_and_flags_unknown(self):
        totals = {
            "by_model": {
                "claude-haiku-4-5": {
                    "tokens_in": 1_000_000, "tokens_out": 0, "calls": 1,
                },
                "mystery-x": {
                    "tokens_in": 10, "tokens_out": 10, "calls": 1,
                },
            },
        }
        apply_costs(totals)
        self.assertAlmostEqual(totals["by_model"]["claude-haiku-4-5"]["cost_usd"], 1.0)
        self.assertEqual(totals["by_model"]["mystery-x"]["cost_usd"], 0.0)
        self.assertAlmostEqual(totals["cost_usd"], 1.0)
        self.assertTrue(totals["cost_unknown"])


if __name__ == "__main__":
    unittest.main()
