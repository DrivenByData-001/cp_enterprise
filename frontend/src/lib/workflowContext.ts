import { useLocation } from 'react-router-dom'

// Workflow context vs navigation state: `location.state.returnTo` only says
// where the user came from. These URL params say what they are *doing*, so
// they survive refresh and deep links:
//   ?application=<id>  — the user is preparing this (existing) application and
//                        is visiting shared opportunity/requirements/comparison
//                        screens on its behalf.
//   ?intent=apply      — the user is browsing Opportunities in order to start
//                        an application; no application record exists yet.
// `application` wins when both are present. Neither is ever added to a
// journey that started from plain Opportunities.
export const APPLICATION_PARAM = 'application'
export const INTENT_PARAM = 'intent'
export const APPLY_INTENT = 'apply'
export const APPLY_INTENT_URL = `/opportunities?${INTENT_PARAM}=${APPLY_INTENT}`

const ID_RE = /^[A-Za-z0-9_-]{1,64}$/

export type WorkflowContext = { applicationId: string | null; applyIntent: boolean }

export function readWorkflowContext(search: string | URLSearchParams): WorkflowContext {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search
  const raw = params.get(APPLICATION_PARAM)
  const applicationId = raw && ID_RE.test(raw) ? raw : null
  return { applicationId, applyIntent: !applicationId && params.get(INTENT_PARAM) === APPLY_INTENT }
}

export function isApplicationWorkflow(search: string): boolean {
  const ctx = readWorkflowContext(search)
  return ctx.applicationId !== null || ctx.applyIntent
}

// Append the current workflow context to `path` (which may already carry a
// query string). No context → `path` is returned untouched.
export function withWorkflow(path: string, ctx: WorkflowContext): string {
  const extra = ctx.applicationId ? { [APPLICATION_PARAM]: ctx.applicationId } : ctx.applyIntent ? { [INTENT_PARAM]: APPLY_INTENT } : null
  if (!extra) return path
  const [base, query = ''] = path.split('?')
  const params = new URLSearchParams(query)
  for (const [k, v] of Object.entries(extra)) params.set(k, v)
  return `${base}?${params.toString()}`
}

// Copy the context keys from `current` onto a freshly built params object, so
// code that rewrites the whole query string (e.g. `setParams({ step })`)
// cannot erase the workflow.
export function keepWorkflow(next: Record<string, string>, current: URLSearchParams): URLSearchParams {
  const out = new URLSearchParams(next)
  for (const key of [APPLICATION_PARAM, INTENT_PARAM]) {
    const value = current.get(key)
    if (value !== null) out.set(key, value)
  }
  return out
}

export function useWorkflowContext() {
  const { search } = useLocation()
  const ctx = readWorkflowContext(search)
  return { ...ctx, link: (path: string) => withWorkflow(path, ctx) }
}
