import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Citizen from './Citizen.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Citizen />
  </StrictMode>,
)
