from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
# Prefer the Codex Desktop runtime when present; otherwise the Python running this installer.
_CODEX_PYTHON = HOME / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
PYTHON = _CODEX_PYTHON if _CODEX_PYTHON.exists() else Path(sys.executable)
CLAUDE = HOME / ".claude"
CODEX = HOME / ".codex"
SETTINGS = CLAUDE / "settings.json"


def documents_dir() -> Path:
    """The real Documents folder (it may be redirected to OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return HOME / "Documents"


PROFILES = list(dict.fromkeys(
    docs / shell / "Microsoft.PowerShell_profile.ps1"
    for docs in (documents_dir(), HOME / "Documents")
    for shell in ("PowerShell", "WindowsPowerShell")
))
CLAUDE_EXE = HOME / ".local" / "bin" / "claude.exe"
MARKER_START = "# >>> CLAUDE SKILL ROUTER >>>"
MARKER_END = "# <<< CLAUDE SKILL ROUTER <<<"
HOOK_EVENTS = {"SessionStart": None, "UserPromptSubmit": None, "PostToolUse": "Skill"}


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        # Sandboxed Python runtimes cannot write to OneDrive; PowerShell can.
        import subprocess
        staging = ROOT / ".state" / "profile-staging.txt"
        staging.parent.mkdir(exist_ok=True)
        staging.write_text(text, encoding="utf-8")
        script = (f"New-Item -ItemType Directory -Force -Path '{path.parent}' | Out-Null; "
                  f"Copy-Item -LiteralPath '{staging}' -Destination '{path}' -Force")
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], check=True)
        staging.unlink()


def append_block(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if MARKER_START in text and MARKER_END in text:
        before, rest = text.split(MARKER_START, 1)
        _, after = rest.split(MARKER_END, 1)
        # Remove stale lines that older versions left outside the markers.
        after = "\n".join(line for line in after.splitlines() if "claude-skill-router" not in line)
        text = before.rstrip() + "\n\n" + block + ("\n" + after.lstrip("\n") if after.strip() else "\n")
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    _write(path, text)


def install_hooks() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig")) if SETTINGS.exists() else {}
    if SETTINGS.exists():
        backup = SETTINGS.with_suffix(f".json.skill-router-{datetime.now():%Y%m%d-%H%M%S}.bak")
        shutil.copy2(SETTINGS, backup)
    hooks = settings.setdefault("hooks", {})
    command = f'"{PYTHON.as_posix()}" "{(ROOT / "hook.py").as_posix()}"'
    for event, matcher in HOOK_EVENTS.items():
        entries = hooks.setdefault(event, [])
        exists = any("claude-skill-router" in h.get("command", "") for e in entries for h in e.get("hooks", []))
        if not exists:
            entry: dict = {"hooks": [{"type": "command", "command": command, "timeout": 20}]}
            if matcher:
                entry["matcher"] = matcher
            entries.append(entry)
    SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def app_version() -> str:
    match = re.search(r'^__version__ = "([^"]+)"', (ROOT / "app.py").read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else "0.0.0"


def install_skill() -> None:
    source = ROOT / "skill" / "route-skills" / "SKILL.md"
    target = CLAUDE / "skills" / "route-skills"
    target.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8").replace("{{PYTHON}}", str(PYTHON)).replace("{{ROOT}}", str(ROOT)).replace("{{VERSION}}", app_version())
    (target / "SKILL.md").write_text(text, encoding="utf-8")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    install_hooks()
    install_skill()
    rule = f"""{MARKER_START}
## Skill Router

- The Skill Router at `{ROOT}` is this machine's source of skill selection.
- At the start of each session and on the first prompt in a project, let the hook select only the skills that are needed.
- Do not read every skill up front. Use the set the router injects and revisit it only if the task scope changes.
- When the router reports the context level, suggest `/compact` or `/clear` at a natural stopping point.
- Improvements to the mechanism, ranking and interface belong in the router project itself and must pass its tests."""
    append_block(CLAUDE / "CLAUDE.md", rule + "\n" + MARKER_END)
    codex_rule = rule + (
        f"\n- In local Codex/ChatGPT, on the first prompt of a new project, run"
        f" `\"{PYTHON}\" \"{ROOT / 'app.py'}\" route --project . --task \"<prompt>\"` and follow only the listed skills.\n{MARKER_END}"
    )
    append_block(CODEX / "AGENTS.md", codex_rule)

    ps = f"""{MARKER_START}
function claude {{
    # Respect a mode that is already set (e.g. 'launcher' from the panel).
    $owned = -not $env:SKILL_ROUTER_MODE
    if ($owned) {{ $env:SKILL_ROUTER_MODE = 'strict' }}
    try {{ & '{CLAUDE_EXE}' --disable-slash-commands @args }} finally {{ if ($owned) {{ Remove-Item Env:SKILL_ROUTER_MODE -ErrorAction SilentlyContinue }} }}
}}
function claude-raw {{ & '{CLAUDE_EXE}' @args }}
function skill-router {{ & '{PYTHON}' '{ROOT / 'app.py'}' serve --port 9127 --open }}
function route-skills {{ param([Parameter(ValueFromRemainingArguments)][string[]]$Task) & '{PYTHON}' '{ROOT / 'app.py'}' route --project . --task ($Task -join ' ') }}
{MARKER_END}"""
    for profile in PROFILES:
        append_block(profile, ps)
    print("Integration installed: hooks (SessionStart, UserPromptSubmit, PostToolUse/Skill), /route-skills skill, PowerShell and Codex.")


if __name__ == "__main__":
    main()
