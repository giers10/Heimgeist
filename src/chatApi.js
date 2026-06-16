import { expectBackendJson } from './backendApi'

export async function fetchModelCapabilities(apiBase, modelName, signal) {
  const response = await fetch(
    `${apiBase}/models/capabilities?name=${encodeURIComponent(modelName)}`,
    { signal }
  )
  return expectBackendJson(response)
}

export async function fetchStartupOllamaStatus(apiBase) {
  const response = await fetch(`${apiBase}/ollama/startup-status`)
  return expectBackendJson(response)
}

export async function prepareStartupModels(apiBase) {
  const response = await fetch(`${apiBase}/startup/prepare-models`, { method: 'POST' })
  return expectBackendJson(response)
}

export async function fetchLocalLibraryContext(apiBase, prompt, signal) {
  const response = await fetch(`${apiBase}/knowledge/context`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      prompt,
      top_k: 5,
    }),
  })
  const data = await response.json()
  return {
    contextBlock: typeof data?.context_block === 'string' && data.context_block.trim()
      ? data.context_block.trim()
      : null,
    sources: Array.isArray(data?.sources) ? data.sources : [],
  }
}

export async function fetchWebSearchContext({
  apiBase,
  engines,
  historyLimit = 8,
  messages,
  model,
  prompt,
  searxUrl,
  signal,
}) {
  const response = await fetch(`${apiBase}/websearch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      prompt,
      model,
      messages,
      history_limit: historyLimit,
      searx_url: searxUrl || null,
      engines: Array.isArray(engines) ? engines : null,
    }),
  })
  const data = await response.json()
  return {
    contextBlock: typeof data?.context_block === 'string' && data.context_block.trim()
      ? data.context_block.trim()
      : null,
    sources: Array.isArray(data?.sources) ? data.sources : [],
  }
}

export function postChatMessage({
  apiBase,
  attachments,
  enrichedMessage,
  message,
  model,
  sessionId,
  signal,
  sources,
  stream,
  transcriptionModel,
  visionModel,
}) {
  return fetch(`${apiBase}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      session_id: sessionId,
      model,
      message,
      enriched_message: enrichedMessage,
      stream,
      sources: sources || [],
      attachments,
      vision_model: visionModel || null,
      transcription_model: transcriptionModel || null,
    }),
  })
}

export function postRegenerateMessage({
  apiBase,
  enrichedMessage,
  index,
  model,
  sessionId,
  signal,
  sources,
  stream,
  transcriptionModel,
  visionModel,
}) {
  return fetch(`${apiBase}/sessions/${sessionId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      index,
      model,
      stream,
      enriched_message: enrichedMessage,
      sources: sources || [],
      vision_model: visionModel || null,
      transcription_model: transcriptionModel || null,
    }),
  })
}

export function requestGeneratedTitle({ apiBase, message, model, sessionId }) {
  return fetch(`${apiBase}/generate-title`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      model,
    }),
  })
}
