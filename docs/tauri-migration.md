# Tauri Migration Notes

Tauri is now the primary experimental development shell for Heimgeist. Electron remains intact as a fallback until the remaining desktop and packaging gaps are closed.

References checked during the spike:

- Tauri Vite setup: https://v2.tauri.app/start/frontend/vite/
- Tauri JavaScript API: https://v2.tauri.app/reference/javascript/api/
- Tauri CLI: https://v2.tauri.app/reference/cli/
- Tauri dialog plugin: https://v2.tauri.app/plugin/dialog/
- Tauri opener plugin: https://v2.tauri.app/plugin/opener/

## Current Desktop Responsibility Map

| Area | Electron implementation | Tauri status |
| --- | --- | --- |
| Main app window | `BrowserWindow` in `electron/main.cjs`, loading Vite in dev and `dist/index.html` in production | Implemented through `src-tauri/tauri.conf.json`; dev loads the same React/Vite renderer on the Tauri-specific port `5174` |
| Settings window | Separate modal `BrowserWindow` routed to `#/settings` | Later phase: either a second Tauri webview window or the existing in-app settings route |
| Settings persistence | JSON under Electron `app.getPath('userData')`, or `HEIMGEIST_SETTINGS_FILE` when set | Implemented with persistent JSON under Tauri app config, with a first-run Electron settings import when safe |
| Renderer bridge | `electron/preload.cjs` exposes `window.electronAPI`; renderer uses `src/desktop/desktopApi.js` | Implemented: `desktopApi` still prefers Electron and falls back to Tauri commands/listeners |
| File picker | `dialog.showOpenDialog` via `pick-paths` IPC | Implemented through the official Tauri dialog plugin; supports the existing `title`, `filters`, and `multiple` option shape where practical |
| Open file/path | `shell.openPath` via `open-path` IPC | Implemented through the official Tauri opener plugin |
| External links | `shell.openExternal` and `setWindowOpenHandler` | Implemented through the official Tauri opener plugin for `http`, `https`, `mailto`, and `tel` URLs |
| Window focus events | Electron `focus` event forwarded to renderer | Implemented by emitting `window-focused` from Tauri and listening in `desktopApi` |
| App menu | Electron `Menu.buildFromTemplate` | Basic Tauri default menu implemented; detailed Electron menu parity remains later work |
| UI scale | Electron `webContents.setZoomFactor` | Implemented for Tauri/browser-like runtimes through `desktopApi.applyUiScale`, which applies renderer CSS zoom while leaving Electron native zoom unchanged |
| DevTools preference | Electron opens/closes DevTools in dev | Later phase: no clean stable Tauri equivalent is wired yet |
| Updates/changelog | Git-based update check, pull, restart, and local changelog | Later phase: replace with Tauri updater or another signed update flow |
| Backend lifecycle | External Node scripts start FastAPI in dev | Development parity implemented by `scripts/run-tauri-dev.cjs`, which starts or reuses the existing FastAPI dev backend; Python sidecar packaging remains later |
| Packaging | Electron starts from `main` and existing npm scripts | Later phase: Tauri bundle config, icons, signing/notarization, and Python backend sidecar |

## Development Workflow

- Electron development remains unchanged: `npm run dev`.
- Tauri development is now `npm run dev:tauri` or `npm run dev:tauri:full`.
- The Tauri wrapper starts or reuses FastAPI on `127.0.0.1:8000`, waits for `/health`, then launches Tauri.
- Tauri uses its own strict Vite dev port: `127.0.0.1:5174`. This avoids conflicts with the existing Electron renderer port `5173`.
- The raw Tauri shell remains available as `npm run dev:tauri:shell`; use it only when the backend lifecycle is managed separately.

The Tauri dev backend is still the normal FastAPI backend started from the repository root through `scripts/run-backend.cjs`. That keeps chat history and local RAG data in the existing backend locations.

## User Data Continuity

- Chats continue to use `backend/app.db` through the existing FastAPI backend. No database move, rewrite, or migration is part of this step.
- Local RAG libraries continue to use `backend/libraries` through the existing FastAPI backend. No library state is moved or rewritten.
- `HEIMGEIST_SETTINGS_FILE` remains an explicit shared settings override for both Electron and Tauri. When it is set, Tauri reads and writes that file directly and does not perform first-run import.
- Without `HEIMGEIST_SETTINGS_FILE`, Tauri stores settings in its app config `settings.json`.
- On first Tauri run only, when the Tauri settings file does not exist, Tauri attempts to read Electron settings from the platform config directory, including the existing `Heimgeist/settings.json` path used by Electron.
- If Electron settings are found, Tauri migrates and normalizes them into the Tauri settings path.
- Existing Tauri settings are not replaced by Electron settings.

## Current Tauri Scaffold

- `src-tauri/src/main.rs` registers Tauri commands for settings, dialogs, path opening, external links, focus events, and update placeholders.
- `src-tauri/capabilities/default.json` grants the main window core, dialog, and opener permissions used by the scaffold.
- `src-tauri/tauri.conf.json` points Tauri at the existing Vite renderer on `http://127.0.0.1:5174` in dev and `../dist` for build output.
- `scripts/run-tauri-dev.cjs` is the full Tauri dev wrapper.
- `src/desktop/desktopApi.js` remains the only renderer bridge layer for Electron/Tauri desktop functions.

## Intentional Non-Goals

- No Electron files, scripts, dependencies, or IPC behavior were removed.
- No backend API contracts changed.
- No Python backend sidecar, production backend packaging, or data relocation was added.
- No Tauri updater, changelog parity, or restart flow was implemented.

## Remaining Gaps Before Electron Removal

- Update flow: startup/manual update checks, changelog, restart, and signed release strategy.
- Production backend lifecycle: package the FastAPI backend, set ffmpeg/ffprobe paths, health gate startup, and shut down cleanly.
- DevTools preference: Electron honors `openDevToolsOnStartup`; Tauri currently documents this as an Electron-only setting.
- Settings window parity: Electron still has a separate settings modal; Tauri currently uses the existing renderer route.
- App menu parity: Tauri has a basic default menu, not the full Electron template.
- Packaging: bundle metadata, final icons, signing/notarization, updater channel, and installer behavior.
