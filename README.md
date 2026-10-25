# Nova Core

Nova Core is a FastAPI-based personal AI assistant with chat, vision, persistent memory, and an approval-based programming agent.

## GitHub branch and pull request workflow

The programming agent exposes a review-first workflow under /programming/github/pull-requests. Existing workspace and GitHub proposal routes remain available.

1. POST /programming/github/pull-requests/proposals creates a proposal and returns a diff. It does not create a branch or commit.
2. Review the diff and explicitly approve the proposal with POST /programming/github/pull-requests/proposals/{proposal_id}/approve.
3. On approval, Nova checks that the target file has not changed, creates a dedicated nova/... feature branch from the selected base branch, commits the proposed file to that branch, and opens a draft pull request.
4. Review the draft pull request on GitHub. Nova does not merge the pull request or deploy the application.

### Proposal request

    {
      "repo": "owner/repository",
      "path": "src/example.py",
      "content": "print('updated')\\n",
      "message": "Update example",
      "title": "Update example",
      "body": "What changed and why.",
      "base_branch": "main"
    }

The repository must be included in NOVA_GITHUB_ALLOWED_REPOS, and NOVA_GITHUB_TOKEN must have permission to read/write repository contents and create pull requests. The existing Nova access dependency protects the endpoints. Pending proposals are held in memory and disappear if the server restarts, so approve them before restarting the service.

The workflow validates branch names, checks for stale file changes before writing, and reports the feature branch and pull-request URL. It never merges or deploys automatically.

## Existing application

The existing /chat, /vision, login/session, Supabase memory, and programming-agent routes are preserved. Configure the environment variables used by your deployment rather than committing secrets to the repository.
