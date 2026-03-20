// src/WebsearchSettings.jsx
import React, { useEffect, useMemo, useState } from 'react';

export default function WebsearchSettings({
  searxUrl,
  setSearxUrl,
  engines,
  setEngines,
}) {
  const KNOWN_ENGINES = useMemo(
    () => ["google","bing","yahoo","duckduckgo","brave","github","stackoverflow","reddit","arxiv"],
    []
  );

  const [custom, setCustom] = useState("");

  const toggleEngine = (name) => {
    const set = new Set(engines || []);
    if (set.has(name)) set.delete(name); else set.add(name);
    setEngines(Array.from(set));
  };

  const addCustom = () => {
    const name = custom.trim();
    if (!name) return;
    const set = new Set(engines || []);
    set.add(name);
    setEngines(Array.from(set));
    setCustom("");
  };

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
        {KNOWN_ENGINES.map(name => (
          <label key={name} className="engine-row">
            <input
              type="checkbox"
              checked={Array.isArray(engines) ? engines.includes(name) : false}
              onChange={() => toggleEngine(name)}
            />
            <span>{name}</span>
          </label>
        ))}
      </div>
    </div>
  </div>
);
}
