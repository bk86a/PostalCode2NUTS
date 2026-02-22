# Contributing to PostalCode2NUTS

Thank you for your interest in contributing to PostalCode2NUTS! This guide explains how to get involved.

## How to Contribute

1. **Open an issue first** — before starting any work, please [open an issue](https://github.com/bk86a/PostalCode2NUTS/issues) describing the bug, feature, or improvement you'd like to work on. This helps avoid duplicate effort and ensures alignment with the project's direction.
2. **Fork the repository** and create a new branch from `main`.
3. **Make your changes** and ensure they follow the guidelines below.
4. **Submit a pull request** referencing the related issue.

## Types of Contributions

- **Bug reports** — open an issue with steps to reproduce, expected vs actual behavior, and your environment details.
- **Bug fixes** — submit a PR with a clear description of the fix and a reference to the issue.
- **New country data** — adding or improving postal code data for countries. See the note on postal patterns below.
- **Documentation** — README improvements, API usage examples, or clarifications.

## Postal Patterns

The file `postal_patterns.json` contains curated per-country regex patterns and is maintained by the project owner. If you'd like to propose changes to postal patterns, please open an issue describing the problem and suggested fix — do not submit PRs that modify `postal_patterns.json` without prior approval.

## Development Setup

See the [README](README.md) for instructions on installing dependencies, running the app locally, and using Docker.

## Code Style and Commits

- **Linting**: Run `ruff check app/ scripts/` before submitting. The project uses ruff with E/F/W rules and a line length of 110.
- **Commit messages**: Use [Conventional Commits](https://www.conventionalcommits.org/) format:
  - `feat:` for new features
  - `fix:` for bug fixes
  - `docs:` for documentation changes
  - `refactor:` for code restructuring
  - `chore:` for maintenance tasks

## Pull Request Process

1. Reference the related issue in your PR description (e.g., "Closes #12").
2. Describe what your changes do and why.
3. All CI checks (lint, import-check, security audit, Docker build) must pass before your PR will be reviewed.
4. Keep PRs focused — one issue per PR where possible.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.
