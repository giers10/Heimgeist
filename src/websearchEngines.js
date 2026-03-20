export const WEBSEARCH_ENGINE_OPTIONS = [
  { value: 'google', label: 'Google' },
  { value: 'bing', label: 'Bing' },
  { value: 'yahoo', label: 'Yahoo' },
  { value: 'duckduckgo', label: 'DuckDuckGo' },
  { value: 'brave', label: 'Brave' },
  { value: 'github', label: 'GitHub' },
  { value: 'stack_overflow', label: 'Stack Overflow' },
  { value: 'reddit', label: 'Reddit' },
  { value: 'arxiv', label: 'arXiv' },
]

export const DEFAULT_WEBSEARCH_ENGINES = [
  'google',
  'bing',
  'yahoo',
  'duckduckgo',
  'brave',
]

const WEBSEARCH_ENGINE_ALIASES = {
  stackoverflow: 'stack_overflow',
}

const ENGINE_ORDER = new Map(
  WEBSEARCH_ENGINE_OPTIONS.map((option, index) => [option.value, index])
)

export function normalizeWebsearchEngineId(value) {
  if (typeof value !== 'string') return null

  const trimmed = value.trim().toLowerCase()
  if (!trimmed) return null

  const canonical = WEBSEARCH_ENGINE_ALIASES[trimmed] ?? trimmed
  return ENGINE_ORDER.has(canonical) ? canonical : null
}

export function normalizeWebsearchEngines(value) {
  if (!Array.isArray(value)) return []

  const seen = new Set()
  const normalized = []

  for (const entry of value) {
    const canonical = normalizeWebsearchEngineId(entry)
    if (!canonical || seen.has(canonical)) continue
    seen.add(canonical)
    normalized.push(canonical)
  }

  normalized.sort((left, right) => ENGINE_ORDER.get(left) - ENGINE_ORDER.get(right))
  return normalized
}

export function loadStoredWebsearchEngines(rawValue) {
  if (typeof rawValue !== 'string') return [...DEFAULT_WEBSEARCH_ENGINES]

  try {
    const parsed = JSON.parse(rawValue)
    if (!Array.isArray(parsed)) return [...DEFAULT_WEBSEARCH_ENGINES]

    const normalized = normalizeWebsearchEngines(parsed)
    if (parsed.length > 0 && normalized.length === 0) {
      return [...DEFAULT_WEBSEARCH_ENGINES]
    }

    return normalized
  } catch {
    return [...DEFAULT_WEBSEARCH_ENGINES]
  }
}
