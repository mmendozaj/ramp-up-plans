import copy
import json
import unittest

from backend.core import apply_track_proposal


class ConditionalTargetTests(unittest.TestCase):
    def setUp(self):
        self.track = {"id": "base-vault", "name": "Base vault", "facet": "ERC4626Facet",
                      "sources": [{"label": "Plan", "url": "https://docs.google.com/document/d/aaaaaaaaaaaaaaaaaaaa/edit", "visibility": "public"}],
                      "phases": [{"id": "phase-1", "rate_limit_targets": [], "conditional_rate_limit_targets": [{"id": "exposure-unmet", "condition": "Minimum exposure unmet", "rate_limit_targets": [{"label": "deposit", "max_amount_usd": 5000000, "slope_per_day_usd": 1000000}]}]}]}
        self.plans = {"primes": [{"tracks": [self.track]}]}
        self.document = {"id": "plan", "url": self.track["sources"][0]["url"], "track_sources": [{"track_id": "base-vault", "label": "Plan"}]}

    def apply(self, track):
        return apply_track_proposal(self.plans, self.document, {"tracks": [{"track_id": "base-vault", "track_json": json.dumps(track)}], "review_notes": []})

    def test_doc_update_cannot_delete_or_change_independent_fallback(self):
        for action in ["delete", "change"]:
            with self.subTest(action=action):
                revised = copy.deepcopy(self.track)
                if action == "delete": del revised["phases"][0]["conditional_rate_limit_targets"]
                else: revised["phases"][0]["conditional_rate_limit_targets"][0]["rate_limit_targets"][0]["max_amount_usd"] = 15000000
                with self.assertRaisesRegex(ValueError, "conditional rate-limit targets need manual review"):
                    self.apply(revised)

    def test_other_phase_update_retains_fallback(self):
        revised = copy.deepcopy(self.track)
        revised["phases"][0]["minimum_exposure_usd"] = 40000000
        result = self.apply(revised)
        self.assertEqual(result["primes"][0]["tracks"][0]["phases"][0]["conditional_rate_limit_targets"], self.track["phases"][0]["conditional_rate_limit_targets"])
        self.assertEqual(self.plans["primes"][0]["tracks"][0], self.track)
