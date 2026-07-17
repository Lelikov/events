import type { Answer, BookingField } from './types.ts'

export type AnswerValues = Record<string, string | string[] | boolean>

export function initialValues(fields: BookingField[]): AnswerValues {
  const v: AnswerValues = {}
  for (const f of fields) {
    if (f.field_type === 'checkbox') {
      v[f.field_key] = []
      continue
    }
    if (f.field_type === 'boolean') {
      v[f.field_key] = false
      continue
    }
    v[f.field_key] = ''
  }
  return v
}

function isEmpty(field: BookingField, value: string | string[] | boolean): boolean {
  if (field.field_type === 'checkbox') return !Array.isArray(value) || value.length === 0
  if (field.field_type === 'boolean') return value !== true
  // Text-like: a missing key (runtime undefined) or a blank/whitespace string is empty —
  // matching the server, which treats an absent required answer as a violation.
  return typeof value !== 'string' || value.trim() === ''
}

export function validateAnswers(fields: BookingField[], values: AnswerValues): string | null {
  for (const f of fields) {
    if (f.required && isEmpty(f, values[f.field_key])) return `Заполните поле «${f.label}»`
  }
  return null
}

export function buildAnswers(fields: BookingField[], values: AnswerValues): Answer[] {
  const out: Answer[] = []
  for (const f of fields) {
    const value = values[f.field_key]
    if (!isEmpty(f, value)) out.push({ key: f.field_key, value })
  }
  return out
}
