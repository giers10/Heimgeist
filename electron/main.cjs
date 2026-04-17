const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require('electron')
const path = require('path')
const { is } = require('@electron-toolkit/utils')
const fs = require('fs')
const { execFile } = require('child_process')
const { promisify } = require('util')

let mainWindow
let settingsWindow = null
const execFileAsync = promisify(execFile)

const DEFAULT_BACKEND_API_URL = 'http://127.0.0.1:8000'
const DEFAULT_OLLAMA_API_URL = 'http://127.0.0.1:11434'
const DEFAULT_EMBED_MODEL = 'nomic-embed-text:latest'
const DEFAULT_RERANK_MODEL = DEFAULT_EMBED_MODEL
const DEFAULT_TRANSCRIPTION_MODEL = 'base'
const BGE_EMBED_MODEL = 'bge-m3:latest'
const REPO_ROOT = path.resolve(__dirname, '..')
const UPDATE_REMOTE_URL = 'https://giers10.uber.space/giers10/Heimgeist.git'
const UPDATE_BRANCH = 'master'
const GIT_ENV = { ...process.env, GIT_TERMINAL_PROMPT: '0' }
const DEV_WRAPPER_RELAUNCH_CODE = Number(process.env.HEIMGEIST_DEV_RELAUNCH_CODE || 75)
const settingsFilePath = process.env.HEIMGEIST_SETTINGS_FILE || path.join(app.getPath('userData'), 'settings.json')
let appSettings = {}
let lastUpdateCheckResult = null
let activeUpdateCheck = null
const DEFAULT_UI_SCALE = 1
const MIN_UI_SCALE = 0.7
const MAX_UI_SCALE = 1.3
const DEFAULT_OPEN_DEVTOOLS_ON_STARTUP = false
const DEFAULT_AUDIO_INPUT_ENABLED = true
const DEFAULT_AUDIO_INPUT_DEVICE_ID = ''
const DEFAULT_AUDIO_INPUT_LANGUAGE = ''
const CHANGELOG_PAGE_SIZE = 50
const CHANGELOG_MESSAGE_MAX_LENGTH = 500

const defaultSettings = {
  backendApiUrl: DEFAULT_BACKEND_API_URL,
  ollamaApiUrl: DEFAULT_OLLAMA_API_URL,
  chatModel: 'llama3',
  visionModel: '',
  embedModel: DEFAULT_EMBED_MODEL,
  rerankModel: DEFAULT_RERANK_MODEL,
  transcriptionModel: DEFAULT_TRANSCRIPTION_MODEL,
  colorScheme: 'Default',
  uiScale: DEFAULT_UI_SCALE,
  openDevToolsOnStartup: DEFAULT_OPEN_DEVTOOLS_ON_STARTUP,
  audioInputEnabled: DEFAULT_AUDIO_INPUT_ENABLED,
  audioInputDeviceId: DEFAULT_AUDIO_INPUT_DEVICE_ID,
  audioInputLanguage: DEFAULT_AUDIO_INPUT_LANGUAGE,
}

function normalizeEmbedModel(value) {
  const trimmed = String(value || '').trim()
  if (!trimmed) {
    return DEFAULT_EMBED_MODEL
  }

  const lowered = trimmed.toLowerCase()
  if (lowered === 'bge' || lowered === 'bge-m3' || lowered === BGE_EMBED_MODEL) {
    return BGE_EMBED_MODEL
  }
  if (lowered === 'nomic' || lowered === 'nomic-embed-text' || lowered === DEFAULT_EMBED_MODEL) {
    return DEFAULT_EMBED_MODEL
  }
  return trimmed
}

function normalizeRerankModel(value) {
  return normalizeEmbedModel(value)
}

function normalizeModelName(value, fallback = '') {
  const trimmed = String(value || '').trim()
  return trimmed || fallback
}

function normalizeTranscriptionModel(value) {
  return normalizeModelName(value, DEFAULT_TRANSCRIPTION_MODEL)
}

function looksLikeOllamaUrl(value) {
  if (typeof value !== 'string') {
    return false
  }

  try {
    const parsed = new URL(value)
    if (parsed.port === '11434') {
      return true
    }

    return /^\/api\/?$/i.test(parsed.pathname || '')
  } catch (_error) {
    return false
  }
}

function migrateSettings(rawSettings) {
  const source = rawSettings && typeof rawSettings === 'object' ? rawSettings : {}
  const nextSettings = { ...defaultSettings, ...source }
  let migrated = false

  if (!Object.prototype.hasOwnProperty.call(source, 'backendApiUrl') && typeof source.ollamaApiUrl === 'string') {
    if (looksLikeOllamaUrl(source.ollamaApiUrl)) {
      nextSettings.backendApiUrl = DEFAULT_BACKEND_API_URL
      nextSettings.ollamaApiUrl = source.ollamaApiUrl
    } else {
      nextSettings.backendApiUrl = source.ollamaApiUrl
      nextSettings.ollamaApiUrl = DEFAULT_OLLAMA_API_URL
    }
    migrated = true
  }

  if (!Object.prototype.hasOwnProperty.call(source, 'openDevToolsOnStartup')) {
    migrated = true
  }

  if (!Object.prototype.hasOwnProperty.call(source, 'rerankModel')) {
    nextSettings.rerankModel = nextSettings.embedModel
    migrated = true
  }

  if (!Object.prototype.hasOwnProperty.call(source, 'visionModel')) {
    nextSettings.visionModel = nextSettings.chatModel || ''
    migrated = true
  }

  if (!Object.prototype.hasOwnProperty.call(source, 'transcriptionModel')) {
    nextSettings.transcriptionModel = DEFAULT_TRANSCRIPTION_MODEL
    migrated = true
  }

  nextSettings.backendApiUrl = String(nextSettings.backendApiUrl || '').trim()
  nextSettings.ollamaApiUrl = String(nextSettings.ollamaApiUrl || '').trim()
  nextSettings.chatModel = normalizeModelName(nextSettings.chatModel)
  nextSettings.visionModel = normalizeModelName(nextSettings.visionModel)
  nextSettings.embedModel = normalizeEmbedModel(nextSettings.embedModel)
  nextSettings.rerankModel = normalizeRerankModel(nextSettings.rerankModel)
  nextSettings.transcriptionModel = normalizeTranscriptionModel(nextSettings.transcriptionModel)
  nextSettings.openDevToolsOnStartup = normalizeOpenDevToolsOnStartup(nextSettings.openDevToolsOnStartup)
  nextSettings.audioInputEnabled = normalizeBooleanSetting(nextSettings.audioInputEnabled)
  nextSettings.audioInputDeviceId = String(nextSettings.audioInputDeviceId || '').trim()
  nextSettings.audioInputLanguage = String(nextSettings.audioInputLanguage || '').trim().toLowerCase()

  return { nextSettings, migrated }
}

function normalizeUiScale(value) {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return DEFAULT_UI_SCALE
  }

  return Math.min(MAX_UI_SCALE, Math.max(MIN_UI_SCALE, Math.round(numericValue * 100) / 100))
}

function normalizeBooleanSetting(value) {
  if (typeof value === 'string') {
    const trimmed = value.trim().toLowerCase()
    if (trimmed === 'true' || trimmed === '1' || trimmed === 'yes' || trimmed === 'on') {
      return true
    }

    if (trimmed === 'false' || trimmed === '0' || trimmed === 'no' || trimmed === 'off' || trimmed === '') {
      return false
    }
  }

  return value === true
}

function normalizeOpenDevToolsOnStartup(value) {
  return normalizeBooleanSetting(value)
}

function applyUiScaleToWindow(window) {
  if (!window || window.isDestroyed()) {
    return
  }

  window.webContents.setZoomFactor(normalizeUiScale(appSettings.uiScale))
}

function applyUiScaleToAllWindows() {
  BrowserWindow.getAllWindows().forEach(applyUiScaleToWindow)
}

function shouldAutoOpenDevTools() {
  return is.dev && appSettings.openDevToolsOnStartup === true
}

function applyDevToolsPreferenceToWindow(window) {
  if (!is.dev || !window || window.isDestroyed()) {
    return
  }

  const webContents = window.webContents
  if (!webContents) {
    return
  }

  if (shouldAutoOpenDevTools()) {
    if (!webContents.isDevToolsOpened()) {
      webContents.openDevTools({ mode: 'detach' })
    }
    return
  }

  if (webContents.isDevToolsOpened()) {
    webContents.closeDevTools()
  }
}

function applyDevToolsPreferenceToAllWindows() {
  BrowserWindow.getAllWindows().forEach(applyDevToolsPreferenceToWindow)
}

function loadSettings() {
  try {
    if (fs.existsSync(settingsFilePath)) {
      const data = fs.readFileSync(settingsFilePath, 'utf8')
      const { nextSettings, migrated } = migrateSettings(JSON.parse(data))
      appSettings = nextSettings
      if (migrated) {
        saveSettings()
      }
    } else {
      appSettings = { ...defaultSettings }
      saveSettings()
    }
    appSettings.uiScale = normalizeUiScale(appSettings.uiScale)
  } catch (error) {
    console.error('Failed to load settings:', error)
    appSettings = { ...defaultSettings }
  }
}

function saveSettings() {
  try {
    fs.mkdirSync(path.dirname(settingsFilePath), { recursive: true })
    fs.writeFileSync(settingsFilePath, JSON.stringify(appSettings, null, 2), 'utf8')
  } catch (error) {
    console.error('Failed to save settings:', error)
  }
}

function setUpdateStatus(status) {
  lastUpdateCheckResult = {
    state: 'idle',
    message: '',
    checkedAt: new Date().toISOString(),
    localCommit: null,
    remoteCommit: null,
    branch: null,
    restartScheduled: false,
    ...status,
  }

  return lastUpdateCheckResult
}

async function runGitCommand(args, options = {}) {
  return execFileAsync('git', ['-C', REPO_ROOT, ...args], {
    env: GIT_ENV,
    maxBuffer: 1024 * 1024,
    timeout: options.timeout ?? 15000,
  })
}

function normalizeChangelogPage(value) {
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed) || parsed < 1) {
    return 1
  }

  return parsed
}

function truncateChangelogMessage(message) {
  const normalized = String(message || '')
    .replace(/\s+/g, ' ')
    .trim()

  if (!normalized) {
    return 'No commit message.'
  }

  if (normalized.length <= CHANGELOG_MESSAGE_MAX_LENGTH) {
    return normalized
  }

  return `${normalized.slice(0, CHANGELOG_MESSAGE_MAX_LENGTH - 3).trimEnd()}...`
}

function parseChangelogOutput(output) {
  return String(output || '')
    .split('\u0000\u0000')
    .map((record) => record.trim())
    .filter(Boolean)
    .map((record) => {
      const [hash = '', committedAt = '', ...messageParts] = record.split('\u0000')
      return {
        hash: hash.trim(),
        committedAt: committedAt.trim(),
        message: truncateChangelogMessage(messageParts.join('\u0000')),
      }
    })
    .filter((entry) => entry.hash && entry.message)
}

async function getChangelogPage(page = 1) {
  const normalizedPage = normalizeChangelogPage(page)
  const pageSize = CHANGELOG_PAGE_SIZE
  const skip = (normalizedPage - 1) * pageSize
  const { stdout } = await runGitCommand([
    'log',
    `--max-count=${pageSize + 1}`,
    `--skip=${skip}`,
    '--format=%H%x00%cI%x00%B%x00%x00',
  ], { timeout: 30000 })

  const entries = parseChangelogOutput(stdout)

  return {
    page: normalizedPage,
    pageSize,
    hasMore: entries.length > pageSize,
    entries: entries.slice(0, pageSize),
  }
}

function scheduleAppRestart() {
  setTimeout(() => {
    if (process.env.HEIMGEIST_DEV_WRAPPER === '1') {
      app.exit(DEV_WRAPPER_RELAUNCH_CODE)
      return
    }

    app.relaunch()
    app.exit(0)
  }, 300)
}

function parseGitStatusPaths(statusOutput) {
  return statusOutput
    .split('\n')
    .map((line) => line.trimEnd())
    .filter(Boolean)
    .map((line) => line.slice(3).trim())
}

function formatChangedPaths(paths) {
  if (!Array.isArray(paths) || paths.length === 0) {
    return ''
  }

  const preview = paths.slice(0, 3).join(', ')
  return paths.length > 3 ? `${preview}, ...` : preview
}

async function performUpdateCheck(trigger = 'manual') {
  if (!fs.existsSync(path.join(REPO_ROOT, '.git'))) {
    return setUpdateStatus({
      state: 'unavailable',
      trigger,
      message: 'Update check unavailable: no Git checkout found.',
    })
  }

  setUpdateStatus({
    state: 'checking',
    trigger,
    message: 'Checking remote repository for updates...',
  })

  try {
    const [
      { stdout: localStdout },
      { stdout: branchStdout },
      { stdout: remoteStdout },
      { stdout: worktreeStdout },
    ] = await Promise.all([
      runGitCommand(['rev-parse', 'HEAD']),
      runGitCommand(['branch', '--show-current']),
      runGitCommand(['ls-remote', UPDATE_REMOTE_URL, `refs/heads/${UPDATE_BRANCH}`]),
      runGitCommand(['status', '--porcelain', '--untracked-files=no']),
    ])

    const localCommit = localStdout.trim()
    const branch = branchStdout.trim() || null
    const remoteCommit = remoteStdout.trim().split(/\s+/)[0] || null
    const changedPaths = parseGitStatusPaths(worktreeStdout)
    const worktreeDirty = changedPaths.length > 0

    if (!localCommit) {
      throw new Error('Could not resolve the current local commit hash.')
    }

    if (!remoteCommit) {
      throw new Error(`Could not resolve the remote ${UPDATE_BRANCH} commit hash.`)
    }

    if (localCommit === remoteCommit) {
      return setUpdateStatus({
        state: 'up-to-date',
        trigger,
        branch,
        localCommit,
        remoteCommit,
        message: 'Heimgeist is already up to date.',
      })
    }

    if (branch && branch !== UPDATE_BRANCH) {
      return setUpdateStatus({
        state: 'skipped',
        trigger,
        branch,
        localCommit,
        remoteCommit,
        message: `Update skipped: current branch is "${branch}", expected "${UPDATE_BRANCH}".`,
      })
    }

    if (worktreeDirty) {
      const changedPathSummary = formatChangedPaths(changedPaths)
      return setUpdateStatus({
        state: 'skipped',
        trigger,
        branch: branch || UPDATE_BRANCH,
        localCommit,
        remoteCommit,
        message: changedPathSummary
          ? `Update skipped: tracked local changes detected in ${changedPathSummary}.`
          : 'Update skipped: tracked local changes detected.',
      })
    }

    setUpdateStatus({
      state: 'updating',
      trigger,
      branch: branch || UPDATE_BRANCH,
      localCommit,
      remoteCommit,
      message: 'Update found. Pulling latest changes and restarting Heimgeist...',
    })

    await runGitCommand(['pull', '--ff-only', UPDATE_REMOTE_URL, UPDATE_BRANCH], { timeout: 120000 })

    const { stdout: updatedStdout } = await runGitCommand(['rev-parse', 'HEAD'])
    const updatedLocalCommit = updatedStdout.trim()

    if (!updatedLocalCommit || updatedLocalCommit === localCommit) {
      return setUpdateStatus({
        state: 'up-to-date',
        trigger,
        branch: branch || UPDATE_BRANCH,
        localCommit,
        remoteCommit,
        message: 'No newer remote update was applied. Local checkout already contains the latest pulled state.',
      })
    }

    const result = setUpdateStatus({
      state: 'updated',
      trigger,
      branch: branch || UPDATE_BRANCH,
      localCommit: updatedLocalCommit || localCommit,
      remoteCommit,
      message: 'Update installed. Heimgeist restarts now.',
      restartScheduled: true,
    })

    scheduleAppRestart()
    return result
  } catch (error) {
    console.error('Failed to check for updates:', error)

    return setUpdateStatus({
      state: 'error',
      trigger,
      message: `Update check failed: ${error.message || String(error)}`,
    })
  }
}

function checkForUpdates(trigger = 'manual') {
  if (!activeUpdateCheck) {
    activeUpdateCheck = performUpdateCheck(trigger).finally(() => {
      activeUpdateCheck = null
    })
  }

  return activeUpdateCheck
}

async function createMainWindow() {
  console.log('Electron: creating main window')
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 720,
    minWidth: 680,
    minHeight: 300,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  applyUiScaleToWindow(mainWindow)

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.on('did-finish-load', () => {
    applyUiScaleToWindow(mainWindow)
  })

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error('Main window failed to load:', { errorCode, errorDescription, validatedURL })
  })

  mainWindow.on('focus', () => {
    mainWindow.webContents.send('window-focused')
  })

  if (is.dev && process.env.VITE_DEV_SERVER_URL) {
    console.log(`Electron: loading renderer ${process.env.VITE_DEV_SERVER_URL}`)
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
    applyDevToolsPreferenceToWindow(mainWindow)
  } else {
    console.log('Electron: loading bundled renderer')
    await mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
}

async function createSettingsWindow() {
  if (settingsWindow) {
    settingsWindow.focus()
    return
  }

  settingsWindow = new BrowserWindow({
    width: 800,
    height: 600,
    title: 'Settings',
    parent: mainWindow,
    modal: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  applyUiScaleToWindow(settingsWindow)

  settingsWindow.on('ready-to-show', () => {
    settingsWindow.show()
  })

  settingsWindow.webContents.on('did-finish-load', () => {
    applyUiScaleToWindow(settingsWindow)
  })

  settingsWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error('Settings window failed to load:', { errorCode, errorDescription, validatedURL })
  })

  settingsWindow.on('closed', () => {
    settingsWindow = null
  })

  if (is.dev && process.env.VITE_DEV_SERVER_URL) {
    await settingsWindow.loadURL(`${process.env.VITE_DEV_SERVER_URL}#/settings`)
    applyDevToolsPreferenceToWindow(settingsWindow)
  } else {
    await settingsWindow.loadFile(path.join(__dirname, '../dist/index.html'), { hash: '/settings' })
  }
}

app.whenReady().then(async () => {
  console.log('Electron: app ready')
  loadSettings()
  const startupUpdateResult = await checkForUpdates('startup')
  if (startupUpdateResult?.restartScheduled) {
    return
  }

  await createMainWindow()

  const menuTemplate = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Settings',
          accelerator: 'CmdOrCtrl+,',
          click: createSettingsWindow,
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'delete' },
        { type: 'separator' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forcereload' },
        { role: 'toggledevtools' },
        { type: 'separator' },
        { role: 'resetzoom' },
        { role: 'zoomin' },
        { role: 'zoomout' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ]

  const menu = Menu.buildFromTemplate(menuTemplate)
  Menu.setApplicationMenu(menu)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow()
  })
})

ipcMain.handle('get-settings', () => appSettings)
ipcMain.handle('get-update-status', () => lastUpdateCheckResult)
ipcMain.handle('check-for-updates', () => checkForUpdates('manual'))
ipcMain.handle('get-changelog-page', (event, page) => getChangelogPage(page))

ipcMain.handle('set-setting', (event, key, value) => {
  if (key === 'uiScale') {
    appSettings[key] = normalizeUiScale(value)
  } else if (key === 'embedModel') {
    appSettings[key] = normalizeEmbedModel(value)
  } else if (key === 'rerankModel') {
    appSettings[key] = normalizeRerankModel(value)
  } else if (key === 'chatModel' || key === 'visionModel') {
    appSettings[key] = normalizeModelName(value)
  } else if (key === 'transcriptionModel') {
    appSettings[key] = normalizeTranscriptionModel(value)
  } else if (key === 'openDevToolsOnStartup') {
    appSettings[key] = normalizeOpenDevToolsOnStartup(value)
  } else if (key === 'audioInputEnabled') {
    appSettings[key] = normalizeBooleanSetting(value)
  } else if (key === 'audioInputDeviceId') {
    appSettings[key] = String(value || '').trim()
  } else if (key === 'audioInputLanguage') {
    appSettings[key] = String(value || '').trim().toLowerCase()
  } else {
    appSettings[key] = value
  }
  saveSettings()
  if (key === 'uiScale') {
    applyUiScaleToAllWindows()
  } else if (key === 'openDevToolsOnStartup') {
    applyDevToolsPreferenceToAllWindows()
  }
  return true
})

ipcMain.handle('update-settings', (event, settings) => {
  appSettings = { ...appSettings, ...settings }
  appSettings.uiScale = normalizeUiScale(appSettings.uiScale)
  appSettings.chatModel = normalizeModelName(appSettings.chatModel)
  appSettings.visionModel = normalizeModelName(appSettings.visionModel)
  appSettings.embedModel = normalizeEmbedModel(appSettings.embedModel)
  appSettings.rerankModel = normalizeRerankModel(appSettings.rerankModel)
  appSettings.transcriptionModel = normalizeTranscriptionModel(appSettings.transcriptionModel)
  appSettings.openDevToolsOnStartup = normalizeOpenDevToolsOnStartup(appSettings.openDevToolsOnStartup)
  appSettings.audioInputEnabled = normalizeBooleanSetting(appSettings.audioInputEnabled)
  appSettings.audioInputDeviceId = String(appSettings.audioInputDeviceId || '').trim()
  appSettings.audioInputLanguage = String(appSettings.audioInputLanguage || '').trim().toLowerCase()
  saveSettings()
  if (Object.prototype.hasOwnProperty.call(settings, 'uiScale')) {
    applyUiScaleToAllWindows()
  }
  if (Object.prototype.hasOwnProperty.call(settings, 'openDevToolsOnStartup')) {
    applyDevToolsPreferenceToAllWindows()
  }
  return true
})

ipcMain.handle('pick-paths', async (event, options = {}) => {
  const dialogOptions = {
    properties: ['openFile', 'multiSelections'],
  }
  if (Array.isArray(options?.filters) && options.filters.length > 0) {
    dialogOptions.filters = options.filters
  }
  if (typeof options?.title === 'string' && options.title.trim()) {
    dialogOptions.title = options.title.trim()
  }

  const result = await dialog.showOpenDialog(mainWindow, {
    ...dialogOptions,
  })
  return result.canceled ? [] : result.filePaths
})

ipcMain.handle('open-path', async (event, filePath) => {
  if (!filePath) return false
  const err = await shell.openPath(filePath)
  return err === ''
})

ipcMain.on('open-external-link', (event, url) => {
  shell.openExternal(url)
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
