// src/WebsearchSettings.jsx
import React from 'react';
import { WEBSEARCH_ENGINE_OPTIONS, normalizeWebsearchEngines } from './websearchEngines'

export default function WebsearchSettings({
  searxUrl,
  setSearxUrl,
  engines,
  setEngines,
}) {
  const toggleEngine = (name) => {
    const set = new Set(engines || []);
    if (set.has(name)) set.delete(name); else set.add(name);
    setEngines(normalizeWebsearchEngines(Array.from(set)));
  }

return (
  <div className="settings-content-panel">
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
