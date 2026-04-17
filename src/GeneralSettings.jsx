import React, { useEffect, useState } from 'react';
import {
  AUDIO_INPUT_DEVICE_ID_KEY,
  AUDIO_INPUT_LANGUAGE_KEY,
  AUDIO_INPUT_LANGUAGE_OPTIONS,
  ensureAudioInputPermission,
  listAudioInputDevices,
  supportsAudioInputCapture,
} from './audioInput';

const EMBED_MODEL_KEY = 'embedModel';
const RERANK_MODEL_KEY = 'rerankModel';
const MODEL_KEY = 'chatModel';
const VISION_MODEL_KEY = 'visionModel';
const TRANSCRIPTION_MODEL_KEY = 'transcriptionModel';
const DEFAULT_AUDIO_INPUT_DEVICE_ID = '';
const DEFAULT_AUDIO_INPUT_LANGUAGE = '';
const DEFAULT_BACKEND_API_URL = 'http://127.0.0.1:8000';
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

function buildSelectOptions(values, currentValue, missingLabel, showMissingLabel = true) {
  const uniqueValues = [...new Set((Array.isArray(values) ? values : []).filter(Boolean))];
  const options = uniqueValues.map(value => ({ value, label: value }));
  if (currentValue && !uniqueValues.includes(currentValue)) {
    options.unshift({
      value: currentValue,
      label: showMissingLabel ? `${currentValue} (${missingLabel})` : currentValue,
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

function formatCommitTimestamp(value) {
  if (!value) {
    return null;
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }

  return parsed.toLocaleString();
}

export default function GeneralSettings({
  panel,
  onModelChange,
  onVisionModelChange,
  onTranscriptionModelChange,
  onLibrariesPurged,
  onAudioInputDeviceChange,
  onAudioInputLanguageChange,
}) {
  const [backendApiUrl, setBackendApiUrl] = useState('');
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
  const [audioInputDeviceId, setAudioInputDeviceId] = useState(DEFAULT_AUDIO_INPUT_DEVICE_ID);
  const [audioInputLanguage, setAudioInputLanguage] = useState(DEFAULT_AUDIO_INPUT_LANGUAGE);
  const [audioInputDevices, setAudioInputDevices] = useState([]);
  const [isRefreshingAudioDevices, setIsRefreshingAudioDevices] = useState(false);
  const [audioInputStatus, setAudioInputStatus] = useState({ tone: 'neutral', message: '' });
  const [updateStatus, setUpdateStatus] = useState(DEFAULT_UPDATE_STATUS);
  const [isCheckingForUpdates, setIsCheckingForUpdates] = useState(false);
  const [isChangelogOpen, setIsChangelogOpen] = useState(false);
  const [changelogEntries, setChangelogEntries] = useState([]);
  const [changelogPage, setChangelogPage] = useState(1);
  const [changelogHasMore, setChangelogHasMore] = useState(false);
  const [isLoadingChangelog, setIsLoadingChangelog] = useState(false);
  const [changelogError, setChangelogError] = useState('');
  const [isPurgingLibraries, setIsPurgingLibraries] = useState(false);
  const [libraryPurgeStatus, setLibraryPurgeStatus] = useState({ tone: 'neutral', message: '' });
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const [isLoadingModelCatalog, setIsLoadingModelCatalog] = useState(false);
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
      setEmbedModel(settings.embedModel || DEFAULT_EMBED_MODEL);
      setRerankModel(settings.rerankModel || settings.embedModel || DEFAULT_EMBED_MODEL);
      setSelectedModel(settings.chatModel || '');
      setVisionModel(settings.visionModel || settings.chatModel || '');
      setTranscriptionModel(settings.transcriptionModel || DEFAULT_TRANSCRIPTION_MODEL);
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
    }).finally(() => {
      if (!cancelled) {
        setSettingsHydrated(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (panel !== 'AI Models') {
      setIsLoadingModelCatalog(false);
      return () => {};
    }
    if (!backendApiUrl) {
      setIsLoadingModelCatalog(false);
      return () => {};
    }

    let cancelled = false;
    setIsLoadingModelCatalog(true);

    fetch(backendApiUrl + '/models')
      .then(r => r.json())
      .then(data => {
        if (cancelled) {
          return;
        }
        setChatModels(Array.isArray(data.chat_models) ? data.chat_models : []);
        setEmbeddingModels(Array.isArray(data.embedding_models) ? data.embedding_models : []);
        setVisionModels(Array.isArray(data.vision_models) ? data.vision_models : []);
        setRerankingModels(Array.isArray(data.reranking_models) ? data.reranking_models : []);
        setWhisperModels(Array.isArray(data.whisper_models) ? data.whisper_models.map(model => model.name).filter(Boolean) : []);
      })
      .catch(err => {
        if (!cancelled) {
          console.error('Failed to load models', err);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingModelCatalog(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [backendApiUrl, panel]);

  useEffect(() => {
    if (panel !== 'AI Models') {
      return;
    }
    if (!settingsHydrated) {
      return;
    }
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
  }, [chatModels, selectedModel, onModelChange, panel, settingsHydrated]);

  useEffect(() => {
    if (panel !== 'AI Models') {
      return;
    }
    if (!settingsHydrated) {
      return;
    }
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
  }, [visionModels, visionModel, onVisionModelChange, panel, settingsHydrated]);

  useEffect(() => {
    if (panel !== 'AI Models') {
      return;
    }
    if (!settingsHydrated) {
      return;
    }
    if (embedModel) {
      return;
    }

    const nextModel = embeddingModels[0] || DEFAULT_EMBED_MODEL;
    setEmbedModel(nextModel);
    window.electronAPI.setSetting(EMBED_MODEL_KEY, nextModel);
  }, [embeddingModels, embedModel, panel, settingsHydrated]);

  useEffect(() => {
    if (panel !== 'AI Models') {
      return;
    }
    if (!settingsHydrated) {
      return;
    }
    if (rerankModel) {
      return;
    }

    const nextModel = embedModel || rerankingModels[0] || DEFAULT_EMBED_MODEL;
    setRerankModel(nextModel);
    window.electronAPI.setSetting(RERANK_MODEL_KEY, nextModel);
  }, [rerankingModels, rerankModel, embedModel, panel, settingsHydrated]);

  useEffect(() => {
    if (panel !== 'AI Models') {
      return;
    }
    if (!settingsHydrated) {
      return;
    }
    if (transcriptionModel) {
      return;
    }

    const nextModel = whisperModels[0] || DEFAULT_TRANSCRIPTION_MODEL;
    setTranscriptionModel(nextModel);
    window.electronAPI.setSetting(TRANSCRIPTION_MODEL_KEY, nextModel);
    if (onTranscriptionModelChange) {
      onTranscriptionModelChange(nextModel);
    }
  }, [whisperModels, transcriptionModel, onTranscriptionModelChange, panel, settingsHydrated]);

  useEffect(() => {
    if (panel !== 'General') {
      return () => {};
    }
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
  }, [audioInputSupported, panel]);

  const handleModelChange = (event) => {
    const newModel = event.target.value;
    setSelectedModel(newModel);
    window.electronAPI.setSetting(MODEL_KEY, newModel);
    if (onModelChange) {
      onModelChange(newModel);
    }
  };

  const handleVisionModelChange = (event) => {
    const newModel = event.target.value;
    setVisionModel(newModel);
    window.electronAPI.setSetting(VISION_MODEL_KEY, newModel);
    if (onVisionModelChange) {
      onVisionModelChange(newModel);
    }
  };

  const handleEmbedModelChange = (event) => {
    const nextModel = event.target.value;
    setEmbedModel(nextModel);
    window.electronAPI.setSetting(EMBED_MODEL_KEY, nextModel);
  };

  const handleRerankModelChange = (event) => {
    const nextModel = event.target.value;
    setRerankModel(nextModel);
    window.electronAPI.setSetting(RERANK_MODEL_KEY, nextModel);
  };

  const handleTranscriptionModelChange = (event) => {
    const nextModel = event.target.value;
    setTranscriptionModel(nextModel);
    window.electronAPI.setSetting(TRANSCRIPTION_MODEL_KEY, nextModel);
    if (onTranscriptionModelChange) {
      onTranscriptionModelChange(nextModel);
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

  const loadChangelogPage = async (page) => {
    setIsLoadingChangelog(true);
    setChangelogError('');

    try {
      const result = await window.electronAPI.getChangelogPage(page);
      setChangelogEntries(Array.isArray(result?.entries) ? result.entries : []);
      setChangelogPage(Number.isInteger(result?.page) ? result.page : page);
      setChangelogHasMore(Boolean(result?.hasMore));
    } catch (error) {
      setChangelogError(`Changelog load failed: ${error.message || String(error)}`);
    } finally {
      setIsLoadingChangelog(false);
    }
  };

  const handleToggleChangelog = async () => {
    if (isChangelogOpen) {
      setIsChangelogOpen(false);
      return;
    }

    setIsChangelogOpen(true);
    if (changelogEntries.length === 0 && !isLoadingChangelog) {
      await loadChangelogPage(1);
    }
  };

  const handleChangelogPageChange = async (nextPage) => {
    if (isLoadingChangelog || nextPage < 1) {
      return;
    }

    if (nextPage > changelogPage && !changelogHasMore) {
      return;
    }

    await loadChangelogPage(nextPage);
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
  const showMissingModelLabel = !isLoadingModelCatalog;
  const chatModelOptions = buildSelectOptions(chatModels, selectedModel, 'saved model unavailable', showMissingModelLabel);
  const visionModelOptions = buildSelectOptions(visionModels, visionModel, 'saved model unavailable', showMissingModelLabel);
  const embeddingModelOptions = buildSelectOptions(embeddingModels, embedModel, 'saved model unavailable', showMissingModelLabel);
  const rerankingModelOptions = buildSelectOptions(rerankingModels, rerankModel, 'saved model unavailable', showMissingModelLabel);
  const transcriptionModelOptions = buildSelectOptions(whisperModels, transcriptionModel, 'saved model unavailable', showMissingModelLabel);

  if (panel === 'AI Models') {
    return (
      <div className="settings-content-panel">
        <div className="setting-section">
          <h3>Chat Model</h3>
          <select
            className="select"
            value={selectedModel}
            onChange={handleModelChange}
          >
            {chatModelOptions.length === 0 && <option value="">{isLoadingModelCatalog ? 'Loading models…' : '— No chat models available —'}</option>}
            {chatModelOptions.map(model => <option key={model.value} value={model.value}>{model.label}</option>)}
          </select>
          <p className="setting-description">
            Heimgeist uses this model for normal text chat.
          </p>
        </div>
        <div className="setting-section">
          <h3>Vision Model</h3>
          <select
            className="select"
            value={visionModel}
            onChange={handleVisionModelChange}
          >
            {visionModelOptions.length === 0 && <option value="">{isLoadingModelCatalog ? 'Loading models…' : '— No vision models available —'}</option>}
            {visionModelOptions.map(model => <option key={model.value} value={model.value}>{model.label}</option>)}
          </select>
          <p className="setting-description">
            Heimgeist uses this model when a chat message includes image attachments.
          </p>
        </div>
        <div className="setting-section">
          <h3>Embedding Model</h3>
          <select
            className="select"
            value={embedModel}
            onChange={handleEmbedModelChange}
          >
            {embeddingModelOptions.length === 0 && <option value="">{isLoadingModelCatalog ? 'Loading models…' : '— No embedding models available —'}</option>}
            {embeddingModelOptions.map(model => (
              <option key={model.value} value={model.value}>{model.label}</option>
            ))}
          </select>
          <p className="setting-description">
            Heimgeist uses this model for building or rebuilding local database embeddings.
          </p>
        </div>
        <div className="setting-section">
          <h3>Reranking Model</h3>
          <select
            className="select"
            value={rerankModel}
            onChange={handleRerankModelChange}
          >
            {rerankingModelOptions.length === 0 && <option value="">{isLoadingModelCatalog ? 'Loading models…' : '— No reranking models available —'}</option>}
            {rerankingModelOptions.map(model => (
              <option key={model.value} value={model.value}>{model.label}</option>
            ))}
          </select>
          <p className="setting-description">
            Heimgeist currently uses an embedding-based reranker for web search, so this should generally be an embedding-capable Ollama model.
          </p>
        </div>
        <div className="setting-section">
          <h3>Transcription Model</h3>
          <select
            className="select"
            value={transcriptionModel}
            onChange={handleTranscriptionModelChange}
          >
            {transcriptionModelOptions.length === 0 && <option value="">{isLoadingModelCatalog ? 'Loading models…' : '— No Whisper models available —'}</option>}
            {transcriptionModelOptions.map(model => (
              <option key={model.value} value={model.value}>
                {model.label}
              </option>
            ))}
          </select>
          <p className="setting-description">
            Heimgeist uses this Whisper model for microphone transcription.
          </p>
        </div>
      </div>
    );
  }

  if (panel === 'General') {
    return (
      <div className="settings-content-panel">
        <div className="setting-section">
          <h3>Microphone</h3>
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
            Microphone input is available in the chat composer. Heimgeist records locally and sends the clip to the configured Whisper model.
          </p>
          <p className="setting-description">
            Whisper can auto-detect the spoken language, but you can force a fixed input language here when auto-detection drifts.
          </p>
        </div>
      </div>
    );
  }

  if (panel === 'Updates') {
    return (
      <div className="settings-content-panel">
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
          <div className="setting-control-row changelog-toggle-row">
            <button
              type="button"
              className="button ghost"
              onClick={handleToggleChangelog}
              disabled={isLoadingChangelog && !isChangelogOpen}
            >
              {isChangelogOpen ? 'v Hide Changelog' : '> Show Changelog'}
            </button>
          </div>
          {isChangelogOpen && (
            <div className="changelog-panel">
              {changelogError && (
                <p className="setting-status error">{changelogError}</p>
              )}
              {isLoadingChangelog && (
                <p className="setting-status neutral">Loading changelog...</p>
              )}
              {!isLoadingChangelog && !changelogError && changelogEntries.length === 0 && (
                <p className="setting-description">No commits found.</p>
              )}
              {changelogEntries.length > 0 && (
                <>
                  <ul className="changelog-list">
                    {changelogEntries.map((entry) => {
                      const committedAtLabel = formatCommitTimestamp(entry.committedAt);
                      return (
                        <li key={entry.hash} className="changelog-item">
                          <div className="changelog-message">{entry.message}</div>
                          <div className="changelog-item-meta">
                            <code>{shortCommit(entry.hash)}</code>
                            {committedAtLabel && <span>{committedAtLabel}</span>}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                  <div className="setting-control-row changelog-pagination">
                    <button
                      type="button"
                      className="button ghost"
                      onClick={() => handleChangelogPageChange(changelogPage - 1)}
                      disabled={isLoadingChangelog || changelogPage <= 1}
                    >
                      Previous
                    </button>
                    <span className="changelog-page-label">Page {changelogPage}</span>
                    <button
                      type="button"
                      className="button ghost"
                      onClick={() => handleChangelogPageChange(changelogPage + 1)}
                      disabled={isLoadingChangelog || !changelogHasMore}
                    >
                      Next
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (panel === 'Advanced') {
    return (
      <div className="settings-content-panel">
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

  return null;
}
