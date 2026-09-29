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

### Live backend smoke

The [tests Makefile](tests/Makefile) provides shorter commands from the frontend directory:

```bash
make -C tests list
make -C tests test FILE=shared/kernel/result.test.ts
make -C tests test FILE=e2e/chat-scroll.test.ts NAME='keeps a long conversation inside the viewport at 390px' HEADED=1
make -C tests bootstrap
make -C tests login-page
make -C tests login
```

`FILE` is relative to `tests/`; `NAME` filters an individual Jest case. Run `make -C tests help`
for other targets. Set `TEST_ORIGIN` once near the top of `tests/Makefile` to change the browser's
frontend IP and port. Do not put SSO usernames or passwords in the Makefile, `.env`, or command
arguments: `make -C tests login` opens Chrome and waits for you to enter them directly on Intel's
login page (including any MFA). It cannot complete login unattended.

`test:e2e:live` is separate from the scripted browser tests and from `verify`. When chat is
available it sends a real message and may incur model costs. Run it only against a disposable
backend; use disposable data and never point it at production.

Start the backend and the Vite proxy (for example, `make dev` from the workspace root), then run:

```bash
TEST_ORIGIN=http://localhost:3001 npm run test:e2e:live:bootstrap
TEST_ORIGIN=http://localhost:3001 npm run test:e2e:live
```

`TEST_ORIGIN` is the browser-visible Vite origin, not the backend port. The bootstrap-only
command works with an auth-enabled backend. With anonymous chat or an already authenticated browser
session, the full command sends a unique prompt and waits for a finished assistant reply. When
sign-in is required, use the headed command below: it clicks **Login with SSO** and waits up to
five minutes for you to complete the identity-provider login in the Chrome window, then sends the
prompt. A missing return to chat, locked composer, or missing reply fails the test; reaching the
sign-in dialog alone is not a pass. Neither command runs without an explicit origin.
Chrome and a reachable FastAPI backend with a working model are required; no credentials or
fixture responses are supplied by this suite. It is not included in the default Jest or E2E runs.

To watch the live journey, run from a terminal in the same graphical desktop session as Chrome:

```bash
TEST_ORIGIN=http://localhost:3001 npm run test:e2e:live:headed -- -t 'loads the real bootstrap'
TEST_ORIGIN=http://localhost:3001 E2E_SLOWMO=1500 npm run test:e2e:live:headed
TEST_ORIGIN=http://localhost:3001 npm run test:e2e:live:login-page
```

Chrome stays visible, outlines the target and shows the current step in a banner and on stderr.
The default is 500 ms between interactions; the completed page remains open for 10 seconds after
each test so a person can inspect it. The second command requires a test SSO account if the backend
enforces sign-in; enter credentials directly in Chrome, never in the test command or source. With VS
Code Remote/SSH, Chrome opens on the host running the tests, not automatically on your local
desktop; a graphical display or forwarding is required.

The live Chrome instance uses `HTTPS_PROXY` for external SSO pages when it is set, while bypassing
the proxy for localhost. The `login-page` command only checks that Chrome can reach the external
sign-in page; it does not log in or send chat. It is excluded from the normal live suite so the
headed chat journey asks you to sign in only once. If Intel SSO is unreachable, confirm the test
host is on the required VPN and has access to its HTTPS proxy.

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
