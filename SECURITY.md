# Security Policy

## Supported Versions

Only the latest release of PostalCode2NUTS receives security updates.

| Version | Supported |
| ------- | --------- |
| Latest  | Yes       |
| Older   | No        |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it through [GitHub's private vulnerability reporting](https://github.com/bk86a/PostalCode2NUTS/security/advisories/new). **Do not open a public issue for security vulnerabilities.**

Please include:

- A description of the vulnerability
- Steps to reproduce
- Potential impact

Reports will be acknowledged on a best-effort basis. Once a fix is developed and released, the vulnerability will be disclosed publicly through a GitHub security advisory.

## Security Measures

This project runs automated security checks on every push via CI:

- **pip-audit** — scans dependencies for known vulnerabilities
- **Bandit** — static analysis for common security issues in Python code
