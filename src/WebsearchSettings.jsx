// src/WebsearchSettings.jsx
import React, { useEffect, useState } from 'react';
import { WEBSEARCH_ENGINE_OPTIONS, normalizeWebsearchEngines } from './websearchEngines'
import desktopApi from './desktop/desktopApi'

const BACKEND_API_URL_KEY = 'backendApiUrl'
const OLLAMA_API_URL_KEY = 'ollamaApiUrl'
const DEFAULT_BACKEND_API_URL = 'http://127.0.0.1:8000'
const DEFAULT_OLLAMA_API_URL = 'http://127.0.0.1:11434'

function resolveBackendApiUrl(settings) {
  return settings.backendApiUrl || settings.ollamaApiUrl || DEFAULT_BACKEND_API_URL
}

export default function WebsearchSettings({
  onBackendApiUrlChange,
  searxUrl,
  setSearxUrl,
  engines,
  setEngines,
}) {
  const [backendApiUrl, setBackendApiUrl] = useState('')
  const [ollamaApiUrl, setOllamaApiUrl] = useState('')

  useEffect(() => {
    let cancelled = false

    desktopApi.getSettings().then(settings => {
      if (cancelled) {
        return
      }
      setBackendApiUrl(resolveBackendApiUrl(settings))
      setOllamaApiUrl(settings.ollamaApiUrl || DEFAULT_OLLAMA_API_URL)
    })

    return () => {
      cancelled = true
    }
  }, [])

  const toggleEngine = (name) => {
    const set = new Set(engines || []);
    if (set.has(name)) set.delete(name); else set.add(name);
    setEngines(normalizeWebsearchEngines(Array.from(set)));
  }

  const handleBackendUrlChange = (event) => {
    const newUrl = event.target.value
    setBackendApiUrl(newUrl)
    desktopApi.setSetting(BACKEND_API_URL_KEY, newUrl)
    if (onBackendApiUrlChange) {
      onBackendApiUrlChange(newUrl)
    }
  }

  const handleOllamaUrlChange = (event) => {
    const newUrl = event.target.value
    setOllamaApiUrl(newUrl)
    desktopApi.setSetting(OLLAMA_API_URL_KEY, newUrl)
  }

return (
  <div className="settings-content-panel">
    <div className="setting-section">
      <h3>Heimgeist Backend URL</h3>
      <input
        type="text"
        className="input"
        value={backendApiUrl}
        onChange={handleBackendUrlChange}
        placeholder={`e.g., ${DEFAULT_BACKEND_API_URL}`}
      />
      <p className="setting-description">Internal UI requests like chats, sessions, and databases go to this URL.</p>
    </div>

    <div className="setting-section">
      <h3>Ollama URL</h3>
      <input
        type="text"
        className="input"
        value={ollamaApiUrl}
        onChange={handleOllamaUrlChange}
        placeholder={`e.g., ${DEFAULT_OLLAMA_API_URL}`}
      />
      <p className="setting-description">Heimgeist uses this URL to talk to Ollama for models and chat generation.</p>
    </div>

    <div className="setting-section">
      <h3>SearXNG URL</h3>
      <input
        type="text"
        className="input"
        value={searxUrl}
        onChange={e => setSearxUrl(e.target.value)}
        placeholder="e.g., http://127.0.0.1:8888"
      />
    </div>

    <div className="setting-section">
      <h3>Search Engines</h3>
      <div className="engine-grid">
        {WEBSEARCH_ENGINE_OPTIONS.map(({ value, label }) => (
          <label key={value} className="engine-row">
            <input
              type="checkbox"
              checked={Array.isArray(engines) ? engines.includes(value) : false}
              onChange={() => toggleEngine(value)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>
    </div>
  </div>
);
}
