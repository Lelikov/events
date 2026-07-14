declare global {
  interface Window {
    _env_?: Record<string, string>
  }
}

// Runtime value (window._env_, injected by docker-entrypoint.d/40-env-config.sh)
// wins over the build-time import.meta.env value so one image serves every env.
export const getEnv = (key: string): string => {
  const runtimeEnv = typeof window === 'undefined' ? undefined : window._env_
  if (runtimeEnv && runtimeEnv[key]) {
    return runtimeEnv[key]
  }
  return (import.meta.env[key] as string | undefined) ?? ''
}
