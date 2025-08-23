
import React, { useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter as Router, Routes, Route } from 'react-router-dom'
import App from './App.jsx'
import './styles.css'
import { applyColorScheme } from './colorSchemes'

function Main() {
  useEffect(() => {
    window.electronAPI.getSettings().then(settings => {
      if (settings.colorScheme) {
        applyColorScheme(settings.colorScheme)
      }
    })
  }, [])

  return (
    <React.StrictMode>
      <Router>
        <Routes>
          <Route path="/" element={<App />} />
        </Routes>
      </Router>
    </React.StrictMode>
  )
}

const root = createRoot(document.getElementById('root'))
root.render(<Main />)
