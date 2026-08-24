# Design overview

The project is delivered as independently verifiable child tasks. The authoritative chain is:

`repository page -> project context -> file inventory -> file family/evidence -> file judgment -> frozen file selection -> Web/export projections`.

Projects provide shared context only. `file_id` is the selection authority. Long reasons are generated only for selected files after structural judgments and evidence checks are complete. Reviewed excluded and investigate files receive shorter model reasons in the structural pass. Deterministic SDRF relations are frozen by code; the model may judge usefulness but cannot erase those relations.
