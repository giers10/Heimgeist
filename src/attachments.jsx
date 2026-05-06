const CHAT_IMAGE_EXTENSION_LIST = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tif', '.tiff', '.heic', '.avif']
const CHAT_FILE_EXTENSION_LIST = [
  '.pdf', '.html', '.htm', '.txt', '.md', '.rst', '.epub',
  '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.aac',
  '.mp4', '.mkv', '.mov', '.webm', '.avi', '.ts',
]
const CHAT_IMAGE_EXTENSION_SET = new Set(CHAT_IMAGE_EXTENSION_LIST)
const CHAT_FILE_EXTENSION_SET = new Set(CHAT_FILE_EXTENSION_LIST)
const CHAT_AUDIO_EXTENSION_SET = new Set(['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.aac'])
const CHAT_VIDEO_EXTENSION_SET = new Set(['.mp4', '.mkv', '.mov', '.webm', '.avi', '.ts'])
const CHAT_TEXT_EXTENSION_SET = new Set(['.txt', '.md', '.rst'])

export const CHAT_FILE_PICKER_FILTERS = [
  { name: 'Documents and Media', extensions: CHAT_FILE_EXTENSION_LIST.map(extension => extension.slice(1)) },
]

export function getFileExtension(value) {
  const text = String(value || '').trim()
  const match = /(?:\.([A-Za-z0-9]+))$/.exec(text)
  return match ? `.${match[1].toLowerCase()}` : ''
}

export function getFileName(value, fallback = 'attachment') {
  const text = String(value || '').trim()
  if (!text) return fallback
  const parts = text.split(/[\\/]/)
  return parts[parts.length - 1] || fallback
}

export function guessMimeTypeFromName(value) {
  const extension = getFileExtension(value)
  switch (extension) {
    case '.pdf': return 'application/pdf'
    case '.html':
    case '.htm': return 'text/html'
    case '.txt': return 'text/plain'
    case '.md': return 'text/markdown'
    case '.rst': return 'text/x-rst'
    case '.epub': return 'application/epub+zip'
    case '.mp3': return 'audio/mpeg'
    case '.wav': return 'audio/wav'
    case '.m4a': return 'audio/mp4'
    case '.flac': return 'audio/flac'
    case '.ogg': return 'audio/ogg'
    case '.opus': return 'audio/opus'
    case '.aac': return 'audio/aac'
    case '.mp4': return 'video/mp4'
    case '.mkv': return 'video/x-matroska'
    case '.mov': return 'video/quicktime'
    case '.webm': return 'video/webm'
    case '.avi': return 'video/x-msvideo'
    case '.ts': return 'video/mp2t'
    default: return ''
  }
}

export function attachmentIsImage(attachment) {
  return Boolean(attachment?.data_url) || String(attachment?.kind || '').toLowerCase() === 'image'
}

export function attachmentIsFile(attachment) {
  return Boolean(attachment) && !attachmentIsImage(attachment)
}

export function isSupportedChatFilePath(value) {
  return CHAT_FILE_EXTENSION_SET.has(getFileExtension(value))
}

export function isSupportedChatImagePath(value) {
  return CHAT_IMAGE_EXTENSION_SET.has(getFileExtension(value))
}

export function isImageFile(file) {
  if (!file) return false
  if (typeof file.type === 'string' && file.type.toLowerCase().startsWith('image/')) {
    return true
  }
  return /\.(png|jpe?g|gif|bmp|webp|tiff?|heic|avif)$/i.test(file.name || '')
}

export function isSupportedChatFile(file) {
  if (!file || isImageFile(file)) return false
  return isSupportedChatFilePath(file.name || '')
}

export function getAttachmentDisplayName(attachment, fallback = 'attachment') {
  return String(attachment?.name || '').trim() || getFileName(attachment?.source_path, fallback)
}

export function getFileAttachmentBadge(attachment) {
  const mimeType = String(attachment?.mime_type || '').toLowerCase()
  const extension = getFileExtension(attachment?.name || attachment?.source_path || '')
  if (mimeType.startsWith('audio/') || CHAT_AUDIO_EXTENSION_SET.has(extension)) return 'AUDIO'
  if (mimeType.startsWith('video/') || CHAT_VIDEO_EXTENSION_SET.has(extension)) return 'VIDEO'
  if (CHAT_TEXT_EXTENSION_SET.has(extension)) return 'TEXT'
  return 'DOC'
}

export function buildComposerAttachmentId(prefix = 'attachment') {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function buildComposerFileAttachment({ sourcePath, name, mimeType, size }) {
  const displayName = String(name || '').trim() || getFileName(sourcePath, 'file')
  return {
    id: buildComposerAttachmentId('file'),
    kind: 'file',
    name: displayName,
    mime_type: String(mimeType || '').trim() || guessMimeTypeFromName(displayName || sourcePath),
    source_path: sourcePath,
    size: Number.isFinite(Number(size)) ? Number(size) : undefined,
  }
}

export function hasFilePayload(event) {
  const types = Array.from(event?.dataTransfer?.types || [])
  return types.includes('Files')
}

export function eventHasImageFiles(event) {
  const items = Array.from(event?.dataTransfer?.items || [])
  if (items.length > 0) {
    return items.some(item => item.kind === 'file' && isImageFile(item.getAsFile?.()))
  }
  const files = Array.from(event?.dataTransfer?.files || [])
  return files.some(isImageFile)
}

export function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error || new Error(`Failed to read ${file?.name || 'image'}`))
    reader.onload = () => resolve(String(reader.result || ''))
    reader.readAsDataURL(file)
  })
}

export function AttachmentStrip({ attachments, className = '', removable = false, onRemove }) {
  if (!Array.isArray(attachments) || attachments.length === 0) {
    return null
  }

  return (
    <div className={`attachment-strip ${className}`.trim()}>
      {attachments.map((attachment, index) => {
        const isImage = attachmentIsImage(attachment)
        const keySeed = isImage
          ? attachment?.data_url?.length || attachment?.name || index
          : attachment?.source_path || attachment?.name || index
        const key = attachment.id || `${getAttachmentDisplayName(attachment, 'attachment')}-${index}-${keySeed}`

        if (isImage) {
          const src = attachment?.data_url
          if (!src) return null
          return (
            <div key={key} className="image-attachment-card">
              {removable && (
                <button
                  type="button"
                  className="attachment-remove"
                  onClick={() => onRemove?.(attachment.id)}
                  aria-label={`Remove ${getAttachmentDisplayName(attachment, 'image')}`}
                  title="Remove attachment"
                >
                  ×
                </button>
              )}
              <img
                className="image-attachment-thumb"
                src={src}
                alt={getAttachmentDisplayName(attachment, `Attachment ${index + 1}`)}
                loading="lazy"
              />
            </div>
          )
        }

        const label = getAttachmentDisplayName(attachment, `Attachment ${index + 1}`)
        return (
          <div
            key={key}
            className="file-attachment-card"
            title={attachment?.source_path || label}
          >
            {removable && (
              <button
                type="button"
                className="attachment-remove attachment-remove--file"
                onClick={() => onRemove?.(attachment.id)}
                aria-label={`Remove ${label}`}
                title="Remove attachment"
              >
                ×
              </button>
            )}
            <span className="file-attachment-badge">{getFileAttachmentBadge(attachment)}</span>
            <span className="file-attachment-name">{label}</span>
          </div>
        )
      })}
    </div>
  )
}
