# Pre-Commit Configuration Guide

## Overview

This project uses [pre-commit](https://pre-commit.com/) to automatically run linters and formatters before each commit. This ensures code quality and prevents pushing issues to GitHub.

## Installation

### 1. Install pre-commit

```bash
# Using pip
pip install pre-commit

# Or using Homebrew (macOS)
brew install pre-commit
```

### 2. Install the Git hooks

From the repository root:

```bash
pre-commit install
```

This creates `.git/hooks/pre-commit` and sets up the hooks to run automatically.

### 3. (Optional) Install Docker

The Super Linter hook requires Docker:

```bash
# macOS
brew install docker

# Ubuntu/Debian
sudo apt-get install docker.io

# Or install Docker Desktop
# https://www.docker.com/products/docker-desktop
```

## Usage

### Automatic (Default)

Once installed, pre-commit hooks run automatically on `git commit`:

```bash
git add your_changes.py
git commit -m "Your message"
# Hooks run automatically here
```

If any checks fail, commit is blocked until issues are fixed.

### Manual Runs

Run all hooks on all files:

```bash
pre-commit run --all-files
```

Run specific hook:

```bash
pre-commit run black --all-files
pre-commit run flake8 --all-files
```

Update hook versions:

```bash
pre-commit autoupdate
```

Skip hooks for a specific commit (not recommended):

```bash
git commit --no-verify
```

## Hooks Included

### Python

| Hook | Purpose | Config |
|------|---------|--------|
| **Black** | Code formatter | Line length: 120 |
| **isort** | Import sorting | Black profile |
| **Flake8** | Style guide | Max line: 120 |

### Markup & Config

| Hook | Purpose |
|------|---------|
| **Markdownlint** | Markdown formatting |
| **Prettier** | Format Markdown, JSON, YAML |
| **yamllint** | YAML validation |
| **check-json** | JSON validation |

### Infrastructure

| Hook | Purpose |
|------|---------|
| **Hadolint** | Docker linting |
| **Shellcheck** | Bash script linting |

### Utilities

| Hook | Purpose |
|------|---------|
| **end-of-file-fixer** | Ensure newline at EOF |
| **trailing-whitespace** | Remove trailing spaces |
| **check-merge-conflict** | Detect merge conflicts |
| **detect-private-key** | Prevent committing secrets |

## Auto-Fix Behavior

Most hooks can auto-fix issues:

### Auto-Fixed by Pre-Commit
- Black - Reformats code
- isort - Reorders imports
- Prettier - Formats Markdown/JSON/YAML
- Trailing whitespace - Removed
- End of file - Fixed

### Requires Manual Fix
- Flake8 - Reports style issues
- Shellcheck - Reports bash issues
- Hadolint - Reports Docker issues

## Troubleshooting

### "Docker not found"

If you see Docker errors, either:

1. **Install Docker Desktop** (recommended)
2. **Skip Super Linter locally** (use GitHub Actions only)

To skip Super Linter hook:

```bash
pre-commit run --all-files --hook-stage=commit --exclude=super-linter
```

Or comment out in `.pre-commit-config.yaml`:

```yaml
# - repo: https://github.com/super-linter/super-linter
#   ...
```

### "Hook failed"

Example output:

```
Flake8.................................................Failed
- hook id: flake8
- exit code: 1

auto_bedrock_chat_fastapi/app.py:42:1: E501 line too long (125 > 120 characters)
```

**Fix**: Edit the file and shorten the line, then commit again.

### "pre-commit: command not found"

Install pre-commit:

```bash
pip install pre-commit
pre-commit install
```

### Slow on first run

First run downloads all hook images/dependencies. Subsequent runs are faster:

```bash
# First run (slow)
pre-commit run --all-files

# Subsequent runs (fast)
pre-commit run --all-files
```

## Workflow Integration

### Local Development

1. Make code changes
2. Stage changes: `git add .`
3. Commit: `git commit -m "message"`
4. Hooks run automatically
5. Fix any issues
6. Commit again or amend: `git commit --amend`
7. Push when all hooks pass

### With GitHub Actions

Both run together:

1. **Local (pre-commit)** - Runs on your machine before push
2. **Remote (GitHub Actions)** - Runs on GitHub servers before merge

If pre-commit passes locally, GitHub Actions usually passes too.

## Configuration

### Update Hook Versions

```bash
pre-commit autoupdate
```

This updates all hooks to latest versions in `.pre-commit-config.yaml`.

### Modify Settings

Edit `.pre-commit-config.yaml` to:

- Change line lengths: Update `args: [--line-length=120]`
- Enable/disable hooks: Comment/uncomment repo sections
- Add new hooks: Add new repo entry

Example - disable Hadolint:

```yaml
  # - repo: https://github.com/hadolint/hadolint
  #   ...disabled
```

## Skip Hooks (Temporary)

For specific commit:

```bash
git commit --no-verify
```

For development branch (not recommended for main):

```bash
SKIP=super-linter,flake8 git commit -m "WIP"
```

## Pre-Commit CI

This project uses [pre-commit.ci](https://pre-commit.ci/) for automated updates:

- **Updates hooks weekly** from `.pre-commit-config.yaml`
- **Auto-fixes** and creates PRs
- **Runs on all PRs** for consistency

Current settings:

- Auto-fix: Enabled
- Auto-update: Weekly
- Skipped: `super-linter` (too slow for CI)

## Useful Commands

```bash
# Run all hooks on all files
pre-commit run --all-files

# Run specific hook
pre-commit run black --all-files

# Install/update hooks
pre-commit install
pre-commit autoupdate

# See hook status
pre-commit run --all-files --verbose

# Uninstall hooks
pre-commit uninstall
```

## Integration with IDE

### VS Code

Install extensions:

- **Python**: ms-python.python
- **Black Formatter**: ms-python.black-formatter
- **Flake8**: ms-python.flake8
- **Prettier**: esbenp.prettier-vscode

Then configure `.vscode/settings.json`:

```json
{
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "ms-python.black-formatter"
  },
  "[markdown]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "black-formatter.args": ["--line-length", "120"],
  "flake8.args": ["--max-line-length=120"]
}
```

### PyCharm

1. **Settings** → **Tools** → **Python Integrated Tools**
2. **Package requirements file**: `pyproject.toml`
3. **Default test runner**: pytest
4. **Python → Black** → Enable and set line length to 120

## See Also

- 📖 [pre-commit Documentation](https://pre-commit.com/)
- 📖 [GitHub Actions Workflow](GITHUB_ACTIONS.md)
- 🔗 [pre-commit.ci](https://pre-commit.ci/)
