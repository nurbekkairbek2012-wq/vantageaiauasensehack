import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Economy from './Economy.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Economy />
  </StrictMode>,
)
