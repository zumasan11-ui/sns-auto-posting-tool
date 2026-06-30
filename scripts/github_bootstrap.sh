#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="${1:-sns-auto-posting-tool}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI が見つかりません。先に GitHub CLI をインストールして gh auth login を実行してください。" >&2
  exit 1
fi

gh auth status >/dev/null

if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$REPO_NAME" --public --source=. --remote=origin --push
else
  git push -u origin main
fi

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
OWNER="${REPO%%/*}"
NAME="${REPO#*/}"

get_env() {
  .venv/bin/python - "$1" <<'PY'
import sys
from pathlib import Path

key = sys.argv[1]
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if "=" not in line or line.lstrip().startswith("#"):
        continue
    name, value = line.split("=", 1)
    if name == key:
        print(value.strip(), end="")
        break
PY
}

set_secret_from_env() {
  local key="$1"
  local value
  value="$(get_env "$key")"
  if [ -n "$value" ]; then
    gh secret set "$key" --body "$value"
  else
    echo "skip empty secret: $key" >&2
  fi
}

ENV_SECRET_KEYS=(
  GH_SECRETS_TOKEN
  API_KEY API_SECRET ACCESS_TOKEN ACCESS_TOKEN_SECRET
  THREADS_USER_ID THREADS_ACCESS_TOKEN THREADS_ACCESS_TOKEN_EXPIRES_AT THREADS_LAST_REFRESHED_AT
  THREADS_APP_ID THREADS_APP_SECRET THREADS_REDIRECT_URI
  INSTAGRAM_USER_ID INSTAGRAM_ACCESS_TOKEN INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT INSTAGRAM_LAST_REFRESHED_AT
  INSTAGRAM_APP_ID INSTAGRAM_APP_SECRET INSTAGRAM_REDIRECT_URI
  META_APP_ID META_APP_SECRET META_REDIRECT_URI
  FACEBOOK_APP_ID FACEBOOK_APP_SECRET FACEBOOK_REDIRECT_URI
  FACEBOOK_PAGE_ID FACEBOOK_PAGE_ACCESS_TOKEN FACEBOOK_USER_ACCESS_TOKEN
  FACEBOOK_USER_ACCESS_TOKEN_EXPIRES_AT FACEBOOK_PAGE_ACCESS_TOKEN_EXPIRES_AT FACEBOOK_LAST_REFRESHED_AT
  LINKEDIN_CLIENT_ID LINKEDIN_CLIENT_SECRET LINKEDIN_REDIRECT_URI LINKEDIN_SCOPES
  LINKEDIN_ACCESS_TOKEN LINKEDIN_ACCESS_TOKEN_EXPIRES_AT LINKEDIN_REFRESH_TOKEN
  LINKEDIN_REFRESH_TOKEN_EXPIRES_AT LINKEDIN_LAST_REFRESHED_AT LINKEDIN_PERSON_URN
  YOUTUBE_CLIENT_ID YOUTUBE_CLIENT_SECRET YOUTUBE_REDIRECT_URI YOUTUBE_REFRESH_TOKEN
  YOUTUBE_ACCESS_TOKEN YOUTUBE_ACCESS_TOKEN_EXPIRES_AT YOUTUBE_LAST_REFRESHED_AT
  NOTION_TOKEN NOTION_DATABASE_ID NOTION_VERSION
  GOOGLE_SHEETS_SPREADSHEET_ID GOOGLE_SHEETS_DEFAULT_SHEET
  NOTION_STATUS_PROPERTY NOTION_ERROR_PROPERTY
)

for key in "${ENV_SECRET_KEYS[@]}"; do
  set_secret_from_env "$key"
done

SHEETS_FILE="$(get_env GOOGLE_SHEETS_CREDENTIALS_FILE)"
if [ -n "$SHEETS_FILE" ] && [ -f "$SHEETS_FILE" ]; then
  gh secret set GOOGLE_SHEETS_CREDENTIALS_JSON < "$SHEETS_FILE"
else
  echo "skip GOOGLE_SHEETS_CREDENTIALS_JSON: credentials file not found" >&2
fi

PUBLIC_URL="https://${OWNER}.github.io/${NAME}"
gh secret set PUBLIC_ASSET_BASE_URL --body "$PUBLIC_URL"

tmp_dir="$(mktemp -d)"
git clone --no-checkout "$(git remote get-url origin)" "$tmp_dir"
(
  cd "$tmp_dir"
  git checkout --orphan gh-pages
  git rm -rf . >/dev/null 2>&1 || true
  printf "GitHub Pages assets for SNS auto posting\n" > index.html
  touch .nojekyll
  git add index.html .nojekyll
  git commit -m "Initialize GitHub Pages"
  git push -u origin gh-pages
)
rm -rf "$tmp_dir"

gh api \
  --method POST \
  "repos/${REPO}/pages" \
  -f "source[branch]=gh-pages" \
  -f "source[path]=/" >/dev/null || true

echo "GitHub repository: https://github.com/${REPO}"
echo "GitHub Pages URL: ${PUBLIC_URL}"
echo "Run now from Actions: https://github.com/${REPO}/actions/workflows/daily-sns-auto-post.yml"
