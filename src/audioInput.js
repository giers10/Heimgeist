export const AUDIO_INPUT_ENABLED_KEY = 'audioInputEnabled'
export const AUDIO_INPUT_DEVICE_ID_KEY = 'audioInputDeviceId'
export const AUDIO_INPUT_LANGUAGE_KEY = 'audioInputLanguage'

export const AUDIO_INPUT_LANGUAGE_OPTIONS = [
  { value: '', label: 'Auto' },
  { value: 'de', label: 'German' },
  { value: 'en', label: 'English' },
  { value: 'fr', label: 'French' },
  { value: 'es', label: 'Spanish' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'nl', label: 'Dutch' },
  { value: 'pl', label: 'Polish' },
  { value: 'tr', label: 'Turkish' },
  { value: 'ru', label: 'Russian' },
  { value: 'ja', label: 'Japanese' },
  { value: 'zh', label: 'Chinese' },
]

const AUDIO_RECORDER_MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
  '',
]

export function supportsAudioInputCapture() {
  return (
    typeof navigator !== 'undefined' &&
    typeof window !== 'undefined' &&
    typeof window.MediaRecorder !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    Boolean(navigator.mediaDevices?.enumerateDevices)
  )
}

export function getAudioInputConstraints(deviceId = '') {
  const audio = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  }

  if (deviceId) {
    audio.deviceId = { exact: deviceId }
  }

  return { audio }
}

export function stopMediaStream(stream) {
  stream?.getTracks?.().forEach(track => track.stop())
}

export async function ensureAudioInputPermission() {
  if (!supportsAudioInputCapture()) {
    throw new Error('Microphone capture is not available in this environment.')
  }

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  stopMediaStream(stream)
}

export async function listAudioInputDevices() {
  if (!supportsAudioInputCapture()) {
    return []
  }

  const devices = await navigator.mediaDevices.enumerateDevices()
  return devices
    .filter(device => device.kind === 'audioinput')
    .map((device, index) => {
      const label = String(device.label || '').trim()
      return {
        deviceId: device.deviceId || `audio-input-${index + 1}`,
        label: label || `Microphone ${index + 1}`,
        hasLabel: Boolean(label),
      }
    })
}

export function getPreferredAudioRecorderMimeType() {
  if (typeof window === 'undefined' || typeof window.MediaRecorder === 'undefined') {
    return ''
  }

  for (const mimeType of AUDIO_RECORDER_MIME_CANDIDATES) {
    if (!mimeType) {
      return ''
    }
    if (
      typeof window.MediaRecorder.isTypeSupported !== 'function' ||
      window.MediaRecorder.isTypeSupported(mimeType)
    ) {
      return mimeType
    }
  }

  return ''
}
