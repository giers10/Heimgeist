const DEFAULT_SETTINGS = Object.freeze({
  backendApiUrl: 'http://127.0.0.1:8000',
  ollamaApiUrl: 'http://127.0.0.1:11434',
  chatModel: 'llama3',
  visionModel: '',
  embedModel: 'nomic-embed-text:latest',
  rerankModel: 'nomic-embed-text:latest',
  transcriptionModel: 'base',
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

const hasWindow = () => typeof window !== 'undefined'
const getElectronApi = () => (hasWindow() ? window.electronAPI : undefined)
const getTauriCore = () => (hasWindow() ? window.__TAURI__?.core : undefined)

function callElectronApi(methodName, ...args) {
  const method = getElectronApi()?.[methodName]
  if (typeof method !== 'function') {
    return undefined
  }
  return method(...args)
}

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

const desktopApi = {
  getSettings: () => resultOrFallback(
    callElectronApi('getSettings') ?? callTauriCommand('get_settings'),
    defaultSettings,
  ),
  getUpdateStatus: () => resultOrFallback(
    callElectronApi('getUpdateStatus') ?? callTauriCommand('get_update_status'),
    defaultUpdateStatus,
  ),
  checkForUpdates: () => resultOrFallback(
    callElectronApi('checkForUpdates') ?? callTauriCommand('check_for_updates'),
    () => defaultUpdateStatus('Desktop update checks are not implemented in this runtime.'),
  ),
  getChangelogPage: (page) => resultOrFallback(
    callElectronApi('getChangelogPage', page) ?? callTauriCommand('get_changelog_page', { page }),
    () => defaultChangelogPage(page),
  ),
  setSetting: (key, value) => resultOrFallback(
    callElectronApi('setSetting', key, value) ?? callTauriCommand('set_setting', { key, value }),
    false,
  ),
  updateSettings: (settings) => resultOrFallback(
    callElectronApi('updateSettings', settings) ?? callTauriCommand('update_settings', { settings }),
    false,
  ),
  pickPaths: (options) => resultOrFallback(
    callElectronApi('pickPaths', options) ?? callTauriCommand('pick_paths', { options }),
    [],
  ),
  openPath: (filePath) => resultOrFallback(
    callElectronApi('openPath', filePath) ?? callTauriCommand('open_path', { filePath }),
    false,
  ),
  openExternalLink: (eventOrUrl) => {
    const electronResult = callElectronApi('openExternalLink', eventOrUrl)
    if (electronResult !== undefined) {
      return electronResult
    }

    eventOrUrl?.preventDefault?.()
    const url = getExternalUrl(eventOrUrl)
    return resultOrFallback(callTauriCommand('open_external_link', { url }), false)
  },
  onWindowFocus: (callback) => callElectronApi('onWindowFocus', callback),
  offWindowFocus: (callback) => callElectronApi('offWindowFocus', callback),
}

export default desktopApi
