#!/usr/bin/env bash
# Publish OpsWatch to GitHub.
#
#   scripts/publish.sh
#
# It pushes the current commit to github.com/EdgeF-4/opswatch (branch "main" by
# default; set PUBLISH_BRANCH to override), creating the public repository first
# if it does not exist yet. A push credential is resolved in this order:
#
#   1. a token read from ~/.config/gh_push_token   (preferred; keep it chmod 600)
#   2. an authenticated GitHub CLI (gh)
#
# If neither is available the script does NOT fail. It leaves everything
# committed locally and prints exactly how to finish the push later. The token is
# fed to git through an askpass helper, so it never lands in git config, the
# remote URL, the process list, or your shell history.
set -euo pipefail

GH_USER="EdgeF-4"
REPO="opswatch"
BRANCH="${PUBLISH_BRANCH:-main}"
DESCRIPTION="Self-hosted automation monitoring and observability stack: scheduling, monitoring, alerting, and LLM cost and quality observability."
REMOTE_URL="https://github.com/${GH_USER}/${REPO}.git"
API="https://api.github.com"
TOKEN_FILE="${HOME}/.config/gh_push_token"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Point origin at the right place (create it or correct it).
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

echo "Repository: $ROOT"
echo "Remote:     $REMOTE_URL"
echo "Branch:     $BRANCH"
echo "Commit:     $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Note: the working tree has uncommitted changes; only committed work is pushed." >&2
fi

# Resolve a token: the token file wins, otherwise an authenticated gh CLI.
TOKEN=""
if [ -f "$TOKEN_FILE" ]; then
  TOKEN="$(tr -d ' \t\r\n' <"$TOKEN_FILE")"
  [ -n "$TOKEN" ] && echo "Credential: token from ${TOKEN_FILE}"
fi
if [ -z "$TOKEN" ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  TOKEN="$(gh auth token 2>/dev/null || true)"
  [ -n "$TOKEN" ] && echo "Credential: authenticated GitHub CLI"
fi

# No credential: do not fail, leave everything committed locally.
if [ -z "$TOKEN" ]; then
  cat <<EOF

No push credential found, so nothing was pushed. This is not an error.

Everything is committed locally on branch '$(git rev-parse --abbrev-ref HEAD)', and
this script is ready. To publish, set up one credential and re-run it:

  Token file (preferred):
    printf '%s' <YOUR_TOKEN> > ${TOKEN_FILE}
    chmod 600 ${TOKEN_FILE}
    scripts/publish.sh

  Or the GitHub CLI:
    gh auth login
    scripts/publish.sh

The repository will push to: ${REMOTE_URL} (${BRANCH})
EOF
  exit 0
fi

# Create the public repository if it does not exist yet.
ensure_repo() {
  local code
  # A 404 here is the expected "not created yet" case, so do not use -f.
  code="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    "${API}/repos/${GH_USER}/${REPO}" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then
    echo "Repository exists on GitHub."
    return 0
  fi
  echo "Creating public repository ${GH_USER}/${REPO}..."
  local body resp
  body="$(printf '{"name":"%s","description":"%s","private":false,"has_issues":true,"has_wiki":false}' \
    "$REPO" "$DESCRIPTION")"
  resp="$(curl -fsS -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    "${API}/user/repos" -d "$body" 2>/dev/null || true)"
  if printf '%s' "$resp" | grep -q "\"full_name\""; then
    echo "Created ${GH_USER}/${REPO}."
    return 0
  fi
  echo "Could not create the repository automatically (the token may lack" >&2
  echo "repository-creation permission). Create ${GH_USER}/${REPO} on github.com," >&2
  echo "then re-run this script. Everything stays committed locally." >&2
  return 1
}

# Push HEAD to the target branch, feeding the token through askpass.
push_with_token() {
  local helper rc
  helper="$(mktemp)"
  cat >"$helper" <<'HELPER'
#!/usr/bin/env bash
printf '%s' "$OPSWATCH_PUSH_TOKEN"
HELPER
  chmod 700 "$helper"
  set +e
  OPSWATCH_PUSH_TOKEN="$TOKEN" GIT_ASKPASS="$helper" GIT_TERMINAL_PROMPT=0 \
    git push "https://${GH_USER}@github.com/${GH_USER}/${REPO}.git" "HEAD:${BRANCH}"
  rc=$?
  set -e
  rm -f "$helper"
  return $rc
}

ensure_repo
if push_with_token; then
  echo "Pushed to ${REMOTE_URL} (${BRANCH})."
  exit 0
fi
echo "Push failed. Check the token and its scopes; everything stays committed locally." >&2
exit 1
