# AGENTS.md

## Project Overview


Heimgeist is a local desktop chat app for Ollama. It uses a React/Vite renderer, a Tauri desktop shell, a FastAPI backend, SQLite chat storage, Whisper transcription, optional SearXNG web search, and local RAG libraries under the backend.

## Important Directories and Entry Points

- `src/` - React renderer code.
  - `src/main.jsx` mounts the app.
  - `src/App.jsx` owns most chat UI state, session flow, model startup checks, and library/sidebar orchestration.
  - `src/chatApi.js`, `src/chatGeneration.js`, and `src/backendApi.js` contain backend request and streaming helpers.
  - `src/desktop/desktopApi.js` is the renderer platform abstraction for desktop APIs.
  - `src/styles.css` contains the main UI styling.
- `src-tauri/` - Tauri desktop shell.
  - `src-tauri/src/main.rs` owns Tauri commands for the renderer `desktopApi` surface, settings persistence, dialogs, path opening, external links, focus events, and update placeholders.
  - `src-tauri/tauri.conf.json` points Tauri at the existing Vite renderer.
  - `src-tauri/capabilities/` contains Tauri permission grants for enabled plugins.
- `backend/` - FastAPI backend and local processing.
  - `backend/main.py` defines chat, session, model, audio, Ollama, and web search routes.
  - `backend/sidecar_main.py` is the PyInstaller entrypoint used by packaged Tauri builds to start `backend.main:app`.
  - `backend/local_rag.py` defines library, file registration, job, and local context routes.
  - `backend/paths.py` centralizes backend runtime data paths for SQLite and local RAG libraries.
  - `backend/schemas.py` and `backend/models.py` define API and database data shapes.
  - `backend/rag/` contains corpus, enrichment, embedding, indexing, and retrieval helpers.
  - `backend/database.py` gets the SQLite path from `backend/paths.py`.
- `scripts/` - development wrappers used by npm scripts.
  - `scripts/run-backend.cjs` starts Uvicorn from `backend/.venv`.
  - `scripts/run-tauri-dev.cjs` starts or reuses FastAPI, then launches the Tauri dev shell.
  - `scripts/build-backend-sidecar.cjs` builds the PyInstaller backend sidecar and helper binaries for Tauri packaging.
- `run.sh` - bootstrap script for local development dependencies and dev startup.
- `vite.config.js` - Vite dev/build configuration. Tauri dev uses the strict Vite port `127.0.0.1:5174`.

## Run and Build Commands

- `./run.sh` - bootstrap/update Python and npm dependencies, then start the full dev stack.
- `npm run dev` - start or reuse FastAPI on `127.0.0.1:8000`, start the Vite renderer on `127.0.0.1:5174`, then launch Tauri.
- `npm run dev:tauri` - alias for the primary Tauri development workflow.
- `npm run dev:backend` - start FastAPI on `127.0.0.1:8000` with reload. Requires `backend/.venv`.
- `npm run dev:renderer` - start Vite on `127.0.0.1:5174` for Tauri.
- `npm run dev:tauri:shell` - launch the raw Tauri shell when backend lifecycle is managed separately.
- `npm run build` - build the renderer into `dist/`.
- `npm run build:sidecar` - build the PyInstaller backend sidecar and helper binaries into `src-tauri/binaries/`.
- `npm run package:mac` - build the sidecar and produce `src-tauri/target/release/bundle/macos/Heimgeist.app`.
- `npm run build:app` - alias for `npm run package:mac`.
- `npm start` - alias for `npm run dev`.

There are currently no npm `test` or `lint` scripts in `package.json`.

## Architectural Boundaries

- Renderer code should remain platform-neutral where practical. It may call backend HTTP APIs and may call `desktopApi`, but it should not import or call Tauri APIs directly.
- `src/desktop/desktopApi.js` is the renderer's platform abstraction. Add desktop capabilities there first, then back them with Tauri commands/events.
- `src-tauri/src/main.rs` owns desktop concerns: windows, menus, dialogs, shell opening, settings persistence, update placeholders, focus events, and Tauri command implementations.
- `src-tauri/src/main.rs` also starts the packaged backend sidecar in release builds, waits for `/health`, passes app-managed data-path environment, and shuts the sidecar down when the app exits.
- `backend/paths.py` owns backend runtime path resolution. Default development paths must remain `backend/app.db` and `backend/libraries`; packaged launchers pass app-managed paths through internal environment hooks.
- Backend code owns chat/session persistence, Ollama integration, Whisper transcription, web search enrichment, and RAG library processing.
- Backend API contracts should stay stable unless intentionally coordinated with renderer changes. If a response/request shape changes in `backend/schemas.py` or route handlers, update the renderer callers in the same change.
- Keep local RAG job orchestration and generated library state in the backend. Renderer code should use backend endpoints rather than reading library files directly.

## Generated and Runtime Data

Do not edit or commit generated/runtime data unless the task explicitly requires it:

- `backend/app.db` and any `*.db` files - local SQLite runtime data.
- `backend/libraries/` - local RAG library metadata, corpora, enrichment outputs, embeddings, indexes, and job state.
- `backend/.venv/` and `.venv/` - Python virtual environments.
- `node_modules/` - npm dependencies.
- `dist/` - Vite build output.
- `__pycache__/`, `*.pyc`, `.DS_Store`, and similar machine-generated files.
- Local app settings files under the Tauri app config directory or `HEIMGEIST_SETTINGS_FILE`.
- Packaged-build backend data directories supplied through internal deployment hooks such as `HEIMGEIST_DATA_DIR`, `HEIMGEIST_DB_PATH`, or `HEIMGEIST_LIB_ROOT`.
- Tauri-generated `src-tauri/target/`, `src-tauri/gen/`, and local `src-tauri/Cargo.lock` if generated by local verification.
- Generated sidecar binaries in `src-tauri/binaries/` except `src-tauri/binaries/.gitignore`.

Avoid broad file operations that traverse these directories. Use ignore patterns with search tools when inspecting the repo.

## Verification Expectations

- Frontend changes: run `npm run build` at minimum. For UI or interaction changes, also run the dev stack and exercise the affected flow in Tauri.
- Backend changes: run a targeted syntax/import check such as `backend/.venv/bin/python -m py_compile backend/main.py backend/local_rag.py` for touched modules, then start `npm run dev:backend` or the full dev stack and check `GET /health`.
- Desktop bridge/Tauri changes: run `cargo check --manifest-path src-tauri/Cargo.toml` with a temp `CARGO_TARGET_DIR` when useful, run `npm run build`, and if practical run `npm run dev`. Use a temporary `HEIMGEIST_SETTINGS_FILE` when testing ordinary shared settings behavior; use a temporary `HOME` with a fake legacy settings file when testing first-run import, because `HEIMGEIST_SETTINGS_FILE` intentionally bypasses import.
- Tauri packaging changes: run `npm run build:sidecar`, then `npm run package:mac` on macOS. Verify `src-tauri/target/release/bundle/macos/Heimgeist.app` exists, launches, starts or reuses a compatible backend on `127.0.0.1:8000`, uses app-managed data outside the app bundle, and stops its sidecar processes on quit.
- API contract changes: verify both sides together. Update backend schemas/routes and renderer callers in the same change, then exercise the affected request path.
- RAG, Whisper, Ollama, or SearXNG changes: note any external services or local models required for verification, and test graceful failure paths when those services are unavailable.

## Tauri Guidance

- Keep renderer desktop calls behind `src/desktop/desktopApi.js`; do not import Tauri APIs directly in React components.
- When adding desktop capabilities, prefer method names and payloads that stay stable for renderer callers.
- Preserve `HEIMGEIST_SETTINGS_FILE` as an explicit shared settings override.
- Keep first-run legacy settings import available when Tauri settings do not already exist.
- Do not move or rewrite `backend/app.db` or `backend/libraries`; the development backend must keep using the existing backend working directory.
- Treat `HEIMGEIST_DATA_DIR`, `HEIMGEIST_DB_PATH`, and `HEIMGEIST_LIB_ROOT` as internal app/sidecar deployment hooks only. Do not expose them in the UI or document them as normal user configuration.
- Packaged Tauri builds use `backend/sidecar_main.py` and `scripts/build-backend-sidecar.cjs` to bundle the FastAPI backend. Do not move or migrate development data into the packaged app data directory as part of packaging.
- Production-release work still needs signed updater/changelog parity, final bundle metadata, signing/notarization, full Windows/Linux packaging scripts, and installer behavior.
- Avoid refactors that mix migration prep with unrelated feature work. Migration work should be explicit and reviewable.

## Coding and Refactor Guidelines

- Keep changes scoped to the request. Do not refactor large files such as `src/App.jsx`, `src/styles.css`, `backend/main.py`, or `backend/local_rag.py` unless the task needs it.
- Prefer existing helper modules and patterns over new abstractions.
- Preserve stable backend contracts and persisted data compatibility. Add small migration helpers when changing database-backed fields.
- Keep settings keys backward compatible where possible. Existing settings migrations live in Tauri and backend settings helpers.
- Use structured JSON/schema changes rather than ad hoc string parsing for API payloads.
- Add comments only where they explain non-obvious control flow, concurrency, migration, or integration behavior.
- Do not silently swallow errors in user-facing flows; surface useful messages while preserving graceful degradation for optional services.
- Do not mix formatting churn with behavior changes.

## Known Fragile Areas

- The Tauri bridge is intentionally narrow. Direct `window.__TAURI__` use outside `src/desktop/desktopApi.js` makes desktop behavior harder to reason about.
- `src/App.jsx`, `src/chatGeneration.js`, and backend chat routes share assumptions about streaming, regeneration, attachments, source metadata, and session state.
- `backend/main.py` creates/migrates SQLite tables at import/startup. Schema changes need care because they run against local user data.
- `backend/paths.py` controls where chat and local RAG data are stored. Preserve development defaults unless the task explicitly concerns packaged app data paths.
- `backend/local_rag.py` coordinates asynchronous jobs, file registration, generated artifacts, and per-library locks. Avoid changes that can start duplicate jobs or corrupt `backend/libraries/`.
- RAG builders under `backend/rag/` run heavier file processing and subprocess/threaded work. Test with small inputs before broader runs.
- Whisper depends on ffmpeg/ffprobe paths supplied by the Node backend runner when available.
- Web search depends on an optional SearXNG instance, usually `http://127.0.0.1:8888`; features should fail gracefully when it is absent.
- Ollama availability, model names, embedding model choices, and vision capability checks are runtime-dependent. Keep related UI and backend paths tolerant of missing models.
