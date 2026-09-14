# Target mapping, editing and prepared analysis

Every target requirement retains its original wording. Normalised exact names
and aliases resolve to active vocabulary concepts on save. Different wording
requires explicit vocabulary selection: the API requires an active concept ID
and `mapping_reviewed: true`. An explicit null ID with that flag preserves an
intentional Unmapped decision. AI preview output never sets reviewed mappings.
Changing wording in the editor clears the previous selection for fresh review.

Creation and saved-target editing show Mapped or Unmapped beside each requirement.
Saved target analysis also lists every observation, including inactive/unmapped
concepts and observations excluded by authoritative requirement claims. The page
shows mapped/total counts. Incomplete mapping withholds target readiness and
intermediate-step classifications; evidence counts remain limited to mapped
requirements. Saving incomplete drafts is allowed. Descriptive skill examples
and study subjects are separate from the explicit matching requirements.

Source-aware postings without raw JSON use metadata PATCH editing: captured source
text and requirement claims are preserved. Legacy postings with raw JSON use full
replacement of flat fields and observations, followed by re-embedding. Targets
use the structured form, with advanced JSON replacement available for existing
JSON records. Both target save routes replace editable fields and observations;
separate requirement claims retain their existing authority and review history.

Migration 0019 adds two rebuildable database caches: concept evidence status and
the final target-path result. Caches are shared across app workers and persist
across restarts. A warm target path reads revision/fingerprint data and its cached
result in two database statements, without loading the role corpus or invoking
either evidence engine. A different target or edited posting can reuse prepared
person-side evidence. Cold/stale results are rebuilt lazily on the next request.

Transactional statement triggers invalidate revisions for role/requirement edits,
vocabulary and capability changes, component/edge-rule changes, evidence mapping
review, assertions and embeddings. Evidence and path revisions are separate so
posting edits do not invalidate stable person-side status. Inserts, updates,
deletes and truncates are covered, including direct database writes.

Profile360 is externally owned and remains read-only. One aggregate query hashes
the content of its claims, capabilities and episodes to detect external edits
and deletes even when updated_at was not changed. Only digests are returned or
retained, not person-side source text. This scan grows with the profile evidence
size; a profile360-owned change revision/outbox could replace it if profiling
justifies that coordination. The cache is designed for the existing single-profile
application. New engine dependencies must be added to invalidation coverage, and
changes to analysis semantics must increment the algorithm version in target_cache.

Each analysis returns and logs candidate count, distinct concepts, concepts
actually evaluated, elapsed milliseconds and cache-hit status. Enable INFO logging
for `app.stepping_stones` to collect timing records. No evidence text is logged.

The CI benchmark uses 1,000 postings, 20,000 observations, 200 distinct concepts
(20 capabilities), and 1,000 external profile claims. It invokes real evidence
engines and verifies one call per distinct concept on a cold build, zero on a warm
read, exactly two warm database statements, and a bounded cold query count.
Elapsed times are recorded rather than enforcing hardware-sensitive millisecond
thresholds. GitHub Actions prints the benchmark and retains its JUnit artifact;
compare cold/warm times on equivalent runners. This guards the role-by-concept
engine-call regression independently of machine speed.

Deploy migration 0019 with the matching backend/frontend after 0018. No background
service or production profile write is required. Clearing either cache is safe;
the next request rebuilds it. Never clear the revision row while the app is serving.
