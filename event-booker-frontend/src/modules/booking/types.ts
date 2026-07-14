export type EventType = {
  id: string
  slug: string
  title: string
  duration_minutes: number
}

export type Slots = {
  event_type_id: string
  time_zone: string
  slots: Record<string, string[]>
}

export type CreateBookingBody = {
  event_type_id: string
  name: string
  email: string
  start_time: string
  time_zone: string
}

export type BookingConfirmation = {
  booking_id: string
  event_type_title: string
  start_time: string
  end_time: string
  status: string
  time_zone: string
}
