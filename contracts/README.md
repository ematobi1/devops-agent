# contracts

Shared JSON Schemas every service validates against — the boundary
`docs/ARCHITECTURE.md` requires services to communicate through, rather than
reaching into each other's internals.

Schemas live under `src/devops_agent_contracts/schemas/` (single source of
truth, distributed with the package — nothing outside this directory
duplicates them). Import `devops_agent_contracts.validate(name, instance)`
from any service; it raises `jsonschema.ValidationError` on mismatch.

Bump a schema version by adding a new field as optional first, updating every
consuming service, then tightening — the same backwards-compatible-first
discipline as a database migration.
