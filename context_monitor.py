"""Context-window monitor: reads real token usage from the Claude Code transcript."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_LIMIT = 200_000
LARGE_LIMIT = 1_000_000
TAIL_BYTES = 600_000

# (minimum percentage, level, message)
BANDS = [
    (85, 3, "Context at {pct}% ({used}/{limit}). Run `/compact` now, or save a summary and `/clear`. Beyond this Claude loses detail and every prompt costs more."),
    (70, 2, "Context at {pct}% ({used}/{limit}). Good time for `/compact <what to keep>` at the end of this subtask."),
    (50, 1, "Context at {pct}% ({used}/{limit}). When switching tasks, prefer `/clear`; use `/context` to see what takes up space."),
]


def read_usage(transcript_path: str | os.PathLike | None) -> dict | None:
    """Return the context size of the last assistant turn, or None when unknown."""
    if not transcript_path:
        return None
    path = Path(transcript_path)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            lines = handle.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") or {}
        usage = message.get("usage") or {}
        if entry.get("isSidechain") or not usage:
            continue
        used = sum(int(usage.get(key) or 0) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        if used:
            return {"used": used, "model": str(message.get("model") or "")}
    return None


def context_limit(model: str, used: int, configured: int | None = None) -> int:
    env = os.environ.get("SKILL_ROUTER_CONTEXT_LIMIT", "")
    if env.isdigit():
        return int(env)
    if configured:
        return int(configured)
    if "[1m]" in model.lower() or used > DEFAULT_LIMIT:
        return LARGE_LIMIT
    return DEFAULT_LIMIT


def _short(n: int) -> str:
    return f"{n / 1000:.0f}k" if n < 1_000_000 else f"{n / 1_000_000:.1f}M"


def assess(transcript_path, configured_limit: int | None = None) -> dict | None:
    usage = read_usage(transcript_path)
    if not usage:
        return None
    limit = context_limit(usage["model"], usage["used"], configured_limit)
    pct = round(100 * usage["used"] / limit)
    level, message = 0, ""
    for threshold, band, text in BANDS:
        if pct >= threshold:
            level, message = band, text.format(pct=pct, used=_short(usage["used"]), limit=_short(limit))
            break
    return {"used": usage["used"], "limit": limit, "pct": pct, "level": level, "message": message}
