import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Timelapse from './Timelapse.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Timelapse />
  </StrictMode>,
)
