import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@/index.css'
import { AppRoot } from '@/app/AppRoot'

const rootElement = document.getElementById('root')

if (rootElement === null) {
  throw new Error('Missing #root element in index.html')
}

createRoot(rootElement).render(
  <StrictMode>
    <AppRoot />
  </StrictMode>,
)
