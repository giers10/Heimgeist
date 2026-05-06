# Tauri Migration Spike

This spike adds an isolated Tauri v2 scaffold while keeping the Electron app as the supported desktop shell.

References checked during the spike:

- Tauri Vite setup: https://v2.tauri.app/start/frontend/vite/
- Tauri JavaScript API: https://v2.tauri.app/reference/javascript/api/
- Tauri CLI: https://v2.tauri.app/reference/cli/
- Tauri dialog plugin: https://v2.tauri.app/plugin/dialog/
- Tauri opener plugin: https://v2.tauri.app/plugin/opener/

## Current Electron Responsibilities

| Area | Current Electron implementation | Tauri target |
| --- | --- | --- |
| Main app window | `BrowserWindow` in `electron/main.cjs`, loading Vite in dev and `dist/index.html` in production | `src-tauri/tauri.conf.json` window loading the same Vite dev URL or `../dist` |
| Settings window | Separate modal `BrowserWindow` routed to `#/settings` | Later phase: either a second Tauri webview window or in-app settings route |
| Settings persistence | JSON file under Electron `app.getPath('userData')`, with migrations and normalization | Implemented for Tauri with `HEIMGEIST_SETTINGS_FILE` override or app config `settings.json`; keys and normalizers mirror Electron closely |
| Renderer bridge | `electron/preload.cjs` exposes `window.electronAPI`; renderer uses `src/desktop/desktopApi.js` | Implemented: `desktopApi` still prefers Electron and falls back to Tauri commands/listeners |
| File picker | `dialog.showOpenDialog` via `pick-paths` IPC | Implemented for Tauri through the official dialog plugin; supports existing `title` and `filters` options |
| Open file/path | `shell.openPath` via `open-path` IPC | Implemented for Tauri through the official opener plugin |
| External links | `shell.openExternal` and `setWindowOpenHandler` | Implemented for Tauri through the official opener plugin for `http`, `https`, `mailto`, and `tel` URLs |
| Window focus events | Electron `focus` event forwarded to renderer | Implemented for Tauri by emitting `window-focused` and listening from `desktopApi` |
| App menu | Electron `Menu.buildFromTemplate` | Later phase: Tauri menu API |
| UI scale | Electron `webContents.setZoomFactor` | Later phase: Tauri/webview equivalent or renderer CSS scale strategy |
| DevTools preference | Electron opens/closes DevTools in dev | Later phase: Tauri devtools behavior and persisted preference |
| Updates/changelog | Git-based update check, pull, restart, and local changelog | Later phase: replace with Tauri updater or another signed update flow |
| Backend lifecycle | External Node scripts start FastAPI in dev | Not part of this spike; later sidecar/package design |
| Packaging | Electron starts from `main` and existing npm scripts | Later phase: Tauri bundle config, icons, signing, backend sidecar |

## Scaffold Added

- `src-tauri/tauri.conf.json` points Tauri at the existing Vite renderer on `http://127.0.0.1:5173` and the existing `dist` build output.
- `src-tauri/src/main.rs` registers Tauri commands for settings, file picking, path opening, external link opening, focus events, and update placeholders.
- `src-tauri/capabilities/default.json` grants the main window core, dialog, and opener permissions used by this spike.
- `src-tauri/icons/icon.png` is a placeholder icon required by Tauri context generation.
- `src/desktop/desktopApi.js` still prefers Electron. When Electron is absent and Tauri is present, it invokes matching Tauri commands. Without either desktop bridge, it falls back to safe defaults so the renderer can mount.

## Running The Spike

- Normal Electron development remains `npm run dev`.
- After installing npm dependencies, Tauri development is `npm run dev:tauri`.
- The Tauri config starts only the Vite renderer; start the FastAPI backend separately when testing chat flows.
- If Vite is already running on `127.0.0.1:5173`, reuse it with `npm run tauri -- dev --config '{"build":{"beforeDevCommand":""}}'`.

## Intentional Non-Goals

- No Electron files, scripts, or IPC behavior were removed.
- No Python backend sidecar, packaging, or lifecycle work was added.
- No Tauri updater, app menu, second settings window, UI zoom side effects, DevTools preference side effects, or packaging parity was implemented.

## Remaining Parity Gaps

- Settings: UI scale side effects and DevTools preference side effects are not wired in Tauri yet.
- Settings window: Electron still has a separate modal settings window; Tauri currently uses the existing renderer route only.
- App menu: Electron menu roles are not recreated in Tauri.
- Update flow: startup/manual checks, changelog, restart, and signed release strategy.
- Backend lifecycle: FastAPI startup, health gating, ffmpeg/ffprobe environment, shutdown, and sidecar packaging.
- Packaging: bundle metadata, icons, signing/notarization, auto-update channel, and platform install behavior.
