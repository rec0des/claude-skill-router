# Changelog

All notable changes to Claude Skill Router are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-15

First public release.

### Added

- **Automatic routing.** Claude Code hooks show a startup banner, inject a minimal skill set on the first prompt of each session and skip conversational prompts.
- **Strict mode.** The PowerShell `claude` wrapper starts sessions with slash-command skills disabled and loads only the selected skill bodies. Lazy and launcher modes are available for other environments.
- **Context warnings.** The router suggests `/compact` or `/clear` at 50, 70 and 85 percent of the context window.
- **Local panel** at `http://127.0.0.1:9127`. You can select skills, compare their context cost, save per-project profiles and launch Claude with the chosen set.
- **Second opinion.** An isolated Claude Haiku call with no tools, plugins or session. Applying its picks trains the ranking for that project.
- **Skill health audit.**
  - Classifies skills as insecure, risky, obsolete, outdated or in need of review, with file and line evidence.
  - Actions are reversible: block, quarantine and restore.
  - Plugins are managed through the official `claude plugin` CLI.
- **Discovery.** Finds missing skills in the official plugin directory, configured marketplaces and GitHub, gives each a 0 to 100 trust score, and installs only after an audit and confirmation.
- **Per-project plugin profile.** Turns off plugins that are never used in a given project, with undo.
- **Claude Code version check.**
  - Compares the installed CLI with the user's update channel once a day.
  - An Update button in the panel and a notice in the startup banner run the official `claude update`.
- **`/route-skills` skill**, with the router version in its frontmatter, plus the `health`, `discover`, `plugins`, `claude`, `version` and `panel` subcommands.
- **Command-line interface:** `route`, `health`, `cleanup`, `discover`, `plugin-profile`, `claude-version`, `claude-update`, `metrics`, `status` and `version`.

### Security

- The panel listens on `127.0.0.1` only. It rejects requests with a foreign `Origin` or `Host`, and requests without its own header.
- Launch paths and quarantine targets are validated against command injection and path traversal.
- Skill code is never executed during analysis, audits or discovery.
- Private vulnerability reporting through GitHub security advisories, described in `SECURITY.md`.

[Unreleased]: https://github.com/rec0des/claude-skill-router/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/rec0des/claude-skill-router/releases/tag/v1.0.0
