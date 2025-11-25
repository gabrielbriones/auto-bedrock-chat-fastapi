# CI/CD Workflows Documentation

## Overview

This project uses GitHub Actions to automate testing, code quality checks, building, and deployment. All workflows are defined in `.github/workflows/`.

## Workflows

### 1. Tests (`tests.yml`)

Runs automated tests across multiple Python versions.

**Triggers:**

- Push to `main`, `dev`, `develop` branches
- Pull requests to `main`, `dev`, `develop` branches
- Manual trigger (`workflow_dispatch`)

**What it does:**

- ✅ Tests on Python 3.9, 3.10, 3.11, 3.12
- ✅ Runs pytest with coverage reporting
- ✅ Uploads coverage to Codecov
- ✅ Generates coverage badge
- ✅ Uploads test artifacts

**Configuration:**

```yaml
Strategy matrix:
  - Python 3.9, 3.10, 3.11, 3.12

Coverage tools:
  - pytest
  - pytest-cov
  - codecov-action
```

**Artifacts:**

- `coverage.xml` - Coverage report
- `coverage.svg` - Coverage badge

### 2. Code Quality (`code-quality.yml`)

Enforces code standards and security checks.

**Triggers:**

- Push to `main`, `dev`, `develop` branches
- Pull requests to `main`, `dev`, `develop` branches
- Manual trigger

**What it does:**

- ✅ Black formatter check
- ✅ isort import sorting check
- ✅ Flake8 style guide enforcement
- ✅ mypy type checking (optional)
- ✅ Bandit security analysis
- ✅ Safety dependency vulnerability check
- ✅ Super Linter comprehensive checks

**Jobs:**

#### Lint

Checks Python code formatting:

- `black --check` - Code format
- `isort --check-only` - Import sorting
- `flake8` - Style guide
- `mypy` - Type hints (optional)

#### Security

Analyzes code for security issues:

- **Bandit** - Security issues in code
- **Safety** - Vulnerable dependencies

#### Super Linter

Comprehensive linting:

- Python (Black, Flake8, isort)
- Markdown, HTML, CSS, JSON, YAML
- Dockerfile (Hadolint)
- Bash (Shellcheck)

### 3. Build & Publish (`build-publish.yml`)

Builds, packages, and publishes releases.

**Triggers:**

- Push to `main` branch
- Push to tags (v\*)
- Manual trigger

**What it does:**

- ✅ Builds Python package with Poetry
- ✅ Builds and pushes Docker image
- ✅ Publishes to PyPI (on version tags)
- ✅ Creates GitHub Releases

**Jobs:**

#### Build

Builds Python distribution package:

- Uses Poetry to build wheel and source dist
- Verifies package can be installed
- Uploads to artifacts

#### Docker

Builds and publishes Docker image to GHCR:

- Only runs on `main` branch or tags
- Uses GitHub Token (no extra secrets needed!)
- Uses GitHub Actions cache for speed
- Tags: branch name, semver, git SHA
- Registry: `ghcr.io/gabrielbriones/auto-bedrock-chat-fastapi`

#### Publish PyPI

Publishes to PyPI (only on version tags):

- Requires `PYPI_API_TOKEN` secret
- Automatically creates release notes

#### Publish Release

Creates GitHub Release:

- Attaches built artifacts
- Generates automatic release notes
- Triggered on version tags

### 4. Documentation (`docs.yml`)

Builds and deploys documentation.

**Triggers:**

- Push to `main`, `dev` branches
- Pull requests to `main`, `dev` branches
- Manual trigger

**What it does:**

- ✅ Builds Sphinx documentation
- ✅ Validates README and Markdown
- ✅ Deploys to GitHub Pages (main only)

**Jobs:**

#### Docs

Builds documentation:

- Uses Sphinx with RTD theme
- Generates HTML documentation
- Uploads artifacts
- Deploys to GitHub Pages on main

#### README Validation

Validates Markdown files:

- README.md
- GITHUB_ACTIONS.md
- PRE_COMMIT_SETUP.md

### 5. Deploy (`deploy.yml`)

Deploys to staging and production environments.

**Triggers:**

- Push to `main` branch (production)
- Push to `dev`/`develop` branches (staging)
- Manual trigger
- Version tags (v\*)

**What it does:**

- ✅ Builds Docker image
- ✅ Deploys to staging (on dev branches)
- ✅ Deploys to production (on main with tags)
- ✅ Creates deployment records
- ✅ Notifies team via Slack

**Jobs:**

#### Deploy Staging

Deploys to staging environment:

- Environment: `staging`
- Triggered on `dev`/`develop` pushes
- Requires staging secrets

#### Deploy Production

Deploys to production environment:

- Environment: `production`
- Triggered on `main` with version tags
- Requires production secrets
- Requires passing tests and code quality

#### Notify

Sends Slack notifications:

- Triggered after deployment
- Requires `SLACK_WEBHOOK_URL` secret

### 6. Super Linter (`super-linter.yml`)

Original comprehensive linting workflow (reference).

**Status:** Optional - Code Quality workflow provides similar functionality

## Environment Setup

### Required Secrets

Configure these in GitHub repository settings:

#### For Docker Publishing

- No secrets needed! Uses GitHub Token automatically
- Image stored in GitHub Container Registry (GHCR)
- Access: `ghcr.io/gabrielbriones/auto-bedrock-chat-fastapi`

#### For PyPI Publishing

- `PYPI_API_TOKEN` - PyPI API token

#### For Deployment

- `STAGING_HOST` - Staging server hostname
- `STAGING_USER` - Staging SSH user
- `STAGING_KEY` - Staging SSH private key
- `PROD_HOST` - Production server hostname
- `PROD_USER` - Production SSH user
- `PROD_KEY` - Production SSH private key

#### For Notifications

- `SLACK_WEBHOOK_URL` - Slack incoming webhook

### Required Variables

Configure in GitHub repository variables:

```yaml
SLACK_WEBHOOK_URL: # Optional, for Slack notifications
```

## Workflow Statuses

### Check Status on GitHub

1. **On Pull Request:**

   - Checks appear as status checks
   - Must pass before merge (if required)
   - Click details to see logs

2. **On Push:**

   - View in Actions tab
   - Check workflow runs list
   - Click run to see details

3. **On Tags:**
   - Build/publish workflows trigger
   - PyPI and GitHub releases created
   - Docker images pushed

## Troubleshooting

### Tests Failing

1. Check Python version compatibility
2. Review test logs in workflow
3. Run locally: `poetry run pytest tests/ -v`

### Code Quality Failures

1. Run formatters locally:
   ```bash
   poetry run black auto_bedrock_chat_fastapi/ tests/ examples/
   poetry run isort auto_bedrock_chat_fastapi/ tests/ examples/
   ```
2. Fix remaining issues manually
3. Commit and re-run workflow

### Docker Build Failures

1. Check Dockerfile syntax
2. Ensure all dependencies in pyproject.toml
3. Test locally: `docker build -t test .`

### PyPI Publish Failures

1. Verify `PYPI_API_TOKEN` is valid
2. Check version in `pyproject.toml`
3. Ensure version doesn't already exist
4. Review PyPI project settings

### Deployment Failures

1. Check deployment secrets are configured
2. Verify target environment is accessible
3. Check deployment logs in workflow
4. Ensure Docker image built successfully

## Performance Tips

### Reduce Build Time

1. **Use caching:**

   - Poetry dependency cache
   - Docker layer caching
   - GitHub Actions cache

2. **Matrix strategies:**

   - Only test on essential Python versions
   - Run jobs in parallel

3. **Skip unnecessary checks:**
   - Use `if: github.ref == 'refs/heads/main'` to skip on dev
   - Skip deployment on every commit

### Example: Development Branch (Faster)

```yaml
on:
  push:
    branches:
      - dev
  pull_request:
    branches:
      - dev

jobs:
  test:
    strategy:
      matrix:
        python-version: ["3.11"] # Single version
  # Skip Docker/PyPI publishing
```

## Customization

### Adding Jobs

1. Edit workflow file in `.github/workflows/`
2. Add job definition
3. Configure triggers and conditions
4. Test on feature branch

### Modifying Triggers

Edit `on:` section:

```yaml
on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main]
  schedule:
    - cron: "0 0 * * 0" # Weekly
  workflow_dispatch: # Manual trigger
```

### Conditional Execution

Use `if:` to run jobs conditionally:

```yaml
if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')
```

## Best Practices

1. **Fail Fast:** Run quick checks first (lint, format)
2. **Parallel Execution:** Run independent jobs in parallel
3. **Caching:** Cache dependencies to speed up builds
4. **Notifications:** Set up Slack/email alerts for failures
5. **Secrets:** Never commit secrets, use GitHub Secrets
6. **Documentation:** Keep workflows documented and discoverable
7. **Testing:** Test workflows on branches before main
8. **Artifacts:** Clean up old artifacts to save storage
9. **Timeouts:** Set reasonable timeouts to catch hung jobs
10. **Version Matrix:** Test on supported Python versions

## References

- 📖 [GitHub Actions Documentation](https://docs.github.com/en/actions)
- 📖 [Workflow Syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- 📖 [Poetry Documentation](https://python-poetry.org/docs/)
- 🔗 [Action Marketplace](https://github.com/marketplace?type=actions)
