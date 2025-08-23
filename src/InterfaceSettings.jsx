import React, { useState, useEffect } from 'react';
import { colorSchemes, applyColorScheme } from './colorSchemes';

const COLOR_SCHEME_KEY = 'colorScheme';

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
