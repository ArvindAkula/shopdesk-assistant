#!/usr/bin/env python3
"""Block commits that contain API keys or secret files. Run by the git pre-commit hook.

  python3 scripts/check_secrets.py          # check what is staged (what the commit would contain)
  python3 scripts/check_secrets.py --all    # check every file git would track

Install the hook once per clone:
  printf '#!/bin/sh\\nexec python3 scripts/check_secrets.py\\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
"""
from __future__ import annotations

import re
import subprocess
import sys

KEY_PATTERNS = [
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
# KEY=value lines in env-style files: the template must leave every secret empty
ENV_SECRET = re.compile(r"^[ \t]*([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))[ \t]*=[ \t]*([^#\s]+)", re.M)
SECRET_FILE = re.compile(r"(^|/)\.env(\.(?!example$)[^/]*)?$|\.(pem|key)$|(^|/)id_(rsa|ed25519)$")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def files_to_check(all_files: bool) -> list[str]:
    if all_files:
        out = git("ls-files", "--cached", "--others", "--exclude-standard")
    else:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [f for f in out.splitlines() if f]


def content(path: str, all_files: bool) -> str:
    try:
        if all_files:
            with open(path, "rb") as fh:
                data = fh.read()
        else:  # the staged version, which is what the commit will contain
            data = subprocess.run(["git", "show", f":{path}"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""
    if b"\0" in data[:4096]:
        return ""  # binary (diagrams); keys live in text
    return data.decode("utf-8", errors="replace")


def main() -> int:
    all_files = "--all" in sys.argv
    problems: list[str] = []
    for path in files_to_check(all_files):
        if SECRET_FILE.search(path):
            problems.append(f"{path}: secret file (keep it out of git; it should be in .gitignore)")
            continue
        text = content(path, all_files)
        for name, pat in KEY_PATTERNS:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{path}:{line}: possible {name} ({m.group(0)[:10]}…)")
        if path.endswith((".env.example", ".env.template")):
            for m in ENV_SECRET.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{path}:{line}: {m.group(1)} has a value in the template; leave it empty")
    if problems:
        print("✋ Commit blocked: possible secrets found\n", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        print("\nRemove them (keys belong in .env, which git ignores), then commit again.", file=sys.stderr)
        return 1
    print(f"check_secrets: no secrets in {'tracked' if all_files else 'staged'} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
