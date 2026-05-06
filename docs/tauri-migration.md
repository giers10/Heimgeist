# Tauri Migration Notes

Tauri is now Heimgeist's active desktop shell for local development. The former desktop shell runtime files and npm scripts have been removed from active development code.

## Current Development Workflow

- Primary dev command: `npm run dev`.
- Compatibility alias: `npm run dev:tauri`.
- The dev wrapper is `scripts/run-tauri-dev.cjs`.
- The wrapper starts or reuses FastAPI on `127.0.0.1:8000`, waits for `/health`, then launches Tauri.
- The Vite renderer runs on strict port `127.0.0.1:5174`.
- `npm run dev:tauri:shell` launches raw `tauri dev` when the backend lifecycle is managed separately.

The development backend is still the normal FastAPI backend started from the repository root through `scripts/run-backend.cjs`.

## User Data Continuity

- Chats continue to use `backend/app.db` through the existing FastAPI backend.
- Local RAG libraries continue to use `backend/libraries` through the existing FastAPI backend.
- No database or library state is moved, rewritten, or migrated by the Tauri shell.
- `HEIMGEIST_SETTINGS_FILE` remains an explicit shared settings override. When set, Tauri reads and writes that file directly and skips first-run import.
- Without `HEIMGEIST_SETTINGS_FILE`, Tauri stores settings in its app config `settings.json`.
- On first Tauri run only, when the Tauri settings file does not exist, Tauri attempts to import the historical Electron settings file from the platform config directory, including `Heimgeist/settings.json`.
- Existing Tauri settings are never overwritten by imported settings.

## Packaged Backend Data Paths

The backend now centralizes runtime data paths in `backend/paths.py`.

- Development remains unchanged when no internal deployment hooks are set: chat data uses `backend/app.db` and local RAG data uses `backend/libraries`.
- Packaged Tauri builds should launch the backend with an app-managed data root so the backend stores data under the OS-appropriate app data directory.
- The intended packaged locations are Application Support on macOS, LocalAppData on Windows, and the XDG data directory or `~/.local/share` equivalent on Linux.
- `HEIMGEIST_DATA_DIR`, `HEIMGEIST_DB_PATH`, and `HEIMGEIST_LIB_ROOT` are internal app/sidecar plumbing hooks for packaged launchers and developer verification. They are not user-facing settings.
- This step does not move or migrate existing development data.

## Desktop Bridge Surface

Renderer code should continue to call only `src/desktop/desktopApi.js` for desktop functions. That bridge owns direct access to the Tauri global and maps the renderer API to Tauri commands/events.

Current Tauri-backed desktop methods:

- `getSettings`
- `setSetting`
- `updateSettings`
- `pickPaths`
- `openPath`
- `openExternalLink`
- `onWindowFocus`
- `offWindowFocus`
- `applyUiScale`
- update/changelog placeholders

## Historical Removal Notes

This pass removed the former Electron runtime and development entry points:

- `electron/main.cjs`
- `electron/preload.cjs`
- `scripts/run-electron-dev.cjs`
- Electron npm metadata, dependencies, and scripts from `package.json`
- Electron-specific branches from `src/desktop/desktopApi.js`

The only remaining Electron references should be historical migration documentation or the Tauri first-run settings import note.

## Remaining Production-Release Tasks

- Replace update/changelog placeholders with a signed Tauri update flow or another release strategy.
- Package the FastAPI backend as a sidecar or equivalent production process.
- Launch the packaged backend with app-managed data paths from the Tauri shell.
- Preserve ffmpeg/ffprobe environment handling for packaged audio transcription.
- Add production backend startup health gating and shutdown behavior.
- Finalize bundle metadata, icons, signing, notarization, and installer behavior.
- Decide whether the settings route needs a dedicated Tauri window for production parity.
- Expand the basic Tauri menu if product-specific app menu actions are needed.
