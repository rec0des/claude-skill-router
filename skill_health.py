"""Skill health: outdated, obsolete, incomplete and insecure skills, plus reversible actions.

Nothing is deleted outside the quarantine. Available actions:
  block       -> the router stops selecting the skill (config.json "blocked").
  quarantine  -> a local skill is moved to .quarantine/ (restorable).
  disable     -> the plugin is disabled through the official `claude plugin` CLI.
  purge       -> permanently deletes an entry, only from inside the quarantine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import app

QUARANTINE = app.ROOT / ".quarantine"
HEALTH_CACHE = app.STATE / "health.json"
HEALTH_TTL = 24 * 3600
MARKETPLACES = app.CLAUDE_HOME / "plugins" / "marketplaces"
BLOCKLIST = app.CLAUDE_HOME / "plugins" / "blocklist.json"
TEXT_EXT = {".md", ".txt", ".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".ps1", ".psm1", ".bat", ".cmd", ".json", ".yaml", ".yml", ".toml", ""}
MAX_FILE_BYTES = 400_000
MAX_FILES_PER_SKILL = 60
STALE_DAYS = 180
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# (id, severity, regex, explanation). Evaluated line by line; nothing is executed.
SECURITY_RULES = [
    ("prompt-injection", "critical", re.compile(r"(?<![\"'“])\b(ignore|disregard) (all |any )?(previous|prior|above|system) (instructions|prompt)\b(?![\"'”])|\b(do not|don't|never) (tell|inform|mention to|reveal to) the user (that|about|what)\b|\bhide (this|it|these) from the user\b|without the user'?s? (knowledge|noticing)", re.I),
     "Text that tries to override instructions or hide actions from the user."),
    ("secret-exfil", "critical", re.compile(r"(cat|type|Get-Content|open\(|read_text\(|readFileSync\()[^\n]{0,60}(\.ssh[/\\]id_|id_rsa|\.aws[/\\]credentials|\.env\b|\.npmrc|\.netrc|Cookies|Login Data)[^\n]{0,120}(curl|wget|Invoke-WebRequest|Invoke-RestMethod|requests\.post|fetch\(|nc\s)|(curl|wget|Invoke-WebRequest|requests\.post)[^\n]{0,80}(-d|--data|-F|--upload-file|-Body|data=|files=)[^\n]{0,60}(\.ssh[/\\]id_|id_rsa|\.aws[/\\]credentials|@\.env\b|\$\(env\)|printenv|Get-ChildItem env:)", re.I),
     "Reads credential files and sends them over the network."),
    ("pipe-to-shell", "high", re.compile(r"(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b|(iwr|irm|invoke-webrequest|invoke-restmethod)\b[^\n|]*\|\s*iex\b", re.I),
     "Installs by running a remote script without verification (common in official installers; check the domain)."),
    ("skip-permissions", "high", re.compile(r"--dangerously-skip-permissions|\bbypassPermissions\b", re.I),
     "Turns off Claude's permission prompts."),
    ("destructive", "high", re.compile(r"\brm\s+-rf\s+(/|~|\$HOME|\*)(\s|$)|Remove-Item\s+[^\n]*(\$HOME|\$env:USERPROFILE|C:\\?)\s[^\n]*-Recurse|\bformat\s+[a-z]:|\bmkfs\.|\bdd\s+if=.*of=/dev/", re.I),
     "Destructive command against the home folder or the disk."),
    ("obfuscated-exec", "high", re.compile(r"(eval|exec|Invoke-Expression|iex)\s*\(?\s*[^\n]{0,40}(base64|b64decode|FromBase64String|atob)\b", re.I),
     "Executes content decoded at runtime."),
    ("persistence", "medium", re.compile(r">>\s*~?/?\.?(bashrc|zshrc|profile)\b|schtasks\s+/create|Register-ScheduledTask|CurrentVersion\\Run\b|Add-Content[^\n]*\$PROFILE", re.I),
     "Writes to system startup or the shell profile."),
    ("security-off", "medium", re.compile(r"Set-MpPreference\s+-Disable|Set-ExecutionPolicy\s+(Unrestricted|Bypass)|chmod\s+(-R\s+)?777|setenforce\s+0|ufw\s+disable", re.I),
     "Weakens operating system protections."),
]
BIDI = re.compile("[\u202a-\u202e\u2066-\u2069]")
ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]")
BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{400,}={0,2}")
# Only counts when the sentence is about the skill itself (start of the description or "use X instead").
DEPRECATED = re.compile(r"^\W*(deprecated|obsolete|descontinuad[ao]|obsolet[ao])\b|\b(this (skill|command|plugin) is (now )?(deprecated|obsolete)|superseded by|no longer maintained|deprecated[^.]{0,40}use [\w:-]+ instead)", re.I)
REPLACEMENT = re.compile(r"\buse ([\w:-]+) instead\b", re.I)
TODO_DESC = re.compile(r"\[?TODO\b|lorem ipsum|^no description|^sem descri", re.I)
# Security-themed skills quote these patterns as examples, so their severity is lowered.
SECURITY_TOPIC = re.compile(r"secur|pentest|red.?team|vulnerab|audit|threat|malware|defen[cs]e|aidefence|safety", re.I)


def _now() -> float:
    return time.time()


def _parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _days(ts: float | None) -> int | None:
    return None if ts is None else int((_now() - ts) // 86400)


def _skill_root(skill: app.Skill) -> Path:
    return Path(skill.path)


def _skill_files(skill: app.Skill) -> list[Path]:
    root = _skill_root(skill)
    if root.is_file():
        return [root]
    files = []
    for path in sorted(root.rglob("*")):
        if len(files) >= MAX_FILES_PER_SKILL:
            break
        if path.is_file() and path.suffix.lower() in TEXT_EXT and "node_modules" not in path.parts:
            try:
                if path.stat().st_size <= MAX_FILE_BYTES:
                    files.append(path)
            except OSError:
                continue
    return files


def scan_security(skill: app.Skill) -> list[dict]:
    findings: list[dict] = []
    topical = bool(SECURITY_TOPIC.search(f"{skill.invoke} {skill.description}"))
    root = _skill_root(skill)
    for path in _skill_files(skill):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.name if root.is_file() else str(path.relative_to(root))
        for number, line in enumerate(text.splitlines(), 1):
            if BIDI.search(line):
                findings.append({"rule": "bidi-unicode", "severity": "critical", "file": rel, "line": number,
                                 "why": "Bidirectional control characters hide the real text (Trojan Source attack).", "evidence": BIDI.sub("⟦BIDI⟧", line.strip())[:160]})
            elif ZERO_WIDTH.search(line.lstrip("\ufeff")) and number > 1:
                findings.append({"rule": "zero-width", "severity": "low", "file": rel, "line": number,
                                 "why": "Invisible characters can hide instructions.", "evidence": ZERO_WIDTH.sub("⟦ZW⟧", line.strip())[:160]})
            if BASE64_BLOB.search(line) and path.suffix.lower() not in {".json"}:
                findings.append({"rule": "base64-blob", "severity": "medium", "file": rel, "line": number,
                                 "why": "Long encoded block; the real content is not readable.", "evidence": line.strip()[:80] + "…"})
            for rule, severity, pattern, why in SECURITY_RULES:
                if pattern.search(line):
                    if topical and severity != "critical":
                        severity = "low"
                    findings.append({"rule": rule, "severity": severity, "file": rel, "line": number, "why": why, "evidence": line.strip()[:160]})
    # One hit per rule and file is enough to decide, and avoids 300-line reports.
    seen, unique = set(), []
    for f in findings:
        key = (f["rule"], f["file"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


SHA = re.compile(r"[0-9a-f]{7,40}")


def _newer(latest: str, current: str) -> bool:
    """True only when latest is a later version; an equal or older marketplace copy is not an update."""
    def parts(value: str) -> list:
        return [int(p) if p.isdigit() else p for p in re.split(r"[.+-]", value.strip().lstrip("vV"))]
    try:
        return parts(latest) > parts(current)
    except TypeError:
        return latest.strip() != current.strip()


def _plugin_version(path: Path) -> str | None:
    try:
        return json.loads((path / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig")).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def plugin_status() -> dict[str, dict]:
    """Per plugin: installed vs marketplace version, official blocklist, update age, stale caches."""
    try:
        installed = json.loads(app.PLUGINS_FILE.read_text(encoding="utf-8-sig")).get("plugins", {})
    except (OSError, json.JSONDecodeError):
        installed = {}
    try:
        blocked = {b["plugin"]: b for b in json.loads(BLOCKLIST.read_text(encoding="utf-8-sig")).get("plugins", [])}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        blocked = {}
    try:
        enabled = json.loads(app.SETTINGS_FILE.read_text(encoding="utf-8-sig")).get("enabledPlugins", {})
    except (OSError, json.JSONDecodeError):
        enabled = {}
    catalogs: dict[str, dict] = {}
    out: dict[str, dict] = {}
    for key, entries in installed.items():
        if not entries:
            continue
        entry = entries[-1]
        name, _, market = key.partition("@")
        install = Path(entry.get("installPath", ""))
        info = {"key": key, "plugin": name, "marketplace": market, "enabled": enabled.get(key, True) is not False,
                "installed": entry.get("version"), "latest": None, "outdated": False, "blocked": None,
                "updated_days": _days(_parse_iso(entry.get("lastUpdated"))), "stale_versions": []}
        if key in blocked:
            info["blocked"] = blocked[key].get("reason") or blocked[key].get("text") or "blocked"
        if market not in catalogs:
            try:
                catalogs[market] = {p.get("name"): p for p in json.loads((MARKETPLACES / market / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8-sig")).get("plugins", [])}
            except (OSError, json.JSONDecodeError):
                catalogs[market] = {}
        listing = catalogs[market].get(name)
        if listing is None and catalogs[market]:
            info["removed_from_marketplace"] = True
        elif listing:
            # Same rule as Claude Code: a plugin's version is its plugin.json version, or the commit
            # sha when it has none, and an update only exists when that version changes.
            source = listing.get("source")
            current = _plugin_version(install) or entry.get("version")
            latest = listing.get("version")
            if isinstance(source, str):
                latest = _plugin_version(MARKETPLACES / market / source) or latest
            if latest and current and not SHA.fullmatch(str(current)):
                info["latest"], info["installed"] = latest, current
                info["outdated"] = _newer(str(latest), str(current))
            elif isinstance(source, dict) and source.get("sha") and current and SHA.fullmatch(str(current)):
                installed_sha = str(entry.get("gitCommitSha") or current)
                info["latest"], info["installed"] = source["sha"][:7], installed_sha[:7]
                info["outdated"] = not source["sha"].startswith(installed_sha[:7])
        if install.parent.is_dir():
            info["stale_versions"] = sorted(p.name for p in install.parent.iterdir() if p.is_dir() and p != install)
        out[name] = info
    return out


def check_skill(skill: app.Skill, plugins: dict[str, dict], usage: dict, duplicates: dict[str, list[str]]) -> dict:
    issues: list[dict] = []
    root = _skill_root(skill)
    main = root if root.is_file() else root / "SKILL.md"
    try:
        head = main.read_text(encoding="utf-8", errors="replace")[:app.HEAD_CHARS]
        mtime = main.stat().st_mtime
    except OSError:
        head, mtime = "", None
        issues.append({"kind": "broken", "severity": "high", "why": "Skill file is unreadable or a broken link."})
    meta = app._frontmatter(head)

    # Quality: directly affects selection.
    if root.is_dir() and not meta:
        issues.append({"kind": "incomplete", "severity": "medium", "why": "No frontmatter: Claude cannot tell when to use it."})
    if not skill.description or TODO_DESC.search(skill.description):
        issues.append({"kind": "incomplete", "severity": "medium", "why": "Empty or TODO description: neither the router nor Claude can pick it."})
    elif len(skill.description) < 40:
        issues.append({"kind": "incomplete", "severity": "low", "why": "Description too short to guide selection."})
    if meta.get("name") and root.is_dir() and meta["name"] != root.name and skill.source != "plugin":
        issues.append({"kind": "incomplete", "severity": "low", "why": f"name '{meta['name']}' differs from the folder '{root.name}'."})

    # Obsolescence.
    if DEPRECATED.search(skill.description):
        replacement = REPLACEMENT.search(skill.description)
        hint = f" Replacement: {replacement.group(1)}." if replacement else ""
        issues.append({"kind": "obsolete", "severity": "medium", "why": "The description itself declares the skill obsolete." + hint})
    if len(duplicates.get(skill.name, [])) > 1:
        others = [d for d in duplicates[skill.name] if d != skill.invoke]
        issues.append({"kind": "duplicate", "severity": "low", "why": "Same name in another source: " + ", ".join(others[:3])})
    plugin = plugins.get(skill.invoke.split(":", 1)[0]) if skill.source == "plugin" else None
    if plugin:
        if plugin["blocked"]:
            issues.append({"kind": "insecure", "severity": "critical", "why": f"Plugin is on the official blocklist: {plugin['blocked']}."})
        if plugin["outdated"]:
            issues.append({"kind": "outdated", "severity": "medium", "why": f"Plugin {plugin['installed']} installed; the marketplace has {plugin['latest']}."})
        if plugin.get("removed_from_marketplace"):
            issues.append({"kind": "obsolete", "severity": "medium", "why": "Plugin is no longer listed in its marketplace."})
        if plugin["updated_days"] is not None and plugin["updated_days"] > STALE_DAYS and not plugin["outdated"]:
            issues.append({"kind": "outdated", "severity": "info", "why": f"Plugin not updated for {plugin['updated_days']} days."})
    elif mtime and (_days(mtime) or 0) > STALE_DAYS:
        # Age alone is not staleness: a local skill has no upstream to compare with, so this stays informational.
        issues.append({"kind": "outdated", "severity": "info", "why": f"Local skill unchanged for {_days(mtime)} days."})

    # Security.
    findings = scan_security(skill)
    for f in findings:
        issues.append({"kind": "insecure", "severity": f["severity"], "why": f["why"], "where": f"{f['file']}:{f['line']}", "evidence": f["evidence"], "rule": f["rule"]})

    worst = min((SEVERITY_ORDER[i["severity"]] for i in issues), default=5)
    status = "ok"
    if any(i["kind"] == "insecure" and i["severity"] == "critical" for i in issues):
        status = "insecure"
    elif any(i["kind"] == "insecure" and i["severity"] == "high" for i in issues):
        status = "risky"
    elif any(i["kind"] in {"obsolete", "broken"} for i in issues):
        status = "obsolete"
    elif any(i["kind"] == "outdated" and i["severity"] != "info" for i in issues):
        status = "outdated"
    elif any(i["kind"] in {"incomplete", "insecure"} and i["severity"] in {"medium", "high"} for i in issues):
        status = "review"
    used = usage.get("global", {}).get(skill.invoke, 0)
    return {
        "invoke": skill.invoke, "name": skill.name, "source": skill.source, "path": skill.path,
        "description": skill.description, "status": status,
        "severity": next((k for k, v in SEVERITY_ORDER.items() if v == worst), "none"),
        "issues": sorted(issues, key=lambda i: SEVERITY_ORDER[i["severity"]]),
        "used": used, "modified_days": _days(mtime),
        "actions": _actions(skill, plugin),
    }


def _actions(skill: app.Skill, plugin: dict | None) -> list[str]:
    actions = ["block"]
    if skill.source in {"global", "command"}:
        actions.append("quarantine")
    if plugin and plugin["enabled"]:
        actions.append("disable_plugin")
    return actions


def audit(force: bool = False) -> dict:
    signature = app._inventory_signature()
    if not force:
        try:
            cached = json.loads(HEALTH_CACHE.read_text(encoding="utf-8"))
            if cached.get("signature") == signature and _now() - cached.get("at", 0) < HEALTH_TTL:
                return _with_current_flags(cached)
        except (OSError, json.JSONDecodeError):
            pass
    started = _now()
    skills = app.discover_skills()
    plugins = plugin_status()
    usage = app.load_usage()
    names: dict[str, list[str]] = {}
    for s in skills:
        names.setdefault(s.name, []).append(s.invoke)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda s: check_skill(s, plugins, usage, names), skills))
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 5), r["invoke"]))
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    report = {
        "signature": signature, "at": _now(), "seconds": round(_now() - started, 1),
        "summary": summary, "skills": rows,
        "plugins": sorted(plugins.values(), key=lambda p: (not p["outdated"], p["plugin"])),
        "quarantine": list_quarantine(),
    }
    if app.load_config().get("auto_cleanup"):
        result = cleanup(report, apply=True, reason="automatic")
        report["last_cleanup"] = {"at": _now(), "done": result["done"], "failed": result["failed"]}
    try:
        app.STATE.mkdir(exist_ok=True)
        HEALTH_CACHE.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return _with_current_flags(report)


def _with_current_flags(report: dict) -> dict:
    """Blocks and quarantine change without a re-audit, so they are always read from the current state."""
    config = app.load_config()
    blocked, protected = set(config.get("blocked", [])), set(config.get("protected", []))
    report["quarantine"] = list_quarantine()
    quarantined = {q["invoke"] for q in report["quarantine"]}
    for row in report["skills"]:
        row["blocked"] = row["invoke"] in blocked
        row["protected"] = row["invoke"] in protected
        row["quarantined"] = row["invoke"] in quarantined
    report["auto_cleanup"] = bool(config.get("auto_cleanup"))
    report["cleanup_plan"] = cleanup_plan(report)
    return report


def auto_excluded() -> set[str]:
    """Skills the router never selects: those with a critical security finding."""
    try:
        cached = json.loads(HEALTH_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {r["invoke"] for r in cached.get("skills", []) if r["status"] == "insecure"}


# ---------- Actions ----------

def _save_config(config: dict) -> None:
    current = {}
    try:
        current = json.loads(app.CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    current.update(config)
    app.CONFIG_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def set_blocked(invoke: str, blocked: bool) -> list[str]:
    config = app.load_config()
    items = [x for x in config.get("blocked", []) if x != invoke]
    if blocked:
        items.append(invoke)
    _save_config({"blocked": sorted(set(items))})
    return items


def quarantine(invoke: str, reason: str = "manual") -> dict:
    skill = next((s for s in app.discover_skills() if s.invoke == invoke), None)
    if not skill or skill.source not in {"global", "command"}:
        raise ValueError("Only local skills can be quarantined; for plugins, block the skill or disable/uninstall the plugin.")
    source = Path(skill.path)
    allowed = [base.resolve() for base in (app.GLOBAL_SKILLS, app.GLOBAL_COMMANDS) if base.exists()]
    if not any(source.parent.resolve() == base for base in allowed):
        raise ValueError("Path is outside the skill folders; operation refused.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Each entry is a folder: <name>--<date>/{original content, .quarantine.json}
    target = QUARANTINE / f"{source.name}--{stamp}"
    target.mkdir(parents=True)
    manifest = {"invoke": invoke, "from": str(source), "name": source.name, "at": stamp, "ts": _now(), "reason": reason}
    if source.is_symlink():
        manifest["symlink_to"] = os.readlink(source)
        source.unlink()
    else:
        shutil.move(str(source), str(target / source.name))
    (target / ".quarantine.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    set_blocked(invoke, True)
    _log("quarantine", invoke, reason)
    return manifest


def list_quarantine() -> list[dict]:
    items = []
    if QUARANTINE.exists():
        for entry in sorted(QUARANTINE.iterdir()):
            try:
                info = json.loads((entry / ".quarantine.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            info["id"] = entry.name
            info["days"] = _days(info.get("ts"))
            items.append(info)
    return items


def _quarantine_entry(entry_id: str) -> Path:
    entry = (QUARANTINE / entry_id).resolve()
    if entry.parent != QUARANTINE.resolve() or not (entry / ".quarantine.json").exists():
        raise ValueError("Unknown quarantine entry.")
    return entry


def restore(entry_id: str) -> dict:
    entry = _quarantine_entry(entry_id)
    info = json.loads((entry / ".quarantine.json").read_text(encoding="utf-8"))
    destination = Path(info["from"])
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Something already exists at {destination}; restore cancelled.")
    if info.get("symlink_to"):
        os.symlink(info["symlink_to"], destination, target_is_directory=True)
    else:
        shutil.move(str(entry / info.get("name", destination.name)), str(destination))
    app.force_rmtree(entry)
    set_blocked(info["invoke"], False)
    # Restoring means the user wants to keep it: auto cleanup never touches this skill again.
    set_protected(info["invoke"], True)
    _log("restore", info["invoke"], "manual")
    return info


def purge(entry_id: str) -> dict:
    """Permanently deletes an entry. Only acts inside the quarantine: nothing outside it is deleted."""
    entry = _quarantine_entry(entry_id)
    info = json.loads((entry / ".quarantine.json").read_text(encoding="utf-8"))
    app.force_rmtree(entry)
    _log("purge", info["invoke"], "manual")
    return info


def set_protected(invoke: str, protected: bool) -> None:
    items = [x for x in app.load_config().get("protected", []) if x != invoke]
    if protected:
        items.append(invoke)
    _save_config({"protected": sorted(set(items))})


def _log(action: str, invoke: str, reason: str) -> None:
    try:
        app.STATE.mkdir(exist_ok=True)
        with (app.STATE / "cleanup-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "action": action, "skill": invoke, "reason": reason}, ensure_ascii=False) + "\n")
    except OSError:
        pass


CLEANUP_STATUSES = {"obsolete", "insecure"}


def cleanup_plan(report: dict) -> list[dict]:
    """What a cleanup would do: local skills go to quarantine, plugin skills are blocked."""
    config = app.load_config()
    protected = set(config.get("protected", [])) | {"route-skills"}
    blocked = set(config.get("blocked", []))
    plan = []
    for row in report["skills"]:
        if row["status"] not in CLEANUP_STATUSES or row["invoke"] in protected:
            continue
        why = row["issues"][0]["why"] if row["issues"] else row["status"]
        if "quarantine" in row["actions"]:
            plan.append({"invoke": row["invoke"], "action": "quarantine", "status": row["status"], "why": why})
        elif row["invoke"] not in blocked:
            plan.append({"invoke": row["invoke"], "action": "block", "status": row["status"], "why": why})
    return plan


def cleanup(report: dict | None = None, apply: bool = False, reason: str = "manual") -> dict:
    report = report or audit()
    plan = cleanup_plan(report)
    done, failed = [], []
    if apply:
        for item in plan:
            try:
                if item["action"] == "quarantine":
                    quarantine(item["invoke"], reason)
                else:
                    set_blocked(item["invoke"], True)
                    _log("block", item["invoke"], reason)
                done.append(item)
            except (OSError, ValueError) as exc:
                failed.append({**item, "error": str(exc)})
    return {"plan": plan, "done": done, "failed": failed, "applied": apply}


def _plugin_cli(verb: str, plugin_key: str, runner=None) -> str:
    """Uses the official Claude Code CLI, which manages the cache, versions and settings."""
    import subprocess
    if not re.fullmatch(r"[\w.-]+@[\w.-]+", plugin_key):
        raise ValueError("Invalid plugin identifier.")
    exe = str(app.CLAUDE_EXE) if app.CLAUDE_EXE.exists() else (shutil.which("claude") or "claude")
    proc = (runner or subprocess.run)([exe, "plugin", verb, plugin_key], capture_output=True, text=True, encoding="utf-8", timeout=180)
    output = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(output[:300] or f"claude plugin {verb} failed")
    _invalidate()
    return output[:300]


def disable_plugin(plugin_key: str, runner=None) -> str:
    return _plugin_cli("disable", plugin_key, runner)


def enable_plugin(plugin_key: str, runner=None) -> str:
    return _plugin_cli("enable", plugin_key, runner)


def update_plugin(plugin_key: str, runner=None) -> str:
    return _plugin_cli("update", plugin_key, runner)


def uninstall_plugin(plugin_key: str, runner=None) -> str:
    _log("uninstall_plugin", plugin_key, "manual")
    return _plugin_cli("uninstall", plugin_key, runner)


def _invalidate() -> None:
    for path in (HEALTH_CACHE, app.STATE / "inventory.json"):
        try:
            path.unlink()
        except OSError:
            pass


def headline(report: dict) -> str:
    s = report["summary"]
    parts = []
    for key, label in (("insecure", "insecure"), ("risky", "risky"), ("obsolete", "obsolete"), ("review", "to review")):
        if s.get(key):
            parts.append(f"{s[key]} {label}")
    # Skills from outdated plugins are counted per plugin, not one by one.
    local_outdated = sum(1 for r in report["skills"] if r["status"] == "outdated" and r["source"] != "plugin")
    if local_outdated:
        parts.append(f"{local_outdated} outdated local")
    last = report.get("last_cleanup") or {}
    if last.get("done"):
        parts.insert(0, f"{len(last['done'])} cleaned up automatically")
    outdated_plugins = sum(1 for p in report["plugins"] if p["outdated"])
    if outdated_plugins:
        parts.append(f"{outdated_plugins} plugins with updates")
    return ", ".join(parts)
