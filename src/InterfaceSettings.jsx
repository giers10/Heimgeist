import React, { useEffect, useState } from 'react'
import { colorSchemes, applyColorScheme } from './colorSchemes'

const COLOR_SCHEME_KEY = 'colorScheme'
const UI_SCALE_KEY = 'uiScale'
const DEFAULT_UI_SCALE = 1
const MIN_UI_SCALE = 0.7
const MAX_UI_SCALE = 1.3
const UI_SCALE_STEP = 0.05

function normalizeUiScale(value) {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return DEFAULT_UI_SCALE
  }

  return Math.min(MAX_UI_SCALE, Math.max(MIN_UI_SCALE, Math.round(numericValue * 100) / 100))
}

export default function InterfaceSettings() {
  const [selectedColorScheme, setSelectedColorScheme] = useState('Default')
  const [uiScale, setUiScale] = useState(DEFAULT_UI_SCALE)

  useEffect(() => {
    window.electronAPI.getSettings().then(settings => {
      const schemeName = settings.colorScheme || 'Default'
      setSelectedColorScheme(schemeName)
      setUiScale(normalizeUiScale(settings.uiScale))
      applyColorScheme(schemeName)
    })
  }, [])

  useEffect(() => {
    applyColorScheme(selectedColorScheme)
  }, [selectedColorScheme])

  const handleColorSchemeChange = (event) => {
    const newScheme = event.target.value
    setSelectedColorScheme(newScheme)
    window.electronAPI.setSetting(COLOR_SCHEME_KEY, newScheme)
  }

  const persistUiScale = (value) => {
    const nextScale = normalizeUiScale(value)
    setUiScale(nextScale)
    window.electronAPI.setSetting(UI_SCALE_KEY, nextScale)
  }

  const handleUiScaleChange = (event) => {
    persistUiScale(event.target.value)
  }

  const handleUiScaleReset = () => {
    persistUiScale(DEFAULT_UI_SCALE)
  }

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
      <div className="setting-section">
        <h3>UI Scale</h3>
        <div className="setting-control-row">
          <input
            type="range"
            className="range-input"
            min={MIN_UI_SCALE}
            max={MAX_UI_SCALE}
            step={UI_SCALE_STEP}
            value={uiScale}
            onChange={handleUiScaleChange}
          />
          <span className="setting-value">{Math.round(uiScale * 100)}%</span>
          <button
            type="button"
            className="button"
            onClick={handleUiScaleReset}
            disabled={uiScale === DEFAULT_UI_SCALE}
          >
            Reset
          </button>
        </div>
        <p className="setting-description">
          Scales the whole interface, including fonts, spacing, and controls. 100% is the default size.
        </p>
      </div>
    </div>
  )
}
