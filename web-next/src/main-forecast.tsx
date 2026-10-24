import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Forecast from './Forecast.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Forecast />
  </StrictMode>,
)
