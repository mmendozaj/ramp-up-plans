import base64
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from backend.agent import CHECK_INTERVAL_SECONDS, ROOT, configured_env, main, refresh_checkout, run
from backend.api import GitHub, google_access_token, propose_with_fallback
from backend.core import (apply_track_proposal, document_hash, make_baseline, next_plan_version,
                          render_plan_changes)


DOC_URL = "https://docs.google.com/document/d/aaaaaaaaaaaaaaaaaaaa/edit"
TRACK = {"id": "one", "facet": "F", "kind": "instance",
         "phases": [{"id": "phase-1", "rate_limit_targets": []}],
         "sources": [{"label": "Plan", "url": DOC_URL, "visibility": "restricted"}]}
PLANS = {"version": "2026-10-01.2", "policy_defaults": {}, "primes": [{"tracks": [TRACK]}]}
DOCUMENT = {"id": "example", "url": DOC_URL, "track_sources": [{"track_id": "one", "label": "Plan"}]}


def response(value, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(value).encode()


class CoreTests(unittest.TestCase):
    def test_dotenv_defaults_and_environment_override(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / ".env").write_text("GITHUB_REPOSITORY=example/repo\nANTHROPIC_MODEL=claude-test\n")
            with patch.dict("os.environ", {"GITHUB_REPOSITORY": "override/repo"}):
                values = configured_env(Path(temp))
            self.assertEqual(values["GITHUB_REPOSITORY"], "override/repo")
            self.assertEqual(values["ANTHROPIC_MODEL"], "claude-test")

    def test_proposal_identity_and_rendering(self):
        revised = copy.deepcopy(TRACK)
        revised["phases"][0]["requirements"] = ["A documented requirement"]
        proposal = {"tracks": [{"track_id": "one", "track_json": json.dumps(revised),
                                "change_summary": "Requirement updated"}], "review_notes": []}
        updated = apply_track_proposal(PLANS, DOCUMENT, proposal)
        self.assertEqual(updated["primes"][0]["tracks"][0], revised)
        self.assertNotIn("requirements", TRACK["phases"][0])
        updated["version"] = "2026-10-01.3"
        text, changed = render_plan_changes(json.dumps(PLANS, indent=2) + "\n", PLANS, updated)
        self.assertEqual(json.loads(text), updated)
        self.assertEqual(changed, ["one"])
        bad = copy.deepcopy(revised)
        bad["phases"] = []
        proposal["tracks"][0]["track_json"] = json.dumps(bad)
        with self.assertRaisesRegex(ValueError, "phase IDs"):
            apply_track_proposal(PLANS, DOCUMENT, proposal)

    def test_version_and_hash(self):
        self.assertEqual(next_plan_version("2026-10-01.2", datetime(2026, 10, 1, tzinfo=timezone.utc)), "2026-10-01.3")
        self.assertEqual(next_plan_version("2026-09-30.2", datetime(2026, 10, 1, tzinfo=timezone.utc)), "2026-10-01.1")
        self.assertEqual(document_hash("\ufeffPhase 1\r\nmaxAmount: 5 million\r\n"),
                         document_hash("Phase 1\nmaxAmount: 5 million\n"))
        self.assertEqual(document_hash("Phase 1\ufeff"), document_hash("Phase 1"))
        self.assertNotEqual(document_hash("\x1cPhase 1"), document_hash("Phase 1"))
        baseline = make_baseline([{"id": "example", "sha256": "abc"}], "2026-10-01.3")
        self.assertEqual(baseline["documents"]["example"]["sha256"], "abc")


class ApiTests(unittest.TestCase):
    def test_claude_failure_falls_back_to_openai(self):
        revised = copy.deepcopy(TRACK)
        revised["phases"][0]["requirements"] = ["A documented requirement"]
        proposal = {"tracks": [{"track_id": "one", "track_json": json.dumps(revised),
                                "change_summary": "Requirement updated"}], "review_notes": []}
        calls = []

        def fake_transport(method, url, *, headers, body=None, timeout=30):
            calls.append((url, json.loads(body)))
            if "anthropic" in url:
                return response({}, 503)
            return response({"status": "completed", "output": [{"content": [
                {"type": "output_text", "text": json.dumps(proposal)}]}]})

        env = {"ANTHROPIC_API_KEY": "test", "ANTHROPIC_MODEL": "claude-test",
               "OPENAI_API_KEY": "test", "OPENAI_MODEL": "openai-test"}
        provider, model, updated = propose_with_fallback(DOCUMENT, "Document evidence", PLANS,
            lambda value: apply_track_proposal(PLANS, DOCUMENT, value), env, fake_transport)
        self.assertEqual((provider, model), ("OpenAI", "openai-test"))
        self.assertEqual(updated["primes"][0]["tracks"][0], revised)
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[1][1]["store"])
        self.assertIn("Document evidence", calls[0][1]["messages"][0]["content"])

    def test_invalid_claude_plan_falls_back_to_openai(self):
        valid = {"tracks": [{"track_id": "one", "track_json": json.dumps(TRACK),
                             "change_summary": "No change"}], "review_notes": []}
        invalid_track = copy.deepcopy(TRACK)
        invalid_track["phases"] = []
        invalid = {"tracks": [{"track_id": "one", "track_json": json.dumps(invalid_track),
                               "change_summary": "Bad phase change"}], "review_notes": []}

        def fake_transport(method, url, *, headers, body=None, timeout=30):
            if "anthropic" in url:
                return response({"stop_reason": "end_turn", "content": [
                    {"type": "text", "text": json.dumps(invalid)}]})
            return response({"status": "completed", "output": [{"content": [
                {"type": "output_text", "text": json.dumps(valid)}]}]})

        env = {"ANTHROPIC_API_KEY": "test", "ANTHROPIC_MODEL": "claude-test",
               "OPENAI_API_KEY": "test", "OPENAI_MODEL": "openai-test"}
        provider, _, updated = propose_with_fallback(DOCUMENT, "Evidence", PLANS,
            lambda value: apply_track_proposal(PLANS, DOCUMENT, value), env, fake_transport)
        self.assertEqual(provider, "OpenAI")
        self.assertEqual(updated, PLANS)

    def test_google_service_account_signing(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "service-account.json"
            file.write_text(json.dumps({"type": "service_account", "client_email": "reader@example.com",
                                        "private_key": pem}), encoding="utf-8")

            def fake_transport(method, url, *, headers, body=None, timeout=30):
                self.assertEqual(url, "https://oauth2.googleapis.com/token")
                assertion = parse_qs(body.decode())["assertion"][0]
                header, payload, signature = assertion.split(".")
                decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
                self.assertEqual(decoded["scope"], "https://www.googleapis.com/auth/drive.readonly")
                private.public_key().verify(base64.urlsafe_b64decode(signature + "=="),
                                            f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
                return response({"access_token": "test-token"})

            self.assertEqual(google_access_token({"GOOGLE_SERVICE_ACCOUNT_FILE": str(file)}, fake_transport), "test-token")

    def test_github_commit_and_pull_request(self):
        calls = []

        def fake_transport(method, url, *, headers, body=None, timeout=30):
            calls.append((method, url, json.loads(body) if body else None))
            if url.endswith("/git/commits/parent"):
                return response({"tree": {"sha": "base-tree"}})
            if url.endswith("/git/trees"):
                return response({"sha": "new-tree"})
            if url.endswith("/git/commits"):
                return response({"sha": "new-commit"})
            if url.endswith("/pulls"):
                return response({"html_url": "https://github.com/owner/repo/pull/1", "node_id": "PR_node"})
            return response({"ref": "refs/heads/test-branch"})

        github = GitHub("owner/repo", "token", fake_transport)
        sha = github.create_commit("parent", [{"path": "plans/plans.json", "text": "{}\n"},
                                              {"path": "documents/baseline.json", "text": "{}\n"}], "Draft plan")
        self.assertEqual(sha, "new-commit")
        self.assertEqual(calls[1][2]["base_tree"], "base-tree")
        self.assertEqual([item["path"] for item in calls[1][2]["tree"]],
                         ["plans/plans.json", "documents/baseline.json"])
        github.create_branch("test-branch", sha)
        pull = github.create_pull_request("test-branch", "Review", "Please review")
        self.assertEqual(pull["node_id"], "PR_node")
        self.assertFalse(calls[-1][2]["draft"])

    def test_auto_merge_requires_review_and_validate(self):
        calls = []

        def fake_transport(method, url, *, headers, body=None, timeout=30):
            calls.append((method, url, json.loads(body) if body else None))
            if "/rules/branches/main" in url:
                return response([
                    {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
                    {"type": "required_status_checks", "parameters": {
                        "required_status_checks": [{"context": "validate"}]}}])
            if url.endswith("/graphql"):
                return response({"data": {"enablePullRequestAutoMerge": {"pullRequest": {"id": "PR_node"}}}})
            raise AssertionError(url)

        github = GitHub("owner/repo", "token", fake_transport)
        self.assertTrue(github.review_and_validation_required())
        github.enable_auto_merge("PR_node", "new-commit")
        self.assertEqual(calls[-1][2]["variables"]["input"]["pullRequestId"], "PR_node")
        self.assertEqual(calls[-1][2]["variables"]["input"]["expectedHeadOid"], "new-commit")

    def test_auto_merge_refuses_missing_review(self):
        def fake_transport(method, url, *, headers, body=None, timeout=30):
            if "/rules/branches/main" in url:
                return response([{"type": "required_status_checks", "parameters": {
                    "required_status_checks": [{"context": "validate"}]}}])
            if url.endswith("/branches/main/protection"):
                return response({"required_pull_request_reviews": None, "required_status_checks": {"contexts": ["validate"]}})
            raise AssertionError(url)

        self.assertFalse(GitHub("owner/repo", "token", fake_transport).review_and_validation_required())

    def test_auto_merge_checks_classic_protection(self):
        def fake_transport(method, url, *, headers, body=None, timeout=30):
            if "/rules/branches/main" in url:
                return response([], 404)
            if url.endswith("/branches/main/protection"):
                return response({"required_pull_request_reviews": {"required_approving_review_count": 1},
                                 "required_status_checks": {"contexts": ["validate"]}})
            raise AssertionError(url)

        self.assertTrue(GitHub("owner/repo", "token", fake_transport).review_and_validation_required())


class DryRunTests(unittest.TestCase):
    def test_synthetic_document_revisions(self):
        registry = json.loads((ROOT / "documents/documents.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            for document in registry["documents"]:
                (Path(temp) / f"{document['id']}.txt").write_text("Phase 1\n" + "Synthetic source text. " * 10,
                                                                  encoding="utf-8")

            def fake_transport(method, url, *, headers, body=None, timeout=30):
                self.assertEqual(url, "https://api.anthropic.com/v1/messages")
                return response({"stop_reason": "end_turn", "content": [{"type": "text", "text":
                    json.dumps({"tracks": [], "review_notes": []})}]})

            result = run(dry_run=True, offline_dir=temp,
                         env={"ANTHROPIC_API_KEY": "test", "ANTHROPIC_MODEL": "mock"}, transport=fake_transport)
            self.assertEqual(len(result["changed"]), len(registry["documents"]))
            self.assertEqual(result["tracks"], [])


class ScheduleTests(unittest.TestCase):
    def test_hourly_service_retries_after_a_failed_check(self):
        with (patch("backend.agent.refresh_checkout") as refresh,
              patch("backend.agent.run", side_effect=[RuntimeError("temporary failure"), {"changed": []}]) as check,
              patch("backend.agent.time.sleep", side_effect=[None, KeyboardInterrupt]) as sleep,
              patch("sys.stderr", new_callable=io.StringIO) as stderr):
            self.assertEqual(main([]), 0)
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(check.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args, (CHECK_INTERVAL_SECONDS,))
        self.assertEqual(sleep.call_args_list[1].args, (CHECK_INTERVAL_SECONDS,))
        self.assertIn("temporary failure", stderr.getvalue())

    def test_single_dry_run_does_not_sync_or_sleep(self):
        with (patch("backend.agent.refresh_checkout") as refresh,
              patch("backend.agent.run") as check,
              patch("backend.agent.time.sleep") as sleep):
            self.assertEqual(main(["--dry-run"]), 0)
        refresh.assert_not_called()
        check.assert_called_once_with(dry_run=True, offline_dir=None)
        sleep.assert_not_called()

    def test_refresh_checkout_requires_main_and_clean_tree(self):
        with (patch("backend.agent.subprocess.check_output", side_effect=["main\n", ""]),
              patch("backend.agent.subprocess.run") as pull):
            pull.return_value.returncode = 0
            refresh_checkout(ROOT)
        self.assertEqual(pull.call_args.args[0], ("git", "pull", "--ff-only", "origin", "main"))


if __name__ == "__main__":
    unittest.main()
