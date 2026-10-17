# Complete career coverage in tailored CVs

CV generation supplements application-selected requirement evidence with the full
Profile360 employment/project tree and career claims. Requirement decisions still
control tailoring and remain unchanged. Other document types retain their curated
source boundary. Career reads are batched, not limited to the timeline browse page.

Claims retain their evidence class, confidence, uncertainty and disposition.
Inferred or lower-confidence claims are partial; user assertions are labelled as
such. Boundary claims constrain wording and cannot alone support achievements.
The source manifest retains the original episode ID and separately records its
resolved employment ID. Explicit parent links can traverse multiple project levels;
missing parents and cycles never result in guessed attribution or dates.

The model must account for every employment: include it (chronology only is allowed
when evidence is sparse) or supply a structured omission reason. Duplicate entries,
silent omissions and achievements placed under unrelated employers are rejected.
Project claims can support their verified employment ancestor, not arbitrary roles.
The prompt lists permitted achievement sources for each employment. If a bullet
cites another employer or an unattributed source, generation gets one automatic
correction attempt with the offending bullet index and source handles. The entire
corrected CV is validated against the unchanged registry. A second failure leaves
the existing draft untouched and names the employer in the error, rather than
showing an episode UUID. No code strips citations or moves claims to bypass checks.
Limitation notes are rendered separately as non-citable constraints, not among
positive career sources. Summary/skills receive an explicit eligible-source list.
The same single correction budget also covers limitation-only, missing-citation
and role/strategy-only grounding failures; a repair still undergoes full validation.
Stored older CVs are grouped by the same ancestry on read without rewriting their
original content or provenance. Chronological sorting uses the employment dates.

Professional qualifications have their own sourced field in generation, rendering,
editing and Markdown export. A career claim can establish a qualification without
a separate education table. The prompt asks for balanced achievement selection,
concise skills, audience-appropriate detail, no duplicate achievement sentences,
and precise limits on ownership, validation and financial impacts.

Deterministic review warnings flag omitted employment, sparse coverage despite
available claims, missing dates/ancestry, timeline gaps, repeated wording, long
bullets and qualifications placed in skills. These are editorial signals, not
automated proof of semantic correctness; users still review prose. Warnings are
shown separately from the CV and recalculated on manual edits. Existing manual-edit
grounding semantics remain unchanged.

CV fingerprints include the complete career records and a pipeline version, so
changed claims, parent links or this input-policy change make earlier CVs stale.
Unselected career changes do not stale unrelated document types.

Validation: unit tests cover grouping, cycles, attribution, omissions, evidence
limits, warnings and staleness; a PostgreSQL integration test checks unmapped claim
retrieval; workspace tests cover qualifications and review warnings. No database
migration is needed: Profile360 already stores parent_episode_id and episode_type.
