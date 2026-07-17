import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as Sentry from '@sentry/react'
import 'events-design-system/styles.css'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from 'events-design-system'
import { initSentry } from './observability/sentry'

initSentry()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary onError={(e, info) => Sentry.captureException(e, { extra: { componentStack: info.componentStack } })}>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
