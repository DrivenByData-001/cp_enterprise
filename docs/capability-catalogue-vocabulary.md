# Vocabulary capability specifications

Vocabulary owns concept identity. The Catalogue lists active capability concepts
without detail under **Needs specification**, and configured active capabilities
under **Assessment-ready**. Search applies to both groups. Proposed and deprecated
configured capabilities remain available through the existing status filter.

Select **Configure <name>** to add an assessment specification. The canonical name
and definition are read-only in this flow. Saving opens the existing detail and
component curation view. **+ New capability** remains a secondary action.

- `GET /api/capabilities/unconfigured?q=...` returns active capability concepts
  without `capability_detail`; it never fabricates assessment fields.
- `POST /api/capabilities/{concept_id}/configure` accepts demonstration standard,
  depth/autonomy thresholds, core-rule fields, economic salience and notes only.
  It locks the concept, verifies active capability type, and inserts only detail.
  Missing concepts return 404, ineligible concepts 400, invalid input 422 and
  already configured concepts 409. Concurrent requests create at most one detail.
- Existing list, create, edit, components, proposal review and coverage routes
  continue to operate. Optional specification metadata can be explicitly cleared.

No migration is required. Concept rows, aliases, provenance and relationships are
preserved. Proposed component edges still require human review, and the engine
continues to evaluate only configured capabilities with accepted components.
