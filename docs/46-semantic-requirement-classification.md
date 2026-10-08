# Classify before matching vocabulary

Requirement extraction uses the app-wide AI model to read the complete posting
and classify each meaningful statement as a professional requirement, practical
eligibility condition, role metadata or irrelevant text. Every item requires a
category, a reason and an exact source quotation. Missing/unknown categories fail
schema validation; quotations not present in the source are not stored or matched.
The categories are decided by meaning, not location names or heading patterns.

Only professional requirements enter exact/alias matching, semantic retrieval and
AI vocabulary adjudication. Other categories remain in an immutable classification
snapshot linked to the extraction run (and its model/prompt version). Application
Requirements displays these separately, without a vocabulary approval task or an
automatic edit to role metadata. An incorrectly excluded requirement can still be
added manually using supporting text. Existing accepted requirements are preserved.

Re-extraction supplies the role's old pending terms to the classifier so it can
reconsider them. A current, source-bound non-professional classification removes
that term from this application's vocabulary queue and pending counts. It also
prevents subsequent global vocabulary acceptance from creating a requirement for
that occurrence. Another role using the term is unaffected. If a term has both a
professional and non-professional reading, the professional reading wins for review.
Unclassified terms are never silently suppressed. There is no automatic AI call on
page reads: old proposals are reconsidered when the user runs extraction again.

Changing the role's source document or its text invalidates the snapshot. Historical
snapshots remain available for audit. Reclassification is semantic and probabilistic;
schema/source validation cannot guarantee the model always interprets meaning correctly.

# Application navigation

The application screen is retained while changing steps. Navigation flushes edits
and saves the resume position but does not reload the full process twice. Mutation
handlers still refresh process state, and backend save/checkpoint endpoints retain
revision/fingerprint checks. A fresh page load fetches current server state.

An individual evidence requirement now points to the AI discovery workflow, which
searches Profile360 claims and supporting records across the confirmed requirements.
The existing keyword search remains separate; AI findings require explicit review.
