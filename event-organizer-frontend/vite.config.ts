import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// All backend traffic goes to the event-organizer BFF: it authenticates the
// organizer and proxies /api/me/* to event-scheduling / event-users itself.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiBaseUrl = env.VITE_API_BASE_URL || 'http://localhost:8006'
  const toOrganizer = { target: apiBaseUrl, changeOrigin: true }
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': toOrganizer,
        '/auth': toOrganizer,
        '/health': toOrganizer,
      },
    },
  }
})
