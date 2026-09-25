# autolangchat frontend

React + TypeScript + Vite single-page app for the autolangchat chat and admin UI. It is built
here and served by the FastAPI plugin at `/bedrock-chat/ui` (chat and assets) and
`/bedrock-chat/dashboard` (admin) (`CONTRACT-002` `BC-002`, `ADR-006`) —
no standalone Vite server runs for the deployed application.

## Layout

- `src/` — production code (see `docs/architecture.md`)
- `tests/` — unit, component, contract, lint and Selenium e2e specs
- `specs/` — normative product/engineering docs, contracts, ADRs and the migration plan
- `dist/` — Vite build output (gitignored); `autolangchat` serves this directory

## Develop

```bash
cd frontend
npm ci
cp .env.example .env   # VITE_API_URL -> a running autolangchat backend
npm run dev            # Vite dev server with a same-origin proxy to the backend
```

The dev server serves `/bedrock-chat/ui` and `/bedrock-chat/dashboard` locally and proxies
the remaining `/bedrock-chat`, `/chat` and `/api` requests to `VITE_API_URL` so cookies and the
chat WebSocket behave exactly as they do in production.

## Build

```bash
npm run build          # tsc -b && vite build  -> frontend/dist
```

The Python package resolves `frontend/dist` relative to the repo checkout; override with
`AUTOCHAT_UI_DIST_DIR` when the build lives elsewhere. Vite's asset `base` defaults to
`/bedrock-chat/ui/`; the router's `basepath` is `/bedrock-chat` so chat and dashboard are siblings.
Keep both in step with `ChatConfig.ui_endpoint` when deploying to a different prefix.

The repository `Dockerfile` runs this build in a Node stage and copies `dist/` into the image.

## Verify

```bash
npm run verify         # typecheck, lint, copy parity, coverage, bench, build, chunk split, size
npm run test:e2e       # Selenium journeys against the built dist/ (needs Chrome)
```
