import { Component, type ErrorInfo, type ReactNode } from 'react'
import * as Sentry from '@sentry/react'

type Props = {
  children: ReactNode
}

type State = {
  error: Error | null
}

/**
 * Last-resort boundary: without it any render-time exception unmounts the
 * whole React tree and leaves a blank page with no way to recover.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    Sentry.captureException(error, { extra: { componentStack: info.componentStack } })
    console.error('Unhandled render error', error, info)
  }

  render(): ReactNode {
    if (!this.state.error) {
      return this.props.children
    }

    return (
      <main className="login-shell">
        <section className="login-card">
          <h1>Что-то пошло не так</h1>
          <p className="muted">{this.state.error.message}</p>
          <div className="inline-actions">
            <button type="button" onClick={() => window.location.assign('/')}>
              На главную
            </button>
          </div>
        </section>
      </main>
    )
  }
}
