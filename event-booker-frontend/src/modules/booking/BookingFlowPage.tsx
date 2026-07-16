import { useEffect, useMemo, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { createBooking, getEventType } from './bookerApi.ts'
import { SlotPicker } from './SlotPicker.tsx'
import { GuestForm } from './GuestForm.tsx'
import { Confirmation } from './Confirmation.tsx'
import { formatRange } from './datetime.ts'
import { navigateTo } from '../shared/routing.ts'
import type { BookingConfirmation, EventType } from './types.ts'

type Step = 'slot' | 'details' | 'done'

function detectTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export function BookingFlowPage({ eventTypeId }: { eventTypeId: string }) {
  const [eventType, setEventType] = useState<EventType | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [timeZone, setTimeZone] = useState(detectTimeZone)
  const [step, setStep] = useState<Step>('slot')
  const [selected, setSelected] = useState<string | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [confirmation, setConfirmation] = useState<BookingConfirmation | null>(null)

  useEffect(() => {
    let active = true
    getEventType(eventTypeId)
      .then((et) => active && setEventType(et))
      .catch(() => active && setNotFound(true))
    return () => {
      active = false
    }
  }, [eventTypeId])

  const durationLabel = useMemo(() => (eventType ? `${eventType.duration_minutes} мин` : ''), [eventType])

  if (notFound) {
    return (
      <main className="booker-shell">
        <h1>Тип встречи не найден</h1>
        <a href="/">На главную</a>
      </main>
    )
  }

  if (confirmation) {
    return (
      <main className="booker-shell">
        <Confirmation confirmation={confirmation} />
      </main>
    )
  }

  function handleSelect(startTime: string) {
    setSelected(startTime)
    setBanner(null)
    setStep('details')
  }

  async function handleSubmit(name: string, email: string) {
    if (selected === null) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const result = await createBooking({
        event_type_id: eventTypeId,
        name,
        email,
        start_time: selected,
        time_zone: timeZone,
      })
      setConfirmation(result)
      setStep('done')
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setBanner('Этот слот только что заняли. Выберите другое время.')
        setSelected(null)
        setStep('slot')
        return
      }
      if (err instanceof ApiError && err.status === 422) {
        setSubmitError(err.message)
        return
      }
      setSubmitError('Сервис временно недоступен. Попробуйте ещё раз.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className={`booker-shell${step === 'slot' ? ' booker-shell--wide' : ''}`}>
      <h1>{eventType ? eventType.title : 'Бронирование'}</h1>
      {durationLabel && <p className="muted">{durationLabel}</p>}
      {banner && <p className="banner-error">{banner}</p>}

      {step === 'slot' && eventType && (
        <SlotPicker
          eventTypeId={eventTypeId}
          eventTitle={eventType.title}
          durationMinutes={eventType.duration_minutes}
          timeZone={timeZone}
          onTimeZoneChange={setTimeZone}
          onSelectSlot={handleSelect}
        />
      )}

      {step === 'details' && selected && (
        <div>
          <p className="muted">Выбрано: {formatRange(selected, selected, timeZone)}</p>
          <GuestForm onSubmit={handleSubmit} onBack={() => setStep('slot')} submitError={submitError} submitting={submitting} />
        </div>
      )}

      <p className="inline-actions">
        <button type="button" onClick={() => navigateTo('/')}>
          ← Все типы встреч
        </button>
      </p>
    </main>
  )
}
