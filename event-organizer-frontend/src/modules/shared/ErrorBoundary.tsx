import type { ReactNode } from 'react'
import { ErrorBoundary as DSErrorBoundary } from 'events-design-system'

export function ErrorBoundary({ children }: { children: ReactNode }) {
  return (
    <DSErrorBoundary homeHref="/" onError={(e) => console.error(e)}>
      {children}
    </DSErrorBoundary>
  )
}
