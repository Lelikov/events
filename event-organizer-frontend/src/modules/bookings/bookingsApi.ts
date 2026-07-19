import { apiRequest } from '../shared/api.ts'
import type { BookingRow } from './types.ts'

export async function getBookings(): Promise<BookingRow[]> {
  return apiRequest<BookingRow[]>('/api/me/bookings')
}
