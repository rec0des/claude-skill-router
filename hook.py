"""Claude Code hooks for the Skill Router.

SessionStart     -> banner with the router and project status (shown to the user).
UserPromptSubmit -> minimal skill manifest + compaction warnings at context thresholds.
PostToolUse      -> records which skill was actually used, to improve the ranking.

Modes (SKILL_ROUTER_MODE, set by the PowerShell profile functions):
  lazy (default) -> injects names and descriptions only; bodies load through the Skill tool.
  strict         -> session with --disable-slash-commands: injects the selected bodies.
  launcher       -> opened from the panel: bodies are already in the system prompt; monitoring only.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STARTED = time.perf_counter()

from app import (ROOT, STATE, analyze, discover_skills, is_conversational, laya_online, load_config, load_usage, manifest,
                 project_signals, record_selection, record_usage, skill_body, terms)
import context_monitor

CACHE = ROOT / ".hook-cache"
# Optional Laya classifier: SKILL_ROUTER_LAYA_DIR or a sibling laya-runtime folder.
LAYA_DIR = Path(os.environ.get("SKILL_ROUTER_LAYA_DIR") or ROOT.parent / "laya-runtime")
LAYA_PYTHON = LAYA_DIR / ".venv" / "Scripts" / "python.exe"
LAYA_SERVICE = LAYA_DIR / "laya_service.py"
STRICT_BODY_CHARS = 9000
MIN_PROMPT_CHARS = 12
WEAK_MATCH = 45  # below this, no installed skill covers the prompt well


LAYA_START_GRACE = 180  # seconds the model may take to load before another start is allowed


def spawn_hidden(args: list[str], **kwargs) -> None:
    """Starts a background process with a hidden console. DETACHED_PROCESS is avoided on purpose:
    a detached process has no console, so every console program it runs opens a visible window."""
    subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


def claim_laya_start() -> bool:
    """Atomic marker so that sessions starting together launch a single Laya service."""
    marker = STATE / "laya-starting"
    STATE.mkdir(exist_ok=True)
    for _ in range(2):
        try:
            os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - marker.stat().st_mtime < LAYA_START_GRACE:
                    return False
                marker.unlink()
            except OSError:
                return False
        except OSError:
            return False
    return False


def ensure_laya_service() -> None:
    if not load_config().get("laya") or not LAYA_PYTHON.exists() or not LAYA_SERVICE.exists() or laya_online(0.2):
        return
    if not claim_laya_start():
        return
    env = os.environ.copy()
    env.update({"HF_HOME": str(LAYA_DIR / "cache" / "huggingface"), "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "4"})
    spawn_hidden([str(LAYA_PYTHON), str(LAYA_SERVICE)], cwd=str(LAYA_SERVICE.parent), env=env)


def emit(event: str, context: str = "", system_message: str = "") -> None:
    payload: dict = {}
    if context:
        payload["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": context}
    if system_message:
        payload["systemMessage"] = system_message
    if not payload:
        return
    # Pure ASCII JSON written as UTF-8 bytes: immune to the Windows cp1252 code page.
    sys.stdout.buffer.write(json.dumps(payload).encode("utf-8"))
    sys.stdout.flush()


def health_summary() -> str:
    """Reads the latest report; if older than 24 h, re-runs the audit in the background (~20 s)."""
    import time
    report_file = STATE / "health.json"
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report = None
    if report is None or time.time() - report.get("at", 0) > 24 * 3600:
        try:
            spawn_hidden([sys.executable, str(ROOT / "app.py"), "health", "--refresh"], cwd=str(ROOT))
        except OSError:
            pass
    if not report:
        return "audit running in the background"
    import skill_health
    return skill_health.headline(report) or "no issues"


def claude_cli_notice() -> str:
    """Reads the cached Claude Code version check; refreshes it in the background once a day (no wait)."""
    import time
    import claude_cli
    status = claude_cli.read_cache()
    if status is None or time.time() - status.get("at", 0) > claude_cli.MAX_AGE:
        try:
            spawn_hidden([sys.executable, str(ROOT / "app.py"), "claude-version", "--refresh"], cwd=str(ROOT))
        except OSError:
            pass
    return claude_cli.headline(status)


def write_last(info: dict) -> None:
    """Diagnostics: the latest hook run (read by `app.py status`)."""
    try:
        STATE.mkdir(exist_ok=True)
        (STATE / "last-hook.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def session_file(session: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    return CACHE / (hashlib.sha256(session.encode()).hexdigest()[:20] + ".json")


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def mode() -> str:
    value = os.environ.get("SKILL_ROUTER_MODE", "lazy").lower()
    return value if value in {"lazy", "strict", "launcher"} else "lazy"


def on_session_start(data: dict, cwd: str, state_path: Path) -> None:
    ensure_laya_service()
    housekeeping()
    source = data.get("source", "startup")
    previous = read_state(state_path)
    # After /compact or /clear the context shrank: reset warnings, keep the selection.
    state_path.write_text(json.dumps({"selected": previous.get("selected", []) if source == "compact" else [], "band": 0}), encoding="utf-8")
    skills = discover_skills(Path(cwd))
    counts: dict[str, int] = {}
    for s in skills:
        counts[s.source] = counts.get(s.source, 0) + 1
    _, evidence = project_signals(Path(cwd), "")
    last = load_usage().get("last", {}).get(cwd, {}).get("selected", [])
    inventory = " · ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    lines = [
        f"◆ Skill Router · {Path(cwd).name} · {mode()} mode",
        f"  {len(skills)} skills indexed ({inventory})" + (" · classifier on" if laya_online() else ""),
    ]
    if evidence:
        lines.append("  Stack: " + ", ".join(e for e in evidence if not e.startswith("Read "))[:120])
    if last:
        lines.append("  Last selection here: " + ", ".join(last))
    health_line = health_summary()
    if health_line:
        lines.append("  Health: " + health_line + " · /route-skills health")
    cli_line = claude_cli_notice()
    if cli_line:
        lines.append("  ↑ " + cli_line)
    lines.append("  Describe the task: the router picks only the useful skills. Panel: /route-skills panel")
    context = "Skill Router active: on the first prompt it injects the minimal skill set and warns when it is time to /compact."
    write_last({"event": "SessionStart", "mode": mode(), "cwd": cwd, "source": source, "skills": len(skills)})
    emit("SessionStart", context, "\n".join(lines) if source == "startup" else "")


def on_prompt(data: dict, cwd: str, state_path: Path) -> None:
    config = load_config()
    state = read_state(state_path)
    prompt = str(data.get("prompt") or data.get("user_prompt") or data.get("message") or "")[:8000]
    context_parts: list[str] = []
    notices: list[str] = []

    health = context_monitor.assess(data.get("transcript_path"), config.get("context_limit"))
    if health and health["level"] > state.get("band", 0):
        notices.append("⚠ " + health["message"])
        context_parts.append(f"CONTEXT: {health['pct']}% used. Suggest `/compact` to the user at a natural stopping point; do not run it yourself.")
        state["band"] = health["level"]

    routable = (mode() != "launcher" and len(prompt.strip()) >= MIN_PROMPT_CHARS
                and not prompt.lstrip().startswith("/") and not is_conversational(prompt))
    if routable:
        result = analyze(cwd, prompt, config)
        selected = result["selected"]
        previous = state.get("selected", [])
        top = max((s["score"] for s in result["skills"]), default=0)
        if top < WEAK_MATCH and len(terms(prompt)) >= 3:
            suggestion = suggest_missing(prompt, state)
            if suggestion:
                notices.append(suggestion)
        if selected and selected != previous:
            if mode() == "strict":
                skills = {s.invoke: s for s in discover_skills(Path(cwd))}
                parts = [manifest(result)]
                for name in selected:
                    if name in skills and name not in previous:
                        parts.extend([f"\n## ACTIVE SKILL: {name}", skill_body(skills[name])[:STRICT_BODY_CHARS]])
                context_parts.append("\n".join(parts))
            else:
                context_parts.append(manifest(result))
            changed = [s for s in selected if s not in previous]
            notices.append("◆ Skills: " + ", ".join(selected) + (f"  (+{len(changed)} new)" if previous else ""))
            if previous and not set(selected) & set(previous) and health and health["pct"] >= 30:
                notices.append("↺ Scope changed with the context already busy: `/clear` saves tokens before you start.")
            state["selected"] = selected
            record_selection(cwd, selected)

    state_path.write_text(json.dumps(state), encoding="utf-8")
    context = "\n\n".join(context_parts)
    info = {"event": "UserPromptSubmit", "mode": mode(), "cwd": cwd, "selected": state.get("selected", []),
            "injected_chars": len(context), "context_pct": health["pct"] if health else None,
            "routed": routable, "ms": round((time.perf_counter() - STARTED) * 1000)}
    write_last(info)
    metric({**info, "session": data.get("session_id"), "changed": bool(context_parts and routable)})
    emit("UserPromptSubmit", context, "\n".join(notices))


def on_tool_use(data: dict, cwd: str) -> None:
    if data.get("tool_name") != "Skill":
        return
    skill = (data.get("tool_input") or {}).get("skill") or (data.get("tool_input") or {}).get("command")
    if skill:
        name = str(skill).lstrip("/")
        record_usage(cwd, name)
        metric({"event": "use", "skill": name, "cwd": cwd, "session": data.get("session_id")})


def metric(entry: dict) -> None:
    """Log used to measure accuracy (selected vs used), injected tokens and latency."""
    try:
        with (STATE / "metrics.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": round(time.time()), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def suggest_missing(prompt: str, state: dict) -> str:
    """No installed skill fits: suggest from the official catalog (local, offline). Each suggestion once per session."""
    import discover
    found = [s for s in discover.suggest_local(prompt, 2) if s["name"] not in state.get("suggested", [])]
    if not found:
        return ""
    state.setdefault("suggested", []).extend(s["name"] for s in found)
    parts = [f"{s['name']} ({'reviewed' if s['reviewed'] else s['marketplace']})" for s in found]
    return "◇ No installed skill covers this well. In the catalog: " + ", ".join(parts) + " · /route-skills discover"


def housekeeping() -> None:
    """Deletes only files the router generated itself: old session caches and metrics above the cap."""
    now = time.time()
    for folder, days in ((CACHE, 14), (ROOT / ".sessions", 30)):
        if folder.exists():
            for item in folder.glob("*"):
                try:
                    if item.is_file() and now - item.stat().st_mtime > days * 86400:
                        item.unlink()
                except OSError:
                    pass
    metrics = STATE / "metrics.jsonl"
    try:
        lines = metrics.read_text(encoding="utf-8").splitlines()
        if len(lines) > 6000:
            metrics.write_text("\n".join(lines[-4000:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        event = data.get("hook_event_name") or os.environ.get("CLAUDE_HOOK_EVENT", "UserPromptSubmit")
        cwd = str(Path(data.get("cwd") or os.getcwd()).resolve())
        state_path = session_file(str(data.get("session_id") or "default"))
        STATE.mkdir(exist_ok=True)
        if event == "SessionStart":
            on_session_start(data, cwd, state_path)
        elif event == "UserPromptSubmit":
            on_prompt(data, cwd, state_path)
        elif event == "PostToolUse":
            on_tool_use(data, cwd)
    except Exception as exc:
        # The router must never block startup or the submission of a prompt.
        try:
            STATE.mkdir(exist_ok=True)
            (STATE / "hook-error.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except OSError:
            pass


if __name__ == "__main__":
    main()
