import { useState, type FormEvent } from 'react'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type Props = {
  onSubmit: (name: string, email: string) => void
  onBack: () => void
  submitError?: string | null
  submitting?: boolean
}

export function GuestForm({ onSubmit, onBack, submitError, submitting }: Props) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (name.trim() === '') {
      setError('Введите имя')
      return
    }
    if (!EMAIL_RE.test(email)) {
      setError('Введите корректный email')
      return
    }
    setError(null)
    onSubmit(name.trim(), email.trim())
  }

  return (
    <form onSubmit={handleSubmit}>
      <label className="field">
        <span>Имя</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Email</span>
        <input value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      {error && <p className="field-error">{error}</p>}
      {submitError && <p className="banner-error">{submitError}</p>}
      <div className="inline-actions">
        <button type="button" onClick={onBack} disabled={submitting}>
          ← Назад
        </button>
        <button type="submit" disabled={submitting}>
          {submitting ? 'Бронируем…' : 'Забронировать'}
        </button>
      </div>
    </form>
  )
}
