import GeneralSettings from './GeneralSettings'
import InterfaceSettings from './InterfaceSettings'
import WebsearchSettings from './WebsearchSettings'
import { normalizeWebsearchEngines } from './websearchEngines'

const SETTINGS_SECTIONS = ['General', 'AI Models', 'Interface', 'Backend', 'Updates', 'Advanced']

export function SettingsSidebar({ activeSection, onSelect }) {
  return (
    <div className="settings-list">
      {SETTINGS_SECTIONS.map(section => (
        <div
          key={section}
          className={`settings-item ${activeSection === section ? 'active' : ''}`}
          onClick={() => onSelect(section)}
        >
          {section}
        </div>
      ))}
    </div>
  )
}

export function SettingsPanel({
  activeSection,
  onAudioInputDeviceChange,
  onAudioInputLanguageChange,
  onBackendApiUrlChange,
  onLibrariesPurged,
  onModelChange,
  onStreamOutputChange,
  onTranscriptionModelChange,
  onVisionModelChange,
  searxEngines,
  searxUrl,
  setSearxEngines,
  setSearxUrl,
  streamOutput,
}) {
  return (
    <>
      <div className="header">
        <strong>{activeSection} Settings</strong>
      </div>
      {activeSection === 'General' && (
        <GeneralSettings
          panel="General"
          onAudioInputDeviceChange={onAudioInputDeviceChange}
          onAudioInputLanguageChange={onAudioInputLanguageChange}
        />
      )}
      {activeSection === 'AI Models' && (
        <GeneralSettings
          panel="AI Models"
          onModelChange={onModelChange}
          onVisionModelChange={onVisionModelChange}
          onTranscriptionModelChange={onTranscriptionModelChange}
          onLibrariesPurged={onLibrariesPurged}
        />
      )}
      {activeSection === 'Interface' && (
        <InterfaceSettings
          streamOutput={streamOutput}
          onStreamOutputChange={onStreamOutputChange}
        />
      )}
      {activeSection === 'Backend' && (
        <WebsearchSettings
          onBackendApiUrlChange={onBackendApiUrlChange}
          searxUrl={searxUrl}
          setSearxUrl={setSearxUrl}
          engines={searxEngines}
          setEngines={(next) => setSearxEngines(normalizeWebsearchEngines(next))}
        />
      )}
      {activeSection === 'Updates' && (
        <GeneralSettings panel="Updates" />
      )}
      {activeSection === 'Advanced' && (
        <GeneralSettings panel="Advanced" onLibrariesPurged={onLibrariesPurged} />
      )}
    </>
  )
}
