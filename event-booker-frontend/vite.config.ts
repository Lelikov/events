import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { sentryVitePlugin } from '@sentry/vite-plugin'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // All backend traffic goes to the event-booker BFF (the public trust boundary).
  const apiBaseUrl = env.VITE_API_BASE_URL || 'http://localhost:8005'
  const toBooker = { target: apiBaseUrl, changeOrigin: true }
  return {
    plugins: [
      react(),
      sentryVitePlugin({
        org: process.env.SENTRY_ORG,
        project: process.env.SENTRY_PROJECT,
        authToken: process.env.SENTRY_AUTH_TOKEN,
        release: { name: process.env.SENTRY_RELEASE },
        disable: !process.env.SENTRY_AUTH_TOKEN,
        sourcemaps: { filesToDeleteAfterUpload: ['./dist/**/*.map'] },
      }),
    ],
    build: { sourcemap: 'hidden' },
    server: { proxy: { '/api': toBooker, '/health': toBooker } },
  }
})
