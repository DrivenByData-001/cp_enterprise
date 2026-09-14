const KEY = 'career-navigator-role-list'

export function rememberRoleList(search: string) {
  try { sessionStorage.setItem(KEY, search) } catch { /* Browser storage may be disabled. */ }
}

export function roleListUrl(): string {
  try {
    const search = sessionStorage.getItem(KEY)
    return search ? `/?${new URLSearchParams(search).toString()}` : '/'
  } catch { return '/' }
}
