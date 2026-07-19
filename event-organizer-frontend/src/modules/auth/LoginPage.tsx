import { type FormEvent, useMemo, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { navigateTo } from '../shared/routing.ts'
import { login } from './authApi.ts'
import { useAuth } from './useAuth.ts'

function translateLoginError(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Не удалось выполнить вход'
  if (err.status === 401) return 'Неверный email или пароль'
  return err.message
}

export function LoginPage() {
  const { loginWithToken } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSubmit = useMemo(
    () => email.trim().length > 0 && password.trim().length > 0,
    [email, password],
  )

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return

    setError(null)
    setLoading(true)
    try {
      const response = await login({ email: email.trim(), password })
      loginWithToken(response.access_token)
      navigateTo('/', { replace: true })
    } catch (err) {
      setError(translateLoginError(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-shell">
      <section className="login-split">
        <aside className="login-brand">
          <div className="login-brand-dots" />
          <div className="login-brand-logo">
            <div className="app-logo">EO</div>
            <span>Кабинет организатора</span>
          </div>
          <div>
            <h1>Ваше расписание<br />и встречи</h1>
            <p>Управляйте доступностью, бронями и профилем в одном месте.</p>
          </div>
          <div className="login-brand-foot">Сессия защищена · вход по паролю</div>
        </aside>

        <div className="login-form-panel">
          <div>
            <p className="eyebrow">Вход в кабинет</p>
            <h1>С возвращением</h1>
          </div>

          <form className="form" onSubmit={handleLogin}>
            <label className="field">
              <span>Email</span>
              <input
                type="email"
                name="email"
                autoComplete="username"
                placeholder="organizer@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>

            <label className="field">
              <span>Пароль</span>
              <input
                type="password"
                name="password"
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>

            <div className="inline-actions">
              <button type="submit" disabled={loading || !canSubmit}>
                {loading ? 'Входим…' : 'Войти'}
              </button>
            </div>
          </form>

          {error && <p className="error-text">{error}</p>}
        </div>
      </section>
    </main>
  )
}
