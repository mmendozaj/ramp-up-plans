import copy
import json
import unittest
from pathlib import Path

from backend.core import apply_document_links, apply_track_proposal


ROOT = Path(__file__).resolve().parents[2]


class SharedSourceTests(unittest.TestCase):
    def setUp(self):
        self.plans = json.loads((ROOT / "plans/plans.json").read_text())
        self.documents = json.loads((ROOT / "documents/documents.json").read_text())
        self.base = next(track for prime in self.plans["primes"] for track in prime["tracks"] if track["id"] == "spark-base-cctp-v2")
        self.document = next(document for document in self.documents["documents"] if document["id"] == self.base["id"])

    def test_shared_url_can_be_changed_for_base_independently(self):
        self.assertEqual(apply_document_links(self.plans, self.documents), [])
        arb = next(document for document in self.documents["documents"] if document["id"] == "spark-arbitrum-cctp-v2")
        self.assertEqual(self.document["url"], arb["url"])
        self.document["url"] = "https://docs.google.com/document/d/aaaaaaaaaaaaaaaaaaaa/edit"
        self.assertEqual(apply_document_links(self.plans, self.documents), [])
        self.assertNotEqual(self.document["url"], arb["url"])
        self.assertEqual(self.base["sources"][0]["url"], self.document["url"])

    def test_shared_source_cannot_supply_another_chains_dates_or_identity(self):
        document = {**self.document, "requirements_only": True}
        revised = copy.deepcopy(self.base)
        revised["phases"][0]["earliest_transition"] = "2026-10-08"
        proposal = {"tracks": [{"track_id": self.base["id"], "track_json": json.dumps(revised)}], "review_notes": []}
        with self.assertRaisesRegex(ValueError, "shared-source dates"):
            apply_track_proposal(self.plans, document, proposal)
        revised = copy.deepcopy(self.base)
        revised["onchain_source"]["chain_id"] = 42161
        proposal["tracks"][0]["track_json"] = json.dumps(revised)
        with self.assertRaisesRegex(ValueError, "onchain_source"):
            apply_track_proposal(self.plans, document, proposal)
