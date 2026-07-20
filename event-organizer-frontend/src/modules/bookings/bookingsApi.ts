import { apiRequest } from '../shared/api.ts'
import type { BookingDetail, BookingRow, ReassignTarget } from './types.ts'

export async function getBookings(): Promise<BookingRow[]> {
  return apiRequest<BookingRow[]>('/api/me/bookings')
}

export async function getBookingDetail(id: string): Promise<BookingDetail> {
  return apiRequest<BookingDetail>(`/api/me/bookings/${id}`)
}

export async function getBookingSlots(
  id: string,
  date: string,
  timeZone: string,
): Promise<{ date: string; time_zone: string; slots: string[] }> {
  const q = new URLSearchParams({ date, time_zone: timeZone })
  return apiRequest(`/api/me/bookings/${id}/slots?${q.toString()}`)
}

export async function rescheduleBooking(id: string, startTime: string): Promise<void> {
  await apiRequest(`/api/me/bookings/${id}/reschedule`, { method: 'POST', body: { start_time: startTime } })
}

export async function getReassignTargets(id: string): Promise<ReassignTarget[]> {
  return apiRequest<ReassignTarget[]>(`/api/me/bookings/${id}/reassign-targets`)
}

export async function reassignBooking(id: string, newHostUserId: string): Promise<void> {
  await apiRequest(`/api/me/bookings/${id}/reassign`, { method: 'POST', body: { new_host_user_id: newHostUserId } })
}
