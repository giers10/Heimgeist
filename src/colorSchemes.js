const colorSchemes = {
  'Nightsky': {
    '--bg': '#0a0e1a',
    '--panel': '#18203a',
    '--text': '#ffffff',
    '--muted': '#aab5c4',
    '--accent': '#4a90e2',
    '--border': '#304060',
    '--input-bg': '#121a35',
    '--user-msg-bg': '#1a2545',
    '--assistant-msg-bg': '#15203a',
    '--active-bg': 'rgba(74, 144, 226, 0.15)',
    '--hover-bg': 'rgba(255, 255, 255, 0.05)',
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
    '--active-bg': 'rgba(136, 136, 136, 0.15)',
    '--hover-bg': 'rgba(255, 255, 255, 0.05)',
  },
  'Japan': {
    '--bg': '#ffffff',
    '--panel': '#f5f5f5',
    '--text': '#000000',
    '--muted': '#444444',
    '--accent': '#e74c3c', /* Vibrant Red */
    '--border': '#999999',
    '--input-bg': '#ffffff',
    '--user-msg-bg': '#f0f0f0',
    '--assistant-msg-bg': '#f0f0f0',
    '--active-bg': 'rgba(231, 76, 60, 0.15)', /* Light red for active */
    '--hover-bg': 'rgba(231, 76, 60, 0.08)', /* Lighter red for hover */
  },
  'Lime': {
    '--bg': '#f0fff0',
    '--panel': '#e0ffe0',
    '--text': '#1a1a1a',
    '--muted': '#72a272ff',
    '--accent': '#8e9f38ff',
    '--border': '#a0c0a0',
    '--input-bg': '#ffffff',
    '--user-msg-bg': '#f8f7adff',
    '--assistant-msg-bg': '#f5fff5',
    '--active-bg': 'rgba(104, 159, 56, 0.2)',
    '--hover-bg': 'rgba(104, 159, 56, 0.1)',
  },
  'Vampire': {
    '--bg': '#1a050a',
    '--panel': '#2a1015',
    '--text': '#ffefff',
    '--muted': '#c0a0a0',
    '--accent': '#d81b60',
    '--border': '#4a2025',
    '--input-bg': '#200a10',
    '--user-msg-bg': '#331119',
    '--assistant-msg-bg': '#271019',
    '--active-bg': 'rgba(216, 27, 96, 0.15)',
    '--hover-bg': 'rgba(255, 255, 255, 0.05)',
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

export { colorSchemes, applyColorScheme };
