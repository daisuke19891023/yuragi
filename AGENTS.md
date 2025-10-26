# AGENTS.md (Python, for Codex-like agents)

Authoritative guide for code-writing agents working on this Python repository. Targets Python 3.13+ with uv, Pydantic v2, Nox, Pytest, Ruff, Pyright, and Git.

## 1. Ground Rules (non-negotiable)
- **Dependency management:** use `uv` only (pip and Poetry are prohibited).
- **Quality gate on every edit:** after each file creation or change, run `uv run nox -s lint` and `uv run nox -s typing`.
- **Tests layout:** mirror `src/` structure; files named `test_*.py`; shared fixtures live in `tests/conftest.py`.
- **Commits:** Conventional Commits only. Never claim work was done by “codex”, “claude code”, “cursor”, etc. Never include user names/emails or `Co-authored-by:` lines.
- **No warning suppression:** avoid `# type: ignore`, `# noqa`, and similar markers.

## 2. Project Structure (contract)
```
project_root/
├─ src/<package>/...
├─ tests/
│  ├─ unit/            # mirrors src/*
│  ├─ integration/     # optional
│  └─ conftest.py      # common fixtures live here
├─ docs/
│  ├─ reference/       # specs, public-facing behavior
│  └─ adr/             # design decisions (Architecture Decision Records)
├─ README.md
├─ .env.example
└─ (configs like pyproject.toml, noxfile.py, pyrightconfig.json exist in repo)
```
Configuration contents (Ruff/Pyright/Nox) live in files; do not duplicate them here.

## 3. Workflow (tight loop)
1. **Explore:** read `src/`, `tests/`, and configs. No edits.
2. **Plan:** write a brief implementation plan (what/why, touched files, acceptance criteria).
3. **Implement (TDD-first):**
   - Write or adjust tests (start with unit tests, then integration if helpful).
   - Implement in small patches.
   - After every edit, run `uv run nox -s lint`, then `uv run nox -s typing`, followed by the relevant `uv run nox -s test` sessions.
4. **Commit:** only after all required `uv run nox` sessions pass.

**Suggested plan template (minimal):**
- Goal:
- Scope (files):
- Acceptance tests (unit/integration):
- Risks/decisions (record in `docs/adr/`):

## 4. Testing Policy
- Default to TDD with unit tests first. Integration tests only when they add clear value. E2E is optional.
- For LLM-based features, tests must be deterministic via mocks only. Do not rely on live model calls or flaky E2E flows.
- Mock I/O, network, time, and RNG; assert prompts/handlers/transformers, not external responses.
- Prefer Pytest with `unittest.mock`, `pytest-mock`, or `monkeypatch`.
- Keep coverage at or above the project threshold (see CI config).
- Mirror the `src/` structure in `tests/`, name files `test_*.py`, and centralize fixtures in `tests/conftest.py`.

## 5. Coding Standards
- Use modern Python typing syntax (`list[str]`, `X | None`), precise types, and avoid `Any`.
- Prefer Pydantic (v2) models and types over tuple-based data structures. Avoid using raw tuple
  types for domain data; explicitly model structured data with `pydantic.BaseModel` for validation
  and clarity.
- Keep functions small and single-purpose. Adhere to SOLID principles; split large responsibilities
  into focused functions to maximize readability and maintainability.
- **Settings & environment variables:**
  - Provide a `settings.py` using `pydantic-settings.BaseSettings` to load from `.env`.
  - Do not read environment variables via `os` in application code. Always inject via `Settings`.
  - Keep `.env.example` current with required keys and safe defaults.
- Favor the standard library; avoid adding dependencies casually.
- Remove temporary debug code before committing.
- Use clear, typed exceptions; never allow silent failures.

## 6. Dependency & Task Routines
- `uv`: `uv add`, `uv add --dev`, `uv sync`, `uv run <cmd>`.
- **Nox sessions** (defined in `noxfile.py`):
  - `uv run nox -s lint` – Ruff check/format check.
  - `uv run nox -s sort` – Ruff import sorting.
  - `uv run nox -s format_code` – Ruff formatting.
  - `uv run nox -s typing` – Pyright strict type checking.
  - `uv run nox -s test` – Pytest with coverage (skips when `src/` has no Python files).
  - `uv run nox -s ci` – Full CI bundle (lint, sort, format, typing, test).
  - `uv run nox -s lock` – Generate dependency constraint locks.
  - `uv run nox -s all_checks` – Run all quality checks.
- Run `uv run nox -s lock` before other sessions if the `constraints/` directory is missing or out of date.
- **Mandatory habit:** after every edit, run `uv run nox -s lint` and `uv run nox -s typing` to keep the branch green.

## 7. Documentation & Housekeeping
After implementation is complete (part of Done):
- Update `README.md` (usage, setup, examples).
- Update `.env.example` (all new/changed settings).
- Update `docs/`:
  - `docs/adr/`: record key design decisions (context, options, decision, consequences).
  - `docs/reference/`: maintain user/developer-facing specs, public APIs, CLI details, data contracts.
- Keep documentation concise; link to code where appropriate.

## 8. Git Hygiene
- Use Conventional Commits, e.g.:
  - `feat(parser): add CSV quote handling`
  - `fix(cli): normalize Windows newlines`
  - `test(config): add unit tests for Settings`
- Forbidden in commit messages: mentions of code-writing tools (codex/claude/cursor/etc.), real names, emails, or `Co-authored-by:` lines.

## 9. Definition of Done (checklist)
- [ ] Every recent edit passed `uv run nox -s lint` and `uv run nox -s typing`.
- [ ] Tests are green (unit and, if used, integration) and meet coverage requirements.
- [ ] LLM-related tests use mocks only; no flaky calls.
- [ ] Settings load via `settings.py` (`pydantic-settings`); no direct `os.environ` reads in app code.
- [ ] `README.md`, `.env.example`, and relevant `docs/` sections are updated.
- [ ] Commits follow Conventional Commits and omit tool claims and personal data.

## 10. Quick Commands
```bash
# Install / sync
uv sync

# After every edit (MANDATORY)
uv run nox -s lint
uv run nox -s typing

# Formatting and imports
uv run nox -s format_code
uv run nox -s sort

# Tests
uv run nox -s test

# Full CI (when needed)
uv run nox -s ci
```

Follow this document strictly to keep changes small, deterministic, and continuously green.
