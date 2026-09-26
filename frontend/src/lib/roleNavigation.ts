const KEY = 'career-navigator-role-list'

// The Opportunities list (Dashboard.tsx) is the only consumer of these —
// keep in sync with the params it actually reads from useSearchParams().
export const LEGACY_ROLE_LIST_PARAMS = ['period', 'track', 'facet', 'concept', 'sort', 'year', 'offset'] as const

// Phase 1 product shell: root-query compatibility layer (App.tsx's Root
// route) uses this to tell an old bookmarked `/?period=current`-style link
// apart from a genuine Home visit — never a generic "any query string"
// rule, since Home may grow its own query params later.
export function hasLegacyRoleListQuery(search: string): boolean {
  const params = new URLSearchParams(search)
  return LEGACY_ROLE_LIST_PARAMS.some((key) => params.has(key))
}

export function rememberRoleList(search: string) {
  try { sessionStorage.setItem(KEY, search) } catch { /* Browser storage may be disabled. */ }
}

// Opportunities lives at /opportunities, not /(Phase 1 product shell) — both
// branches must return an Opportunities URL so Role Detail's "back" link and
// the delete-role fallback never land back on Home.
export function roleListUrl(): string {
  try {
    const search = sessionStorage.getItem(KEY)
    return search ? `/opportunities?${new URLSearchParams(search).toString()}` : '/opportunities'
  } catch { return '/opportunities' }
}
