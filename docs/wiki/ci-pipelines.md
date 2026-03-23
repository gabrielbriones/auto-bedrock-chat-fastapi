# CI Pipelines

The project uses GitHub Actions for continuous integration. All workflows are defined in `.github/workflows/`.

---

## Workflows Overview

| Workflow        | File                | Trigger                       | Purpose                               |
| --------------- | ------------------- | ----------------------------- | ------------------------------------- |
| Tests           | `tests.yml`         | Push/PR to main, dev, develop | Run unit tests across Python versions |
| Code Quality    | `code-quality.yml`  | Push/PR to main, dev, develop | Linting, formatting, security         |
| Build & Publish | `build-publish.yml` | Push to main, tags            | Build package and publish             |

---

## Tests Workflow (`tests.yml`)

Runs automated tests across the supported Python version matrix.

**Triggers:** Push or PR to `main`, `dev`, `develop` branches (also manual via `workflow_dispatch`)

**Matrix:** Python 3.9, 3.10, 3.11, 3.12

**What it does:**

- Installs Poetry and dependencies
- Runs `pytest tests/` with coverage reporting
- Uploads coverage XML and badge artifacts
- Reports test results to GitHub Checks

```yaml
strategy:
  matrix:
    python-version: ["3.9", "3.10", "3.11", "3.12"]
```

**Run tests locally:**

```bash
# All unit tests
poetry run pytest tests/

# With coverage
poetry run pytest tests/ --cov=auto_bedrock_chat_fastapi --cov-report=xml

# Specific file
poetry run pytest tests/test_authentication.py -v

# Watch mode
ptw tests/
```

**Test suite stats:** ~204 unit tests, all dependencies mocked, runs in < 5 seconds.

---

## Code Quality Workflow (`code-quality.yml`)

Enforces code standards and performs security analysis.

**Triggers:** Push or PR to `main`, `dev`, `develop` branches

### Lint Job

| Tool   | Command                              | Purpose                      |
| ------ | ------------------------------------ | ---------------------------- |
| Black  | `black --check --diff`               | Code formatting              |
| isort  | `isort --check-only --profile black` | Import sorting               |
| Flake8 | `flake8 --max-line-length=120`       | Style guide                  |
| mypy   | `mypy auto_bedrock_chat_fastapi/`    | Type checking (non-blocking) |

**Run locally:**

```bash
poetry run black --check auto_bedrock_chat_fastapi/ tests/ examples/
poetry run isort --check-only --profile black auto_bedrock_chat_fastapi/ tests/
poetry run flake8 auto_bedrock_chat_fastapi/ tests/ --max-line-length=120
poetry run mypy auto_bedrock_chat_fastapi/ --ignore-missing-imports
```

**Auto-fix formatting:**

```bash
poetry run black auto_bedrock_chat_fastapi/ tests/ examples/
poetry run isort --profile black auto_bedrock_chat_fastapi/ tests/
```

### Security Job

| Tool   | Purpose                                       |
| ------ | --------------------------------------------- |
| Bandit | Scans Python code for security issues         |
| Safety | Checks dependencies for known vulnerabilities |

Reports are uploaded as artifacts (`bandit-report.json`, `safety-report.json`, 30-day retention).

---

## Pre-Commit Hooks

Install pre-commit to run linters automatically before each `git commit`:

```bash
pip install pre-commit
pre-commit install
```

**Hooks configured:**

- Black, isort, Flake8 — format and lint Python
- Markdown linting
- YAML, JSON validation
- Dockerfile linting (Hadolint, requires Docker)

**Manual run on all files:**

```bash
pre-commit run --all-files
```

**Skip hooks for a single commit:**

```bash
git commit -m "WIP" --no-verify
```

---

## Integration Tests

Integration tests require real AWS credentials and run against the actual Bedrock API:

```bash
# Set up AWS credentials in .env first
cp .env.example .env
# Edit .env with AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

# Individual suites
python integration_testing/test_rag_semantic_search.py
python integration_testing/test_rag_quality.py

# All (skip WebSocket)
python integration_testing/run_all.py --skip-chat

# All including WebSocket (requires running server)
# Terminal 1:
uvicorn auto_bedrock_chat_fastapi.app:app --port 8001
# Terminal 2:
python integration_testing/run_all.py
```

**Stats:** ~23 integration tests, runs in 2-3 minutes.

---

## Build & Publish Workflow (`build-publish.yml`)

Builds the Python package and optionally publishes it. See [CD Pipelines](cd-pipelines.md).

---

## See Also

- [CD Pipelines](cd-pipelines.md) — deployment workflows
- `.github/workflows/` — workflow source files
- `tests/` — unit test source
- `integration_testing/` — integration test source
