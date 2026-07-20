import { apiRequest } from '../shared/api.ts'
import type { BookingDetail, BookingRow } from './types.ts'

export async function getBookings(): Promise<BookingRow[]> {
  return apiRequest<BookingRow[]>('/api/me/bookings')
}

export async function getBookingDetail(id: string): Promise<BookingDetail> {
  return apiRequest<BookingDetail>(`/api/me/bookings/${id}`)
}
