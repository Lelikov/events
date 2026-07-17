import { describe, expect, it } from 'vitest'
import { buildAnswers, initialValues, validateAnswers } from './answers.ts'
import type { BookingField } from './types.ts'

const f = (over: Partial<BookingField>): BookingField => ({
  field_key: 'k', field_type: 'text', label: 'L', placeholder: null, required: false, options: [], ...over,
})

describe('answers helpers', () => {
  it('initialValues seeds per type', () => {
    const v = initialValues([f({ field_key: 't', field_type: 'text' }), f({ field_key: 'c', field_type: 'checkbox' }), f({ field_key: 'b', field_type: 'boolean' })])
    expect(v).toEqual({ t: '', c: [], b: false })
  })

  it('validateAnswers flags the first required-empty field', () => {
    const fields = [f({ field_key: 'reason', field_type: 'textarea', label: 'Причина', required: true })]
    expect(validateAnswers(fields, { reason: '' })).toContain('Причина')
    expect(validateAnswers(fields, { reason: 'ok' })).toBeNull()
  })

  it('buildAnswers omits empty optional fields and includes filled ones', () => {
    const fields = [
      f({ field_key: 'reason', field_type: 'textarea' }),
      f({ field_key: 'topics', field_type: 'checkbox', options: [{ value: 'a', label: 'A' }] }),
      f({ field_key: 'agree', field_type: 'boolean' }),
    ]
    const out = buildAnswers(fields, { reason: '', topics: ['a'], agree: true })
    expect(out).toEqual([{ key: 'topics', value: ['a'] }, { key: 'agree', value: true }])
  })
})

describe('answers helpers — required-empty edge cases (mirror server)', () => {
  it('flags required checkbox/boolean/whitespace and a missing key as empty', () => {
    expect(validateAnswers([f({ field_key: 'c', field_type: 'checkbox', required: true })], { c: [] })).toContain('L')
    expect(validateAnswers([f({ field_key: 'b', field_type: 'boolean', required: true })], { b: false })).toContain('L')
    expect(validateAnswers([f({ field_key: 't', field_type: 'text', required: true })], { t: '   ' })).toContain('L')
    // missing key for a required text field → still flagged (server treats absent as empty)
    expect(validateAnswers([f({ field_key: 't', field_type: 'text', required: true })], {})).toContain('L')
  })

  it('buildAnswers omits a text field whose key is missing', () => {
    expect(buildAnswers([f({ field_key: 't', field_type: 'text' })], {})).toEqual([])
  })
})
