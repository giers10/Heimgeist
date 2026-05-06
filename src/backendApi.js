export function isAbortError(error) {
  return error?.name === 'AbortError'
}

export function getErrorText(error) {
  if (error instanceof Error && error.message) return error.message
  return String(error)
}

export async function readBackendErrorText(response) {
  const bodyText = await response.text().catch(() => '')
  if (bodyText) {
    try {
      const data = JSON.parse(bodyText)
      if (typeof data?.detail === 'string' && data.detail.trim()) {
        return data.detail.trim()
      }
      if (typeof data?.message === 'string' && data.message.trim()) {
        return data.message.trim()
      }
    } catch {}
    return bodyText.trim()
  }
  return `HTTP ${response.status}`
}

export async function expectBackendJson(response) {
  const data = await response.json().catch(() => null)
  if (response.ok) return data
  const detail = typeof data?.detail === 'string'
    ? data.detail
    : (typeof data?.message === 'string' ? data.message : '')
  throw new Error(detail || `HTTP ${response.status}`)
}
