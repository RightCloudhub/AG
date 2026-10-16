# Repository Guidelines

## Project Structure & Module Organization

The Python 3.12+ package lives in `src/agentic_graphrag/`; unit tests live in `tests/unit/`. Keep tests grouped by the package area they exercise. `web/` contains the no-build Vue UI, `configs/` holds configuration, schemas, and prompts, and `data/` plus `evals/` contain corpora and evaluation fixtures. Project architecture and operating details are documented in `CLAUDE.md`, `README.md`, and `docs/`.

Preserve the deterministic, offline-default path when changing stores or LLM integrations. Live services are opt-in; see `CLAUDE.md` before changing those boundaries.

## Build, Test, and Development Commands

- `uv pip install -e ".[dev]"` installs the package and development tools into the active environment.
- `ruff check src tests scripts` runs lint checks; `ruff format --check src tests scripts` checks formatting.
- `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q` runs the unit suite and coverage gate. For a focused loop, use `pytest tests/unit/test_scoring.py -q`.
- `python scripts/check_code_metrics.py` checks repository code-size and complexity limits.
- `agr-api` starts the local API and UI at `/web`; the README documents offline queries and optional service setup.

## Coding Style & Naming Conventions

Use four-space Python indentation, a 100-character line limit, and the configured Ruff rules (`E`, `F`, `I`, `UP`, `B`). Use `snake_case` for modules and functions and `PascalCase` for classes. Keep code and comments in English. Follow nearby patterns and keep configuration and prompts in `configs/` rather than embedding them in application code.

## Testing Guidelines

Use pytest; name test modules `test_*.py` and keep them under `tests/unit/`. Add or update focused tests with behavior changes. Prefer offline fixtures so tests remain deterministic and do not require API keys, Docker, or live services. Maintain the 80% coverage gate.

## Commit & Pull Request Guidelines

Recent commits use short type prefixes such as `feat:`, `fix:`, `docs:`, and `chore:`. Keep each commit focused and describe the change after the prefix. PRs should summarize behavior changes, list validation commands and results, and link related issues when available. Include screenshots for UI changes and note configuration or migration impacts.

## Security & Configuration

Keep credentials in local environment configuration; never commit secrets. Offline development does not need API keys. Before changes affecting security or data handling, consult `plan/engineering/rules.md` and relevant operations documentation.
