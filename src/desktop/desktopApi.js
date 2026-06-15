const DEFAULT_SETTINGS = Object.freeze({
  backendApiUrl: 'http://127.0.0.1:8000',
  ollamaApiUrl: 'http://127.0.0.1:11434',
  chatModel: 'llama3',
  visionModel: '',
  embedModel: 'nomic-embed-text:latest',
  rerankModel: 'nomic-embed-text:latest',
  transcriptionModel: 'base',
  autoDeepEnrichment: true,
  colorScheme: 'Default',
  uiScale: 1,
  openDevToolsOnStartup: false,
  audioInputEnabled: true,
  audioInputDeviceId: '',
  audioInputLanguage: '',
})

const DEFAULT_UPDATE_STATUS = Object.freeze({
  state: 'unavailable',
  message: 'Desktop update checks are not available in this runtime.',
  checkedAt: null,
  localCommit: null,
  remoteCommit: null,
  branch: null,
  restartScheduled: false,
})

const DEFAULT_UI_SCALE = 1
const MIN_UI_SCALE = 0.7
const MAX_UI_SCALE = 1.3

const hasWindow = () => typeof window !== 'undefined'
const getTauriCore = () => (hasWindow() ? window.__TAURI__?.core : undefined)
const getTauriEvent = () => (hasWindow() ? window.__TAURI__?.event : undefined)
const tauriWindowFocusUnlisteners = new WeakMap()

function callTauriCommand(commandName, args = {}) {
  const invoke = getTauriCore()?.invoke
  if (typeof invoke !== 'function') {
    return undefined
  }
  return invoke(commandName, args)
}

function fallbackPromise(value) {
  return Promise.resolve(typeof value === 'function' ? value() : value)
}

function defaultSettings() {
  return { ...DEFAULT_SETTINGS }
}

function normalizeUiScale(value) {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return DEFAULT_UI_SCALE
  }

  return Math.min(MAX_UI_SCALE, Math.max(MIN_UI_SCALE, Math.round(numericValue * 100) / 100))
}

function applyRendererUiScale(value) {
  if (!hasWindow() || !window.document) {
    return false
  }

  const scale = normalizeUiScale(value)
  const root = window.document.documentElement
  const body = window.document.body

  root?.style?.setProperty('--heimgeist-ui-scale', String(scale))
  if (body?.style) {
    body.style.zoom = scale === DEFAULT_UI_SCALE ? '' : String(scale)
  }

  return true
}

function defaultUpdateStatus(message = DEFAULT_UPDATE_STATUS.message) {
  return { ...DEFAULT_UPDATE_STATUS, message }
}

function defaultChangelogPage(page = 1) {
  const normalizedPage = Number.isFinite(Number(page)) && Number(page) > 0 ? Number(page) : 1
  return {
    page: normalizedPage,
    pageSize: 50,
    hasMore: false,
    entries: [],
  }
}

function resultOrFallback(result, fallback) {
  return result === undefined ? fallbackPromise(fallback) : result
}

function getExternalUrl(eventOrUrl) {
  if (typeof eventOrUrl === 'string') {
    return eventOrUrl
  }
  return eventOrUrl?.currentTarget?.href || eventOrUrl?.target?.href || ''
}

function listenForTauriWindowFocus(callback) {
  const listen = getTauriEvent()?.listen
  if (typeof listen !== 'function') {
    return undefined
  }

  const unlistenPromise = listen('window-focused', () => callback())
  tauriWindowFocusUnlisteners.set(callback, unlistenPromise)
  return unlistenPromise
}

function unlistenForTauriWindowFocus(callback) {
  const unlistenPromise = tauriWindowFocusUnlisteners.get(callback)
  if (!unlistenPromise) {
    return undefined
  }

  tauriWindowFocusUnlisteners.delete(callback)
  Promise.resolve(unlistenPromise).then((unlisten) => {
    if (typeof unlisten === 'function') {
      unlisten()
    }
  })
  return unlistenPromise
}

const desktopApi = {
  getSettings: () => resultOrFallback(
    callTauriCommand('get_settings'),
    defaultSettings,
  ),
  applyUiScale: (value) => applyRendererUiScale(value),
  getUpdateStatus: () => resultOrFallback(
    callTauriCommand('get_update_status'),
    defaultUpdateStatus,
  ),
  checkForUpdates: () => resultOrFallback(
    callTauriCommand('check_for_updates'),
    () => defaultUpdateStatus('Desktop update checks are not implemented in this runtime.'),
  ),
  getChangelogPage: (page) => resultOrFallback(
    callTauriCommand('get_changelog_page', { page }),
    () => defaultChangelogPage(page),
  ),
  setSetting: (key, value) => resultOrFallback(
    callTauriCommand('set_setting', { key, value }),
    false,
  ),
  updateSettings: (settings) => resultOrFallback(
    callTauriCommand('update_settings', { settings }),
    false,
  ),
  pickPaths: (options) => resultOrFallback(
    callTauriCommand('pick_paths', { options }),
    [],
  ),
  openPath: (filePath) => resultOrFallback(
    callTauriCommand('open_path', { filePath }),
    false,
  ),
  openExternalLink: (eventOrUrl) => {
    eventOrUrl?.preventDefault?.()
    const url = getExternalUrl(eventOrUrl)
    return resultOrFallback(callTauriCommand('open_external_link', { url }), false)
  },
  onWindowFocus: (callback) => listenForTauriWindowFocus(callback),
  offWindowFocus: (callback) => unlistenForTauriWindowFocus(callback),
}

export default desktopApi
