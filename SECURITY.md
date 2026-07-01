# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `1.0.0` | Yes — current Zenodo / GitHub release |
| `0.1.0-rc1` | No — superseded by `v1.0.0` |
| `< 0.1.0-rc1` | Not supported |

## Reporting a vulnerability

**Do not** open public GitHub issues for security-sensitive reports.

Preferred: [GitHub Security Advisories](https://github.com/cesar-andress/earnbench/security/advisories/new)
(private report).

Include:

- Description and impact
- Steps to reproduce
- Affected commands or modules
- Suggested fix (optional)

Reports are acknowledged within **7 business days**.

## Known risk surfaces

EarnBench executes user-supplied shell commands in Phase C agent drivers
(`external_cli` with `command_template` in YAML arms files). Treat agent arm
configs as trusted input only from maintainers.

Docker batch grading runs third-party SWE-bench harness containers with elevated
filesystem and network access relative to the core EF library.

## Disclosure policy

Fixes are coordinated on supported release branches before public disclosure when
practical.
