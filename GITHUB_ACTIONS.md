# GitHub Actions - Super Linter Workflow

## Overview

This document describes the Super Linter GitHub Actions workflow that automatically lints your code on every push and pull request.

## What is Super Linter?

[Super Linter](https://github.com/super-linter/super-linter) is a GitHub Action that combines multiple linters into a single, easy-to-use tool. It automatically checks your code for style issues, potential bugs, and best practices.

## Workflow Configuration

### Trigger Events

The workflow runs on:

- **Push** to `main`, `dev`, and `develop` branches
- **Pull Request** to `main`, `dev`, and `develop` branches
- **Manual trigger** via GitHub Actions UI (`workflow_dispatch`)

### Linters Enabled

#### Python

- **Black** - Code formatter
- **Flake8** - Style guide enforcement
- **isort** - Import sorting
- **mypy** - Type checking

#### Documentation & Markup

- **Markdown** - Markdown linting
- **HTML** - HTML validation
- **CSS** - CSS linting
- **JSON** - JSON validation
- **YAML** - YAML validation

#### Infrastructure

- **Dockerfile** (Hadolint) - Docker best practices
- **Bash** - Shell script linting

#### Disabled Linters

- **JSCPD** - Copy-paste detection (can be noisy)
- **Gitleaks** - Secret scanning (optional)
- **Checkov** - IaC security scanning
- **Terraform** - Not applicable to project
- **Kotlin** - Not applicable to project

## Configuration Files

### `.github/workflows/super-linter.yml`

Main workflow file that defines the CI/CD pipeline.

```yaml
# Key settings:
RUN_LOCAL: false # Run in Docker (not local)
USE_FIND_ALGORITHM: true # Use find for file detection
VALIDATE_ALL_CODEBASE: true # Check all files on push
PARALLEL_PROCESSES: 2 # Parallel execution for speed
```

### `.markdownlint.json`

Markdown linting configuration:

- Line length: 120 characters
- Allows HTML elements like `<br>`, `<img>`, `<kbd>`
- Consistent list styling

### `.flake8`

Python flake8 linting rules:

- Max line length: 88 (Black compatible)
- Excludes common directories (.git, **pycache**, .venv, etc.)
- Max complexity: 10

### `.hadolintignore`

Docker linting exceptions:

- `DL3002` - Allow building with root (necessary for this project)
- `DL3007` - Allow 'latest' tag (managed separately)

### `.github/super-linter.env`

Local environment configuration (used for local runs):

```bash
docker run -v "$(pwd)":/tmp/lint \
  --env-file .github/super-linter.env \
  github/super-linter:latest
```

## How to Use

### View Results

1. **On Pull Request**: Results appear as a check on the PR
2. **On Push**: Results appear in the Actions tab
3. **View Logs**: Click the workflow run to see detailed logs

### Run Locally

Two options:

**Option 1: Using the shell script**

```bash
./run_super_linter.sh
```

**Option 2: Docker directly**

```bash
docker run --rm \
  -e RUN_LOCAL=true \
  -e VALIDATE_ALL_CODEBASE=true \
  -v "$(pwd)":/tmp/lint \
  github/super-linter:latest
```

### Fix Issues

Most linters can auto-fix issues:

```bash
# Black (Python formatter)
black auto_bedrock_chat_fastapi/

# isort (Python import sorting)
isort auto_bedrock_chat_fastapi/

# Prettier (Markdown, JSON, YAML)
npx prettier --write "**/*.{md,json,yaml,yml}"
```

## GitHub Actions Permissions

The workflow requires these permissions:

```yaml
permissions:
  contents: read # Read repository contents
  statuses: write # Write commit statuses
  checks: write # Write check results
  pull-requests: write # Write PR comments
```

## Artifacts

The workflow uploads:

- `report/` - Detailed linter reports
- `super-linter.log` - Full execution log

These are available for download from the Actions tab.

## PR Comments

When running on a pull request, the workflow will comment with a summary of linting issues:

```
## 🔍 Super Linter Results

[Last 10 warnings/errors]
```

## Skipping Linting

To skip the workflow for a specific commit (not recommended):

```bash
git commit -m "Your message" --no-verify
```

Or add to commit message:

```
[skip ci]
```

## Troubleshooting

### Workflow Not Running

1. Check that the workflow file is in `.github/workflows/`
2. Verify the branch name matches the workflow triggers
3. Check repository settings: Actions must be enabled

### Linting Failures

1. Review the detailed logs in the Actions tab
2. Run locally to debug: `./run_super_linter.sh`
3. Fix issues or update configuration
4. Push to trigger workflow again

### Docker Issues

If you see Docker-related errors:

```bash
# Pull latest image
docker pull github/super-linter:latest

# Retry the workflow
```

## Next Steps

1. **Push to GitHub**: The workflow runs automatically
2. **Review Results**: Check PR checks or Actions tab
3. **Fix Issues**: Commit fixes to your branch
4. **Merge**: Once all checks pass

## References

- [Super Linter Docs](https://github.com/super-linter/super-linter)
- [Markdownlint Config](https://github.com/igorshubovych/markdownlint)
- [Flake8 Config](https://flake8.pycqa.org/)
- [Hadolint Rules](https://hadolint.github.io/)
