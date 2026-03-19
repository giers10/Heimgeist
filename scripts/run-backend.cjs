const { spawn } = require('child_process')
const path = require('path')

const projectRoot = path.resolve(__dirname, '..')
const pythonBin = path.join(projectRoot, 'backend', '.venv', 'bin', 'python')

const env = { ...process.env }

try {
  const ffmpegPath = require('ffmpeg-static')
  if (ffmpegPath) {
    env.HEIMGEIST_FFMPEG_PATH = ffmpegPath
  }
} catch (_error) {
  // Fall back to system PATH if the static binary package is unavailable.
}

try {
  const ffprobeStatic = require('ffprobe-static')
  const ffprobePath = ffprobeStatic && ffprobeStatic.path
  if (ffprobePath) {
    env.HEIMGEIST_FFPROBE_PATH = ffprobePath
  }
} catch (_error) {
  // Fall back to system PATH if the static binary package is unavailable.
}

const child = spawn(
  pythonBin,
  ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload'],
  {
    cwd: projectRoot,
    env,
    stdio: 'inherit',
  }
)

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal)
    return
  }
  process.exit(code ?? 0)
})
