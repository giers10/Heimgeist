import React, { useEffect, useState } from 'react';
import {
  AUDIO_INPUT_DEVICE_ID_KEY,
  AUDIO_INPUT_ENABLED_KEY,
  AUDIO_INPUT_LANGUAGE_KEY,
  AUDIO_INPUT_LANGUAGE_OPTIONS,
  ensureAudioInputPermission,
  listAudioInputDevices,
  supportsAudioInputCapture,
} from './audioInput';

const BACKEND_API_URL_KEY = 'backendApiUrl';
const OLLAMA_API_URL_KEY = 'ollamaApiUrl';
const EMBED_MODEL_KEY = 'embedModel';
const RERANK_MODEL_KEY = 'rerankModel';
const MODEL_KEY = 'chatModel';
const VISION_MODEL_KEY = 'visionModel';
const TRANSCRIPTION_MODEL_KEY = 'transcriptionModel';
const STREAM_KEY = 'streamOutput';
const DEFAULT_AUDIO_INPUT_DEVICE_ID = '';
const DEFAULT_AUDIO_INPUT_LANGUAGE = '';
const DEFAULT_BACKEND_API_URL = 'http://127.0.0.1:8000';
const DEFAULT_OLLAMA_API_URL = 'http://127.0.0.1:11434';
const DEFAULT_EMBED_MODEL = 'nomic-embed-text:latest';
const DEFAULT_TRANSCRIPTION_MODEL = 'base';
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

function buildSelectOptions(values, currentValue, missingLabel) {
  const uniqueValues = [...new Set((Array.isArray(values) ? values : []).filter(Boolean))];
  const options = uniqueValues.map(value => ({ value, label: value }));
  if (currentValue && !uniqueValues.includes(currentValue)) {
    options.unshift({
      value: currentValue,
      label: `${currentValue} (${missingLabel})`,
    });
  }
  return options;
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

export default function GeneralSettings({
  onModelChange,
  onVisionModelChange,
  onTranscriptionModelChange,
  onStreamOutputChange,
  onLibrariesPurged,
  onBackendApiUrlChange,
  onAudioInputEnabledChange,
  onAudioInputDeviceChange,
  onAudioInputLanguageChange,
}) {
  const [backendApiUrl, setBackendApiUrl] = useState('');
  const [ollamaApiUrl, setOllamaApiUrl] = useState('');
  const [embedModel, setEmbedModel] = useState(DEFAULT_EMBED_MODEL);
  const [rerankModel, setRerankModel] = useState(DEFAULT_EMBED_MODEL);
  const [chatModels, setChatModels] = useState([]);
  const [embeddingModels, setEmbeddingModels] = useState([]);
  const [visionModels, setVisionModels] = useState([]);
  const [rerankingModels, setRerankingModels] = useState([]);
  const [whisperModels, setWhisperModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [visionModel, setVisionModel] = useState('');
  const [transcriptionModel, setTranscriptionModel] = useState(DEFAULT_TRANSCRIPTION_MODEL);
  const [streamOutput, setStreamOutput] = useState(false);
  const [audioInputEnabled, setAudioInputEnabled] = useState(false);
  const [audioInputDeviceId, setAudioInputDeviceId] = useState(DEFAULT_AUDIO_INPUT_DEVICE_ID);
  const [audioInputLanguage, setAudioInputLanguage] = useState(DEFAULT_AUDIO_INPUT_LANGUAGE);
  const [audioInputDevices, setAudioInputDevices] = useState([]);
  const [isRefreshingAudioDevices, setIsRefreshingAudioDevices] = useState(false);
  const [audioInputStatus, setAudioInputStatus] = useState({ tone: 'neutral', message: '' });
  const [updateStatus, setUpdateStatus] = useState(DEFAULT_UPDATE_STATUS);
  const [isCheckingForUpdates, setIsCheckingForUpdates] = useState(false);
  const [isPurgingLibraries, setIsPurgingLibraries] = useState(false);
  const [libraryPurgeStatus, setLibraryPurgeStatus] = useState({ tone: 'neutral', message: '' });
  const audioInputSupported = supportsAudioInputCapture();

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
      setEmbedModel(settings.embedModel || DEFAULT_EMBED_MODEL);
      setRerankModel(settings.rerankModel || settings.embedModel || DEFAULT_EMBED_MODEL);
      setSelectedModel(settings.chatModel || '');
      setVisionModel(settings.visionModel || settings.chatModel || '');
      setTranscriptionModel(settings.transcriptionModel || DEFAULT_TRANSCRIPTION_MODEL);
      setStreamOutput(settings.streamOutput || false);
      setAudioInputEnabled(settings.audioInputEnabled === true);
      setAudioInputDeviceId(
        typeof settings.audioInputDeviceId === 'string'
          ? settings.audioInputDeviceId
          : DEFAULT_AUDIO_INPUT_DEVICE_ID
      );
      setAudioInputLanguage(
        typeof settings.audioInputLanguage === 'string'
          ? settings.audioInputLanguage
          : DEFAULT_AUDIO_INPUT_LANGUAGE
      );
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
          setChatModels(Array.isArray(data.chat_models) ? data.chat_models : []);
          setEmbeddingModels(Array.isArray(data.embedding_models) ? data.embedding_models : []);
          setVisionModels(Array.isArray(data.vision_models) ? data.vision_models : []);
          setRerankingModels(Array.isArray(data.reranking_models) ? data.reranking_models : []);
          setWhisperModels(Array.isArray(data.whisper_models) ? data.whisper_models.map(model => model.name).filter(Boolean) : []);
        })
        .catch(err => console.error('Failed to load models', err));
    }
  }, [backendApiUrl, ollamaApiUrl]);

  useEffect(() => {
    if (chatModels.length === 0) {
      return;
    }

    const nextModel = chatModels.includes(selectedModel) ? selectedModel : chatModels[0];
    if (nextModel === selectedModel) {
      return;
    }

    setSelectedModel(nextModel);
    window.electronAPI.setSetting(MODEL_KEY, nextModel);
    if (onModelChange) {
      onModelChange(nextModel);
    }
  }, [chatModels, selectedModel, onModelChange]);

  useEffect(() => {
    if (visionModels.length === 0) {
      return;
    }

    const nextModel = visionModels.includes(visionModel) ? visionModel : visionModels[0];
    if (nextModel === visionModel) {
      return;
    }

    setVisionModel(nextModel);
    window.electronAPI.setSetting(VISION_MODEL_KEY, nextModel);
    if (onVisionModelChange) {
      onVisionModelChange(nextModel);
    }
  }, [visionModels, visionModel, onVisionModelChange]);

  useEffect(() => {
    if (embedModel) {
      return;
    }

    const nextModel = embeddingModels[0] || DEFAULT_EMBED_MODEL;
    setEmbedModel(nextModel);
    window.electronAPI.setSetting(EMBED_MODEL_KEY, nextModel);
  }, [embeddingModels, embedModel]);

  useEffect(() => {
    if (rerankModel) {
      return;
    }

    const nextModel = embedModel || rerankingModels[0] || DEFAULT_EMBED_MODEL;
    setRerankModel(nextModel);
    window.electronAPI.setSetting(RERANK_MODEL_KEY, nextModel);
  }, [rerankingModels, rerankModel, embedModel]);

  useEffect(() => {
    if (transcriptionModel) {
      return;
    }

    const nextModel = whisperModels[0] || DEFAULT_TRANSCRIPTION_MODEL;
    setTranscriptionModel(nextModel);
    window.electronAPI.setSetting(TRANSCRIPTION_MODEL_KEY, nextModel);
    if (onTranscriptionModelChange) {
      onTranscriptionModelChange(nextModel);
    }
  }, [whisperModels, transcriptionModel, onTranscriptionModelChange]);

  useEffect(() => {
    if (!audioInputSupported) {
      setAudioInputStatus({
        tone: 'warning',
        message: 'Microphone capture is not available in this environment.',
      });
      return () => {};
    }

    let cancelled = false;

    const refreshDevices = async () => {
      try {
        const devices = await listAudioInputDevices();
        if (cancelled) {
          return;
        }
        setAudioInputDevices(devices);
        if (devices.length === 0) {
          setAudioInputStatus({
            tone: 'warning',
            message: 'No audio input devices found yet. Grant microphone access and refresh the list.',
          });
          return;
        }
        if (!devices.some(device => device.hasLabel)) {
          setAudioInputStatus({
            tone: 'neutral',
            message: 'Device names appear after microphone access has been granted once.',
          });
          return;
        }
        setAudioInputStatus({ tone: 'neutral', message: '' });
      } catch (error) {
        if (!cancelled) {
          setAudioInputStatus({
            tone: 'error',
            message: `Could not list audio devices: ${error.message || String(error)}`,
          });
        }
      }
    };

    refreshDevices();

    const mediaDevices = navigator.mediaDevices;
    if (mediaDevices?.addEventListener) {
      mediaDevices.addEventListener('devicechange', refreshDevices);
    }

    return () => {
      cancelled = true;
      if (mediaDevices?.removeEventListener) {
        mediaDevices.removeEventListener('devicechange', refreshDevices);
      }
    };
  }, [audioInputSupported]);

  const handleBackendUrlChange = (event) => {
    const newUrl = event.target.value;
    setBackendApiUrl(newUrl);
    window.electronAPI.setSetting(BACKEND_API_URL_KEY, newUrl);
    if (onBackendApiUrlChange) {
      onBackendApiUrlChange(newUrl);
    }
  };

  const handleOllamaUrlChange = (event) => {
    const newUrl = event.target.value;
    setOllamaApiUrl(newUrl);
    window.electronAPI.setSetting(OLLAMA_API_URL_KEY, newUrl);
  };

  const handleModelChange = (event) => {
    const newModel = event.target.value;
    setSelectedModel(newModel);
    window.electronAPI.setSetting(MODEL_KEY, newModel);
    if (onModelChange) {
      onModelChange(newModel);
    }
  };

  const handleEmbedModelToggle = () => {
    const nextModel = embedModel === BGE_EMBED_MODEL ? DEFAULT_EMBED_MODEL : BGE_EMBED_MODEL;
    setEmbedModel(nextModel);
    window.electronAPI.setSetting(EMBED_MODEL_KEY, nextModel);
  };

  const handleStreamToggle = () => {
    const newStreamValue = !streamOutput;
    setStreamOutput(newStreamValue);
    window.electronAPI.setSetting(STREAM_KEY, newStreamValue);
    if (onStreamOutputChange) {
      onStreamOutputChange(newStreamValue);
    }
  };

  const refreshAudioDevices = async ({ requestAccess = false } = {}) => {
    if (!audioInputSupported) {
      return;
    }

    setIsRefreshingAudioDevices(true);
    try {
      if (requestAccess) {
        await ensureAudioInputPermission();
      }
      const devices = await listAudioInputDevices();
      setAudioInputDevices(devices);
      if (devices.length === 0) {
        setAudioInputStatus({
          tone: 'warning',
          message: 'No audio input devices found yet. Check the OS microphone permission and refresh again.',
        });
      } else if (!devices.some(device => device.hasLabel)) {
        setAudioInputStatus({
          tone: 'neutral',
          message: 'Microphone access was requested. Refresh again if the system permission dialog just closed.',
        });
      } else {
        setAudioInputStatus({ tone: 'success', message: 'Audio input devices refreshed.' });
      }
    } catch (error) {
      setAudioInputStatus({
        tone: 'error',
        message: `Microphone access failed: ${error.message || String(error)}`,
      });
    } finally {
      setIsRefreshingAudioDevices(false);
    }
  };

  const handleAudioInputToggle = () => {
    const nextValue = !audioInputEnabled;
    setAudioInputEnabled(nextValue);
    window.electronAPI.setSetting(AUDIO_INPUT_ENABLED_KEY, nextValue);
    if (onAudioInputEnabledChange) {
      onAudioInputEnabledChange(nextValue);
    }
    if (nextValue) {
      refreshAudioDevices();
    }
  };

  const handleAudioInputDeviceChange = (event) => {
    const nextDeviceId = event.target.value;
    setAudioInputDeviceId(nextDeviceId);
    window.electronAPI.setSetting(AUDIO_INPUT_DEVICE_ID_KEY, nextDeviceId);
    if (onAudioInputDeviceChange) {
      onAudioInputDeviceChange(nextDeviceId);
    }
  };

  const handleAudioInputLanguageChange = (event) => {
    const nextLanguage = event.target.value;
    setAudioInputLanguage(nextLanguage);
    window.electronAPI.setSetting(AUDIO_INPUT_LANGUAGE_KEY, nextLanguage);
    if (onAudioInputLanguageChange) {
      onAudioInputLanguageChange(nextLanguage);
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
  const selectedAudioDeviceMissing = Boolean(
    audioInputDeviceId &&
    !audioInputDevices.some(device => device.deviceId === audioInputDeviceId)
  );
  const audioInputOptions = selectedAudioDeviceMissing
    ? [
        ...audioInputDevices,
        {
          deviceId: audioInputDeviceId,
          label: 'Saved device (currently unavailable)',
          hasLabel: true,
        },
      ]
    : audioInputDevices;
  const audioDeviceRefreshLabel = audioInputDevices.some(device => device.hasLabel)
    ? 'Refresh devices'
    : 'Allow microphone access';

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
        <h3>Embedding Model</h3>
        <div className="setting-switch-row">
          <span className={"setting-switch-label" + (embedModel !== BGE_EMBED_MODEL ? " active" : "")}>
            nomic
          </span>
          <label className="toggle-switch toggle-switch--binary-select">
            <input
              type="checkbox"
              checked={embedModel === BGE_EMBED_MODEL}
              onChange={handleEmbedModelToggle}
            />
            <span className="slider"></span>
          </label>
          <span className={"setting-switch-label" + (embedModel === BGE_EMBED_MODEL ? " active" : "")}>
            bge-m3
          </span>
        </div>
        <p className="setting-description">
          Heimgeist uses this model for web-search reranking and for building or rebuilding local database embeddings.
        </p>
      </div>
      <div className="setting-section">
        <h3>Audio Input</h3>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={audioInputEnabled}
            onChange={handleAudioInputToggle}
            disabled={!audioInputSupported}
          />
          <span className="slider"></span>
        </label>
        <p className="setting-description">
          Enables microphone transcription in the chat composer. Heimgeist records locally and sends the clip to the local Whisper runtime.
        </p>
        {audioInputEnabled && (
          <>
            <div className="setting-control-row">
              <select
                className="select"
                value={audioInputDeviceId}
                onChange={handleAudioInputDeviceChange}
                disabled={!audioInputSupported}
              >
                <option value={DEFAULT_AUDIO_INPUT_DEVICE_ID}>System default microphone</option>
                {audioInputOptions.map(device => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label}
                  </option>
                ))}
              </select>
              <select
                className="select"
                value={audioInputLanguage}
                onChange={handleAudioInputLanguageChange}
                disabled={!audioInputSupported}
              >
                {AUDIO_INPUT_LANGUAGE_OPTIONS.map(language => (
                  <option key={language.value || 'auto'} value={language.value}>
                    {language.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="button"
                onClick={() => refreshAudioDevices({ requestAccess: true })}
                disabled={!audioInputSupported || isRefreshingAudioDevices}
              >
                {isRefreshingAudioDevices ? 'Working…' : audioDeviceRefreshLabel}
              </button>
            </div>
            {audioInputStatus.message && (
              <p className={`setting-status ${audioInputStatus.tone}`}>{audioInputStatus.message}</p>
            )}
            <p className="setting-description">
              Whisper can auto-detect the spoken language, but you can force a fixed input language here when auto-detection drifts.
            </p>
          </>
        )}
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
