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

The application owns the CV structure. `CVWritingGeneration` is a separate,
strict AI-only schema: summary, flat experience bullets, skills, qualifications
and cautions. Each block contains text and source handles. Employment IDs,
employment entries, omissions and metadata are forbidden in the writer output.
`ApplicationCVGeneration` remains the persisted/editable document contract.

The composer builds all employments from the complete career snapshot in stable
reverse chronology (dates, then ID), including legacy untyped root episodes.
It resolves every person-side citation in a bullet through explicit project ancestry.
All must agree on one employment, and at least one must be substantive evidence.
Only then is the bullet attached to that employment. Exact duplicate bullets are
collapsed. Employer names in prose cannot establish placement. Jobs without generated
content remain chronology-only, with a review warning; absent bullets never cause
a coverage failure or retry. The model cannot omit jobs or invent omission reasons.

If a bullet mixes employers or cites unattributed evidence, generation gets one
automatic correction attempt with its index and source handles. The entire
corrected writing payload is validated against the unchanged registry and composed
again. A second failure leaves the existing draft untouched. No code strips
citations or guesses links to bypass checks. Unknown handles still fail closed.
Limitation notes are rendered separately as non-citable constraints, not among
positive career sources. Summary/skills receive an explicit eligible-source list.
The same single correction budget also covers limitation-only, missing-citation
and role/strategy-only grounding failures; a repair still undergoes full validation.
Stored older CVs remain readable and are grouped by the same ancestry on read
without rewriting content or provenance. Reads do not insert newly discovered
jobs into an adopted document. Titles, organisations and dates continue to resolve
from authoritative Profile360 records. The pipeline version is now 3, so older CVs
are marked stale and can be regenerated through the new writer/composer contract.

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
grounding semantics remain unchanged: user text is labelled as not automatically
revalidated. Manual saves restore silently removed employment entries, while
preserving explicit user omissions with a nonblank reason. Unknown entries,
duplicates and conflicting omissions are rejected before any draft is replaced.
Chronology-only roles render and export normally; the review-only explanatory
notice is not exported. The editor can add a human-authored achievement to any role
without manufacturing source citations.

CV fingerprints include the complete career records and a pipeline version, so
changed claims, parent links or this input-policy change make earlier CVs stale.
Unselected career changes do not stale unrelated document types.

Validation: unit tests cover sparse/empty writing across a long career, strict
writer shape, nested ancestry, ambiguous attribution, stable ordering, bounded
repair and draft preservation. PostgreSQL tests exercise generation, canonical
persistence, manual editing, adoption and stale historical reads. Workspace tests
cover chronology-only rendering, export and adding a manual achievement. Existing
grounding, warnings and staleness suites remain in place. No database migration
is needed: Profile360 already stores parent_episode_id and episode_type.
