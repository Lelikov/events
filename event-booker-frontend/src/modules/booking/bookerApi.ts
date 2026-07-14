import { apiRequest } from '../shared/api.ts'
import type { BookingConfirmation, CreateBookingBody, EventType, Slots } from './types.ts'

export function listEventTypes(): Promise<EventType[]> {
  return apiRequest<{ items: EventType[] }>('/api/public/event-types').then((r) => r.items)
}

export function getEventType(id: string): Promise<EventType> {
  return apiRequest<EventType>(`/api/public/event-types/${encodeURIComponent(id)}`)
}

export function getSlots(
  eventTypeId: string,
  startISO: string,
  endISO: string,
  timeZone: string,
): Promise<Slots> {
  const params = new URLSearchParams({
    event_type_id: eventTypeId,
    start: startISO,
    end: endISO,
    time_zone: timeZone,
  })
  return apiRequest<Slots>(`/api/public/slots?${params.toString()}`)
}

export function createBooking(body: CreateBookingBody): Promise<BookingConfirmation> {
  return apiRequest<BookingConfirmation>('/api/public/bookings', { method: 'POST', body })
}
