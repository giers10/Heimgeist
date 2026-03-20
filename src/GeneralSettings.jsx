import React, { useState, useEffect } from 'react';

const BACKEND_API_URL_KEY = 'backendApiUrl';
const OLLAMA_API_URL_KEY = 'ollamaApiUrl';
const MODEL_KEY = 'chatModel';
const STREAM_KEY = 'streamOutput';
const DEFAULT_BACKEND_API_URL = 'http://127.0.0.1:8000';
const DEFAULT_OLLAMA_API_URL = 'http://127.0.0.1:11434';

function resolveBackendApiUrl(settings) {
  return settings.backendApiUrl || settings.ollamaApiUrl || DEFAULT_BACKEND_API_URL;
}

export default function GeneralSettings({ onModelChange, onStreamOutputChange }) {
  const [backendApiUrl, setBackendApiUrl] = useState('');
  const [ollamaApiUrl, setOllamaApiUrl] = useState('');
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [streamOutput, setStreamOutput] = useState(false);

  useEffect(() => {
    window.electronAPI.getSettings().then(settings => {
      setBackendApiUrl(resolveBackendApiUrl(settings));
      setOllamaApiUrl(settings.ollamaApiUrl);
      setSelectedModel(settings.chatModel || '');
      setStreamOutput(settings.streamOutput || false);
    });
  }, []);

  useEffect(() => {
    if (backendApiUrl) {
      fetch(backendApiUrl + '/models')
        .then(r => r.json())
        .then(data => {
          const names = data.models?.map(m => m.name) || [];
          setModels(names);
          // If no model is selected or the selected model is no longer available, select the first one
          if (!selectedModel || !names.includes(selectedModel)) {
            const defaultModel = names[0] || '';
            setSelectedModel(defaultModel);
            window.electronAPI.setSetting(MODEL_KEY, defaultModel);
          }
        })
        .catch(err => console.error('Failed to load models', err));
    }
  }, [backendApiUrl, selectedModel]); // Depend on selectedModel to re-evaluate default selection

  const handleBackendUrlChange = (e) => {
    const newUrl = e.target.value;
    setBackendApiUrl(newUrl);
    window.electronAPI.setSetting(BACKEND_API_URL_KEY, newUrl);
  };

  const handleOllamaUrlChange = (e) => {
    const newUrl = e.target.value;
    setOllamaApiUrl(newUrl);
    window.electronAPI.setSetting(OLLAMA_API_URL_KEY, newUrl);
  };

  const handleModelChange = (e) => {
    const newModel = e.target.value;
    setSelectedModel(newModel);
    window.electronAPI.setSetting(MODEL_KEY, newModel);
    if (onModelChange) {
      onModelChange(newModel);
    }
  };

  const handleStreamToggle = () => {
    const newStreamValue = !streamOutput;
    setStreamOutput(newStreamValue);
    window.electronAPI.setSetting(STREAM_KEY, newStreamValue);
    if (onStreamOutputChange) {
      onStreamOutputChange(newStreamValue);
    }
  };

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
        <h3>Chat Model</h3>
        <select
          className="select"
          value={selectedModel}
          onChange={handleModelChange}
        >
          {models.length === 0 && <option>— No models available —</option>}
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <div className="setting-section">
        <h3>Stream Output</h3>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={streamOutput}
            onChange={handleStreamToggle}
          />
          <span className="slider"></span>
        </label>
      </div>
    </div>
  );
}
