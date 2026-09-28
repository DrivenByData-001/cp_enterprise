import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Trends from './Trends'
import { api, type CorpusOverview, type MarketCoverageSummary, type TopRequirements } from '../lib/api'

// Phase 8 (docs/39 build §20/§40): Trends' coverage strip and per-requirement
// evidence-depth badge are context around the existing trend machinery —
// these tests assert they render, link out, and never change the underlying
// (unmodified) trend classification, and that a coverage failure never
// blanks the rest of the page.

vi.mock('../lib/api', () => ({
  api: {
    getTrendOverview: vi.fn(),
    getTopRequirements: vi.fn(),
    getRequirementTrend: vi.fn(),
    getCooccurrence: vi.fn(),
    compareDimension: vi.fn(),
    getTrendMethodology: vi.fn(),
    getMarketCoverageSummary: vi.fn(),
  },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const overview: CorpusOverview = {
  sample_size: 10,
  by_year: [{ value: 2023, role_count: 5 }, { value: 2024, role_count: 5 }],
  by_country: [{ value: 'Ireland', role_count: 10 }],
  by_region: [{ value: 'Europe', role_count: 10 }],
  by_seniority: [{ value: 'senior', role_count: 10 }],
  by_career_track: [{ value: 'actuarial', role_count: 10 }],
}

const topRequirements: TopRequirements = {
  sample_size: 10,
  insufficient_sample: false,
  min_sample_size: 5,
  items: [
    {
      concept_id: 'c1', label: 'Python', type_code: 'tool', is_canonical: true, role_count: 6,
      proportion: 0.6, by_requirement_type: { required: 4, preferred: 2, inferred: 0 },
    },
  ],
}

function coverageSummary(overrides: Partial<MarketCoverageSummary['roles']> = {}): MarketCoverageSummary {
  const roles = {
    total: 10, excluded_undated_count: 0,
    with_source_document: { count: 10, total: 10, proportion: 1, meaning: 'x' },
    without_source_document: { count: 0, total: 10, proportion: 0, meaning: 'x' },
    known_posting_date: { count: 7, total: 10, proportion: 0.7, meaning: 'x' },
    unknown_posting_date: { count: 3, total: 10, proportion: 0.3, meaning: 'x' },
    known_country: { count: 10, total: 10, proportion: 1, meaning: 'x' },
    unknown_country: { count: 0, total: 10, proportion: 0, meaning: 'x' },
    known_seniority: { count: 10, total: 10, proportion: 1, meaning: 'x' },
    unknown_seniority: { count: 0, total: 10, proportion: 0, meaning: 'x' },
    known_employment_type: { count: 10, total: 10, proportion: 1, meaning: 'x' },
    unknown_employment_type: { count: 0, total: 10, proportion: 0, meaning: 'x' },
    known_remote_type: { count: 10, total: 10, proportion: 1, meaning: 'x' },
    unknown_remote_type: { count: 0, total: 10, proportion: 0, meaning: 'x' },
    ...overrides,
  }
  return {
    scope: { year_from: null, year_to: null, country: null, seniority_level: null, archetype_id: null, dated_scope_active: false },
    roles,
    time: {
      earliest_known_posting_date: '2023-01-01', latest_known_posting_date: '2024-01-01',
      known_posting_date: roles.known_posting_date, unknown_posting_date: roles.unknown_posting_date,
      by_year: [], current_calendar_year_count: 0, previous_calendar_year_count: 0, older_count: 10,
      freshly_captured_but_posting_date_unknown_count: 0,
      document_capture_date_range: { earliest: null, latest: null },
    },
    geography: {
      known_country: roles.known_country, unknown_country: roles.unknown_country,
      distinct_known_country_count: 1, country_distribution: [], region_distribution: [],
    },
    sources: {
      with_source_document: roles.with_source_document, without_source_document: roles.without_source_document,
      capture_source_distribution: [], provenance_quality_distribution: [], document_kind_distribution: [],
      url_present: { count: 0, total: 0, proportion: null, meaning: 'x' }, url_absent: { count: 0, total: 0, proportion: null, meaning: 'x' },
    },
    requirements: {
      review_complete: { count: 8, total: 10, proportion: 0.8, meaning: 'x' },
      accepted_present: { count: 8, total: 10, proportion: 0.8, meaning: 'x' },
      unreviewed_present: { count: 2, total: 10, proportion: 0.2, meaning: 'x' },
      unresolved_vocabulary_present: { count: 0, total: 10, proportion: 0, meaning: 'x' },
      needs_reextraction_present: { count: 0, total: 10, proportion: 0, meaning: 'x' },
      extraction_attempted_incomplete: { count: 0, total: 10, proportion: 0, meaning: 'x' },
      extraction_never_attempted: { count: 0, total: 10, proportion: 0, meaning: 'x' },
      legacy_only: { count: 2, total: 10, proportion: 0.2, meaning: 'x' },
      no_usable_evidence: { count: 0, total: 10, proportion: 0, meaning: 'x' },
      state_precedence: ['needs_reextraction', 'unresolved_vocabulary', 'review_pending', 'reviewed', 'legacy_only', 'not_extracted'],
      state_distribution: { needs_reextraction: 0, unresolved_vocabulary: 0, review_pending: 2, reviewed: 6, legacy_only: 2, not_extracted: 0 },
    },
    vocabulary: {
      roles_with_accepted_canonical_requirements: { count: 8, total: 10, proportion: 0.8, meaning: 'x' },
      roles_relying_on_legacy_fallback: { count: 2, total: 10, proportion: 0.2, meaning: 'x' },
      role_skill_observations_resolved: { count: 1, total: 1, proportion: 1, meaning: 'x' },
      role_skill_observations_unresolved_count: 0,
      unresolved_vocabulary_proposal_occurrence_count: 0,
    },
    archetypes: {
      active_archetype_count: 1,
      assigned_active: { count: 5, total: 10, proportion: 0.5, meaning: 'x' },
      assigned_deprecated: { count: 0, meaning: 'x' },
      unassigned: { count: 5, total: 10, proportion: 0.5, meaning: 'x' },
      active_archetypes_with_support: { count: 1, total: 1, proportion: 1, meaning: 'x' },
      active_archetypes_without_support_count: 0,
      unsupported_active_archetypes: [],
      support: [],
      filter_options: [],
    },
    compensation: {
      coverage: {
        accepted_observation_count: 0, distinct_source_document_count: 0, distinct_provider_count: 0,
        earliest_period: null, latest_period: null, with_reported_median_count: 0, with_reported_mean_count: 0,
        with_range_count: 0, with_sample_size_count: 0, archetype_linked_count: 0, not_archetype_linked_count: 0,
      },
      source_kind_distribution: [], basis_distribution: [], top_providers: [],
      role_linked_coverage: {
        with_accepted_role_linked_compensation: { count: 0, total: 10, proportion: 0, meaning: 'x' },
        without_accepted_role_linked_compensation: { count: 10, total: 10, proportion: 1, meaning: 'x' },
      },
      scope_note: 'x',
    },
    derived_economics: { state: 'fresh', fresh: true, stale_inputs: [], reason: null, last_rebuilt_at: null, engine_version: null },
    representativeness: { known: false, reason: 'No known sampling frame.' },
    concentration: { top_capture_sources: [], top_countries: [], top_organisations: [], top_compensation_providers: [], note: 'x' },
    limitations: ['No known sampling frame.'],
  }
}

function renderTrends() {
  return render(
    <MemoryRouter initialEntries={['/trends']}>
      <Routes>
        <Route path="/trends" element={<Trends />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Trends — Phase 8 coverage context', () => {
  it('renders the coverage strip with dated vs undated counts', async () => {
    vi.mocked(api.getTrendOverview).mockResolvedValue(overview)
    vi.mocked(api.getTopRequirements).mockResolvedValue(topRequirements)
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(coverageSummary())
    renderTrends()

    expect(await screen.findByText(/roles in scope/)).toBeTruthy()
    expect(screen.getByText(/7 dated/)).toBeTruthy()
    expect(screen.getByText(/3 undated/)).toBeTruthy()
  })

  it('links to Market Coverage', async () => {
    vi.mocked(api.getTrendOverview).mockResolvedValue(overview)
    vi.mocked(api.getTopRequirements).mockResolvedValue(topRequirements)
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(coverageSummary())
    renderTrends()

    const link = await screen.findByText('Full market coverage →')
    expect(link.closest('a')?.getAttribute('href')).toBe('/market/coverage')
  })

  it('a coverage-strip failure never blanks the rest of the Trends page', async () => {
    vi.mocked(api.getTrendOverview).mockResolvedValue(overview)
    vi.mocked(api.getTopRequirements).mockResolvedValue(topRequirements)
    vi.mocked(api.getMarketCoverageSummary).mockRejectedValue(new Error('coverage down'))
    renderTrends()

    expect(await screen.findByText('Most common requirements')).toBeTruthy()
    expect(screen.getByText('Python')).toBeTruthy()
    expect(screen.queryByText(/roles in scope/)).toBeNull()
  })

  it('shows evidence_depth on a selected requirement trend without altering its classification label', async () => {
    vi.mocked(api.getTrendOverview).mockResolvedValue(overview)
    vi.mocked(api.getTopRequirements).mockResolvedValue(topRequirements)
    vi.mocked(api.getMarketCoverageSummary).mockResolvedValue(coverageSummary())
    vi.mocked(api.getRequirementTrend).mockResolvedValue({
      granularity: 'year',
      series: [
        { period: 2023, role_count: 3, total_roles: 10, proportion: 0.3, sample_size: 10 },
        { period: 2024, role_count: 6, total_roles: 10, proportion: 0.6, sample_size: 10 },
      ],
      classification: {
        label: 'increasing', rationale: 'early 0.3 vs late 0.6', usable_periods: 2, total_periods: 2,
        early_mean_proportion: 0.3, late_mean_proportion: 0.6,
      },
      evidence_depth: { state: 'thin', reason: '2 usable period(s) (>= 5 roles each) behind this trend.', usable_periods: 2 },
    })
    vi.mocked(api.getCooccurrence).mockResolvedValue({ sample_size: 10, items: [] })
    vi.mocked(api.compareDimension).mockResolvedValue({ dimension: 'seniority_level', items: [] })

    renderTrends()
    fireEvent.click(await screen.findByText('Python'))

    expect(await screen.findByText('Increasing')).toBeTruthy() // unchanged classification label
    await waitFor(() => expect(screen.getByText(/Thin evidence/i)).toBeTruthy())
  })
})
