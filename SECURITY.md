# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities privately through [GitHub security advisories](https://github.com/rec0des/claude-skill-router/security/advisories/new). Do not open a public issue.

Include the router version (`python app.py version`), the steps to reproduce and the impact. You will get a reply within 7 days.

## Scope

The router reads skill files, runs a local HTTP panel on `127.0.0.1` and calls the `claude` and `gh` CLIs. Of particular interest:

- requests to the panel API that bypass its Origin, Host or header checks;
- command injection through project paths, skill names or launch arguments;
- path traversal in quarantine, restore or purge actions;
- skill content that the health audit should flag as insecure but does not.
