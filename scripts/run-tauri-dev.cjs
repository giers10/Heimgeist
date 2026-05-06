const { spawn } = require('child_process')
const fs = require('fs')
const http = require('http')
const path = require('path')
const waitOn = require('wait-on')

const projectRoot = path.resolve(__dirname, '..')
const backendHealthUrl = 'http://127.0.0.1:8000/health'
const backendHealthResource = `http-get://${backendHealthUrl.replace(/^https?:\/\//, '')}`
const tauriCliPackage = '@tauri-apps/cli@2.11.0'
const localTauriBin = path.join(
  projectRoot,
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'tauri.cmd' : 'tauri',
)

let backendProcess = null
let tauriProcess = null
let shuttingDown = false

function isBackendHealthy(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const request = http.get(backendHealthUrl, { timeout: timeoutMs }, (response) => {
      response.resume()
      resolve(response.statusCode >= 200 && response.statusCode < 300)
    })

    request.on('timeout', () => {
      request.destroy()
      resolve(false)
    })
    request.on('error', () => resolve(false))
  })
}

function spawnBackend() {
  backendProcess = spawn(process.execPath, [path.join('scripts', 'run-backend.cjs')], {
    cwd: projectRoot,
    env: process.env,
    stdio: 'inherit',
  })

  backendProcess.on('exit', (code, signal) => {
    if (!shuttingDown && code !== 0) {
      console.error(`Tauri dev wrapper: backend exited before shutdown (${signal || code}).`)
    }
  })
}

function resolveTauriCommand() {
  if (fs.existsSync(localTauriBin)) {
    return { command: localTauriBin, args: ['dev'] }
  }

  return {
    command: process.platform === 'win32' ? 'npm.cmd' : 'npm',
    args: ['exec', '--yes', '--package', tauriCliPackage, '--', 'tauri', 'dev'],
  }
}

function stopChild(child, signal = 'SIGTERM') {
  if (child && !child.killed) {
    child.kill(signal)
  }
}

function stopSpawnedBackend(signal = 'SIGTERM') {
  if (backendProcess) {
    stopChild(backendProcess, signal)
  }
}

function handleShutdown(signal) {
  if (shuttingDown) {
    return
  }

  shuttingDown = true
  stopChild(tauriProcess, signal)
  stopSpawnedBackend(signal)
  setTimeout(() => process.exit(0), 750).unref()
}

async function waitForBackend() {
  await waitOn({
    resources: [backendHealthResource],
    proxy: false,
    timeout: 120000,
  })
}

async function launchTauri() {
  const { command, args } = resolveTauriCommand()
  const env = {
    ...process.env,
    HEIMGEIST_DEV_WRAPPER: '1',
  }

  tauriProcess = spawn(command, args, {
    cwd: projectRoot,
    env,
    stdio: 'inherit',
  })

  tauriProcess.on('exit', (code, signal) => {
    shuttingDown = true
    stopSpawnedBackend()

    if (signal) {
      process.exit(signal === 'SIGINT' || signal === 'SIGTERM' ? 0 : 1)
      return
    }

    process.exit(code ?? 0)
  })
}

async function main() {
  process.on('SIGINT', () => handleShutdown('SIGINT'))
  process.on('SIGTERM', () => handleShutdown('SIGTERM'))

  if (await isBackendHealthy()) {
    console.log('Tauri dev wrapper: FastAPI backend already healthy on 127.0.0.1:8000; reusing it.')
  } else {
    console.log('Tauri dev wrapper: starting FastAPI backend from scripts/run-backend.cjs.')
    spawnBackend()
    await waitForBackend()
  }

  console.log('Tauri dev wrapper: backend ready, launching Tauri on the Tauri renderer port.')
  await launchTauri()
}

main().catch((error) => {
  shuttingDown = true
  stopChild(tauriProcess)
  stopSpawnedBackend()
  console.error('Failed to launch Tauri dev wrapper:', error)
  process.exit(1)
})
