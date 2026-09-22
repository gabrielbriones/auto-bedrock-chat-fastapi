# autolangchat frontend

React + TypeScript + Vite single-page app for the autolangchat chat and admin UI. It is built
here and served by the FastAPI plugin at site-root `/chat/ui` (`CONTRACT-002` `BC-002`, `ADR-006`) —
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

The dev server proxies `/bedrock-chat`, `/chat` and `/api` to `VITE_API_URL` so cookies and the
chat WebSocket behave exactly as they do in production.

## Build

```bash
npm run build          # tsc -b && vite build  -> frontend/dist
```

The Python package resolves `frontend/dist` relative to the repo checkout; override with
`AUTOCHAT_UI_DIST_DIR` when the build lives elsewhere. `base` (vite.config.ts) and `basepath`
(src/app/router.tsx) are baked in at build time and must match `ChatConfig.ui_endpoint` on the
backend serving this build — both default to `/chat/ui`; edit them together if you change it.

The repository `Dockerfile` runs this build in a Node stage and copies `dist/` into the image.

## Verify

```bash
npm run verify         # typecheck, lint, copy parity, coverage, bench, build, chunk split, size
npm run test:e2e       # Selenium journeys against the built dist/ (needs Chrome)
```
