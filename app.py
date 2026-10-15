from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import shutil
import threading
import time
import unicodedata
import webbrowser
from dataclasses import dataclass, asdict
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PROFILES = ROOT / "profiles"
STATE = ROOT / ".state"
CONFIG_FILE = ROOT / "config.json"
CLAUDE_HOME = Path.home() / ".claude"
GLOBAL_SKILLS = CLAUDE_HOME / "skills"
GLOBAL_COMMANDS = CLAUDE_HOME / "commands"
PLUGINS_FILE = CLAUDE_HOME / "plugins" / "installed_plugins.json"
SETTINGS_FILE = CLAUDE_HOME / "settings.json"
MAX_FILES = 1200
MAX_SKILL_CHARS = 18_000
HEAD_CHARS = 16000
INVENTORY_TTL = 3600
__version__ = "1.0.0"
INVENTORY_VERSION = "2"  # bump when the cached Skill fields change meaning
SCAN_TTL = 900
LAYA_URL = "http://127.0.0.1:9130"
IGNORED_DIRS = {".git", "node_modules", ".next", "dist", "build", "vendor", "venv", ".venv", "__pycache__", ".pnpm-store", ".cache"}
MANIFESTS = {"package.json", "pyproject.toml", "requirements.txt", "docker-compose.yml", "dockerfile", "wrangler.toml", "vite.config.ts", "next.config.js", "next.config.mjs", "cargo.toml", "go.mod", "pubspec.yaml", "composer.json", "gemfile"}
DEFAULT_CONFIG = {"max_skills": 4, "include_plugins": True, "pinned": [], "blocked": [], "context_limit": None}

SIGNALS = {
    "frontend": {"react", "next", "vue", "svelte", "astro", "css", "tailwind", "landing", "frontend", "website", "ui", "ux"},
    "backend": {"api", "backend", "server", "fastapi", "django", "express", "node", "database", "postgres", "sqlite", "redis"},
    "testing": {"test", "pytest", "vitest", "jest", "playwright", "qa", "bug", "debug", "diff"},
    "deploy": {"deploy", "docker", "vercel", "cloudflare", "aws", "azure", "ci", "github actions", "production"},
    "design": {"figma", "design", "layout", "brand", "visual", "interface", "motion", "animation"},
    "research": {"research", "recon", "competitor", "clone", "replica", "reverse engineer", "audit"},
    "business": {"launch", "pricing", "marketing", "brand", "copy", "sales", "seo", "growth"},
    "security": {"security", "pentest", "red team", "vulnerability", "cve", "firewall", "audit"},
}

SKILL_HINTS = {
    "recon": {"research", "audit", "competitor", "clone", "replica", "reverse engineer"},
    "architect": {"architecture", "plan", "system", "structure", "scaffold"},
    "design": {"design", "ui", "ux", "figma", "layout", "frontend", "website"},
    "build": {"build", "implement", "frontend", "react", "website", "landing"},
    "backend": {"backend", "api", "database", "auth", "server", "node"},
    "test": {"test", "qa", "bug", "debug", "playwright", "pytest"},
    "diff": {"compare", "diff", "pixel", "visual", "replica"},
    "deploy": {"deploy", "docker", "cloudflare", "vercel", "production"},
    "launch": {"launch", "release", "go to market", "pricing"},
    "brand": {"brand", "copy", "identity", "marketing"},
    "entrepreneur": {"business", "sales", "pricing", "idea", "market"},
}

PURPOSE_NAMES = {
    "frontend": {"build", "frontend", "product-builder"},
    "backend": {"backend", "api", "database"},
    "testing": {"test", "tester", "quality", "diff"},
    "deploy": {"deploy", "docker", "cloudflare"},
    "design": {"design", "impeccable", "ui", "ux"},
    "research": {"recon", "research", "audit"},
    "business": {"launch", "brand", "entrepreneur", "marketing"},
    "security": {"security", "pentest", "redteam"},
}

# Prompts may arrive in Portuguese while skill descriptions are almost always in English.
PT_EN = {
    "teste": "test", "testes": "test", "testar": "test", "implantar": "deploy", "publicar": "deploy",
    "banco": "database", "dados": "data", "seguranca": "security", "desempenho": "performance",
    "pesquisa": "research", "pesquisar": "research", "desenho": "design", "compactar": "compact",
    "pagina": "page", "paginas": "pages", "site": "website", "anuncio": "ads", "anuncios": "ads",
    "agente": "agent", "agentes": "agents", "melhorar": "improve", "corrigir": "fix", "erro": "bug",
    "erros": "bug", "depurar": "debug", "revisao": "review", "rever": "review", "documentacao": "documentation",
    "pagamento": "payment", "pagamentos": "payments", "mensagem": "message", "automacao": "automation",
    "grafico": "chart", "graficos": "chart", "painel": "dashboard", "habilidade": "skill", "habilidades": "skills",
    "implementar": "implement", "criar": "create", "lancamento": "launch", "marca": "brand", "vendas": "sales",
    "animacao": "animation", "interface": "interface", "componente": "component", "componentes": "components",
    "migracao": "migration", "arquitetura": "architecture", "planear": "plan", "planejar": "plan", "plano": "plan",
    "auditoria": "audit", "auditar": "audit", "otimizar": "optimize", "otimizacao": "optimization",
    "contexto": "context", "memoria": "memory", "imagem": "image", "imagens": "images", "video": "video",
    "planilha": "spreadsheet", "apresentacao": "presentation", "slides": "slides", "contrato": "contract",
}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "use", "when", "user", "your", "you", "are", "into",
    "any", "all", "can", "not", "has", "have", "will", "its", "via", "also", "more", "such", "using", "used",
    "que", "com", "para", "uma", "por", "como", "mais", "nos", "nas", "dos", "das", "seu", "sua", "isso",
    "esta", "este", "essa", "esse", "ter", "ser", "faz", "fazer", "vamos", "tem", "toda", "todo", "mas",
    "skill", "skills", "claude", "code", "ate", "tambem", "quando", "onde", "qual", "nao", "sim",
    "create", "fix", "improve", "implement", "make", "new", "need", "want", "help", "check", "segue",
}


@dataclass
class Skill:
    name: str
    path: str
    description: str
    tokens: int
    source: str
    invoke: str = ""
    score: int = 0
    tier: str = "excluded"
    reasons: list[str] | None = None


def force_rmtree(path) -> None:
    """shutil.rmtree that also deletes read-only files (git objects on Windows)."""
    import stat

    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    # onexc only exists on Python 3.12+; onerror covers older versions.
    kw = {"onexc": retry} if sys.version_info >= (3, 12) else {"onerror": retry}
    shutil.rmtree(path, **kw)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def terms(text: str) -> list[str]:
    out = []
    for word in re.findall(r"[a-z0-9][a-z0-9+#-]*", _fold(text)):
        word = word.strip("-")
        word = PT_EN.get(word, word)
        if len(word) < 3 or word in STOPWORDS:
            continue
        out.append(word)
    return out


def _stem(word: str) -> str:
    return word[:5] if len(word) >= 6 else word


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return config


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    key = None
    for line in text[3:end].splitlines():
        if line.startswith((" ", "\t")) and key:
            # Continuation of a YAML block value (description: >- or |).
            values[key] = (values[key] + " " + line.strip()).strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            values[key] = "" if value in {">", ">-", "|", "|-"} else value.strip('"\'')
    return values


def _skill_from_file(skill_file: Path, default_name: str, source: str, prefix: str = "") -> Skill | None:
    try:
        size = skill_file.stat().st_size
        with skill_file.open(encoding="utf-8", errors="replace") as handle:
            head = handle.read(HEAD_CHARS)
    except OSError:
        return None
    meta = _frontmatter(head)
    name = meta.get("name") or default_name
    desc = meta.get("description") or next((x.strip("# ") for x in head.splitlines() if x.startswith("#")), "No description")
    invoke = f"{prefix}:{name}" if prefix else name
    return Skill(name, str(skill_file.parent), desc[:600], max(1, round(min(size, MAX_SKILL_CHARS) / 4)), source, invoke)


def enabled_plugin_paths() -> list[tuple[str, Path]]:
    try:
        installed = json.loads(PLUGINS_FILE.read_text(encoding="utf-8-sig")).get("plugins", {})
    except (OSError, json.JSONDecodeError):
        return []
    try:
        enabled = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig")).get("enabledPlugins", {})
    except (OSError, json.JSONDecodeError):
        enabled = {}
    result = []
    for key, entries in installed.items():
        if enabled.get(key) is False or not entries:
            continue
        path = Path(entries[-1].get("installPath", ""))
        if path.is_dir():
            result.append((key.split("@", 1)[0], path))
    return result


def _local_inventory() -> list[Skill]:
    skills: list[Skill] = []
    if GLOBAL_SKILLS.exists():
        for skill_file in GLOBAL_SKILLS.glob("*/SKILL.md"):
            if skill := _skill_from_file(skill_file, skill_file.parent.name, "global"):
                skills.append(skill)
        # Loose files (e.g. compact.md) work as /name commands.
        for loose in GLOBAL_SKILLS.glob("*.md"):
            if skill := _skill_from_file(loose, loose.stem, "global"):
                skill.name = skill.invoke = loose.stem
                skill.path = str(loose)
                skills.append(skill)
    if GLOBAL_COMMANDS.exists():
        for command in GLOBAL_COMMANDS.glob("*.md"):
            if skill := _skill_from_file(command, command.stem, "command"):
                skill.name = skill.invoke = command.stem
                skill.path = str(command)
                skills.append(skill)
    return skills


def _plugin_inventory() -> list[Skill]:
    skills: list[Skill] = []
    for plugin, base in enabled_plugin_paths():
        for skill_file in base.glob("skills/*/SKILL.md"):
            if skill := _skill_from_file(skill_file, skill_file.parent.name, "plugin", plugin):
                skills.append(skill)
    return skills


def _inventory_signature() -> str:
    parts = [INVENTORY_VERSION]
    for path in (PLUGINS_FILE, SETTINGS_FILE, GLOBAL_SKILLS, GLOBAL_COMMANDS):
        try:
            parts.append(f"{path}:{path.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{path}:-")
    # Editing a SKILL.md does not change the folder's mtime, so local skills are checked one by one (a few dozen stats).
    for pattern, base in (("*/SKILL.md", GLOBAL_SKILLS), ("*.md", GLOBAL_SKILLS), ("*.md", GLOBAL_COMMANDS)):
        for path in sorted(base.glob(pattern)) if base.is_dir() else []:
            try:
                parts.append(f"{path.name}:{path.stat().st_mtime_ns}")
            except OSError:
                pass
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def inventory(include_plugins: bool = True) -> list[Skill]:
    """Global + plugin skills, cached on disk because plugins hold hundreds of files."""
    cache = STATE / "inventory.json"
    signature = _inventory_signature()
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("signature") == signature and time.time() - cached.get("at", 0) < INVENTORY_TTL:
            skills = [Skill(**item) for item in cached["skills"]]
            return [s for s in skills if include_plugins or s.source != "plugin"]
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        pass
    skills = _local_inventory() + _plugin_inventory()
    try:
        STATE.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"signature": signature, "at": time.time(), "skills": [asdict(s) for s in skills]}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return [s for s in skills if include_plugins or s.source != "plugin"]


def discover_skills(project: Path | None = None, include_plugins: bool = True) -> list[Skill]:
    found: dict[str, Skill] = {}
    for skill in inventory(include_plugins):
        found.setdefault(skill.invoke, skill)
    if project:
        base = project / ".claude" / "skills"
        if base.exists():
            for skill_file in base.glob("*/SKILL.md"):
                if skill := _skill_from_file(skill_file, skill_file.parent.name, "project"):
                    found[skill.invoke] = skill
    return sorted(found.values(), key=lambda x: x.invoke.lower())


def _scan_project(project: Path) -> tuple[set[str], list[str]]:
    tokens: set[str] = set()
    evidence: list[str] = []
    count = 0
    for current, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        for filename in files:
            if count >= MAX_FILES:
                evidence.append(f"Read limited to the first {MAX_FILES} files")
                return tokens, evidence
            count += 1
            low = filename.lower()
            tokens.update(re.findall(r"[a-z0-9+#.-]+", low))
            if low in MANIFESTS:
                evidence.append(low)
                try:
                    with open(os.path.join(current, filename), encoding="utf-8", errors="ignore") as handle:
                        tokens.update(re.findall(r"[a-z0-9+#.-]+", handle.read(6000).lower()))
                except OSError:
                    pass
    return tokens, evidence


def project_signals(project: Path, task: str) -> tuple[set[str], list[str]]:
    tokens = set(re.findall(r"[a-z0-9+#.-]+", task.lower()))
    if not (project.exists() and project.is_dir()):
        return tokens, []
    cache = STATE / f"scan-{hashlib.sha256(str(project).encode()).hexdigest()[:16]}.json"
    try:
        mtime = project.stat().st_mtime_ns
    except OSError:
        mtime = 0
    words: set[str] | None = None
    evidence: list[str] = []
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("mtime") == mtime and time.time() - cached.get("at", 0) < SCAN_TTL:
            words, evidence = set(cached["words"]), cached["evidence"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    if words is None:
        words, evidence = _scan_project(project)
        try:
            STATE.mkdir(exist_ok=True)
            cache.write_text(json.dumps({"mtime": mtime, "at": time.time(), "words": sorted(words)[:6000], "evidence": evidence}), encoding="utf-8")
        except OSError:
            pass
    return tokens | words, list(evidence)[:12]





def laya_categories(task: str) -> tuple[list[str], str | None]:
    if not task.strip():
        return [], None
    try:
        if time.time() - (STATE / "laya-down").stat().st_mtime < 60:
            return [], None
    except OSError:
        pass
    try:
        body = json.dumps({"task": task}, ensure_ascii=False).encode("utf-8")
        import urllib.request
        request = urllib.request.Request(LAYA_URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=1.8) as response:
            result = json.loads(response.read())
        probabilities = result.get("probabilities", {})
        ordered = sorted(probabilities, key=probabilities.get, reverse=True)
        chosen = [name for name in ordered if probabilities[name] >= 0.18][:2]
        if result.get("choice") and result["choice"] not in chosen:
            chosen.insert(0, result["choice"])
        return chosen, f"Laya: {result.get('choice')} {result.get('confidence', 0):.0%}"
    except Exception:
        try:
            STATE.mkdir(exist_ok=True)
            (STATE / "laya-down").touch()
        except OSError:
            pass
        return [], None


def laya_online(timeout: float = 0.25) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(LAYA_URL, timeout=timeout).close()
        return True
    except Exception:
        return False


# ---------- Learning from usage ----------

def _usage_file() -> Path:
    return STATE / "usage.json"


def load_usage() -> dict:
    try:
        return json.loads(_usage_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"global": {}, "projects": {}, "last": {}}


def record_usage(project: str, skill: str, weight: int = 1) -> None:
    data = load_usage()
    data.setdefault("global", {})
    data.setdefault("projects", {})
    data["global"][skill] = data["global"].get(skill, 0) + weight
    bucket = data["projects"].setdefault(str(Path(project).resolve()), {})
    bucket[skill] = bucket.get(skill, 0) + weight
    STATE.mkdir(exist_ok=True)
    _usage_file().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def record_selection(project: str, selected: list[str]) -> None:
    data = load_usage()
    data.setdefault("last", {})[str(Path(project).resolve())] = {"selected": selected, "at": int(time.time())}
    STATE.mkdir(exist_ok=True)
    _usage_file().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


CONVERSATIONAL = re.compile(
    r"^\s*(ok|okay|yes|no|thanks|thank you|great|perfect|sure|go on|continue|proceed|"
    r"sim|n[aã]o|obrigad[oa]|valeu|beleza|perfeito|certo|boa|show|continua|segue|prossiga|pode|isso)\b"
    r"|\b(is it (done|ready)|what('s| is) (left|missing)|what do you think|how does it work|what('s| is) the difference)\b"
    r"|\b(t[aá] pront[oa]|est[aá] pront[oa]|o que falta|o que achas|na tua vis[aã]o|explica|porque|por que|como funciona|qual a diferen[cç]a)\b",
    re.I,
)


def is_conversational(prompt: str) -> bool:
    """Questions about work in progress need no new skills: the conversation context is enough."""
    text = prompt.strip()
    words = terms(text)
    task_area = any(any((k in set(words)) if " " not in k else (k in text.lower()) for k in keys) for keys in SIGNALS.values())
    if CONVERSATIONAL.search(text) and len(words) < 10 and not task_area:
        return True
    return text.endswith("?") and len(words) < 6 and not task_area


def health_flags() -> dict[str, str]:
    """Health status per skill, read from the latest report (skill_health.py). Empty until the first audit."""
    try:
        report = json.loads((STATE / "health.json").read_text(encoding="utf-8"))
        return {row["invoke"]: row["status"] for row in report.get("skills", [])}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}


# ---------- Ranking ----------

def _relevance(skills: list[Skill], task_terms: list[str]) -> dict[str, tuple[float, list[str]]]:
    docs = {}
    df: dict[str, int] = {}
    for skill in skills:
        name_terms = {_stem(t) for t in terms(skill.invoke.replace(":", " ").replace("-", " "))}
        desc_terms = {_stem(t) for t in terms(skill.description)}
        docs[skill.invoke] = (name_terms, desc_terms)
        for term in name_terms | desc_terms:
            df[term] = df.get(term, 0) + 1
    n = max(1, len(skills))
    query = list(dict.fromkeys(_stem(t) for t in task_terms))
    out = {}
    for invoke, (name_terms, desc_terms) in docs.items():
        total, hits = 0.0, []
        for term in query:
            idf = math.log(1 + n / (1 + df.get(term, 0)))
            if term in name_terms:
                total += 3 * idf
                hits.append(term)
            elif term in desc_terms:
                total += idf
                hits.append(term)
        out[invoke] = (total, hits)
    return out


def analyze(project_path: str, task: str, config: dict | None = None) -> dict:
    config = config or load_config()
    project = Path(project_path).expanduser().resolve()
    words, evidence = project_signals(project, task)
    task_text = task.lower()
    task_words = set(terms(task)) | set(re.findall(r"[a-z0-9]+", task_text))
    # Plain plurals ("tests", "apis") should hit the same areas as the singular.
    task_words |= {w[:-1] for w in task_words if len(w) > 3 and w.endswith("s")}
    categories = [name for name, keys in SIGNALS.items() if any((k in task_words) if " " not in k else (k in task_text) for k in keys)]
    # The classifier only runs when keywords are not enough: saves ~300 ms per prompt.
    laya_hits, laya_evidence = laya_categories(task) if not categories else ([], None)
    # Laya always returns some category, so it only confirms or adds with a lower weight.
    laya_only = [c for c in laya_hits if c not in categories]
    # Project-only signals (no hint in the task) weigh little: the stack does not say what is being done.
    project_categories = [name for name, keys in SIGNALS.items() if name not in categories and any(k in words for k in keys)]
    if laya_evidence:
        evidence.insert(0, laya_evidence)
    health = health_flags()
    excluded = set(config.get("blocked", [])) | {name for name, status in health.items() if status == "insecure"}
    skills = [s for s in discover_skills(project, config.get("include_plugins", True)) if s.invoke not in excluded]
    relevance = _relevance(skills, terms(task))
    usage = load_usage()
    project_usage = usage.get("projects", {}).get(str(project), {})
    global_usage = usage.get("global", {})
    pinned = set(config.get("pinned", []))
    skill_purposes: dict[str, list[str]] = {}
    for skill in skills:
        name_text = skill.name.lower()
        reasons: list[str] = []
        score = 0
        curated = skill.source != "plugin"
        tail = name_text.split("-")[-1]
        for hint, keys in (SKILL_HINTS.items() if curated else ()):
            if hint in tail or hint in name_text:
                matches = [k for k in keys if k in task_text]
                if matches:
                    score += min(65, 24 + 12 * len(matches))
                    reasons.append("Task: " + ", ".join(matches[:3]))
        name_parts = set(re.findall(r"[a-z0-9]+", name_text))
        purposes = [
            purpose for purpose, aliases in PURPOSE_NAMES.items()
            if curated and any(alias == name_text or alias in name_parts for alias in aliases)
        ]
        skill_purposes[skill.invoke] = purposes
        purpose_hits = [p for p in purposes if p in categories]
        if purpose_hits:
            score += 32
            reasons.append("Area: " + ", ".join(purpose_hits))
        elif any(p in laya_only for p in purposes):
            score += 16
            reasons.append("Laya: " + ", ".join(p for p in purposes if p in laya_only))
        elif any(p in project_categories for p in purposes):
            score += 8
            reasons.append("Project stack: " + ", ".join(p for p in purposes if p in project_categories))
        rel, hits = relevance.get(skill.invoke, (0.0, []))
        if hits:
            gain = min(70, round(rel * 4)) + (6 if curated else 0)
            # A single word shared with a plugin description is noise ("recommendation" -> recommend).
            if not curated and len(hits) < 2:
                gain = min(gain, 18)
            score += gain
            reasons.append("Description: " + ", ".join(hits[:4]))
        desc_terms = {w for w in re.findall(r"[a-z0-9+#.-]+", skill.description.lower()) if len(w) > 5}
        overlap = sorted(desc_terms & words)
        if overlap:
            score += min(12, len(overlap) * 3)
            reasons.append("Project: " + ", ".join(overlap[:4]))
        if skill.source == "project":
            score += 12
            reasons.append("Skill specific to this project")
        used_here = project_usage.get(skill.invoke, 0)
        if used_here and score:
            score += min(15, 5 * used_here)
            reasons.append(f"Used {used_here}x in this project")
        elif global_usage.get(skill.invoke, 0) > 0 and score:
            score += min(6, 2 * global_usage[skill.invoke])
            reasons.append("Used in other projects")
        if health.get(skill.invoke) == "obsolete" and score:
            score -= 40
            reasons.append("Obsolete: see Skill health")
        elif health.get(skill.invoke) == "risky" and score:
            reasons.append("Review security before using")
        if skill.invoke in pinned:
            score += 100
            reasons.insert(0, "Pinned in config.json")
        skill.score = max(0, min(99, score))
        skill.tier = "recommended" if skill.score >= 28 else "optional" if skill.score >= 10 else "excluded"
        skill.reasons = reasons or ["No strong match for this task"]
    limit = int(config.get("max_skills", 4))
    candidates = [s for s in sorted(skills, key=lambda x: (-x.score, x.tokens)) if s.tier == "recommended"]
    recommended: list[Skill] = [s for s in candidates if s.invoke in pinned][:limit]
    for category in categories + laya_only:
        # Covering an area is only worth it with a real match; otherwise the score wins.
        match = next((s for s in candidates if category in skill_purposes[s.invoke] and s not in recommended and s.score >= 40), None)
        if match and len(recommended) < limit:
            recommended.append(match)
    def plugin_of(s: Skill) -> str:
        return s.invoke.split(":", 1)[0] if ":" in s.invoke else s.invoke

    # A free slot is not a reason to add a weak match: fillers must be close to the best one.
    floor = 0.6 * max((s.score for s in candidates), default=0)
    for skill in candidates:
        if len(recommended) >= limit:
            break
        same_plugin = sum(1 for r in recommended if ":" in r.invoke and plugin_of(r) == plugin_of(skill))
        if skill not in recommended and same_plugin < 2 and (skill.score >= floor or skill.invoke in pinned):
            recommended.append(skill)
    keep = {s.invoke for s in recommended}
    for s in skills:
        if s.tier == "recommended" and s.invoke not in keep:
            s.tier = "optional"
            s.reasons.append(f"Outside the minimal set of {limit} skills")
    all_tokens = sum(s.tokens for s in skills)
    selected_tokens = sum(s.tokens for s in skills if s.invoke in keep)
    sources: dict[str, int] = {}
    for s in skills:
        sources[s.source] = sources.get(s.source, 0) + 1
    return {
        "project": str(project),
        "task": task,
        "categories": list(dict.fromkeys(categories + laya_only)),
        "project_categories": project_categories,
        "evidence": evidence,
        "sources": sources,
        "skills": [asdict(s) for s in sorted(skills, key=lambda x: ({"recommended": 0, "optional": 1, "excluded": 2}[x.tier], -x.score, x.invoke))],
        "selected": [s.invoke for s in sorted(recommended, key=lambda x: -x.score)],
        "tokens": {"all": all_tokens, "selected": selected_tokens, "saved": max(0, all_tokens - selected_tokens)},
    }


def manifest(result: dict) -> str:
    """Compact instructions for a live session: names + one-line purpose, bodies load on demand."""
    by_name = {s["invoke"]: s for s in result["skills"]}
    lines = [
        "SKILL ROUTER: minimal skill set for this prompt (selected locally, no code sent anywhere).",
        "When the work reaches one of these areas, invoke the skill with the Skill tool. Do not load other skills unless the scope clearly changes.",
    ]
    for name in result["selected"]:
        skill = by_name.get(name, {})
        desc = re.sub(r"\s+", " ", skill.get("description", ""))[:160]
        lines.append(f"- {name}: {desc}")
    if not result["selected"]:
        lines.append("- no skill needed; answer directly.")
    return "\n".join(lines)


def skill_body(skill: Skill) -> str:
    target = Path(skill.path)
    target = target if target.is_file() else target / "SKILL.md"
    return target.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_CHARS]


def route_markdown(result: dict) -> str:
    """Human/agent-readable summary used by /route-skills and Codex."""
    by_name = {s["invoke"]: s for s in result["skills"]}
    lines = [f"Project: {result['project']}", f"Signals: {', '.join(result['categories']) or 'none dominant'}"]
    if result["evidence"]:
        lines.append("Evidence: " + ", ".join(result["evidence"][:6]))
    lines.append("")
    lines.append("Selected:")
    for name in result["selected"]:
        s = by_name[name]
        lines.append(f"- {name} ({s['score']}/99, ~{s['tokens']} tk) - {s['reasons'][0]}")
    optional = [s for s in result["skills"] if s["tier"] == "optional"][:5]
    if optional:
        lines.append("Optional: " + ", ".join(f"{s['invoke']} ({s['score']})" for s in optional))
    t = result["tokens"]
    lines.append(f"Skill context: {t['selected']} of {t['all']} tokens ({t['saved']} avoided)")
    return "\n".join(lines)


CLAUDE_EXE = Path.home() / ".local" / "bin" / "claude.exe"
CONSULT_MODEL = "haiku"
CONSULT_CANDIDATES = 40


def consult_prompt(result: dict) -> str:
    candidates = [s for s in result["skills"] if s["score"] > 0][:CONSULT_CANDIDATES]
    lines = [
        "You are a developer's skill advisor. Pick the most useful skills for this task, only from the list below.",
        f"Task: {result['task'] or '(not described; decide from the project)'}",
        f"Project: {Path(result['project']).name}",
        "Project signals: " + (", ".join(result["evidence"][:8]) or "no manifests"),
        "Detected areas: " + (", ".join(result["categories"] + result.get("project_categories", [])) or "none"),
        "",
        "Candidates (name | source | description):",
    ]
    for s in candidates:
        desc = re.sub(r"\s+", " ", s["description"])[:180]
        lines.append(f"- {s['invoke']} | {s['source']} | {desc}")
    lines += [
        "",
        "Rules: at most 4 picks; prefer few, precise ones; a generic skill only goes in if no specific skill covers the area.",
        'Reply with JSON only, no markdown: {"summary": "one sentence about the kind of project and the approach",'
        ' "picks": [{"skill": "exact name", "why": "short reason"}], "avoid": [{"skill": "exact name", "why": "short reason"}]}',
    ]
    return "\n".join(lines)


def _json_from_text(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Claude did not return JSON.")
    return json.loads(text[start:end + 1])


def consult_claude(project: str, task: str, runner=subprocess.run) -> dict:
    """Ask the local Claude CLI to pick skills. Isolated: no tools, hooks, plugins, MCP or saved session."""
    result = analyze(project, task)
    exe = str(CLAUDE_EXE) if CLAUDE_EXE.exists() else (shutil.which("claude") or "claude")
    STATE.mkdir(exist_ok=True)
    started = time.time()
    proc = runner(
        [exe, "-p", "--model", CONSULT_MODEL, "--output-format", "json", "--tools", "", "--strict-mcp-config",
         "--setting-sources", "", "--no-session-persistence", "--disable-slash-commands"],
        input=consult_prompt(result), capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(STATE),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "Claude CLI failed").strip()[:300])
    envelope = json.loads(proc.stdout)
    answer = _json_from_text(str(envelope.get("result", "")))
    known = {s["invoke"]: s for s in result["skills"]}
    picks = [p for p in answer.get("picks", []) if isinstance(p, dict) and p.get("skill") in known][:4]
    avoid = [a for a in answer.get("avoid", []) if isinstance(a, dict) and a.get("skill") in known][:4]
    usage = envelope.get("usage", {})
    return {
        "summary": str(answer.get("summary", "")),
        "picks": picks,
        "avoid": avoid,
        "router": result["selected"],
        "model": CONSULT_MODEL,
        "seconds": round(time.time() - started, 1),
        "tokens": sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")),
        "cost_usd": envelope.get("total_cost_usd"),
    }


def health_action(health, data: dict) -> str:
    """Single entry point for management actions; all reversible except purge and uninstall."""
    action = data.get("action")
    target = str(data.get("target", ""))
    if action == "block":
        health.set_blocked(target, True)
        return f"{target} blocked in the router."
    if action == "unblock":
        health.set_blocked(target, False)
        health.set_protected(target, True)
        return f"{target} unblocked and protected from auto cleanup."
    if action == "unprotect":
        health.set_protected(target, False)
        return f"{target} is back under auto cleanup."
    if action == "purge":
        info = health.purge(target)
        return f"{info['invoke']} permanently deleted."
    if action == "uninstall_plugin":
        health.uninstall_plugin(target)
        return f"Plugin {target} uninstalled. Restart Claude to apply."
    if action == "auto_cleanup":
        enabled = target == "on"
        health._save_config({"auto_cleanup": enabled})
        return "Auto cleanup on." if enabled else "Auto cleanup off."
    if action == "quarantine":
        info = health.quarantine(target)
        return f"{target} moved to quarantine (from {info['from']})."
    if action == "restore":
        info = health.restore(target)
        return f"{info['invoke']} restored to {info['from']}."
    if action == "disable_plugin":
        health.disable_plugin(target)
        return f"Plugin {target} disabled. Restart Claude to apply."
    if action == "enable_plugin":
        health.enable_plugin(target)
        return f"Plugin {target} enabled. Restart Claude to apply."
    if action == "update_plugin":
        health.update_plugin(target)
        return f"Plugin {target} updated. Restart Claude to apply."
    raise ValueError("Unknown action.")


def metrics(days: int = 30) -> dict:
    """Router accuracy from .state/metrics.jsonl: selected vs used skills, injected tokens, latency."""
    since = time.time() - days * 86400
    sessions: dict[str, dict] = {}
    latencies, injected = [], []
    try:
        lines = (STATE / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("ts", 0) < since:
            continue
        bucket = sessions.setdefault(str(entry.get("session")), {"selected": set(), "used": set()})
        if entry.get("event") == "use":
            bucket["used"].add(entry["skill"])
        elif entry.get("event") == "UserPromptSubmit":
            if entry.get("mode") in {"strict", "launcher"}:
                # In these modes bodies ride in the prompt and the Skill tool is not used, so accuracy cannot be measured.
                bucket["unmeasurable"] = True
            bucket["selected"].update(entry.get("selected") or [])
            if entry.get("ms") is not None:
                latencies.append(entry["ms"])
            if entry.get("changed"):
                injected.append(entry.get("injected_chars", 0))
    selected_total = used_hit = used_total = 0
    misses: dict[str, int] = {}
    ignored: dict[str, int] = {}
    for bucket in sessions.values():
        if (not bucket["selected"] and not bucket["used"]) or bucket.get("unmeasurable"):
            continue
        selected_total += len(bucket["selected"])
        used_total += len(bucket["used"])
        used_hit += len(bucket["selected"] & bucket["used"])
        for name in bucket["used"] - bucket["selected"]:
            misses[name] = misses.get(name, 0) + 1
        for name in bucket["selected"] - bucket["used"]:
            ignored[name] = ignored.get(name, 0) + 1
    latencies.sort()
    pct = lambda part, whole: round(100 * part / whole) if whole else None
    return {
        "days": days,
        "sessions": sum(1 for b in sessions.values() if b["selected"] or b["used"]),
        "precision": pct(used_hit, selected_total),
        "recall": pct(used_hit, used_total),
        "avg_injected_tokens": round(sum(injected) / len(injected) / 4) if injected else None,
        "latency_ms": {"median": latencies[len(latencies) // 2] if latencies else None,
                       "p95": latencies[int(len(latencies) * 0.95)] if latencies else None},
        "top_misses": sorted(misses.items(), key=lambda x: -x[1])[:5],
        "top_ignored": sorted(ignored.items(), key=lambda x: -x[1])[:5],
    }


def health_markdown(report: dict, include_low: bool = False) -> str:
    labels = {"insecure": "INSECURE", "risky": "RISKY", "obsolete": "OBSOLETE", "outdated": "OUTDATED", "review": "REVIEW"}
    s = report["summary"]
    lines = [f"Health of {sum(s.values())} skills: " + ", ".join(f"{n} {k}" for k, n in sorted(s.items()))]
    shown = [r for r in report["skills"] if r["status"] in labels and (include_low or r["status"] != "outdated")]
    for row in shown:
        issue = row["issues"][0]
        where = f" ({issue['where']})" if issue.get("where") else ""
        flag = " [blocked]" if row.get("blocked") else ""
        lines.append(f"- {labels[row['status']]:13} {row['invoke']}{flag}: {issue['why']}{where}")
    plugins = [p for p in report["plugins"] if p["outdated"]]
    if plugins:
        lines.append("Plugins with updates: " + ", ".join(f"{p['key']} {p['installed']} -> {p['latest']}" for p in plugins))
    if report.get("quarantine"):
        lines.append("In quarantine: " + ", ".join(q["id"] for q in report["quarantine"]))
    lines.append("Actions: app.py block|unblock|quarantine|restore|disable-plugin|update-plugin <target>")
    return "\n".join(lines)


def status() -> dict:
    skills = discover_skills()
    sources: dict[str, int] = {}
    for s in skills:
        sources[s.source] = sources.get(s.source, 0) + 1
    try:
        last = json.loads((STATE / "last-hook.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        last = None
    import claude_cli
    return {"version": __version__, "skills": len(skills), "sources": sources, "laya": laya_online(), "config": load_config(),
            "last_hook": last, "claude_cli": claude_cli.read_cache()}


def profile_path(project: str) -> Path:
    PROFILES.mkdir(exist_ok=True)
    return PROFILES / (hashlib.sha256(project.encode()).hexdigest()[:16] + ".json")


def save_profile(data: dict) -> Path:
    target = profile_path(data["project"])
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def build_prompt(project: str, task: str, selected: list[str]) -> tuple[Path, str]:
    available = {s.invoke: s for s in discover_skills(Path(project))}
    chunks = [
        "# Session profile generated by Claude Skill Router",
        "Use only the skill instructions included below. Do not search for or load other skills in this session.",
        f"Project: {project}",
        f"Initial goal: {task or 'To be set by the user in the terminal.'}",
    ]
    valid = []
    for name in selected:
        skill = available.get(name)
        if not skill:
            continue
        chunks.extend([f"\n## ACTIVE SKILL: {name}", skill_body(skill)])
        valid.append(name)
    session_dir = ROOT / ".sessions"
    session_dir.mkdir(exist_ok=True)
    digest = hashlib.sha256((project + task + "|".join(valid)).encode()).hexdigest()[:12]
    prompt_file = session_dir / f"session-{digest}.md"
    prompt_file.write_text("\n".join(chunks), encoding="utf-8")
    command = f"claude --disable-slash-commands --append-system-prompt-file \"{prompt_file}\""
    return prompt_file, command


def safe_project_dir(project: str) -> Path:
    """A real, existing folder with no control characters; anything else is refused."""
    if not project or any(ord(ch) < 32 for ch in project):
        raise ValueError("Invalid project folder.")
    path = Path(project).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Folder does not exist: {path}")
    return path


def launch_terminal(project: str, prompt_file: Path) -> None:
    # The folder becomes the process cwd and the prompt is our own file: no request text reaches the command line.
    cwd = safe_project_dir(project)
    if prompt_file.resolve().parent != (ROOT / ".sessions").resolve():
        raise ValueError("Unexpected session file.")
    exe = str(CLAUDE_EXE) if CLAUDE_EXE.exists() else "claude"
    ps = f"& '{exe}' --disable-slash-commands --append-system-prompt-file '{prompt_file.resolve()}'"
    bundled_pwsh = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "powershell" / "pwsh.exe"
    shell = shutil.which("pwsh") or (str(bundled_pwsh) if bundled_pwsh.exists() else "powershell.exe")
    # The hook does not inject bodies again: they are already in the system prompt.
    env = {**os.environ, "SKILL_ROUTER_MODE": "launcher"}
    subprocess.Popen([shell, "-NoExit", "-NoProfile", "-Command", ps], cwd=str(cwd), env=env,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        relative = (urlparse(path).path.lstrip("/") or "index.html").replace("\\", "/")
        target = (STATIC / relative).resolve()
        try:
            target.relative_to(STATIC.resolve())
        except ValueError:
            return str(STATIC / "__not_found__")
        return str(target)

    def end_headers(self) -> None:
        # Always revalidate so the panel never keeps stale JS/CSS after the router is updated.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, payload: dict, status_code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _trusted(self, write: bool) -> bool:
        """Blocks DNS rebinding (Host) and CSRF (Origin + a custom header that forces a CORS preflight)."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").lower()
        if host not in {f"{h}:{port}" for h in ALLOWED_HOSTS}:
            return False
        if not write:
            return True
        origin = (self.headers.get("Origin") or "").lower()
        if origin and origin not in {f"http://{h}:{port}" for h in ALLOWED_HOSTS}:
            return False
        return self.headers.get("X-Skill-Router") == "1" and (self.headers.get("Content-Type") or "").startswith("application/json")

    def _refuse(self) -> None:
        self.send_json({"error": "Request refused: only the local panel may use this API."}, 403)

    def do_GET(self) -> None:
        if not self._trusted(write=False):
            self._refuse()
            return
        route = urlparse(self.path).path
        if route == "/api/skills":
            self.send_json({"skills": [asdict(s) for s in discover_skills()]})
            return
        if route == "/api/status":
            self.send_json(status())
            return
        if route == "/api/health":
            import skill_health
            self.send_json(skill_health.audit())
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not self._trusted(write=True):
            self._refuse()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            route = urlparse(self.path).path
            if route == "/api/analyze":
                self.send_json(analyze(data.get("project", ""), data.get("task", "")))
            elif route == "/api/consult":
                self.send_json(consult_claude(data.get("project", ""), data.get("task", "")))
            elif route == "/api/claude-cli":
                import claude_cli
                if data.get("op") == "update":
                    self.send_json(claude_cli.update())
                else:
                    self.send_json(claude_cli.check(force=bool(data.get("force"))))
            elif route == "/api/health/refresh":
                import skill_health
                self.send_json(skill_health.audit(force=True))
            elif route == "/api/discover":
                import discover
                self.send_json(discover.discover(data.get("task", ""), data.get("project", "."), with_github=data.get("github", True)))
            elif route == "/api/install":
                import discover
                if data.get("kind") == "plugin":
                    message = discover.install_plugin(data["name"], data["marketplace"], data.get("scope", "project"), safe_project_dir(data["project"]))
                else:
                    message = "Installed at " + discover.install_staged(data["stage"], data["path"], bool(data.get("accept_risk")))
                self.send_json({"ok": True, "message": message})
            elif route == "/api/stage":
                import discover
                self.send_json(discover.stage_repo(data.get("url", "")))
            elif route == "/api/plugin-profile":
                import plugin_profile
                op = data.get("op", "plan")
                if op == "apply":
                    self.send_json({**plugin_profile.apply(data["project"], data.get("keys")), "plan": plugin_profile.plan(data["project"])})
                elif op == "undo":
                    self.send_json({**plugin_profile.undo(data["project"]), "plan": plugin_profile.plan(data["project"])})
                else:
                    self.send_json(plugin_profile.plan(data["project"]))
            elif route == "/api/metrics":
                self.send_json(metrics())
            elif route == "/api/health/cleanup":
                import skill_health
                result = skill_health.cleanup(apply=bool(data.get("apply")))
                self.send_json({**result, "report": skill_health.audit()})
            elif route == "/api/health/action":
                import skill_health
                self.send_json({"ok": True, "message": health_action(skill_health, data)})
            elif route == "/api/feedback":
                # The advisor trains the ranking: picks go up, avoided skills go down in this project.
                for name in data.get("up", [])[:6]:
                    record_usage(data["project"], name, 1)
                for name in data.get("down", [])[:6]:
                    record_usage(data["project"], name, -1)
                self.send_json({"ok": True})
            elif route == "/api/profile":
                target = save_profile(data)
                self.send_json({"ok": True, "path": str(target)})
            elif route in {"/api/command", "/api/launch"}:
                safe_project_dir(data.get("project", ""))
                prompt, command = build_prompt(data["project"], data.get("task", ""), data.get("selected", []))
                if route == "/api/launch":
                    launch_terminal(data["project"], prompt)
                    record_selection(data["project"], data.get("selected", []))
                self.send_json({"ok": True, "prompt": str(prompt), "command": command})
            else:
                self.send_json({"error": "Unknown route"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def serve(port: int, open_browser: bool = False) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Claude Skill Router at http://127.0.0.1:{port}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    server.serve_forever()


def main() -> None:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Claude Skill Router")
    sub = parser.add_subparsers(dest="cmd")
    run = sub.add_parser("serve")
    run.add_argument("--port", type=int, default=9127)
    run.add_argument("--open", action="store_true")
    for name in ("analyze", "route", "consult"):
        check = sub.add_parser(name)
        check.add_argument("--project", default=".")
        check.add_argument("--task", default="")
    sub.add_parser("status")
    cli = sub.add_parser("claude-version", help="Installed Claude Code version vs the latest release on your update channel")
    cli.add_argument("--refresh", action="store_true")
    sub.add_parser("claude-update", help="Update Claude Code with the official `claude update`")
    sub.add_parser("version", help="Skill Router version")
    health_cli = sub.add_parser("health", help="Audit: outdated, obsolete, insecure and incomplete skills")
    health_cli.add_argument("--refresh", action="store_true")
    health_cli.add_argument("--json", action="store_true")
    health_cli.add_argument("--all", action="store_true", help="Include low-severity issues")
    sub.add_parser("metrics", help="Router accuracy, injected tokens and latency over the last 30 days")
    disc = sub.add_parser("discover", help="Find new skills/plugins for the task (official catalog + GitHub)")
    disc.add_argument("--project", default=".")
    disc.add_argument("--task", required=True)
    disc.add_argument("--offline", action="store_true", help="Local catalog only, no GitHub")
    disc.add_argument("--json", action="store_true")
    stage = sub.add_parser("stage", help="Download a GitHub repository to staging and audit its skills")
    stage.add_argument("url")
    inst = sub.add_parser("install", help="Install a plugin (name@marketplace) or an audited staged skill (id/path)")
    inst.add_argument("target")
    inst.add_argument("--project", default=".")
    inst.add_argument("--scope", default="project", choices=["user", "project", "local"])
    inst.add_argument("--accept-risk", action="store_true")
    prof = sub.add_parser("plugin-profile", help="Suggest (or apply with --apply) plugins to turn off in this project only")
    prof.add_argument("--project", default=".")
    prof.add_argument("--apply", action="store_true")
    prof.add_argument("--undo", action="store_true")
    clean = sub.add_parser("cleanup", help="Show (or apply with --apply) the cleanup of obsolete and insecure skills")
    clean.add_argument("--apply", action="store_true")
    for verb in ("block", "unblock", "unprotect", "quarantine", "restore", "purge", "disable_plugin", "enable_plugin", "update_plugin", "uninstall_plugin", "auto_cleanup"):
        act = sub.add_parser(verb.replace("_", "-"))
        act.add_argument("target")
    feedback = sub.add_parser("feedback", help="Reward (+) or penalize (-) a skill in this project")
    feedback.add_argument("--project", default=".")
    feedback.add_argument("--skill", required=True)
    feedback.add_argument("--vote", choices=["up", "down"], default="up")
    args = parser.parse_args()
    if args.cmd == "analyze":
        print(json.dumps(analyze(args.project, args.task), ensure_ascii=False, indent=2))
    elif args.cmd == "route":
        result = analyze(args.project, args.task)
        record_selection(result["project"], result["selected"])
        print(route_markdown(result))
    elif args.cmd == "consult":
        print(json.dumps(consult_claude(args.project, args.task), ensure_ascii=False, indent=2))
    elif args.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    elif args.cmd == "health":
        import skill_health
        report = skill_health.audit(force=args.refresh)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(health_markdown(report, args.all))
    elif args.cmd == "metrics":
        print(json.dumps(metrics(), ensure_ascii=False, indent=2))
    elif args.cmd == "discover":
        import discover
        result = discover.discover(args.task, args.project, with_github=not args.offline)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Best local match: {result['local_top_score']}/99 ({', '.join(result['local_best']) or 'none'})")
            for c in result["candidates"]:
                gh = c.get("github") or {}
                stars = f" · {gh['stars']}★" if gh.get("stars") is not None else ""
                print(f"- {c['name']} [{c['origin']}] trust {c['trust']}/100{stars} · {c['summary'][:110]}")
                print(f"    {c['install'] or c['repo']}")
    elif args.cmd == "stage":
        import discover
        staged = discover.stage_repo(args.url)
        print(f"Staging {staged['id']} ({staged['repo']}): {len(staged['skills'])} skills")
        for s in staged["skills"]:
            print(f"- {s['verdict']:9} {s['path']} · {s['description'][:90]}")
        print("Install: app.py install <id>/<path>")
    elif args.cmd == "install":
        import discover
        if "@" in args.target:
            name, market = args.target.split("@", 1)
            print(discover.install_plugin(name, market, args.scope, args.project))
        else:
            stage_id, _, path = args.target.partition("/")
            print("Installed at " + discover.install_staged(stage_id, path, args.accept_risk))
    elif args.cmd == "plugin-profile":
        import plugin_profile
        if args.undo:
            print("Re-enabled: " + (", ".join(plugin_profile.undo(args.project)["restored"]) or "none"))
        elif args.apply:
            result = plugin_profile.apply(args.project)
            print(f"Turned off in this project: {len(result['changed'])} · {result['file']}")
        else:
            print(plugin_profile.markdown(plugin_profile.plan(args.project)))
    elif args.cmd == "cleanup":
        import skill_health
        result = skill_health.cleanup(apply=args.apply)
        verb = {"quarantine": "quarantine", "block": "block"}
        items = result["done"] if args.apply else result["plan"]
        print(("Applied:" if args.apply else "Plan (use --apply to run it):") if items else "Nothing to clean up.")
        for item in items:
            print(f"- {verb[item['action']]:10} {item['invoke']}: {item['why']}")
        for item in result["failed"]:
            print(f"- FAILED     {item['invoke']}: {item['error']}")
    elif args.cmd in {"block", "unblock", "unprotect", "quarantine", "restore", "purge", "disable-plugin", "enable-plugin", "update-plugin", "uninstall-plugin", "auto-cleanup"}:
        import skill_health
        print(health_action(skill_health, {"action": args.cmd.replace("-", "_"), "target": args.target}))
    elif args.cmd == "claude-version":
        import claude_cli
        info = claude_cli.check(force=args.refresh)
        print(claude_cli.headline(info) or f"Claude Code {info['installed'] or '?'} is up to date ({info['channel']} channel)."
              + (f" {info['error']}" if info.get("error") else ""))
    elif args.cmd == "claude-update":
        import claude_cli
        print(claude_cli.update()["message"])
    elif args.cmd == "version":
        print(f"Claude Skill Router {__version__}")
    elif args.cmd == "feedback":
        record_usage(args.project, args.skill, 2 if args.vote == "up" else -2)
        print(f"Recorded: {args.skill} {'+' if args.vote == 'up' else '-'} in {Path(args.project).resolve()}")
    else:
        serve(getattr(args, "port", 9127), getattr(args, "open", False))


if __name__ == "__main__":
    main()
