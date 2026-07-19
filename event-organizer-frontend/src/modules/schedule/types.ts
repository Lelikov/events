// day_of_week: 1=Mon..7=Sun (ISO). Times as "HH:MM" on write; the BFF returns
// "HH:MM:SS" on read — the editor normalises to "HH:MM".
export type WeeklyHour = { day_of_week: number; start_time: string; end_time: string }
export type DateOverride = { date: string; start_time: string | null; end_time: string | null }
export type Travel = {
  time_zone: string
  start_date: string
  end_date: string | null
  prev_time_zone: string | null
}
export type ScheduleMeta = { id: string; owner_user_id: string; name: string; time_zone: string }

export type ScheduleBundle = {
  schedule: ScheduleMeta
  weekly_hours: WeeklyHour[]
  date_overrides: DateOverride[]
  travel_schedules: Travel[]
}

export type UpsertScheduleBody = {
  name: string
  time_zone: string
  weekly_hours: WeeklyHour[]
  date_overrides: DateOverride[]
}

export type TravelBody = { travel_schedules: Travel[] }
