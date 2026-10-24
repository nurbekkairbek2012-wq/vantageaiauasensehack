import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Label from './Label.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Label />
  </StrictMode>,
)
