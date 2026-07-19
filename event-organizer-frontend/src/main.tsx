import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ErrorBoundary } from './modules/shared/ErrorBoundary.tsx'
import 'events-design-system/styles.css'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './modules/auth/AuthContext.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ErrorBoundary>
  </StrictMode>,
)
