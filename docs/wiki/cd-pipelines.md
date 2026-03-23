# CD Pipelines

The project uses GitHub Actions for continuous delivery. Two workflows handle deployment:

| Workflow        | File                | Purpose                                      |
| --------------- | ------------------- | -------------------------------------------- |
| Build & Publish | `build-publish.yml` | Build package, Docker image, publish to PyPI |
| Deploy          | `deploy.yml`        | Deploy to staging or production environments |

---

## Build & Publish Workflow (`build-publish.yml`)

**Triggers:**

- Push to `main` branch
- Any `v*` tag (e.g., `v1.2.3`)
- Manual via `workflow_dispatch`

### Jobs

#### 1. Build Package

Builds the Python wheel and sdist, uploads as artifacts (30-day retention).

```bash
# Local equivalent
poetry build
# Artifacts in dist/
```

Verifies the built package installs and imports correctly before proceeding.

#### 2. Build & Push Docker Image

Runs on push to `main` or any version tag.

- Builds the Docker image from `Dockerfile`
- Pushes to **GitHub Container Registry** (`ghcr.io`)
- Tags: branch name, semver (`v1.2.3`, `1.2`), and commit SHA

```bash
# Local equivalent
docker build -t auto-bedrock-chat-fastapi:local .

# Pull image
docker pull ghcr.io/<owner>/auto-bedrock-chat-fastapi:main
```

#### 3. Publish to PyPI

Only runs on version tags (`v*`). Requires the `pypi` environment secret `PYPI_API_TOKEN`.

```bash
# Trigger a release
git tag v1.0.0
git push origin v1.0.0
```

Once released, users can install via:

```bash
pip install auto-bedrock-chat-fastapi
```

#### 4. Publish GitHub Release

Only runs on version tags. Automatically generates release notes from commits and attaches built artifacts.

---

## Deploy Workflow (`deploy.yml`)

**Triggers:**

- Push to `main` or any `v*` tag (production)
- Push to `dev` or `develop` branch (staging)
- Manual via `workflow_dispatch`

### Staging Deployment

Triggered on push to `dev` or `develop`.

```yaml
environment:
  name: staging
  url: https://staging-api.example.com
```

Steps:

1. Build Docker image
2. Export image to tarball
3. Deploy to staging host via SSH (configure `STAGING_HOST`, `STAGING_USER`, `STAGING_KEY` secrets)

### Production Deployment

Triggered on push to `main` or version tags.

```yaml
environment:
  name: production
  url: https://api.example.com
```

Steps:

1. Build Docker image tagged with `github.ref_name`
2. Deploy to production host via SSH (configure `PROD_HOST`, `PROD_USER`, `PROD_KEY` secrets)
3. Create and update GitHub deployment record

---

## Required Secrets

| Secret           | Workflow      | Description                     |
| ---------------- | ------------- | ------------------------------- |
| `PYPI_API_TOKEN` | build-publish | PyPI publish token              |
| `STAGING_HOST`   | deploy        | Staging server hostname         |
| `STAGING_USER`   | deploy        | Staging SSH user                |
| `STAGING_KEY`    | deploy        | Staging SSH private key         |
| `PROD_HOST`      | deploy        | Production server hostname      |
| `PROD_USER`      | deploy        | Production SSH user             |
| `PROD_KEY`       | deploy        | Production SSH private key      |
| `GITHUB_TOKEN`   | build-publish | Auto-provided by GitHub Actions |

Set secrets in: **Repository → Settings → Secrets and variables → Actions**

---

## Docker

A `Dockerfile` is included at the repository root.

```bash
# Build locally
docker build -t auto-bedrock-chat-fastapi .

# Run with Docker Compose
docker-compose up

# Environment variables for production
docker run -e AWS_REGION=us-east-1 \
           -e AWS_ACCESS_KEY_ID=... \
           -e AWS_SECRET_ACCESS_KEY=... \
           -e BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0 \
           -p 8000:8000 \
           auto-bedrock-chat-fastapi
```

---

## Release Process

1. Merge changes to `main`
2. Tag the release: `git tag v1.x.x && git push origin v1.x.x`
3. GitHub Actions automatically:
   - Builds the package
   - Builds and pushes the Docker image
   - Publishes to PyPI
   - Creates a GitHub Release with release notes

---

## See Also

- [CI Pipelines](ci-pipelines.md) — testing and code quality
- `.github/workflows/build-publish.yml` — build/publish workflow
- `.github/workflows/deploy.yml` — deployment workflow
- `Dockerfile`, `docker-compose.yml` — container configuration
