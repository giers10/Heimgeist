
const { contextBridge, ipcRenderer } = require('electron')

// Expose a secure API to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  getSettings: () => ipcRenderer.invoke('get-settings'),
  getUpdateStatus: () => ipcRenderer.invoke('get-update-status'),
  checkForUpdates: () => ipcRenderer.invoke('check-for-updates'),
  setSetting: (key, value) => ipcRenderer.invoke('set-setting', key, value),
  updateSettings: (settings) => ipcRenderer.invoke('update-settings', settings),
  pickPaths: (options = {}) => ipcRenderer.invoke('pick-paths', options),
  openPath: (filePath) => ipcRenderer.invoke('open-path', filePath),
  openExternalLink: (event) => {
    event.preventDefault();
    const url = event.currentTarget.href;
    ipcRenderer.send('open-external-link', url);
  },
  onWindowFocus: (callback) => ipcRenderer.on('window-focused', callback)
})
