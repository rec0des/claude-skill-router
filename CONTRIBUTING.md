# Contributing

Thanks for helping improve Claude Skill Router.

## Setup

Requirements: Python 3.10 or newer on Windows. There are no third-party dependencies.

```powershell
git clone https://github.com/rec0des/claude-skill-router.git
cd claude-skill-router
python -m unittest discover -s tests
```

## Pull requests

- Keep changes focused; one topic per pull request.
- Add or update tests for behaviour changes. Tests must not depend on the skills installed on your machine.
- List user-facing changes under `[Unreleased]` in `CHANGELOG.md`.
- Never commit personal paths, emails, tokens or files from `.state/`, `.claude/` or other tool caches.

## Reporting issues

Use the issue templates. For security problems, follow [SECURITY.md](SECURITY.md).
