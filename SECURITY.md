# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `0.1.0-rc1` | Pre-release; security fixes on best effort |
| `0.1.0` (tag pending) | Supported after public release |
| `< 0.1.0` | Not supported |

## Reporting a vulnerability

**Do not** open public GitHub issues for security-sensitive reports.

Email the maintainers (replace with project contact before public launch) with:

- Description and impact
- Steps to reproduce
- Affected commands or modules
- Suggested fix (optional)

We aim to acknowledge reports within **7 business days**.

## Known risk surfaces

EarnBench executes user-supplied shell commands in Phase C agent drivers
(`external_cli` with `command_template` in YAML arms files). Treat agent arm
configs as **trusted input** only.

Docker-based SWE-bench grading runs third-party harness code inside containers.
Review Docker socket access and network policies in shared environments.

## Out of scope

- Vulnerabilities in upstream SWE-bench harnesses or base images
- Misconfiguration of API keys in user-provided agent YAML (documented user responsibility)
