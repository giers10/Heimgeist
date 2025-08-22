
const { contextBridge, ipcRenderer } = require('electron')

// Expose a secure API to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  getSettings: () => ipcRenderer.invoke('get-settings'),
  setSetting: (key, value) => ipcRenderer.invoke('set-setting', key, value)
})
