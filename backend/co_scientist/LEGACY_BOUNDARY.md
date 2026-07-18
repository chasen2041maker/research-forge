# Research Studio public boundary

`backend/co_scientist` remains the exploration product. It may run its existing LangGraph
pipeline, literature discovery, critique, planning, and code-suggestion modules, but every
new external consumer must use `co_scientist.public_api`.

The public boundary exposes a completed `ExplorationSnapshot` and `ResearchProposal v1`.
`ResearchProposal v1` is always `UNVERIFIED`; it never creates a Forge Mission by itself.

Forbidden dependencies for consumers, including the product bridge:

- `co_scientist.graph`
- `co_scientist.state`
- `co_scientist.modules.*`

Do not add handoff fields to `ResearchState`. Translate its existing snapshot at the public
boundary instead, keeping the Studio graph independent from Forge execution and evidence logic.
