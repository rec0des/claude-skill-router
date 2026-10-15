---
name: route-skills
version: {{VERSION}}
description: Local Skill Router. Use at the start of a new project or task, when the scope changes, or when the user asks which skills or plugins fit the project. Picks the minimal skill set, reviews the router's candidates, records feedback, reports context usage and when to /compact or /clear, audits skill health, finds new skills, or opens the router panel.
---

# Route Skills

Skill Router {{VERSION}}. Source: `{{ROOT}}`. Edit `skill/route-skills/SKILL.md` there and run `install_integration.py` to reinstall it.

The hooks already handle the automatic part: a banner at startup, a skill manifest on the first prompt and context warnings at 50/70/85%. This skill is the manual review.

`PY` = `"{{PYTHON}}"`
`APP` = `"{{ROOT}}\app.py"`

## Procedure

`$ARGUMENTS` is the task. If it is empty, ask in one line what is going to be built.

1. Run `PY APP route --project . --task "<task>"`.
2. Review the selection yourself. The ranking is lexical and has two known failure modes:
   - generically named skills (`replica-*`, `*-build`, `*-deploy`) get in through single shared words without fitting the kind of project;
   - plugin skills land in Optional because of short descriptions.
   Swap the ones that do not fit for optional ones that do. At most 4. Prefer the specific skill over the generic one.
3. Answer in at most 6 lines: the kind of project, the final skills with one reason each, and the swaps you made.
4. Record the swaps so the ranking learns:
   - `PY APP feedback --project . --skill <added> --vote up`
   - `PY APP feedback --project . --skill <removed> --vote down`
5. Invoke each final skill with the Skill tool only when the work reaches its area.

Do not call `app.py consult` from inside this session: you already are Claude. `consult` exists for the panel and for Codex.

## Subcommands

- `status`: `PY APP status`. Summarize skills by source, the classifier state and the last hook run (`last_hook`: mode, skills, injected characters, context %).
- `panel`: run `PY APP serve --open` in the background. URL `http://127.0.0.1:9127`. "Ask Claude" gets a second opinion from an isolated Haiku and "Apply picks" trains the ranking.
- `health`: `PY APP health` (add `--refresh` to re-audit, `--all` to include outdated plugin skills). Lists insecure, risky, obsolete, outdated and to-review skills with file:line and evidence. Then:
  - read the evidence before concluding; official installers that download and run a script, and flags mentioned in documentation, are risks to confirm, not malware;
  - propose each action and wait for confirmation: `PY APP block <skill>` (the router stops picking it), `PY APP quarantine <skill>` (local skills only; moved to `.quarantine/`, restorable with `PY APP restore <id>`), `PY APP disable-plugin <plugin@marketplace>`, `PY APP update-plugin <plugin@marketplace>`;
  - `PY APP cleanup` shows the cleanup plan (obsolete and insecure skills); `--apply` runs it. Local skills go to quarantine, plugin skills are blocked. Restoring or unblocking protects a skill from future cleanups;
  - permanent deletion only from the quarantine: `PY APP purge <id>`, with the user's explicit confirmation for each entry. For plugins: `PY APP uninstall-plugin <plugin@marketplace>`;
  - never delete skill files directly with the shell;
  - automatic cleanup: `PY APP auto-cleanup on|off` (runs during the daily audit);
  - skills with a critical finding (prompt injection, credential exfiltration, bidi Unicode, official blocklist) are already excluded from the router.
- `discover`: when no installed skill fits (the hook warns with `◇`), run `PY APP discover --task "<task>"`. Lists plugins from the official directory and GitHub repositories with a 0–100 trust score (Anthropic review, stars, activity, license, archived). Recommend at most 2, preferring reviewed ones with trust ≥ 60. To install:
  - marketplace plugin: `PY APP install <plugin>@<marketplace> --project .` (project scope, not global);
  - GitHub repository: `PY APP stage <url>` downloads it to staging and audits it; then `PY APP install <id>/<path>`. Critical findings are refused; high risk needs `--accept-risk` and the user's confirmation;
  - never install anything until the user picks the specific item.
- `plugins`: `PY APP plugin-profile --project .` shows plugins never used in this project and the tokens they cost per session. `--apply` turns them off in this project only (`.claude/settings.local.json`), `--undo` restores them. Plugins with hooks are never turned off.
- `claude`: `PY APP claude-version --refresh` compares the installed Claude Code with the latest release on the user's update channel (`autoUpdatesChannel`, `latest` by default). When the startup banner shows `↑ Claude Code … available`, recommend the update once; on confirmation run `PY APP claude-update` (the official `claude update`) and tell the user to restart open sessions. Never update without confirmation. The panel has an Update button for the same thing.
- `version`: `PY APP version` prints the Skill Router version.
- `metrics`: `PY APP metrics` shows accuracy (selected skills that were used), coverage, injected tokens and hook latency.
- `context`: read the percentage from the latest warning and give the action:
  - below 50%: keep going;
  - 50 to 70%: `/clear` when switching tasks;
  - 70 to 85%: `/compact <what to keep>` at the end of the subtask;
  - above 85%: `/compact` now, or save a summary and `/clear`.

## Configuration

`config.json` in the router folder: `max_skills`, `include_plugins`, `pinned`, `blocked`, `protected`, `keep_plugins`, `auto_cleanup`, `context_limit`.

`SKILL_ROUTER_MODE`: `lazy` (default, injects names), `strict` (`claude` in PowerShell, with skills disabled, injects bodies), `launcher` (opened from the panel, monitoring only).
