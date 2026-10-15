"""Claude Code CLI version: installed vs the latest release on the user's update channel, and the official update.

The installed version comes from `claude --version`. The latest one comes from the npm registry dist-tags
(the same release feed Claude Code uses), read for the channel set in `autoUpdatesChannel` ("latest" by
default, or "stable"). Only a version number is requested; nothing about the machine is sent.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.request

import app

DIST_TAGS = "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags"
MAX_AGE = 24 * 3600
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def cache_file():
    return app.STATE / "claude_cli.json"


def executable() -> str:
    return str(app.CLAUDE_EXE) if app.CLAUDE_EXE.exists() else (shutil.which("claude") or "claude")


def installed_version(runner=subprocess.run) -> str | None:
    try:
        proc = runner([executable(), "--version"], capture_output=True, text=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = VERSION_RE.search(proc.stdout or "")
    return match.group(0) if match else None


def channel() -> str:
    try:
        value = json.loads(app.SETTINGS_FILE.read_text(encoding="utf-8-sig")).get("autoUpdatesChannel")
    except (OSError, json.JSONDecodeError):
        value = None
    return value if value in {"latest", "stable"} else "latest"


def latest_versions(opener=urllib.request.urlopen) -> dict:
    with opener(DIST_TAGS, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _newer(latest: str, current: str) -> bool:
    return tuple(int(p) for p in latest.split(".")) > tuple(int(p) for p in current.split("."))


def check(force: bool = False, runner=subprocess.run, opener=urllib.request.urlopen) -> dict:
    """Cached for 24 h. Outdated only when the channel's release is newer than the installed one."""
    if not force:
        cached = read_cache()
        if cached and time.time() - cached.get("at", 0) < MAX_AGE:
            return cached
    result = {"at": round(time.time()), "channel": channel(), "installed": installed_version(runner),
              "latest": None, "outdated": False, "error": None}
    try:
        tags = latest_versions(opener)
        latest = tags.get(result["channel"]) or tags.get("latest")
        result["latest"] = latest if latest and VERSION_RE.fullmatch(latest) else None
    except (OSError, ValueError) as exc:
        result["error"] = f"Could not reach the release feed: {exc}"
    if result["installed"] and result["latest"]:
        result["outdated"] = _newer(result["latest"], result["installed"])
    try:
        app.STATE.mkdir(parents=True, exist_ok=True)
        cache_file().write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        pass
    return result


def read_cache() -> dict | None:
    try:
        return json.loads(cache_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def update(runner=subprocess.run) -> dict:
    """Runs the official `claude update`. Open sessions keep the old version until restarted."""
    try:
        proc = runner([executable(), "update"], capture_output=True, text=True, encoding="utf-8", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": f"claude update failed: {exc}", "status": read_cache()}
    output = "\n".join(line for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines() if line.strip())
    status = check(force=True, runner=runner)
    return {"ok": proc.returncode == 0, "message": output[-600:] or "claude update finished.", "status": status}


def headline(status: dict | None) -> str:
    if status and status.get("outdated"):
        return f"Claude Code {status['latest']} available (installed {status['installed']}) · run `claude update` or Update in the panel"
    return ""
