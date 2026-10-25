import unittest
from unittest.mock import patch

import programming_agent as agent


class GitHubPullRequestWorkflowTests(unittest.TestCase):
    def setUp(self):
        agent.PENDING_PROPOSALS.clear()
        router = agent.create_programming_router(
            lambda: None, lambda: "test-session", None
        )
        self.routes = {route.path: route.endpoint for route in router.routes if hasattr(route, "path")}

    def proposal_request(self):
        return agent.GitHubPullRequestProposalRequest(
            repo="owner/repo",
            path="app.py",
            content="print('new')\n",
            message="Update app",
            title="Update app",
            body="Please review this change.",
            base_branch="main",
        )

    def test_branch_name_validation(self):
        self.assertEqual(agent.validate_git_branch_name("feature/safe-name"), "feature/safe-name")
        for invalid in ("", "../main", "feature//name", "bad branch", ".hidden", "release.lock"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(Exception):
                    agent.validate_git_branch_name(invalid)

    def test_legacy_routes_remain_registered(self):
        for path in (
            "/programming/status",
            "/programming/github/read",
            "/programming/github/proposals",
            "/programming/github/proposals/{proposal_id}/approve",
            "/programming/check",
        ):
            self.assertIn(path, self.routes)
        self.assertIn("/programming/github/pull-requests/proposals", self.routes)
        self.assertIn("/programming/github/pull-requests/proposals/{proposal_id}/approve", self.routes)

    def test_proposal_does_not_write_before_approval(self):
        calls = []
        def fake_request(method, endpoint, body=None):
            calls.append((method, endpoint, body))
            return {"object": {"sha": "base-sha"}}
        def fake_file(repo, path, branch):
            return {"content": "print('old')\n", "sha": "old-sha", "branch": branch}

        with patch.object(agent, "require_github_config", lambda repo: None), \
             patch.object(agent, "github_request", side_effect=fake_request), \
             patch.object(agent, "github_get_file", side_effect=fake_file):
            result = self.routes["/programming/github/pull-requests/proposals"](self.proposal_request())

        self.assertTrue(result["approval_required"])
        proposal = agent.PENDING_PROPOSALS[result["proposal_id"]]
        self.assertEqual(proposal["kind"], "github_pr")
        self.assertEqual([call[0] for call in calls], ["GET"])
        self.assertTrue(proposal["metadata"]["feature_branch"].startswith("nova/update-app-"))

    def test_approval_commits_on_feature_branch_and_opens_draft_pr(self):
        def fake_request(method, endpoint, body=None):
            if method == "GET" and endpoint.endswith("/heads/main"):
                return {"object": {"sha": "base-sha"}}
            if method == "GET" and "/git/ref/heads/nova/" in endpoint:
                raise agent.HTTPException(404, "Not found")
            if method == "POST" and endpoint.endswith("/git/refs"):
                return {"ref": body["ref"]}
            if method == "PUT" and "/contents/" in endpoint:
                return {"commit": {"sha": "commit-sha", "html_url": "https://github.com/owner/repo/commit/commit-sha"}}
            if method == "POST" and endpoint.endswith("/pulls"):
                return {"number": 42, "html_url": "https://github.com/owner/repo/pull/42", "draft": True}
            raise AssertionError(f"Unexpected GitHub call: {method} {endpoint}")

        def fake_file(repo, path, branch):
            return {"content": "print('old')\n", "sha": "old-sha", "branch": branch}

        with patch.object(agent, "require_github_config", lambda repo: None), \
             patch.object(agent, "github_request", side_effect=fake_request), \
             patch.object(agent, "github_get_file", side_effect=fake_file):
            proposal = self.routes["/programming/github/pull-requests/proposals"](self.proposal_request())
            result = self.routes["/programming/github/pull-requests/proposals/{proposal_id}/approve"](proposal["proposal_id"])

        self.assertEqual(result["status"], "pull_request_opened")
        self.assertEqual(result["pull_request_number"], 42)
        self.assertTrue(result["feature_branch"].startswith("nova/update-app-"))
        self.assertTrue(result["draft"])
        self.assertFalse(result["merged"])
        self.assertFalse(result["deployed"])
        self.assertNotIn(proposal["proposal_id"], agent.PENDING_PROPOSALS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
