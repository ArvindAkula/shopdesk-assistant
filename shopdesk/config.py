"""Settings: one set of defaults for the support agent view, with env overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

MAX_LEVEL = 4
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_dotenv(path: Path = DOTENV_PATH) -> None:
    """Load KEY=value lines from the repo's .env for local runs. Variables already set in the
    environment win, so Spaces secrets and command-line overrides (LLM_MODEL=... python ...) apply."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.split(" #", 1)[0].strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    level: int = 1                     # 1..4, see tools/registry.py
    defense: bool = False              # initial state of the DEFENSE toggle
    max_tokens: int = 4000
    max_iterations: int = 6            # model turns per run (each may call tools)
    queue_concurrency: int = 1         # one run at a time: the inbox, memory and side effects are shared
    queue_max_size: int = 50
    provider: str = "fake"             # anthropic | openai | fake
    model: str = ""
    effort: str = "low"
    naive_prompt: str = "realistic"   # realistic | strict (system prompt used when DEFENSE is off)
    db_path: str = "/tmp/shopdesk.db"
    exfil_address: str = "records@shopdesk-archive.example"


DEFAULTS = Settings()

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4o-mini", "fake": "fake-demo-model"}


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _prompt_style(v: str) -> str:
    v = (v or "").strip().lower() or "realistic"
    if v not in ("realistic", "strict"):
        raise ValueError(f"NAIVE_PROMPT must be realistic or strict, not {v!r}")
    return v


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    s = DEFAULTS

    provider = env.get("LLM_PROVIDER", "").strip().lower()
    if not provider:
        if env.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif env.get("OPENAI_API_KEY"):
            provider = "openai"
        else:
            provider = "fake"
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"Unknown LLM_PROVIDER {provider!r}; use anthropic, openai or fake")

    overrides = dict(
        provider=provider,
        model=env.get("LLM_MODEL", "").strip() or DEFAULT_MODELS[provider],
        effort=env.get("LLM_EFFORT", s.effort).strip(),
        naive_prompt=_prompt_style(env.get("NAIVE_PROMPT", s.naive_prompt)),
        db_path=env.get("DB_PATH", s.db_path).strip(),
        exfil_address=env.get("EXFIL_ADDRESS", s.exfil_address).strip(),
    )
    if "DEFENSE" in env:
        overrides["defense"] = _bool(env["DEFENSE"])
    for key, name, cast in (
        ("MAX_TOKENS", "max_tokens", int),
        ("MAX_ITERATIONS", "max_iterations", int),
        ("QUEUE_CONCURRENCY", "queue_concurrency", int),
    ):
        if env.get(key, "").strip():
            overrides[name] = cast(env[key])
    if "LEVEL" in env:
        overrides["level"] = max(1, min(MAX_LEVEL, int(env["LEVEL"])))
    return replace(s, **overrides)
