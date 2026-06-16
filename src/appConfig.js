export const API_URL_KEY = 'backendApiUrl'
export const COLOR_SCHEME_KEY = 'colorScheme'
export const WEBSEARCH_URL_KEY = 'websearch.searxUrl'
export const WEBSEARCH_ENGINES_KEY = 'websearch.engines'
export const CHAT_WORKFLOW_MAP_KEY = 'chat.workflowBySession'
export const DEFAULT_SEARX_URL = 'http://127.0.0.1:8888'
export const DEFAULT_BACKEND_API_URL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
export const MAX_IMAGE_ATTACHMENTS = 6
export const MAX_IMAGE_ATTACHMENT_BYTES = 20 * 1024 * 1024
export const MAX_AUDIO_RECORDING_MS = 5 * 60 * 1000
export const AUDIO_RECORDING_TICK_MS = 200
export const TOP_ALIGN_OFFSET = 48
export const BOTTOM_EPSILON = 24

export function resolveBackendApiUrl(settings) {
  return settings?.backendApiUrl || settings?.ollamaApiUrl || DEFAULT_BACKEND_API_URL
}

export function migrateLegacySearxUrl(value) {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  if (!trimmed) return DEFAULT_SEARX_URL
  if (trimmed === 'http://localhost:8888') return DEFAULT_SEARX_URL
  return trimmed
}
