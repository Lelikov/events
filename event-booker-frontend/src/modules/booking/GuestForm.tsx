import { useState, type FormEvent } from 'react'
import type { Answer, BookingField } from './types.ts'
import { type AnswerValues, buildAnswers, initialValues, validateAnswers } from './answers.ts'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type Props = {
  fields: BookingField[]
  onSubmit: (name: string, email: string, answers: Answer[]) => void
  onBack: () => void
  submitError?: string | null
  submitting?: boolean
}

export function GuestForm({ fields, onSubmit, onBack, submitError, submitting }: Props) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [values, setValues] = useState<AnswerValues>(() => initialValues(fields))
  const [error, setError] = useState<string | null>(null)

  function setValue(key: string, value: string | string[] | boolean) {
    setValues((v) => ({ ...v, [key]: value }))
  }

  function toggleCheckbox(key: string, optionValue: string, checked: boolean) {
    setValues((v) => {
      const current = Array.isArray(v[key]) ? (v[key] as string[]) : []
      const next = checked ? [...current, optionValue] : current.filter((x) => x !== optionValue)
      return { ...v, [key]: next }
    })
  }

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
    const answerError = validateAnswers(fields, values)
    if (answerError) {
      setError(answerError)
      return
    }
    setError(null)
    onSubmit(name.trim(), email.trim(), buildAnswers(fields, values))
  }

  return (
    <form onSubmit={handleSubmit}>
      <label className="field">
        <span>Имя</span>
        <input name="name" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Email</span>
        <input name="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>

      {fields.map((f) => (
        <DynamicField key={f.field_key} field={f} value={values[f.field_key]} onChange={setValue} onToggle={toggleCheckbox} />
      ))}

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

type FieldProps = {
  field: BookingField
  value: string | string[] | boolean
  onChange: (key: string, value: string | string[] | boolean) => void
  onToggle: (key: string, optionValue: string, checked: boolean) => void
}

function DynamicField({ field, value, onChange, onToggle }: FieldProps) {
  const label = (
    <span>
      {field.label}
      {field.required ? ' *' : ''}
    </span>
  )
  const name = `field-${field.field_key}`

  if (field.field_type === 'textarea') {
    return (
      <label className="field">
        {label}
        <textarea
          name={name}
          placeholder={field.placeholder ?? ''}
          value={value as string}
          onChange={(e) => onChange(field.field_key, e.target.value)}
        />
      </label>
    )
  }
  if (field.field_type === 'select') {
    return (
      <label className="field">
        {label}
        <select name={name} value={value as string} onChange={(e) => onChange(field.field_key, e.target.value)}>
          <option value="">—</option>
          {field.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    )
  }
  if (field.field_type === 'radio') {
    return (
      <div className="field">
        {label}
        <div className="radio-group">
          {field.options.map((o) => (
            <label key={o.value} className="radio-option">
              <input
                type="radio"
                name={name}
                value={o.value}
                checked={value === o.value}
                onChange={() => onChange(field.field_key, o.value)}
              />
              {o.label}
            </label>
          ))}
        </div>
      </div>
    )
  }
  if (field.field_type === 'checkbox') {
    const list = Array.isArray(value) ? value : []
    return (
      <div className="field">
        {label}
        <div className="checkbox-group">
          {field.options.map((o) => (
            <label key={o.value} className="checkbox-option">
              <input
                type="checkbox"
                checked={list.includes(o.value)}
                onChange={(e) => onToggle(field.field_key, o.value, e.target.checked)}
              />
              {o.label}
            </label>
          ))}
        </div>
      </div>
    )
  }
  if (field.field_type === 'boolean') {
    return (
      <label className="checkbox-option">
        <input type="checkbox" checked={value === true} onChange={(e) => onChange(field.field_key, e.target.checked)} />
        {field.label}
        {field.required ? ' *' : ''}
      </label>
    )
  }
  return (
    <label className="field">
      {label}
      <input
        name={name}
        placeholder={field.placeholder ?? ''}
        value={value as string}
        onChange={(e) => onChange(field.field_key, e.target.value)}
      />
    </label>
  )
}
