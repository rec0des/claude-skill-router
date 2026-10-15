"""Discovery of new skills/plugins when nothing installed fits the task.

Sources, in order of trust:
  1. The official plugin directory (Claude Code's local cache, ~3k entries; some reviewed by Anthropic).
  2. Marketplaces already configured on this machine.
  3. GitHub search (through an authenticated `gh`): well-starred skill repositories.

Nothing is installed without an explicit request. GitHub repositories go through staging + audit before being copied.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import app

DIRECTORY = app.CLAUDE_HOME / "plugins" / "plugin-directory-cache-v2.json"
MARKETPLACES = app.CLAUDE_HOME / "plugins" / "marketplaces"
GITHUB_CACHE = app.STATE / "github-cache.json"
STAGING = app.ROOT / ".staging"
GITHUB_TTL = 7 * 24 * 3600
REPO_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/|$)")
MIN_STARS = 25


def _gh() -> str | None:
    return shutil.which("gh")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def marketplace_index() -> dict[str, str]:
    """plugin -> marketplace for everything installable with `claude plugin install name@marketplace`."""
    index: dict[str, str] = {}
    if MARKETPLACES.exists():
        for market in MARKETPLACES.iterdir():
            data = _load_json(market / ".claude-plugin" / "marketplace.json", {})
            for plugin in data.get("plugins", []) if isinstance(data, dict) else []:
                if plugin.get("name"):
                    index.setdefault(plugin["name"], market.name)
    return index


def installed_plugins() -> set[str]:
    data = _load_json(app.PLUGINS_FILE, {}).get("plugins", {})
    return {key.split("@", 1)[0] for key in data}


def catalog() -> list[dict]:
    """Official directory + local marketplaces, normalized."""
    markets = marketplace_index()
    items: dict[str, dict] = {}
    data = _load_json(DIRECTORY, {})
    for entry in data.get("listings", []) if isinstance(data, dict) else []:
        repo = (entry.get("source") or {}).get("repository") or {}
        name = entry.get("name") or ""
        if not name or "claude_code" not in (entry.get("surfaces") or ["claude_code"]):
            continue
        items[name] = {
            "name": name, "summary": entry.get("summary", ""), "repo": repo.get("url", ""),
            "reviewed": (entry.get("checks") or {}).get("review", {}).get("state") == "done",
            "version": (entry.get("release") or {}).get("version"),
            "marketplace": markets.get(name), "origin": "directory",
        }
    if MARKETPLACES.exists():
        for market in MARKETPLACES.iterdir():
            market_data = _load_json(market / ".claude-plugin" / "marketplace.json", {})
            for plugin in market_data.get("plugins", []) if isinstance(market_data, dict) else []:
                name = plugin.get("name")
                if not name or name in items:
                    continue
                source = plugin.get("source")
                repo = source.get("url", "") if isinstance(source, dict) else plugin.get("homepage", "")
                items[name] = {"name": name, "summary": plugin.get("description", ""), "repo": repo or "",
                               "reviewed": False, "version": plugin.get("version"), "marketplace": market.name, "origin": "marketplace"}
    return list(items.values())


def _score(task_terms: list[str], text: str, name: str) -> tuple[float, list[str]]:
    stems = {app._stem(t) for t in task_terms}
    name_terms = {app._stem(t) for t in app.terms(name.replace("-", " "))}
    text_terms = {app._stem(t) for t in app.terms(text)}
    hits = [t for t in stems if t in name_terms or t in text_terms]
    score = sum(3 if t in name_terms else 1 for t in hits)
    return score, hits


def suggest_local(task: str, limit: int = 3) -> list[dict]:
    """Fast and offline: used by the hook. Only suggests reviewed plugins or ones from already trusted marketplaces."""
    task_terms = app.terms(task)
    if len(task_terms) < 2:
        return []
    owned = installed_plugins()
    ranked = []
    for item in catalog():
        if item["name"] in owned or not (item["reviewed"] or item["marketplace"]):
            continue
        score, hits = _score(task_terms, item["summary"], item["name"])
        if len(hits) >= 2:
            ranked.append((score + (2 if item["reviewed"] else 0), item, hits))
    ranked.sort(key=lambda x: -x[0])
    return [{**item, "hits": hits, "score": round(score, 1)} for score, item, hits in ranked[:limit]]


# ---------- GitHub ----------

def _gh_api(path: str, runner=subprocess.run) -> dict | None:
    exe = _gh()
    if not exe:
        return None
    proc = runner([exe, "api", "-H", "Accept: application/vnd.github+json", path], capture_output=True, text=True, encoding="utf-8", timeout=20)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def repo_stats(url: str, runner=subprocess.run) -> dict | None:
    match = REPO_RE.search(url or "")
    if not match:
        return None
    full = f"{match.group(1)}/{match.group(2)}"
    cache = _load_json(GITHUB_CACHE, {})
    hit = cache.get(full)
    if hit and time.time() - hit.get("at", 0) < GITHUB_TTL:
        return hit
    data = _gh_api(f"repos/{full}", runner)
    if not data or "stargazers_count" not in data:
        return None
    stats = {
        "full_name": data.get("full_name", full), "stars": data.get("stargazers_count", 0),
        "pushed_at": data.get("pushed_at"), "archived": bool(data.get("archived")),
        "license": (data.get("license") or {}).get("spdx_id"), "description": data.get("description") or "",
        "url": data.get("html_url", f"https://github.com/{full}"), "at": time.time(),
    }
    cache[full] = stats
    try:
        app.STATE.mkdir(exist_ok=True)
        GITHUB_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return stats


def trust(item: dict) -> tuple[int, list[str]]:
    """0-100. An official review weighs more than stars; archived or inactive repositories score lower."""
    score, notes = 0, []
    if item.get("reviewed"):
        score += 45
        notes.append("reviewed by Anthropic")
    if item.get("marketplace"):
        score += 10
        notes.append(f"marketplace {item['marketplace']}")
    gh = item.get("github") or {}
    if gh:
        stars = gh.get("stars", 0)
        score += min(30, round(8 * math.log10(1 + stars)))
        notes.append(f"{stars} stars")
        if gh.get("pushed_at"):
            days = (time.time() - datetime.fromisoformat(gh["pushed_at"].replace("Z", "+00:00")).timestamp()) / 86400
            if days < 90:
                score += 10
                notes.append("active in the last 3 months")
            elif days > 365:
                score -= 15
                notes.append(f"no commits for {int(days)} days")
        if gh.get("archived"):
            score -= 40
            notes.append("archived")
        if gh.get("license"):
            score += 5
            notes.append(f"{gh['license']} license")
    return max(0, min(100, score)), notes


def search_github(task: str, limit: int = 6, runner=subprocess.run) -> list[dict]:
    """Relevant Claude Code skill/plugin repositories, sorted by stars."""
    keywords = [t for t in dict.fromkeys(app.terms(task)) if len(t) > 3][:3]
    if not keywords:
        return []
    query = " ".join(keywords) + f" claude skill in:name,description,readme stars:>={MIN_STARS} archived:false"
    from urllib.parse import quote
    data = _gh_api(f"search/repositories?q={quote(query)}&sort=stars&order=desc&per_page={limit}", runner)
    results = []
    for repo in (data or {}).get("items", []):
        stats = {"full_name": repo["full_name"], "stars": repo.get("stargazers_count", 0), "pushed_at": repo.get("pushed_at"),
                 "archived": repo.get("archived", False), "license": (repo.get("license") or {}).get("spdx_id"),
                 "description": repo.get("description") or "", "url": repo.get("html_url")}
        results.append({"name": repo["name"], "summary": stats["description"], "repo": stats["url"], "reviewed": False,
                        "marketplace": None, "origin": "github", "github": stats})
    return results


def discover(task: str, project: str = ".", with_github: bool = True, runner=subprocess.run) -> dict:
    task_terms = app.terms(task)
    owned = installed_plugins()
    candidates = []
    for item in catalog():
        if item["name"] in owned:
            continue
        score, hits = _score(task_terms, item["summary"], item["name"])
        if (len(hits) >= min(2, len(set(task_terms))) or score >= 3) and score >= 2:
            candidates.append({**item, "relevance": score, "hits": hits})
    candidates.sort(key=lambda x: (-x["relevance"], not x["reviewed"]))
    candidates = candidates[:12]
    if with_github:
        for item in candidates:
            if item["repo"]:
                item["github"] = repo_stats(item["repo"], runner)
        known = {(c.get("github") or {}).get("full_name") for c in candidates}
        for item in search_github(task, runner=runner):
            if item["github"]["full_name"] not in known and item["name"] not in owned:
                score, hits = _score(task_terms, item["summary"], item["name"])
                candidates.append({**item, "relevance": score, "hits": hits})
    for item in candidates:
        item["trust"], item["trust_notes"] = trust(item)
        item["install"] = install_command(item)
    # Relevance first; trust breaks ties and pushes risky items down.
    candidates.sort(key=lambda x: -(x["relevance"] * 10 + x["trust"] * 0.8))
    local = app.analyze(project, task)
    return {"task": task, "candidates": candidates[:10], "local_best": local["selected"],
            "local_top_score": max((s["score"] for s in local["skills"]), default=0), "github": bool(_gh()) and with_github}


def install_command(item: dict) -> str | None:
    if item.get("marketplace"):
        return f"claude plugin install {item['name']}@{item['marketplace']} --scope project"
    return None


# ---------- Installation ----------

def install_plugin(name: str, marketplace: str, scope: str = "project", project: str = ".", runner=subprocess.run) -> str:
    if not re.fullmatch(r"[\w.-]+", name) or not re.fullmatch(r"[\w.-]+", marketplace) or scope not in {"user", "project", "local"}:
        raise ValueError("Invalid plugin, marketplace or scope.")
    exe = str(app.CLAUDE_EXE) if app.CLAUDE_EXE.exists() else (shutil.which("claude") or "claude")
    proc = runner([exe, "plugin", "install", f"{name}@{marketplace}", "--scope", scope], capture_output=True, text=True,
                  encoding="utf-8", timeout=300, cwd=str(Path(project).resolve()))
    output = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(output[:300] or "Installation failed")
    for path in (app.STATE / "inventory.json", app.STATE / "health.json"):
        path.unlink(missing_ok=True)
    return output[:300]


def stage_repo(url: str, runner=subprocess.run) -> dict:
    """Clones into .staging, finds every SKILL.md and audits each skill. Installs nothing."""
    import skill_health
    match = REPO_RE.search(url or "")
    if not match:
        raise ValueError("Only GitHub repositories are supported.")
    owner, repo = match.group(1), match.group(2)
    target = STAGING / f"{owner}__{repo}"
    if target.exists():
        app.force_rmtree(target)
    STAGING.mkdir(exist_ok=True)
    proc = runner(["git", "clone", "--depth", "1", f"https://github.com/{owner}/{repo}.git", str(target)],
                  capture_output=True, text=True, encoding="utf-8", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "git clone failed").strip()[:300])
    skills = []
    for skill_file in sorted(target.rglob("SKILL.md")):
        if ".git" in skill_file.parts:
            continue
        skill = app._skill_from_file(skill_file, skill_file.parent.name, "staging")
        if not skill:
            continue
        findings = skill_health.scan_security(skill)
        worst = min((skill_health.SEVERITY_ORDER[f["severity"]] for f in findings), default=5)
        skills.append({
            "name": skill.name, "description": skill.description, "path": str(skill_file.parent.relative_to(target)),
            "tokens": skill.tokens, "findings": findings,
            "verdict": "blocked" if worst == 0 else "review" if worst == 1 else "ok",
            "exists": (app.GLOBAL_SKILLS / skill_file.parent.name).exists(),
        })
    return {"id": target.name, "repo": f"{owner}/{repo}", "skills": skills}


def install_staged(stage_id: str, skill_path: str, accept_risk: bool = False) -> str:
    """Copies an audited skill from .staging to ~/.claude/skills. Always refuses critical findings; high risk only with accept_risk."""
    import skill_health
    stage = (STAGING / stage_id).resolve()
    source = (stage / skill_path).resolve()
    if stage.parent != STAGING.resolve() or not source.is_relative_to(stage) or not (source / "SKILL.md").exists():
        raise ValueError("Skill is outside the staging folder.")
    skill = app._skill_from_file(source / "SKILL.md", source.name, "staging")
    findings = skill_health.scan_security(skill)
    severities = {f["severity"] for f in findings}
    if "critical" in severities:
        raise ValueError("Skill has a critical finding: installation refused.")
    if "high" in severities and not accept_risk:
        raise ValueError("Skill has a high-risk finding: review the evidence and confirm again.")
    target = app.GLOBAL_SKILLS / source.name
    if target.exists():
        raise ValueError(f"{target} already exists; installation cancelled to avoid overwriting it.")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))
    app.STATE.joinpath("inventory.json").unlink(missing_ok=True)
    skill_health._log("install", source.name, f"staging {stage_id}")
    return str(target)
