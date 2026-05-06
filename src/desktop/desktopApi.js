const getElectronApi = () => window.electronAPI

function callElectronApi(methodName, ...args) {
  const method = getElectronApi()?.[methodName]
  if (typeof method !== 'function') {
    return undefined
  }
  return method(...args)
}

const desktopApi = {
  getSettings: () => callElectronApi('getSettings'),
  getUpdateStatus: () => callElectronApi('getUpdateStatus'),
  checkForUpdates: () => callElectronApi('checkForUpdates'),
  getChangelogPage: (page) => callElectronApi('getChangelogPage', page),
  setSetting: (key, value) => callElectronApi('setSetting', key, value),
  updateSettings: (settings) => callElectronApi('updateSettings', settings),
  pickPaths: (options) => callElectronApi('pickPaths', options),
  openPath: (filePath) => callElectronApi('openPath', filePath),
  openExternalLink: (event) => callElectronApi('openExternalLink', event),
  onWindowFocus: (callback) => callElectronApi('onWindowFocus', callback),
  offWindowFocus: (callback) => callElectronApi('offWindowFocus', callback),
}

export default desktopApi
