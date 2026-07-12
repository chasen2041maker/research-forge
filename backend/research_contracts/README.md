# Research contracts

`research_contracts` is the only shared vocabulary between Research Studio and Research Forge.
It imports neither product. The contract is deliberately JSON transportable and versioned.

- `ResearchProposal v1` is emitted by Studio and is always `UNVERIFIED`. It may contain
  useful paper references, hypotheses, repository candidates, and execution suggestions, but
  none of those fields authorizes an execution or proves a result.
- `VerifiedResult v1` is emitted only after Forge has completed its normal frozen-spec and
  evidence-gate workflow. It points to the Mission, frozen spec hash, observed metric, and
  reproducible Bundle.

Changing a contract requires a new versioned file. Do not make either product import the
other product's graph, state, workers, adapters, or persistence models.
