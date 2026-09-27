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

### Watching the e2e journeys

The journeys run headless and at full speed by default, which is right for CI and useless for
checking what a journey actually does. `test:e2e:headed` opens a visible Chrome window, runs one
spec at a time, pauses before every interaction and narrates each step, both in the terminal and
in a banner at the top of the page (the current test name plus the step about to happen, with the
target element outlined):

```bash
npm run build                               # the journeys serve dist/, so build first
npm run test:e2e:headed                     # every journey, 500 ms pause before each action
npm run test:e2e:headed -- feedback         # only the specs whose path matches "feedback"
E2E_SLOWMO=1500 npm run test:e2e:headed     # slower; E2E_SLOWMO=0 keeps the window but not the pauses
```

`HEADED=1` and `E2E_SLOWMO` also work with `npm run test:e2e` directly; add `--runInBand` there,
otherwise Jest opens one window per worker. The banner is hidden while screenshots are taken and
during the axe scans, so the same specs pass headed and headless, with one exception: screenshot
baselines are not compared in headed mode. A visible window loses part of its requested size to
Chrome's tab strip and toolbar, so its captures can never match the headless baselines, and the
comparison is skipped with a note instead. Headed mode is for watching a journey, not for gating it.

### Screenshot baselines

`tests/e2e/__screenshots__/` holds the PNGs the visual regression and journey specs compare
against, captured headless in the light theme. They depend on the machine's font rendering, so a
capture from a different OS, font stack or Chrome version differs by thousands of scattered pixels
even when nothing changed. Regenerate them on the setup that runs the comparison, after reviewing
the failure's `*.actual.png` capture:

```bash
rm tests/e2e/__screenshots__/<name>.png   # or vr-*.png for the whole visual matrix
npm run test:e2e                          # a missing baseline is recorded, not compared
```

Headed Chrome needs the desktop session. A shell that has neither `DISPLAY` nor `WAYLAND_DISPLAY`
exported (common in IDE terminals and tool runners) is handled: the helper attaches Chrome to the
session's Wayland socket, or to X display `:0` with GNOME's Xwayland authority file. If Chrome
still cannot open a window the failure says which display it tried, instead of Selenium's bare
"session not created".
