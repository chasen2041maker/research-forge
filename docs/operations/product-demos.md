---
title: Product demonstrations
status: active
---

# Research Forge product demonstrations

The product demos are deterministic local test flows. They demonstrate contract and evidence
boundaries; they do not claim independent external-fixture evaluation.

Run all three and write an inspectable JSON report:

```bash
cd /opt/research-forge
/opt/research-forge/.venv/bin/python backend/scripts/run_research_forge_demos.py \
  --output-dir /var/lib/research-forge/demo-reports
```

1. **Studio to Forge handoff** — an `UNVERIFIED` Studio Proposal plus explicit human completion
   enters the normal Forge Mission creation boundary.
2. **Forge to Studio VerifiedResult** — only completed Mission, Bundle, Metric, and VERIFIED claims
   with a persisted handoff proposal identity produce the read-only contract.
3. **Bounded repair approval loop** — a failed baseline creates one persisted PATCH artifact and
   approval; the approved child Attempt applies those exact bytes, performs one candidate run, and
   seals a Bundle.

The command exits non-zero if any demo fails. Keep its JSON report with the release evidence. The
separate frozen evaluation suite and Linux Docker gate remain required release checks.
