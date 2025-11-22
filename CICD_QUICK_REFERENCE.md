# CI/CD Quick Reference

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    GitHub Actions CI/CD                          │
└─────────────────────────────────────────────────────────────────┘

PUSH TO main/dev                    PULL REQUEST
│                                   │
├─→ tests.yml                       ├─→ tests.yml
│   ├─ Python 3.9-3.12             │   ├─ Python 3.11
│   ├─ pytest + coverage           │   └─ Fail on errors
│   └─ Upload to Codecov           │
│                                   │
├─→ code-quality.yml               ├─→ code-quality.yml
│   ├─ Black check                 │   ├─ All checks
│   ├─ isort check                 │   └─ Fail on errors
│   ├─ Flake8 check                │
│   ├─ mypy (optional)             │
│   ├─ Bandit (security)           │
│   ├─ Safety (dependencies)       │
│   └─ Super Linter                │
│                                   │
├─→ docs.yml (if markdown changed)  │
│   ├─ Build Sphinx docs            │
│   ├─ Validate README              │
│   └─ Deploy to GitHub Pages       │
│                                   │
├─→ build-publish.yml (main/tags)   │
│   ├─ Build Python package         │
│   ├─ Build Docker image           │
│   ├─ Publish to PyPI (tags only)  │
│   └─ Create GitHub Release        │
│                                   │
└─→ deploy.yml (main/tags)
    ├─ Deploy to staging (dev)
    └─ Deploy to production (main)
```

## Quick Commands

### View Workflow Status

```bash
# Open Actions tab in GitHub
https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/actions

# Or use GitHub CLI
gh workflow list
gh run list
gh run view <run-id>
```

### Run Workflow Manually

```bash
# Using GitHub CLI
gh workflow run tests.yml -r dev
gh workflow run code-quality.yml -r dev
```

### Required Setup

1. **Secrets** (GitHub repo settings → Secrets and variables → Actions):

   - `PYPI_API_TOKEN` - PyPI API token (optional, for publishing)
   - `SLACK_WEBHOOK_URL` - Slack webhook (optional)
   - ✅ Docker: No secrets needed! Uses GitHub Token

2. **Deployment Secrets** (if deploying):
   - `STAGING_HOST`, `STAGING_USER`, `STAGING_KEY`
   - `PROD_HOST`, `PROD_USER`, `PROD_KEY`

## Workflow Triggers

### tests.yml

- ✅ Push to main/dev/develop
- ✅ Pull request to main/dev/develop
- ✅ Manual trigger

### code-quality.yml

- ✅ Push to main/dev/develop
- ✅ Pull request to main/dev/develop
- ✅ Manual trigger

### build-publish.yml

- ✅ Push to main (builds Docker)
- ✅ Tags matching v\* (publishes to PyPI + releases)
- ✅ Manual trigger

### docs.yml

- ✅ Push to main/dev
- ✅ Pull requests to main/dev
- ✅ Manual trigger

### deploy.yml

- ✅ Push to main (production)
- ✅ Tags matching v\* (production)
- ✅ Manual trigger

## Typical Workflow

### Development

```bash
# 1. Create feature branch
git checkout -b feature/my-feature

# 2. Make changes
# 3. Commit and push
git push origin feature/my-feature

# 4. Create PR
# → tests.yml runs
# → code-quality.yml runs
# → All checks must pass
```

### Release

```bash
# 1. Update version in pyproject.toml
# 2. Commit: git commit -m "chore: bump version to x.y.z"
# 3. Create tag: git tag -a vx.y.z -m "Release x.y.z"
# 4. Push: git push origin main && git push origin vx.y.z

# → build-publish.yml runs
# → Builds package
# → Publishes to PyPI
# → Creates GitHub Release
# → Builds & pushes Docker image
```

## Status Badges

Add to README.md:

```markdown
### CI/CD Status

[![Tests](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/workflows/Tests/badge.svg)](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/actions/workflows/tests.yml)
[![Code Quality](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/workflows/Code%20Quality/badge.svg)](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/actions/workflows/code-quality.yml)
[![Build & Publish](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/workflows/Build%20%26%20Publish/badge.svg)](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/actions/workflows/build-publish.yml)
[![Documentation](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/workflows/Documentation/badge.svg)](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/actions/workflows/docs.yml)
[![codecov](https://codecov.io/gh/gabrielbriones/auto-bedrock-chat-fastapi/branch/main/graph/badge.svg)](https://codecov.io/gh/gabrielbriones/auto-bedrock-chat-fastapi)
```

## Common Issues & Solutions

### Tests Fail Locally but Pass in CI

1. Check Python version: `python --version`
2. Install dependencies: `poetry install`
3. Run same command as CI: `poetry run pytest tests/ -v`

### Code Quality Failures

```bash
# Fix automatically
poetry run black auto_bedrock_chat_fastapi/ tests/
poetry run isort auto_bedrock_chat_fastapi/ tests/

# Check what remains
poetry run flake8 auto_bedrock_chat_fastapi/ tests/
```

### Docker Image Won't Build

```bash
# Test locally
docker build -t test .

# Check Dockerfile
docker build --no-cache -t test .
```

### PyPI Publish Fails

- Check `PYPI_API_TOKEN` is valid (regenerate if needed)
- Ensure version doesn't exist on PyPI
- Check `pyproject.toml` version matches tag

## File Structure

```
.github/workflows/
├── tests.yml                 # Unit tests & coverage
├── code-quality.yml          # Linting & security
├── build-publish.yml         # Build & publish packages
├── docs.yml                  # Documentation build
├── deploy.yml               # Deployment (staging/prod)
└── super-linter.yml         # Comprehensive linting
```

## Learn More

- 📖 Full Documentation: See `CICD_WORKFLOWS.md`
- 📖 GitHub Actions Docs: https://docs.github.com/en/actions
- 🔗 View Workflows: `.github/workflows/`
- 📊 Check Status: Actions tab on GitHub
