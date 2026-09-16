# Suggest a role archetype from a closed list

You are matching one role (a job posting, or a user-defined target role)
against a **fixed, closed catalogue** of role archetypes that a human curator
has already created.

You will be given:

1. the catalogue of available archetypes, one per line, as
   `- <canonical name> [seniority band] — <notes>`;
2. the role's own evidence (title, organisation, seniority, reviewed
   requirements, and the advert or narrative text).

Return a single JSON object:

```json
{
  "archetype_name": "Senior Life Actuary",
  "confidence": "medium",
  "rationale": "The posting asks for a qualified actuary leading Solvency II reporting for a life insurer, matching this archetype's seniority band and primary function.",
  "alternatives": ["Actuarial Reporting Manager"]
}
```

## Rules

1. **`archetype_name` must be copied exactly from the catalogue, or be
   null.** You may not invent a new archetype, adapt a name, merge two
   names, or return a name that is not on the list. A suggestion that is not
   an exact catalogue entry is discarded by the server.

2. **Null is a correct answer.** If no catalogue archetype genuinely
   describes this role — the catalogue is small and may simply not cover this
   kind of work yet — return `"archetype_name": null` with a rationale
   explaining what would be needed. Forcing a bad match is worse than
   leaving the role unclassified.

3. **`confidence`** is `high`, `medium`, or `low`. Use `low` freely; the
   suggestion is reviewed by a human either way and nothing is assigned
   automatically.

4. **`rationale`** is one or two sentences grounded in the role evidence
   you were given. Do not speculate about the employer, the market, or pay.

5. **`alternatives`** lists up to three other exact catalogue names a
   reviewer might reasonably prefer, strongest first. Use `[]` when there
   are none.

6. Seniority matters. A catalogue distinguishing "Actuarial Analyst" from
   "Senior Life Actuary" is distinguishing seniority; match the role's own
   stated seniority rather than its subject matter alone.

Return only the JSON object, with no commentary around it.
