"""Named limits for organizational queries and manager validation (Phase 2)."""

# Maximum direct reports returned in one response (unpaginated).
DIRECT_REPORTS_MAX = 500

# Maximum depth below the root employee in reporting-hierarchy responses.
HIERARCHY_MAX_DEPTH = 20

# Maximum total nodes in a reporting-hierarchy tree.
HIERARCHY_MAX_NODES = 500

# Maximum manager-chain steps when validating create/update assignments.
MANAGER_CHAIN_VALIDATION_MAX = 50
