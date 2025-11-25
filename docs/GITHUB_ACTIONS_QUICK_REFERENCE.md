# GitHub Actions - Super Linter Quick Reference

## Files Created/Modified

### New Workflow

- **`.github/workflows/super-linter.yml`** - Main GitHub Actions workflow

### Configuration Files

- **`.markdownlint.json`** - Markdown linting rules
- **`.flake8`** - Python flake8 configuration
- **`.hadolintignore`** - Docker linting exceptions

### Documentation

- **`GITHUB_ACTIONS.md`** - Comprehensive workflow documentation

### Modified

- **`.gitignore`** - Added linting report directories

## Quick Start

### 1. Push to GitHub

```bash
git add .github/workflows/super-linter.yml .markdownlint.json .flake8 .hadolintignore GITHUB_ACTIONS.md
git commit -m "feat: add GitHub Actions Super Linter workflow"
git push origin dev
```

### 2. Create a Pull Request

- Create a PR to `main` or `dev`
- The workflow will automatically run
- Check the "Checks" tab for results

### 3. View Results

- **PR Checks**: See results directly on PR
- **Actions Tab**: View full logs and artifacts
- **Artifacts**: Download reports from workflow run

## What Gets Linted

### Python

✅ Black, Flake8, isort, mypy

### Markdown & Docs

✅ Markdown, HTML, CSS, JSON, YAML

### Infrastructure

✅ Dockerfile (Hadolint), Bash scripts

## Running Locally

```bash
# Using provided script
./run_super_linter.sh

# Or manually with Docker
docker run --rm \
  -e RUN_LOCAL=true \
  -v "$(pwd)":/tmp/lint \
  github/super-linter:latest
```

## Auto-Fix Issues

```bash
# Format Python code
black auto_bedrock_chat_fastapi/

# Sort imports
isort auto_bedrock_chat_fastapi/

# Format markdown/JSON/YAML (requires prettier)
npx prettier --write "**/*.{md,json,yaml,yml}"
```

## Workflow Triggers

- ✅ Push to `main`, `dev`, `develop` branches
- ✅ Pull requests to `main`, `dev`, `develop` branches
- ✅ Manual trigger via GitHub Actions UI

## Configuration

All linters are pre-configured with sensible defaults:

| Linter   | Config File          | Status     |
| -------- | -------------------- | ---------- |
| Black    | `pyproject.toml`     | ✅ Enabled |
| Flake8   | `.flake8`            | ✅ Enabled |
| isort    | `pyproject.toml`     | ✅ Enabled |
| mypy     | `pyproject.toml`     | ✅ Enabled |
| Markdown | `.markdownlint.json` | ✅ Enabled |
| Hadolint | `.hadolintignore`    | ✅ Enabled |

## Disable a Linter (if needed)

Edit `.github/workflows/super-linter.yml` and set:

```yaml
VALIDATE_PYTHON_BLACK: false # Example: disable Black
```

## See Also

- 📖 [Full Documentation](GITHUB_ACTIONS.md)
- 🔗 [Super Linter GitHub](https://github.com/super-linter/super-linter)
- 📋 [View Workflow Runs](../../actions)
