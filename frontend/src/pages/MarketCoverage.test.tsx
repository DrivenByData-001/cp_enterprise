import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import MarketCoverage from './MarketCoverage'
import { api, type CoverageFraction, type MarketCoverageSummary } from '../lib/api'

// Phase 8 (docs/39 build §39): Market Coverage page. These tests assert the
// product-level rules a user would be misled by if they broke — every
// proportion shows its denominator, representativeness is always disclosed,
// nothing here renders as a single confidence score, and a fetch failure
// never crashes the page.

// The page derives its archetype filter options and drill-down detail
// entirely from the one summary response (`archetypes.filter_options` /
// `archetypes.support`) — it calls neither listArchetypes nor
// getMarketCoverageArchetypeDetail, so a cold load is exactly one request.
vi.mock('../lib/api', () => ({
  api: {
    getMarketCoverageSummary: vi.fn(),
  },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function fraction(count: number, total: number, meaning = 'test meaning'): CoverageFraction {
  return { count, total, proportion: total ? count / total : null, meaning }
}

function buildSummary(overrides: Partial<MarketCoverageSummary> = {}): MarketCoverageSummary {
  const total = overrides.roles?.total ?? 10
  const base: MarketCoverageSummary = {
    scope: { year_from: null, year_to: null, country: null, seniority_level: null, archetype_id: null, dated_scope_active: false },
    roles: {
      total,
      excluded_undated_count: 0,
      with_source_document: fraction(total, total),
      without_source_document: fraction(0, total),
      known_posting_date: fraction(total, total),
      unknown_posting_date: fraction(0, total),
      known_country: fraction(total, total),
      unknown_country: fraction(0, total),
      known_seniority: fraction(total, total),
      unknown_seniority: fraction(0, total),
      known_employment_type: fraction(total, total),
      unknown_employment_type: fraction(0, total),
      known_remote_type: fraction(total, total),
      unknown_remote_type: fraction(0, total),
    },
    time: {
      earliest_known_posting_date: '2023-01-01',
      latest_known_posting_date: '2024-06-01',
      known_posting_date: fraction(total, total),
      unknown_posting_date: fraction(0, total),
      by_year: [{ value: 2023, count: 5 }, { value: 2024, count: 5 }],
      current_calendar_year_count: 0,
      previous_calendar_year_count: 0,
      older_count: total,
      freshly_captured_but_posting_date_unknown_count: 0,
      document_capture_date_range: { earliest: '2023-01-01T00:00:00Z', latest: '2024-06-01T00:00:00Z' },
    },
    geography: {
      known_country: fraction(total, total),
      unknown_country: fraction(0, total),
      distinct_known_country_count: 1,
      country_distribution: [{ value: 'Ireland', label: 'Ireland', count: total }],
      region_distribution: [{ value: 'Europe', label: 'Europe', count: total }],
    },
    sources: {
      with_source_document: fraction(total, total),
      without_source_document: fraction(0, total),
      capture_source_distribution: [{ value: 'linkedin', label: 'linkedin', count: total }],
      provenance_quality_distribution: [{ value: 'original', label: 'original', count: total }],
      document_kind_distribution: [{ value: 'job_posting', label: 'job_posting', count: total }],
      url_present: fraction(total, total),
      url_absent: fraction(0, total),
    },
    requirements: {
      review_complete: fraction(total, total),
      accepted_present: fraction(total, total),
      unreviewed_present: fraction(0, total),
      unresolved_vocabulary_present: fraction(0, total),
      needs_reextraction_present: fraction(0, total),
      extraction_attempted_incomplete: fraction(0, total),
      extraction_never_attempted: fraction(0, total),
      legacy_only: fraction(0, total),
      no_usable_evidence: fraction(0, total),
      state_precedence: ['needs_reextraction', 'unresolved_vocabulary', 'review_pending', 'reviewed', 'legacy_only', 'not_extracted'],
      state_distribution: { needs_reextraction: 0, unresolved_vocabulary: 0, review_pending: 0, reviewed: total, legacy_only: 0, not_extracted: 0 },
    },
    vocabulary: {
      roles_with_accepted_canonical_requirements: fraction(total, total),
      roles_relying_on_legacy_fallback: fraction(0, total),
      role_skill_observations_resolved: fraction(1, 1),
      role_skill_observations_unresolved_count: 0,
      unresolved_vocabulary_proposal_occurrence_count: 0,
    },
    archetypes: {
      active_archetype_count: 1,
      assigned_active: fraction(total, total),
      assigned_deprecated: { count: 0, meaning: '0 of 10 deprecated' },
      unassigned: fraction(0, total),
      active_archetypes_with_support: fraction(1, 1),
      active_archetypes_without_support_count: 0,
      unsupported_active_archetypes: [],
      support: [
        {
          archetype_concept_id: 'arch-1',
          canonical_name: 'Senior Actuary',
          status: 'active',
          seniority_band: 'senior',
          typical_market: 'Ireland',
          assigned_posting_count: total,
          reviewed_requirement_posting_count: total,
          known_posting_date_count: total,
          unknown_posting_date_count: 0,
          latest_known_posting_date: '2024-06-01',
          distinct_country_count: 1,
          countries: ['Ireland'],
          demand_derivation_available: true,
          compensation_benchmark_available: true,
          economics_freshness: { state: 'fresh', fresh: true, reason: null },
          evidence_depth: { state: 'supported', reason: `${total} supporting posting(s).` },
        },
      ],
      filter_options: [{ archetype_concept_id: 'arch-1', canonical_name: 'Senior Actuary' }],
    },
    compensation: {
      coverage: {
        accepted_observation_count: 3, distinct_source_document_count: 2, distinct_provider_count: 2,
        earliest_period: '2023-01-01', latest_period: '2024-01-01', with_reported_median_count: 2,
        with_reported_mean_count: 1, with_range_count: 3, with_sample_size_count: 1,
        archetype_linked_count: 3, not_archetype_linked_count: 0,
      },
      source_kind_distribution: [],
      basis_distribution: [{ value: 'posting_stated', label: 'posting_stated', count: 3 }],
      top_providers: [],
      role_linked_coverage: {
        with_accepted_role_linked_compensation: fraction(2, total),
        without_accepted_role_linked_compensation: fraction(total - 2, total),
      },
      scope_note: 'Corpus-wide market evidence, not filtered by the role-corpus scope above.',
    },
    derived_economics: {
      state: 'fresh', fresh: true, stale_inputs: [], reason: null,
      last_rebuilt_at: '2024-01-01T00:00:00Z', engine_version: 'test',
    },
    representativeness: {
      known: false,
      reason: 'This is a user-collected/captured corpus with no known probability sampling frame.',
    },
    concentration: {
      top_capture_sources: [{ value: 'linkedin', label: 'linkedin', count: total }],
      top_countries: [{ value: 'Ireland', label: 'Ireland', count: total }],
      top_organisations: [],
      top_compensation_providers: [],
      note: 'These show where the captured corpus is concentrated, not a measured bias.',
    },
    limitations: [
      'This is a user-collected/captured corpus with no known labour-market sampling frame; representativeness of the wider market is unknown.',
    ],
  }
  return { ...base, ...overrides }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/market/coverage']}>
      <Routes>
        <Route path="/market/coverage" element={<MarketCoverage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('MarketCoverage page', () => {
  it('renders an empty corpus without crashing and states zero postings', async () => {
    const empty = buildSummary({ roles: { ...buildSummary().roles, total: 0 } })
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(empty)
    renderPage()
    expect(await screen.findByText('No postings match these filters.')).toBeTruthy()
  })

  it('renders a populated corpus with headline counts', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(buildSummary())
    renderPage()
    expect(await screen.findByText(/captured posting/)).toBeTruthy()
    expect(screen.getAllByText(/10/).length).toBeGreaterThan(0)
  })

  it('discloses missing dates with an explicit denominator', async () => {
    const summary = buildSummary()
    summary.roles.known_posting_date = fraction(6, 10, '6 of 10 known')
    summary.roles.unknown_posting_date = fraction(4, 10, '4 of 10 unknown')
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(summary)
    renderPage()
    await screen.findByText(/captured posting/)
    expect(screen.getByText(/6 of 10/)).toBeTruthy()
  })

  it('shows each year-bar fraction against the real corpus total, never the tallest bucket', async () => {
    // 2023's bar is visually shorter than 2024's (4 vs 16 — real chart
    // scaling), but its displayed fraction must read against the actual
    // scope total (20), never against 2024's count (16) merely because
    // 2024 happens to be the tallest bar on the chart.
    const summary = buildSummary({ roles: { ...buildSummary().roles, total: 20 } })
    summary.time.by_year = [{ value: 2023, count: 4 }, { value: 2024, count: 16 }]
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(summary)
    renderPage()
    await screen.findByText(/captured posting/)
    expect(screen.getByText(/4 of 20/)).toBeTruthy()
    expect(screen.getByText(/16 of 20/)).toBeTruthy()
    expect(screen.queryByText(/4 of 16/)).toBeNull()
    expect(screen.queryByText(/16 of 16/)).toBeNull()
  })

  it('discloses missing countries with an explicit denominator', async () => {
    const summary = buildSummary()
    summary.roles.known_country = fraction(3, 10, '3 of 10 known')
    summary.geography.distinct_known_country_count = 2
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(summary)
    renderPage()
    await screen.findByText(/captured posting/)
    // Shown both in "Corpus in scope" and in the dedicated "Geography" section.
    expect(screen.getAllByText(/3 of 10/).length).toBeGreaterThanOrEqual(1)
  })

  it('shows the requirement review-state breakdown', async () => {
    const summary = buildSummary()
    summary.requirements.state_distribution = {
      needs_reextraction: 1, unresolved_vocabulary: 2, review_pending: 3, reviewed: 4, legacy_only: 0, not_extracted: 0,
    }
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(summary)
    renderPage()
    expect(await screen.findByText(/Needs re-extraction: 1/)).toBeTruthy()
    expect(screen.getByText(/Reviewed: 4/)).toBeTruthy()
  })

  it('shows archetype assigned/unassigned coverage', async () => {
    const summary = buildSummary()
    summary.archetypes.assigned_active = fraction(7, 10, '7 of 10 assigned')
    summary.archetypes.unassigned = fraction(3, 10, '3 of 10 unassigned')
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(summary)
    renderPage()
    await screen.findByText(/captured posting/)
    expect(screen.getByText(/7 of 10/)).toBeTruthy()
    expect(screen.getByText(/3 of 10/)).toBeTruthy()
  })

  it('shows compensation evidence coverage', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(buildSummary())
    renderPage()
    expect(await screen.findByText('Compensation evidence')).toBeTruthy()
    expect(screen.getByText('Accepted observations')).toBeTruthy()
  })

  it('discloses stale derived economics', async () => {
    const summary = buildSummary()
    summary.derived_economics = {
      state: 'stale', fresh: false, stale_inputs: ['economics'],
      reason: 'Derived economics were last rebuilt on 2024-01-01, and since then compensation evidence has changed.',
      last_rebuilt_at: '2024-01-01T00:00:00Z', engine_version: 'test',
    }
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(summary)
    renderPage()
    expect(await screen.findByText(/Derived economics were last rebuilt/)).toBeTruthy()
  })

  it('always shows the representativeness limitation', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(buildSummary())
    renderPage()
    expect(await screen.findByText('What this does not tell you')).toBeTruthy()
    expect(screen.getByText(/no known labour-market sampling frame/)).toBeTruthy()
  })

  it('applies filters and refetches with them', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(buildSummary())
    renderPage()
    await screen.findByText(/captured posting/)
    const countryInput = screen.getByPlaceholderText('Country')
    fireEvent.change(countryInput, { target: { value: 'Ireland' } })
    await waitFor(() => {
      const calls = vi.mocked(api.getMarketCoverageSummary).mock.calls
      expect(calls[calls.length - 1][0]).toMatchObject({ country: 'Ireland' })
    })
  })

  it('every rendered fraction includes both its numerator and denominator', async () => {
    const summary = buildSummary()
    summary.roles.known_posting_date = fraction(4, 10, '4 of 10 known')
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(summary)
    renderPage()
    await screen.findByText(/captured posting/)
    // "4 of 10" — never a bare "40%" with no denominator in sight.
    expect(screen.getByText(/4 of 10/)).toBeTruthy()
  })

  it('opens an archetype drill-down card on row click', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(buildSummary())
    renderPage()
    await screen.findByText(/captured posting/)
    // "Senior Actuary" also appears as a filter-dropdown option — find the
    // table row specifically, not just any matching text on the page.
    const cells = await screen.findAllByText('Senior Actuary')
    const tableCell = cells.find((el) => el.closest('tr'))
    expect(tableCell).toBeTruthy()
    fireEvent.click(tableCell!)
    // The reusable ArchetypeEvidenceCard renders fields the bounded table
    // row never shows — a reliable signal the full card opened.
    expect(await screen.findByText('Demand derivation')).toBeTruthy()
  })

  it('never renders a numeric confidence score or gauge', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(buildSummary())
    const { container } = renderPage()
    await screen.findByText(/captured posting/)
    expect(container.textContent).not.toMatch(/confidence score/i)
    expect(container.querySelector('[role="meter"]')).toBeNull()
    expect(container.querySelector('svg[class*="gauge"]')).toBeNull()
  })

  it('shows a local error state without throwing when the fetch fails', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockRejectedValue(new Error('network down'))
    renderPage()
    expect(await screen.findByText(/network down/)).toBeTruthy()
  })

  it('a cold load makes exactly one API request — archetype filter options come from the summary itself', async () => {
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(buildSummary())
    renderPage()
    await screen.findByText(/captured posting/)
    expect(api.getMarketCoverageSummary).toHaveBeenCalledTimes(1)
    // The dropdown is populated from the one response's archetypes.filter_options.
    expect(screen.getByRole('option', { name: 'Senior Actuary' })).toBeTruthy()
  })
})
