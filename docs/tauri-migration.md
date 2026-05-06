# Tauri Migration Spike

This spike adds an isolated Tauri v2 scaffold while keeping the Electron app as the supported desktop shell.

References checked during the spike:

- Tauri Vite setup: https://v2.tauri.app/start/frontend/vite/
- Tauri JavaScript API: https://v2.tauri.app/reference/javascript/api/
- Tauri CLI: https://v2.tauri.app/reference/cli/

## Current Electron Responsibilities

| Area | Current Electron implementation | Tauri target |
| --- | --- | --- |
| Main app window | `BrowserWindow` in `electron/main.cjs`, loading Vite in dev and `dist/index.html` in production | `src-tauri/tauri.conf.json` window loading the same Vite dev URL or `../dist` |
| Settings window | Separate modal `BrowserWindow` routed to `#/settings` | Later phase: either a second Tauri webview window or in-app settings route |
| Settings persistence | JSON file under Electron `app.getPath('userData')`, with migrations and normalization | Later phase: Tauri command backed by app config/data dir with the same setting keys and migrations |
| Renderer bridge | `electron/preload.cjs` exposes `window.electronAPI`; renderer uses `src/desktop/desktopApi.js` | `desktopApi` can call Tauri commands through `window.__TAURI__.core.invoke` when running in Tauri |
| File picker | `dialog.showOpenDialog` via `pick-paths` IPC | Later phase: official Tauri dialog plugin with scoped permissions |
| Open file/path | `shell.openPath` via `open-path` IPC | Later phase: official Tauri opener plugin or a scoped Rust command |
| External links | `shell.openExternal` and `setWindowOpenHandler` | Later phase: official Tauri opener plugin and window navigation policy |
| Window focus events | Electron `focus` event forwarded to renderer | Later phase: Tauri window event listener or emitted command event |
| App menu | Electron `Menu.buildFromTemplate` | Later phase: Tauri menu API |
| UI scale | Electron `webContents.setZoomFactor` | Later phase: Tauri/webview equivalent or renderer CSS scale strategy |
| DevTools preference | Electron opens/closes DevTools in dev | Later phase: Tauri devtools behavior and persisted preference |
| Updates/changelog | Git-based update check, pull, restart, and local changelog | Later phase: replace with Tauri updater or another signed update flow |
| Backend lifecycle | External Node scripts start FastAPI in dev | Not part of this spike; later sidecar/package design |
| Packaging | Electron starts from `main` and existing npm scripts | Later phase: Tauri bundle config, icons, signing, backend sidecar |

## Scaffold Added

- `src-tauri/tauri.conf.json` points Tauri at the existing Vite renderer on `http://127.0.0.1:5173` and the existing `dist` build output.
- `src-tauri/src/main.rs` registers placeholder Tauri commands that match the current `desktopApi` surface.
- `src-tauri/capabilities/default.json` grants the main window the default core capability for this spike.
- `src/desktop/desktopApi.js` still prefers Electron. When Electron is absent and Tauri is present, it invokes matching Tauri commands. Without either desktop bridge, it falls back to safe defaults so the renderer can mount.

## Intentional Non-Goals

- No Electron files, scripts, or IPC behavior were removed.
- No Python backend sidecar, packaging, or lifecycle work was added.
- No Tauri updater, opener, dialog, menu, or settings persistence parity was implemented.
- The Tauri settings command is in-memory only and exists to let the renderer hydrate during the spike.

## Remaining Parity Gaps

- Settings: persistent storage, migrations, normalization, UI scale side effects, DevTools preference side effects.
- File picker: native picker and path filters.
- Open path and external links: scoped native opener behavior.
- Window focus events: renderer notification equivalent.
- Update flow: startup/manual checks, changelog, restart, and signed release strategy.
- Backend lifecycle: FastAPI startup, health gating, ffmpeg/ffprobe environment, shutdown, and sidecar packaging.
- Packaging: bundle metadata, icons, signing/notarization, auto-update channel, and platform install behavior.
