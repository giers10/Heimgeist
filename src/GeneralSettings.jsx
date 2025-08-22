import React, { useState, useEffect } from 'react';

const API_URL_KEY = 'ollamaApiUrl';
const MODEL_KEY = 'chatModel';

export default function GeneralSettings() {
  const [ollamaApiUrl, setOllamaApiUrl] = useState('');
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');

  useEffect(() => {
    window.electronAPI.getSettings().then(settings => {
      setOllamaApiUrl(settings.ollamaApiUrl);
      // Set selectedModel from settings, or fallback to an empty string if not found
      setSelectedModel(settings.chatModel || '');
    });
  }, []);

  useEffect(() => {
    if (ollamaApiUrl) {
      fetch(ollamaApiUrl + '/models')
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
  }, [ollamaApiUrl, selectedModel]); // Depend on selectedModel to re-evaluate default selection

  const handleUrlChange = (e) => {
    const newUrl = e.target.value;
    setOllamaApiUrl(newUrl);
    window.electronAPI.setSetting(API_URL_KEY, newUrl);
  };

  const handleModelChange = (e) => {
    const newModel = e.target.value;
    setSelectedModel(newModel);
    window.electronAPI.setSetting(MODEL_KEY, newModel);
  };

  return (
    <div className="settings-content-panel">
      <div className="setting-section">
        <h3>Ollama API URL</h3>
        <input
          type="text"
          className="input"
          value={ollamaApiUrl}
          onChange={handleUrlChange}
          placeholder="e.g., http://localhost:11434"
        />
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
    </div>
  );
}
