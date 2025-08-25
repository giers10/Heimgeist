
const { contextBridge, ipcRenderer } = require('electron')

// Expose a secure API to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  getSettings: () => ipcRenderer.invoke('get-settings'),
  setSetting: (key, value) => ipcRenderer.invoke('set-setting', key, value),
  updateSettings: (settings) => ipcRenderer.invoke('update-settings', settings),
  openExternalLink: (event) => {
    event.preventDefault();
    const url = event.currentTarget.href;
    ipcRenderer.send('open-external-link', url);
  },
  onWindowFocus: (callback) => ipcRenderer.on('window-focused', callback)
})
