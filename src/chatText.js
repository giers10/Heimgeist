export function sanitizeChatTitle(title) {
  let cleanedTitle = String(title || '')
    .replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '')
    .trim()

  let previousTitle = null
  while (cleanedTitle && cleanedTitle !== previousTitle) {
    previousTitle = cleanedTitle
    cleanedTitle = cleanedTitle
      .replace(/^\s*#+\s*/, '')
      .replace(/^\s*\*{1,2}\s*/, '')
      .replace(/\s*\*{1,2}\s*$/, '')
      .trim()
  }

  return cleanedTitle.replace(/\s+/g, ' ').trim()
}

function appendOllamaErrorHint(text, marker, block) {
  if (text.includes(marker)) {
    return text
  }
  return `${text}\n\n${block}`
}

export function enrichOllamaErrorText(text) {
  const raw = String(text || '')
  const trimmed = raw.trim()
  if (!trimmed) {
    return raw
  }

  const lower = trimmed.toLowerCase()
  const looksLikeOllamaError = (
    lower.startsWith('error:') ||
    lower.startsWith('ollama error:') ||
    lower.includes("client error '") ||
    lower.includes("server error '") ||
    (lower.includes('http') && lower.includes('error')) ||
    lower.includes('unknown model architecture') ||
    lower.includes('out of memory')
  )

  if (!looksLikeOllamaError) {
    return raw
  }

  if (lower.includes('unknown model architecture')) {
    return appendOllamaErrorHint(
      raw,
      '[ERROR - Unsupported Model]',
      '[ERROR - Unsupported Model]\nThis Ollama version does not support the model.\nUpdate Ollama.'
    )
  }
  if (lower.includes('out of memory')) {
    return appendOllamaErrorHint(
      raw,
      '[ERROR - Out of Memory]',
      '[ERROR - Out of Memory]\nThe model is too large for available memory.\nUse a smaller or quantized model.'
    )
  }
  if (lower.includes('502')) {
    return appendOllamaErrorHint(
      raw,
      '[ERROR 502 - Bad Gateway]',
      '[ERROR 502 - Bad Gateway]\nOllama did not return a valid response.\nTry restarting or updating Ollama.'
    )
  }
  if (lower.includes('500')) {
    return appendOllamaErrorHint(
      raw,
      '[ERROR 500 - Internal Server Error]',
      "[ERROR 500 - Internal Server Error]\nOllama crashed while processing the request.\nCheck 'ollama logs' and memory usage."
    )
  }
  if (lower.includes('404')) {
    return appendOllamaErrorHint(
      raw,
      '[ERROR 404 - Not Found]',
      "[ERROR 404 - Not Found]\nThe model or endpoint was not found.\nCheck the model name or run 'ollama pull <model>'."
    )
  }
  if (lower.includes('400')) {
    return appendOllamaErrorHint(
      raw,
      '[ERROR 400 - Bad Request]',
      '[ERROR 400 - Bad Request]\nThe request sent to Ollama was invalid.\nCheck parameters or payload format.'
    )
  }
  return raw
}

export function splitThinkBlocks(text) {
  if (!text) return { think: null, answer: '' }

  const openTagRe = /<think(?:ing)?>/i
  const closeTagRe = /<\/think(?:ing)?>/i

  const openMatch = text.match(openTagRe)

  if (!openMatch) {
    return { think: null, answer: text }
  }

  const openTagIndex = openMatch.index
  const openTagLength = openMatch[0].length

  const answerPartBeforeThink = text.substring(0, openTagIndex).trim()
  const contentAfterOpenTag = text.substring(openTagIndex + openTagLength)

  const closeMatch = contentAfterOpenTag.match(closeTagRe)

  let thinkInner = null
  let finalAnswer = answerPartBeforeThink

  if (closeMatch) {
    thinkInner = contentAfterOpenTag.substring(0, closeMatch.index).trim()
    finalAnswer += contentAfterOpenTag.substring(closeMatch.index + closeMatch[0].length)
  } else {
    thinkInner = contentAfterOpenTag.trim()
  }

  return { think: thinkInner || null, answer: finalAnswer.trim() }
}
