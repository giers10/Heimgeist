const { execFileSync, spawnSync } = require('child_process')
const fs = require('fs')
const path = require('path')

const projectRoot = path.resolve(__dirname, '..')
const pythonBin = path.join(projectRoot, 'backend', '.venv', 'bin', 'python')
const binariesDir = path.join(projectRoot, 'src-tauri', 'binaries')
const targetTriple = resolveTargetTriple()
const executableExtension = process.platform === 'win32' ? '.exe' : ''
const backendOutputName = `heimgeist-backend-${targetTriple}${executableExtension}`
const backendOutputPath = path.join(binariesDir, backendOutputName)
const ffmpegOutputPath = outputPathForBaseName('heimgeist-ffmpeg')
const ffprobeOutputPath = outputPathForBaseName('heimgeist-ffprobe')

function resolveTargetTriple() {
  const output = execFileSync('rustc', ['-vV'], { encoding: 'utf8' })
  const match = output.match(/^host:\s+(\S+)/m)
  if (!match) {
    throw new Error('Could not determine Rust target triple from rustc -vV output.')
  }
  return match[1]
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: 'inherit',
    env: process.env,
    ...options,
  })
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status}`)
  }
}

function outputPathForBaseName(outputBaseName) {
  return path.join(binariesDir, `${outputBaseName}-${targetTriple}${executableExtension}`)
}

function ensurePython() {
  if (!fs.existsSync(pythonBin)) {
    throw new Error(`Missing backend Python virtualenv at ${pythonBin}. Run ./run.sh or create backend/.venv first.`)
  }
}

function ensurePyInstaller() {
  const result = spawnSync(pythonBin, ['-c', 'import PyInstaller'], {
    cwd: projectRoot,
    stdio: 'ignore',
  })
  if (result.status === 0) {
    return
  }

  console.log('Installing PyInstaller into backend/.venv for sidecar packaging.')
  run(pythonBin, ['-m', 'pip', 'install', '--upgrade', 'pyinstaller'])
}

function copyExecutable(sourcePath, outputBaseName) {
  if (!sourcePath || !fs.existsSync(sourcePath)) {
    return null
  }
  fs.mkdirSync(binariesDir, { recursive: true })
  const outputPath = outputPathForBaseName(outputBaseName)
  fs.copyFileSync(sourcePath, outputPath)
  fs.chmodSync(outputPath, 0o755)
  return outputPath
}

function copyRequiredFfmpeg() {
  const ffmpegPath = require('ffmpeg-static')
  const outputPath = copyExecutable(ffmpegPath, 'heimgeist-ffmpeg')
  if (!outputPath) {
    throw new Error('Could not bundle ffmpeg-static: package did not provide a binary path.')
  }
  console.log(`Bundled ffmpeg helper: ${outputPath}`)
}

function copyRequiredFfprobe() {
  const ffprobeStatic = require('ffprobe-static')
  const ffprobePath = ffprobeStatic && ffprobeStatic.path
  if (!ffprobePath || !fs.existsSync(ffprobePath)) {
    throw new Error('Could not bundle ffprobe-static: package did not provide a binary path.')
  }

  const probe = spawnSync(ffprobePath, ['-version'], {
    encoding: 'utf8',
    timeout: 20000,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  if (probe.status !== 0) {
    throw new Error(
      `Could not bundle ffprobe-static: ${ffprobePath} did not run successfully on this machine.`,
    )
  }

  const outputPath = copyExecutable(ffprobePath, 'heimgeist-ffprobe')
  console.log(`Bundled ffprobe helper: ${outputPath}`)
}

function writeBinariesIgnore() {
  fs.mkdirSync(binariesDir, { recursive: true })
  const ignorePath = path.join(binariesDir, '.gitignore')
  if (!fs.existsSync(ignorePath)) {
    fs.writeFileSync(ignorePath, '*\n!.gitignore\n', 'utf8')
  }
}

function buildBackendSidecar() {
  const pyinstallerRoot = path.join('/tmp', `heimgeist-pyinstaller-${targetTriple}`)
  const distPath = path.join(pyinstallerRoot, 'dist')
  const workPath = path.join(pyinstallerRoot, 'build')
  const specPath = path.join(pyinstallerRoot, 'spec')

  fs.rmSync(pyinstallerRoot, { recursive: true, force: true })
  fs.rmSync(backendOutputPath, { force: true })
  fs.rmSync(ffmpegOutputPath, { force: true })
  fs.rmSync(ffprobeOutputPath, { force: true })
  fs.mkdirSync(distPath, { recursive: true })
  fs.mkdirSync(workPath, { recursive: true })
  fs.mkdirSync(specPath, { recursive: true })

  run(pythonBin, [
    '-m',
    'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--name',
    'heimgeist-backend',
    '--distpath',
    distPath,
    '--workpath',
    workPath,
    '--specpath',
    specPath,
    '--paths',
    projectRoot,
    '--collect-submodules',
    'backend.rag',
    '--hidden-import',
    'backend.main',
    '--hidden-import',
    'backend.sidecar_main',
    '--hidden-import',
    'uvicorn.logging',
    '--hidden-import',
    'uvicorn.loops.auto',
    '--hidden-import',
    'uvicorn.protocols.http.auto',
    '--hidden-import',
    'uvicorn.protocols.websockets.auto',
    '--hidden-import',
    'uvicorn.lifespan.on',
    path.join(projectRoot, 'backend', 'sidecar_main.py'),
  ])

  const builtBinary = path.join(distPath, `heimgeist-backend${executableExtension}`)
  if (!fs.existsSync(builtBinary)) {
    throw new Error(`PyInstaller did not produce expected binary at ${builtBinary}`)
  }

  fs.mkdirSync(binariesDir, { recursive: true })
  fs.copyFileSync(builtBinary, backendOutputPath)
  fs.chmodSync(backendOutputPath, 0o755)
  console.log(`Built backend sidecar: ${backendOutputPath}`)
}

ensurePython()
ensurePyInstaller()
writeBinariesIgnore()
buildBackendSidecar()
copyRequiredFfmpeg()
copyRequiredFfprobe()
