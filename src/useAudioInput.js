import { useEffect, useRef, useState } from 'react'
import { readFileAsDataUrl } from './attachments'
import {
  AUDIO_RECORDING_TICK_MS,
  MAX_AUDIO_RECORDING_MS,
} from './appConfig'
import { expectBackendJson, getErrorText, isAbortError } from './backendApi'
import {
  getAudioInputConstraints,
  getPreferredAudioRecorderMimeType,
  stopMediaStream,
  supportsAudioInputCapture,
} from './audioInput'

function appendTranscriptToComposer(currentInput, transcript) {
  const nextTranscript = String(transcript || '').trim()
  if (!nextTranscript) {
    return currentInput || ''
  }

  const existing = String(currentInput || '')
  if (!existing.trim()) {
    return nextTranscript
  }

  const separator = /[\s\n]$/.test(existing) ? '' : '\n'
  return `${existing}${separator}${nextTranscript}`
}

export function formatRecordingDuration(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export function useAudioInput({
  activeSidebarMode,
  audioInputDeviceId,
  audioInputEnabled,
  audioInputLanguage,
  backendApiUrl,
  isSending,
  setInput,
  textareaRef,
  transcriptionModel,
}) {
  const [audioInputRuntimeReady, setAudioInputRuntimeReady] = useState(true)
  const [audioInputRuntimeMessage, setAudioInputRuntimeMessage] = useState('')
  const [isRecordingAudio, setIsRecordingAudio] = useState(false)
  const [isTranscribingAudio, setIsTranscribingAudio] = useState(false)
  const [audioRecordingMs, setAudioRecordingMs] = useState(0)
  const audioRecorderRef = useRef(null)
  const audioStreamRef = useRef(null)
  const audioChunksRef = useRef([])
  const audioStopPromiseRef = useRef(null)
  const audioRecorderMimeTypeRef = useRef('')
  const audioTickTimerRef = useRef(null)
  const audioAutoStopTimerRef = useRef(null)
  const audioStartedAtRef = useRef(0)
  const audioTranscriptionAbortRef = useRef(null)

  function syncAudioInputRuntimeFromStartupStatus(status) {
    const whisperReady = Boolean(status?.whisper_model_available)
    if (whisperReady) {
      setAudioInputRuntimeReady(true)
      setAudioInputRuntimeMessage('')
      return
    }

    const modelName = status?.whisper_model || 'base'
    const reason = String(status?.whisper_error || '').trim()
    setAudioInputRuntimeReady(false)
    setAudioInputRuntimeMessage(reason || `Whisper ${modelName} is not available.`)
  }

  function clearAudioTimers() {
    if (audioTickTimerRef.current) {
      window.clearInterval(audioTickTimerRef.current)
      audioTickTimerRef.current = null
    }
    if (audioAutoStopTimerRef.current) {
      window.clearTimeout(audioAutoStopTimerRef.current)
      audioAutoStopTimerRef.current = null
    }
  }

  function cleanupAudioRecorder(stream = audioStreamRef.current) {
    clearAudioTimers()
    stopMediaStream(stream)
    if (audioStreamRef.current === stream) {
      audioStreamRef.current = null
    }
    audioRecorderRef.current = null
  }

  async function transcribeRecordedAudio(blob, mimeType) {
    if (!backendApiUrl) {
      throw new Error('The Heimgeist backend is not configured.')
    }

    const controller = new AbortController()
    audioTranscriptionAbortRef.current = controller
    setIsTranscribingAudio(true)

    try {
      const dataUrl = await readFileAsDataUrl(blob)
      const commaIndex = dataUrl.indexOf(',')
      if (commaIndex <= 5) {
        throw new Error('Recorded audio could not be encoded for upload.')
      }
      const header = dataUrl.slice(0, commaIndex)
      const payload = dataUrl.slice(commaIndex + 1)
      if (!/^data:[^,]+;base64$/i.test(header) || !payload) {
        throw new Error('Recorded audio could not be encoded for upload.')
      }
      const detectedMimeType = header
        .slice(5)
        .replace(/;base64$/i, '')
        .trim()

      const response = await fetch(`${backendApiUrl}/audio/transcribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          mime_type: mimeType || detectedMimeType || 'audio/webm',
          audio_base64: payload,
          model: transcriptionModel || null,
          language: audioInputLanguage || null,
        }),
      })
      const data = await expectBackendJson(response)
      const transcript = String(data?.text || '').trim()

      if (!transcript) {
        window.alert('No speech was detected. Try again and speak closer to the selected microphone.')
        return
      }

      setInput(prev => appendTranscriptToComposer(prev, transcript))
      requestAnimationFrame(() => textareaRef.current?.focus())
    } catch (error) {
      if (isAbortError(error)) {
        return
      }
      throw error
    } finally {
      if (audioTranscriptionAbortRef.current === controller) {
        audioTranscriptionAbortRef.current = null
      }
      setIsTranscribingAudio(false)
    }
  }

  async function stopAudioRecording(options = {}) {
    const { shouldTranscribe = true } = options
    const recorder = audioRecorderRef.current
    const stopPromise = audioStopPromiseRef.current
    const mimeType = audioRecorderMimeTypeRef.current || recorder?.mimeType || 'audio/webm'

    if (!recorder || !stopPromise) {
      return
    }

    setIsRecordingAudio(false)
    clearAudioTimers()

    try {
      if (recorder.state !== 'inactive') {
        recorder.stop()
      }

      const blob = await stopPromise
      if (!shouldTranscribe) {
        return
      }
      if (!blob || blob.size <= 0) {
        throw new Error('Recorded audio was empty.')
      }
      await transcribeRecordedAudio(blob, mimeType)
    } catch (error) {
      if (shouldTranscribe) {
        console.error('Failed to stop microphone recording', error)
        window.alert(`Microphone transcription failed: ${getErrorText(error)}`)
      }
    } finally {
      audioStopPromiseRef.current = null
      audioChunksRef.current = []
      audioRecorderMimeTypeRef.current = ''
      audioStartedAtRef.current = 0
      setAudioRecordingMs(0)
    }
  }

  async function startAudioRecording() {
    if (!audioInputEnabled || !audioInputRuntimeReady || isRecordingAudio || isTranscribingAudio || isSending) {
      return
    }
    if (!supportsAudioInputCapture()) {
      window.alert('Microphone capture is not available in this environment.')
      return
    }

    let stream = null
    try {
      stream = await navigator.mediaDevices.getUserMedia(getAudioInputConstraints(audioInputDeviceId))
    } catch (error) {
      console.error('Failed to access microphone', error)
      window.alert(`Microphone access failed: ${getErrorText(error)}`)
      return
    }

    const preferredMimeType = getPreferredAudioRecorderMimeType()
    let recorder = null
    try {
      recorder = preferredMimeType
        ? new MediaRecorder(stream, { mimeType: preferredMimeType })
        : new MediaRecorder(stream)
    } catch (error) {
      cleanupAudioRecorder(stream)
      console.error('Failed to create audio recorder', error)
      window.alert(`Microphone recording is not available: ${getErrorText(error)}`)
      return
    }

    audioStreamRef.current = stream
    audioRecorderRef.current = recorder
    audioChunksRef.current = []
    audioRecorderMimeTypeRef.current = recorder.mimeType || preferredMimeType || 'audio/webm'
    audioStartedAtRef.current = Date.now()
    setAudioRecordingMs(0)
    setIsRecordingAudio(true)

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        audioChunksRef.current.push(event.data)
      }
    }

    audioStopPromiseRef.current = new Promise((resolve, reject) => {
      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || audioRecorderMimeTypeRef.current || 'audio/webm',
        })
        cleanupAudioRecorder(stream)
        resolve(blob)
      }
      recorder.onerror = (event) => {
        cleanupAudioRecorder(stream)
        reject(event?.error || new Error('Audio recording failed.'))
      }
    })

    audioTickTimerRef.current = window.setInterval(() => {
      setAudioRecordingMs(Date.now() - audioStartedAtRef.current)
    }, AUDIO_RECORDING_TICK_MS)
    audioAutoStopTimerRef.current = window.setTimeout(() => {
      stopAudioRecording()
    }, MAX_AUDIO_RECORDING_MS)

    recorder.start()
  }

  async function toggleAudioRecording() {
    if (isTranscribingAudio) {
      return
    }
    if (isRecordingAudio) {
      await stopAudioRecording()
      return
    }
    await startAudioRecording()
  }

  useEffect(() => {
    if (audioInputEnabled || !isRecordingAudio) {
      return
    }
    stopAudioRecording({ shouldTranscribe: false })
  }, [audioInputEnabled, isRecordingAudio])

  useEffect(() => {
    if (audioInputRuntimeReady || !isRecordingAudio) {
      return
    }
    stopAudioRecording({ shouldTranscribe: false })
  }, [audioInputRuntimeReady, isRecordingAudio])

  useEffect(() => {
    if (activeSidebarMode === 'chats' || !isRecordingAudio) {
      return
    }
    stopAudioRecording()
  }, [activeSidebarMode, isRecordingAudio])

  useEffect(() => {
    return () => {
      audioTranscriptionAbortRef.current?.abort()
      clearAudioTimers()
      const recorder = audioRecorderRef.current
      if (recorder) {
        recorder.ondataavailable = null
        recorder.onstop = null
        recorder.onerror = null
        try {
          if (recorder.state !== 'inactive') {
            recorder.stop()
          }
        } catch {}
      }
      stopMediaStream(audioStreamRef.current)
    }
  }, [])

  return {
    audioInputRuntimeMessage,
    audioInputRuntimeReady,
    audioRecordingMs,
    isRecordingAudio,
    isTranscribingAudio,
    setAudioInputRuntimeMessage,
    setAudioInputRuntimeReady,
    syncAudioInputRuntimeFromStartupStatus,
    toggleAudioRecording,
  }
}
