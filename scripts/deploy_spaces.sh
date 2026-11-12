#!/usr/bin/env bash
# Push this repo to one public Hugging Face Space that visitors can duplicate.
#
# One-time setup (replace <user>):
#   hf auth login
#   git remote add space https://huggingface.co/spaces/<user>/shopdesk-assistant
#
# Do NOT add an API key to this public Space: anyone could spend it, and everyone would share one
# inbox and one memory. Without a key it runs the offline stub. Visitors who want a real model use
# "Duplicate this Space" and add ANTHROPIC_API_KEY as a secret in their own copy.
set -euo pipefail
cd "$(dirname "$0")/.."
branch="$(git rev-parse --abbrev-ref HEAD)"
if git remote get-url space >/dev/null 2>&1; then
  echo ">> pushing $branch to space"
  git push space "$branch:main"
else
  echo "!! remote 'space' not configured (see the comments in this script)"
  exit 1
fi
