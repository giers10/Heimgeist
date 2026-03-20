import React, { useState, useEffect } from 'react';

const BACKEND_API_URL_KEY = 'backendApiUrl';
const OLLAMA_API_URL_KEY = 'ollamaApiUrl';
const MODEL_KEY = 'chatModel';
const STREAM_KEY = 'streamOutput';
const DEFAULT_BACKEND_API_URL = 'http://127.0.0.1:8000';
const DEFAULT_OLLAMA_API_URL = 'http://127.0.0.1:11434';
const DEFAULT_UPDATE_STATUS = {
  state: 'idle',
  message: '',
  checkedAt: null,
  localCommit: null,
  remoteCommit: null,
};

function resolveBackendApiUrl(settings) {
  return settings.backendApiUrl || settings.ollamaApiUrl || DEFAULT_BACKEND_API_URL;
}

function shortCommit(commit) {
  return typeof commit === 'string' && commit.length > 7 ? commit.slice(0, 7) : commit || '—';
}

function getStatusTone(state) {
  if (state === 'error') return 'error';
  if (state === 'updated' || state === 'up-to-date') return 'success';
  if (state === 'skipped' || state === 'unavailable') return 'warning';
  return 'neutral';
}

export default function GeneralSettings({ onModelChange, onStreamOutputChange, onLibrariesPurged }) {
  const [backendApiUrl, setBackendApiUrl] = useState('');
  const [ollamaApiUrl, setOllamaApiUrl] = useState('');
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [streamOutput, setStreamOutput] = useState(false);
  const [updateStatus, setUpdateStatus] = useState(DEFAULT_UPDATE_STATUS);
  const [isCheckingForUpdates, setIsCheckingForUpdates] = useState(false);
  const [isPurgingLibraries, setIsPurgingLibraries] = useState(false);
  const [libraryPurgeStatus, setLibraryPurgeStatus] = useState({ tone: 'neutral', message: '' });

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      window.electronAPI.getSettings(),
      window.electronAPI.getUpdateStatus(),
    ]).then(([settings, status]) => {
      if (cancelled) {
        return;
      }

      setBackendApiUrl(resolveBackendApiUrl(settings));
      setOllamaApiUrl(settings.ollamaApiUrl || DEFAULT_OLLAMA_API_URL);
      setSelectedModel(settings.chatModel || '');
      setStreamOutput(settings.streamOutput || false);
      setUpdateStatus(status || DEFAULT_UPDATE_STATUS);
    });

    return () => {
      cancelled = true;
    };
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
  }, [backendApiUrl, ollamaApiUrl, selectedModel]); // Depend on selectedModel to re-evaluate default selection

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

  const handleCheckForUpdates = async () => {
    setIsCheckingForUpdates(true);
    try {
      const status = await window.electronAPI.checkForUpdates();
      setUpdateStatus(status || DEFAULT_UPDATE_STATUS);
    } catch (error) {
      setUpdateStatus({
        state: 'error',
        message: `Update check failed: ${error.message || String(error)}`,
        checkedAt: new Date().toISOString(),
        localCommit: null,
        remoteCommit: null,
      });
    } finally {
      setIsCheckingForUpdates(false);
    }
  };

  const handlePurgeLibraries = async () => {
    const confirmed = window.confirm(
      'Delete all Heimgeist databases, staged files, and indexes from local storage? Chat history will be kept.'
    );
    if (!confirmed) {
      return;
    }

    setIsPurgingLibraries(true);
    setLibraryPurgeStatus({ tone: 'neutral', message: '' });

    try {
      const response = await fetch(`${backendApiUrl}/libraries/purge`, {
        method: 'POST',
      });
      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(data?.detail || `HTTP ${response.status}`);
      }

      const count = Number(data?.count) || 0;
      setLibraryPurgeStatus({
        tone: 'success',
        message: count > 0
          ? `Removed ${count} database${count === 1 ? '' : 's'} from local storage.`
          : 'No local databases were found to remove.',
      });

      if (onLibrariesPurged) {
        await Promise.resolve(onLibrariesPurged());
      }
    } catch (error) {
      setLibraryPurgeStatus({
        tone: 'error',
        message: `Database purge failed: ${error.message || String(error)}`,
      });
    } finally {
      setIsPurgingLibraries(false);
    }
  };

  const updateCheckedAtLabel = updateStatus.checkedAt
    ? new Date(updateStatus.checkedAt).toLocaleString()
    : null;

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
      <div className="setting-section">
        <h3>Updates</h3>
        <div className="setting-control-row">
          <button
            type="button"
            className="button"
            onClick={handleCheckForUpdates}
            disabled={isCheckingForUpdates}
          >
            {isCheckingForUpdates ? 'Checking...' : 'Check for Update'}
          </button>
        </div>
        <p className="setting-description">
          Compares the local Git commit with remote <code>master</code>, pulls changes when needed, and restarts Heimgeist automatically. The same check also runs on every startup.
        </p>
        {updateStatus.message && (
          <p className={`setting-status ${getStatusTone(updateStatus.state)}`}>
            {updateStatus.message}
          </p>
        )}
        {(updateStatus.localCommit || updateStatus.remoteCommit || updateCheckedAtLabel) && (
          <div className="setting-meta">
            {updateStatus.localCommit && <div>Local: <code>{shortCommit(updateStatus.localCommit)}</code></div>}
            {updateStatus.remoteCommit && <div>Remote: <code>{shortCommit(updateStatus.remoteCommit)}</code></div>}
            {updateCheckedAtLabel && <div>Last checked: {updateCheckedAtLabel}</div>}
          </div>
        )}
      </div>
      <div className="setting-section danger-zone">
        <h3>Purge Databases</h3>
        <div className="setting-control-row">
          <button
            type="button"
            className="button danger"
            onClick={handlePurgeLibraries}
            disabled={isPurgingLibraries || !backendApiUrl}
          >
            {isPurgingLibraries ? 'Purging...' : 'Delete All Databases'}
          </button>
        </div>
        <p className="setting-description">
          Removes every local Heimgeist database, including staged files, corpora, and indexes. This is meant as a recovery action when the DB panel becomes unusable. Chat history stays intact.
        </p>
        {libraryPurgeStatus.message && (
          <p className={`setting-status ${libraryPurgeStatus.tone}`}>
            {libraryPurgeStatus.message}
          </p>
        )}
      </div>
    </div>
  );
}
