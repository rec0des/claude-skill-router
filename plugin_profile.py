"""Per-project plugin profile: disable, only in one project, plugins that project does not use.

Every enabled plugin puts its skill descriptions in the system prompt of every session.
Turning it off in <project>/.claude/settings.local.json (enabledPlugins: false) saves those tokens there only.
Verified: `claude plugin list --json` run inside the project shows the plugin as enabled=false; outside it, nothing changes.

Never proposes turning off plugins with hooks (they change Claude's behavior) or those listed in keep_plugins.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import app

PROFILES_STATE = app.STATE / "plugin-profiles.json"
RECENT_DAYS = 45


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def has_hooks(install: Path) -> bool:
    if (install / "hooks" / "hooks.json").exists():
        return True
    manifest = _load(install / ".claude-plugin" / "plugin.json", {})
    return bool(isinstance(manifest, dict) and manifest.get("hooks"))


def _project_settings(project: Path) -> Path:
    return project / ".claude" / "settings.local.json"


def plan(project: str) -> dict:
    root = app.safe_project_dir(project)
    config = app.load_config()
    keep_always = set(config.get("keep_plugins", []))
    installed = _load(app.PLUGINS_FILE, {}).get("plugins", {})
    enabled_user = _load(app.SETTINGS_FILE, {}).get("enabledPlugins", {})
    project_flags = _load(_project_settings(root), {}).get("enabledPlugins", {})
    usage = app.load_usage()
    used_here = usage.get("projects", {}).get(str(root), {})
    used_anywhere = usage.get("global", {})
    last_selected = set(usage.get("last", {}).get(str(root), {}).get("selected", []))
    words, _ = app.project_signals(root, "")
    skills_by_plugin: dict[str, list[app.Skill]] = {}
    for skill in app.discover_skills(root):
        if skill.source == "plugin":
            skills_by_plugin.setdefault(skill.invoke.split(":", 1)[0], []).append(skill)

    keep, disable = [], []
    for key, entries in installed.items():
        if not entries or enabled_user.get(key) is False:
            continue
        name = key.split("@", 1)[0]
        install = Path(entries[-1].get("installPath", ""))
        skills = skills_by_plugin.get(name, [])
        listing_tokens = sum(round(min(len(s.description), 250) / 4) + 8 for s in skills)
        invokes = {s.invoke for s in skills}
        stack_hits = sorted({w for s in skills for w in app.terms(s.description) if len(w) > 4 and w in words})[:4]
        row = {"key": key, "plugin": name, "skills": len(skills), "listing_tokens": listing_tokens,
               "disabled_here": project_flags.get(key) is False}
        if key in keep_always or name in keep_always:
            keep.append({**row, "why": "in keep_plugins"})
        elif has_hooks(install):
            keep.append({**row, "why": "has hooks: changes Claude's behavior"})
        elif invokes & set(used_here):
            keep.append({**row, "why": "used in this project"})
        elif invokes & last_selected:
            keep.append({**row, "why": "recently selected here"})
        elif len(stack_hits) >= 2:
            keep.append({**row, "why": "matches the stack: " + ", ".join(stack_hits)})
        elif not skills:
            keep.append({**row, "why": "no skills (MCP/agents only)"})
        else:
            used_elsewhere = sum(used_anywhere.get(i, 0) for i in invokes)
            disable.append({**row, "why": "never used here" + (f"; used {used_elsewhere}x in other projects" if used_elsewhere else "")})
    disable.sort(key=lambda r: -r["listing_tokens"])
    return {"project": str(root), "keep": keep, "disable": disable,
            "tokens_saved": sum(r["listing_tokens"] for r in disable if not r["disabled_here"])}


def apply(project: str, keys: list[str] | None = None) -> dict:
    """Turns off the planned plugins (or only `keys`) in this project, recording what changed so it can be undone."""
    proposal = plan(project)
    allowed = {r["key"] for r in proposal["disable"]}
    chosen = [k for k in (keys or allowed) if k in allowed]
    if not chosen:
        return {"changed": [], "file": None}
    root = Path(proposal["project"])
    target = _project_settings(root)
    settings = _load(target, {})
    if target.exists():
        backup = target.with_suffix(f".json.skill-router-{datetime.now():%Y%m%d-%H%M%S}.bak")
        backup.write_bytes(target.read_bytes())
    flags = settings.setdefault("enabledPlugins", {})
    changed = [k for k in chosen if flags.get(k) is not False]
    for key in changed:
        flags[key] = False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state = _load(PROFILES_STATE, {})
    state.setdefault(str(root), [])
    state[str(root)] = sorted(set(state[str(root)]) | set(changed))
    app.STATE.mkdir(exist_ok=True)
    PROFILES_STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return {"changed": changed, "file": str(target)}


def undo(project: str) -> dict:
    """Removes only the entries the router wrote; the rest of settings.local.json stays as it was."""
    root = app.safe_project_dir(project)
    state = _load(PROFILES_STATE, {})
    ours = state.pop(str(root), [])
    if not ours:
        return {"restored": []}
    target = _project_settings(root)
    settings = _load(target, {})
    flags = settings.get("enabledPlugins", {})
    restored = [k for k in ours if flags.get(k) is False]
    for key in restored:
        flags.pop(key, None)
    if not flags:
        settings.pop("enabledPlugins", None)
    if target.exists():
        target.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PROFILES_STATE.parent.mkdir(exist_ok=True)
    PROFILES_STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return {"restored": restored}


def markdown(proposal: dict) -> str:
    lines = [f"Project: {proposal['project']}",
             f"Turning off {len(proposal['disable'])} plugins here saves ~{proposal['tokens_saved']} tokens per session in this project."]
    for r in proposal["disable"]:
        mark = " (already off)" if r["disabled_here"] else ""
        lines.append(f"- turn off {r['key']}: {r['skills']} skills, ~{r['listing_tokens']} tk · {r['why']}{mark}")
    lines.append(f"Kept: {len(proposal['keep'])} (" + ", ".join(sorted({r['why'].split(':')[0] for r in proposal['keep']})) + ")")
    lines.append("Apply: app.py plugin-profile --apply · undo: app.py plugin-profile --undo")
    return "\n".join(lines)
