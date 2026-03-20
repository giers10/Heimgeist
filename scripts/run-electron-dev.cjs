const { spawn } = require('child_process')
const path = require('path')
const waitOn = require('wait-on')

const projectRoot = path.resolve(__dirname, '..')
const electronBinary = require('electron')
const relaunchExitCode = 75
const startupResources = ['http://localhost:5173', 'tcp:8000']
const env = {
  ...process.env,
  VITE_DEV_SERVER_URL: process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173',
  HEIMGEIST_DEV_WRAPPER: '1',
  HEIMGEIST_DEV_RELAUNCH_CODE: String(relaunchExitCode),
}

async function waitForDependencies() {
  await waitOn({
    resources: startupResources,
    timeout: 120000,
  })
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

function runElectronOnce() {
  return new Promise((resolve) => {
    const child = spawn(electronBinary, ['.'], {
      cwd: projectRoot,
      env,
      stdio: 'inherit',
    })

    child.on('exit', (code, signal) => {
      resolve({ code, signal })
    })
  })
}

async function main() {
  await waitForDependencies()

  while (true) {
    const { code, signal } = await runElectronOnce()

    if (signal) {
      process.kill(process.pid, signal)
      return
    }

    if (code === relaunchExitCode) {
      await sleep(750)
      await waitForDependencies()
      continue
    }

    process.exit(code ?? 0)
  }
}

main().catch((error) => {
  console.error('Failed to launch Electron dev wrapper:', error)
  process.exit(1)
})
