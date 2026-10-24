import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import MapApp from './MapApp.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MapApp />
  </StrictMode>,
)
