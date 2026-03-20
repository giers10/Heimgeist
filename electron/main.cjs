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

const defaultSettings = {
  backendApiUrl: DEFAULT_BACKEND_API_URL,
  ollamaApiUrl: DEFAULT_OLLAMA_API_URL,
  embedModel: DEFAULT_EMBED_MODEL,
  colorScheme: 'Default',
  uiScale: DEFAULT_UI_SCALE,
  chatModel: 'llama3',
}

function normalizeEmbedModel(value) {
  const trimmed = String(value || '').trim().toLowerCase()
  if (trimmed === 'bge' || trimmed === 'bge-m3' || trimmed === BGE_EMBED_MODEL) {
    return BGE_EMBED_MODEL
  }
  return DEFAULT_EMBED_MODEL
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

  nextSettings.backendApiUrl = String(nextSettings.backendApiUrl || '').trim()
  nextSettings.ollamaApiUrl = String(nextSettings.ollamaApiUrl || '').trim()
  nextSettings.embedModel = normalizeEmbedModel(nextSettings.embedModel)

  return { nextSettings, migrated }
}

function normalizeUiScale(value) {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return DEFAULT_UI_SCALE
  }

  return Math.min(MAX_UI_SCALE, Math.max(MIN_UI_SCALE, Math.round(numericValue * 100) / 100))
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
    mainWindow.webContents.openDevTools({ mode: 'detach' })
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
    settingsWindow.webContents.openDevTools({ mode: 'detach' })
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

ipcMain.handle('set-setting', (event, key, value) => {
  if (key === 'uiScale') {
    appSettings[key] = normalizeUiScale(value)
  } else if (key === 'embedModel') {
    appSettings[key] = normalizeEmbedModel(value)
  } else {
    appSettings[key] = value
  }
  saveSettings()
  if (key === 'uiScale') {
    applyUiScaleToAllWindows()
  }
  return true
})

ipcMain.handle('update-settings', (event, settings) => {
  appSettings = { ...appSettings, ...settings }
  appSettings.uiScale = normalizeUiScale(appSettings.uiScale)
  appSettings.embedModel = normalizeEmbedModel(appSettings.embedModel)
  saveSettings()
  if (Object.prototype.hasOwnProperty.call(settings, 'uiScale')) {
    applyUiScaleToAllWindows()
  }
  return true
})

ipcMain.handle('pick-paths', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
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
