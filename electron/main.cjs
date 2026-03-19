const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require('electron')
const path = require('path')
const { is } = require('@electron-toolkit/utils')
const fs = require('fs')

let mainWindow
let settingsWindow = null

const settingsFilePath = path.join(app.getPath('userData'), 'settings.json')
let appSettings = {}
const DEFAULT_UI_SCALE = 1
const MIN_UI_SCALE = 0.7
const MAX_UI_SCALE = 1.3

const defaultSettings = {
  ollamaApiUrl: 'http://127.0.0.1:8000',
  colorScheme: 'Default',
  uiScale: DEFAULT_UI_SCALE,
  chatModel: 'llama3',
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
      appSettings = { ...defaultSettings, ...JSON.parse(data) }
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
    fs.writeFileSync(settingsFilePath, JSON.stringify(appSettings, null, 2), 'utf8')
  } catch (error) {
    console.error('Failed to save settings:', error)
  }
}

async function createMainWindow() {
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

  mainWindow.on('focus', () => {
    mainWindow.webContents.send('window-focused')
  })

  if (is.dev && process.env.VITE_DEV_SERVER_URL) {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
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

app.whenReady().then(() => {
  loadSettings()
  createMainWindow()

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

ipcMain.handle('set-setting', (event, key, value) => {
  appSettings[key] = key === 'uiScale' ? normalizeUiScale(value) : value
  saveSettings()
  if (key === 'uiScale') {
    applyUiScaleToAllWindows()
  }
  return true
})

ipcMain.handle('update-settings', (event, settings) => {
  appSettings = { ...appSettings, ...settings }
  appSettings.uiScale = normalizeUiScale(appSettings.uiScale)
  saveSettings()
  if (Object.prototype.hasOwnProperty.call(settings, 'uiScale')) {
    applyUiScaleToAllWindows()
  }
  return true
})

ipcMain.handle('pick-paths', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'openDirectory', 'multiSelections'],
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
