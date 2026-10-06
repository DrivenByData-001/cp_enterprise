export const APPLICATION_CONTEXT_PARAM = 'application'

export function applicationContextId(search: string): string | null {
  return new URLSearchParams(search).get(APPLICATION_CONTEXT_PARAM)
}

export function withApplicationContext(path: string, applicationId: string | null | undefined): string {
  if (!applicationId) return path
  const [pathname, hash = ''] = path.split('#', 2)
  const separator = pathname.includes('?') ? '&' : '?'
  return `${pathname}${separator}${APPLICATION_CONTEXT_PARAM}=${encodeURIComponent(applicationId)}${hash ? `#${hash}` : ''}`
}
