export type BookingRow = {
  id: string
  start_time: string
  end_time: string
  status: string
}

export type BookingFieldAnswer = { label: string; value: string }

export type ReassignTarget = { user_id: string; name: string | null; email: string }

export type BookingDetail = {
  id: string
  title: string
  start_time: string
  end_time: string
  status: string
  client_name: string | null
  client_email: string | null
  client_time_zone: string | null
  created_at: string | null
  field_answers: BookingFieldAnswer[]
}
