export type FieldOption = {
  value: string
  label: string
}

export type BookingField = {
  field_key: string
  field_type: 'text' | 'textarea' | 'select' | 'radio' | 'checkbox' | 'boolean'
  label: string
  placeholder: string | null
  required: boolean
  options: FieldOption[]
}

export type Answer = {
  key: string
  value: string | string[] | boolean
}

export type EventType = {
  id: string
  slug: string
  title: string
  duration_minutes: number
  booking_fields?: BookingField[]
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
  answers?: Answer[]
}

export type BookingConfirmation = {
  booking_id: string
  event_type_title: string
  start_time: string
  end_time: string
  status: string
  time_zone: string
}
