import React, { useState, useEffect } from 'react';

const COLOR_SCHEME_KEY = 'colorScheme';

const colorSchemes = {
  'Default': {
    '--bg': '#0b1020',
    '--panel': '#141b34',
    '--text': '#e6e8ef',
    '--muted': '#9aa3b2',
    '--accent': '#6ea8fe',
    '--border': '#24304f',
    '--input-bg': '#0e1530',
    '--user-msg-bg': '#111933',
    '--assistant-msg-bg': '#101927',
  },
  'Grayscale': {
    '--bg': '#1a1a1a',
    '--panel': '#2a2a2a',
    '--text': '#f0f0f0',
    '--muted': '#aaaaaa',
    '--accent': '#888888',
    '--border': '#4a4a4a',
    '--input-bg': '#202020',
    '--user-msg-bg': '#333333',
    '--assistant-msg-bg': '#252525',
  },
  'Rose': {
    '--bg': '#200a10',
    '--panel': '#301a20',
    '--text': '#ffe0e0',
    '--muted': '#a09090',
    '--accent': '#E91E63',
    '--border': '#402025',
    '--input-bg': '#2a1015',
    '--user-msg-bg': '#331119',
    '--assistant-msg-bg': '#271019',
  },
};

function applyColorScheme(schemeName) {
  const scheme = colorSchemes[schemeName];
  if (scheme) {
    for (const [key, value] of Object.entries(scheme)) {
      document.documentElement.style.setProperty(key, value);
    }
  }
}

export default function InterfaceSettings() {
  const [selectedColorScheme, setSelectedColorScheme] = useState('Default');

  useEffect(() => {
    window.electronAPI.getSettings().then(settings => {
      setSelectedColorScheme(settings.colorScheme);
      applyColorScheme(settings.colorScheme);
    });
  }, []);

  useEffect(() => {
    applyColorScheme(selectedColorScheme);
  }, [selectedColorScheme]);

  const handleColorSchemeChange = (e) => {
    const newScheme = e.target.value;
    setSelectedColorScheme(newScheme);
    window.electronAPI.setSetting(COLOR_SCHEME_KEY, newScheme);
  };

  return (
    <div className="settings-content-panel">
      <div className="setting-section">
        <h3>Color Scheme</h3>
        <select
          className="select"
          value={selectedColorScheme}
          onChange={handleColorSchemeChange}
        >
          {Object.keys(colorSchemes).map((schemeName) => (
            <option key={schemeName} value={schemeName}>
              {schemeName}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
