import type {
  CareerStep,
  DayInLifeItem,
  GroundedNote,
  GroundingSummary,
  ManagerContext,
  RoleContextBasis,
  TeamContext,
  TypicalWeekItem,
} from '../../lib/api'

// The grounded/inferred distinction is the point of this whole feature, so
// it is rendered on every single item rather than summarised once at the top.
function BasisTag({ basis, confidence }: { basis: RoleContextBasis | null; confidence?: string }) {
  if (!basis) return null
  const grounded = basis === 'advert_grounded'
  return (
    <span
      className="muted"
      style={{
        fontSize: 11,
        padding: '1px 6px',
        borderRadius: 3,
        border: `1px solid ${grounded ? 'var(--good)' : 'var(--border)'}`,
        color: grounded ? 'var(--good)' : 'var(--text-muted)',
        whiteSpace: 'nowrap',
      }}
    >
      {grounded ? 'from source' : 'inferred'}
      {confidence ? ` · ${confidence}` : ''}
    </span>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <h4 style={{ margin: '0 0 6px', fontSize: 13, textTransform: 'uppercase', letterSpacing: 0.4 }}>{title}</h4>
      {children}
    </div>
  )
}

export type StructuredContext = {
  day_in_life: DayInLifeItem[]
  typical_week: TypicalWeekItem[]
  team_context: TeamContext
  manager_context: ManagerContext
  stakeholder_context: { stakeholders: GroundedNote[] }
  career_progression: CareerStep[]
  grounding_summary: GroundingSummary
  caveats: string | null
}

/** Shared renderer for the structured Day-in-the-Life shape, which a role,
 * a target and an archetype all produce identically. `syntheses` carries the
 * "this is a synthesis across evidence, not a fact about every job" note an
 * archetype's context must always show. */
export function DayInTheLife({
  context,
  synthesisNote,
}: {
  context: StructuredContext
  synthesisNote?: string
}) {
  const stakeholders = context.stakeholder_context?.stakeholders ?? []
  return (
    <div>
      {synthesisNote && (
        <p className="secondary" style={{ fontSize: 13, margin: '0 0 8px', fontStyle: 'italic' }}>
          {synthesisNote}
        </p>
      )}

      {context.day_in_life.length > 0 && (
        <Section title="A typical day">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {context.day_in_life.map((item, i) => (
              <li key={`${item.time_or_phase}-${i}`} style={{ marginBottom: 6 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <strong style={{ fontWeight: 600 }}>{item.time_or_phase}</strong>
                  <span>{item.activity}</span>
                  <BasisTag basis={item.basis} confidence={item.confidence} />
                </div>
                {item.detail && (
                  <div className="secondary" style={{ fontSize: 13 }}>
                    {item.detail}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {context.typical_week.length > 0 && (
        <Section title="A typical week">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {context.typical_week.map((item, i) => (
              <li key={`${item.day_or_theme}-${i}`} style={{ marginBottom: 6 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <strong style={{ fontWeight: 600 }}>{item.day_or_theme}</strong>
                  <span>{item.activity}</span>
                  <BasisTag basis={item.basis} confidence={item.confidence} />
                </div>
                {item.detail && (
                  <div className="secondary" style={{ fontSize: 13 }}>
                    {item.detail}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(context.team_context?.team_work?.length > 0 || context.team_context?.expected_team_size) && (
        <Section title="Team">
          {context.team_context.expected_team_size && (
            <div className="secondary" style={{ fontSize: 13, marginBottom: 4 }}>
              Expected team size: {context.team_context.expected_team_size.min ?? '?'}–
              {context.team_context.expected_team_size.max ?? '?'}{' '}
              <BasisTag
                basis={context.team_context.expected_team_size.basis}
                confidence={context.team_context.expected_team_size.confidence}
              />
            </div>
          )}
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {context.team_context.team_work.map((note, i) => (
              <li key={i}>
                {note.text} <BasisTag basis={note.basis} confidence={note.confidence} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(context.manager_context?.likely_manager_title || context.manager_context?.dynamic) && (
        <Section title="Manager">
          {context.manager_context.likely_manager_title && (
            <div className="secondary" style={{ fontSize: 13 }}>
              Likely reports to: {context.manager_context.likely_manager_title}{' '}
              <BasisTag basis={context.manager_context.title_basis ?? null} />
            </div>
          )}
          {context.manager_context.dynamic && (
            <div className="secondary" style={{ fontSize: 13 }}>
              {context.manager_context.dynamic}{' '}
              <BasisTag
                basis={context.manager_context.dynamic_basis ?? null}
                confidence={context.manager_context.dynamic_confidence}
              />
            </div>
          )}
        </Section>
      )}

      {stakeholders.length > 0 && (
        <Section title="Stakeholders">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {stakeholders.map((note, i) => (
              <li key={i}>
                {note.text} <BasisTag basis={note.basis} confidence={note.confidence} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {context.career_progression.length > 0 && (
        <Section title="Where it tends to lead">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {context.career_progression.map((step, i) => (
              <li key={i}>
                {step.step} <BasisTag basis={step.basis} confidence={step.confidence} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(context.grounding_summary?.advert_grounded_points?.length > 0 ||
        context.grounding_summary?.inferred_points?.length > 0) && (
        <Section title="What this rests on">
          {context.grounding_summary.advert_grounded_points.length > 0 && (
            <>
              <div className="muted" style={{ fontSize: 12 }}>
                Supported by the source:
              </div>
              <ul className="secondary" style={{ margin: '2px 0 6px', paddingLeft: 18, fontSize: 13 }}>
                {context.grounding_summary.advert_grounded_points.map((point, i) => (
                  <li key={i}>{point}</li>
                ))}
              </ul>
            </>
          )}
          {context.grounding_summary.inferred_points.length > 0 && (
            <>
              <div className="muted" style={{ fontSize: 12 }}>
                Inferred:
              </div>
              <ul className="secondary" style={{ margin: '2px 0 0', paddingLeft: 18, fontSize: 13 }}>
                {context.grounding_summary.inferred_points.map((point, i) => (
                  <li key={i}>{point}</li>
                ))}
              </ul>
            </>
          )}
        </Section>
      )}

      {context.caveats && (
        <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          <strong style={{ fontWeight: 600 }}>Caveats:</strong> {context.caveats}
        </p>
      )}
    </div>
  )
}
