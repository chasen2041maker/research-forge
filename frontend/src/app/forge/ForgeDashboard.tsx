"use client";

import { type FormEvent, type ReactNode, useMemo, useState } from "react";
import {
  Archive,
  CheckCircle2,
  ClipboardList,
  Download,
  GitBranch,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";

type Attempt = {
  attempt_id: string;
  task_id: string;
  status: string;
  lease_epoch: number;
  failure_code: string | null;
};

type Task = {
  task_id: string;
  task_type: string;
  status: string;
  attempts: Attempt[];
};

type Approval = {
  approval_id: string;
  task_id: string;
  attempt_id: string;
  action_hash: string;
  risk_level: string;
  scope: string;
  status: string;
  requested_at: string;
  expires_at: string;
  decided_by: string | null;
};

type Mission = {
  mission_id: string;
  status: string;
  spec_sha256: string;
  proposal_id: string | null;
  tasks: Task[];
  approvals: Approval[];
  bundle_sha256: string | null;
};

type VerifiedResult = {
  status: "VERIFIED";
  proposal_id: string;
  mission_id: string;
  spec_sha256: string;
  metric: Record<string, unknown>;
  bundle_sha256: string;
  completed_at: string;
};

const initialSpec = `{
  "schema_version": 1,
  "mode": "reproduce",
  "paper": { "artifact_id": "paper-toy-001", "sha256": "", "extraction_profile": "plain-text-v1" },
  "repository": { "url_or_path": "", "commit_sha": "" },
  "execution": { "image_digest": "", "setup_mode": "prebuilt", "setup_argv": [], "run_argv": ["python", "evaluate.py", "--output", "metrics.json"], "working_directory": ".", "timeout_seconds": 120, "network_policy": "offline", "allowed_domains": [] },
  "metric": { "artifact_path": "metrics.json", "format": "json", "json_pointer": "/accuracy", "comparator": "equals", "expected_value": 0.8, "tolerance": 0.001, "unit": "ratio" },
  "change_budget": { "allowed_paths": [], "max_files": 0, "max_changed_lines": 0, "max_candidate_commits": 0, "max_candidate_runs": 0 },
  "budget": { "max_wall_time_seconds": 300, "max_cost_usd": 0, "max_artifact_bytes": 10485760, "max_log_bytes": 1048576 }
}`;

const initialProposal = `{
  "schema_version": 1,
  "proposal_id": "proposal-from-studio",
  "studio_run_id": "studio-run-id",
  "research_question": "Paste the completed Studio Proposal here.",
  "hypothesis": "This remains unverified until Forge closes the evidence gate.",
  "paper_refs": [],
  "repository_candidate": { "url": "", "commit_sha": "" },
  "objective": { "description": "", "metric_name": "" },
  "suggested_execution": { "run_argv": [], "metric_artifact_path": "", "metric_json_pointer": "" },
  "allowed_change_paths": [],
  "evidence_refs": [],
  "missing_fields": [],
  "status": "UNVERIFIED"
}`;

export default function ForgeDashboard() {
  const apiBase = process.env.NEXT_PUBLIC_RESEARCH_FORGE_API_URL ?? "http://127.0.0.1:8000";
  const [token, setToken] = useState("");
  const [specText, setSpecText] = useState(initialSpec);
  const [proposalText, setProposalText] = useState(initialProposal);
  const [completionText, setCompletionText] = useState(initialSpec);
  const [missionId, setMissionId] = useState("");
  const [mission, setMission] = useState<Mission | null>(null);
  const [verifiedResult, setVerifiedResult] = useState<VerifiedResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"create" | "handoff" | "refresh" | "cancel" | "bundle" | "approval" | "verified" | "">("");
  const authenticated = token.trim().length > 0;
  const attemptCount = useMemo(
    () => mission?.tasks.reduce((sum, task) => sum + task.attempts.length, 0) ?? 0,
    [mission],
  );

  async function request(path: string, init?: RequestInit) {
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${token}`);
    headers.set("Content-Type", "application/json");
    const response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers,
    });
    if (!response.ok) throw new Error(await response.text());
    return response;
  }

  async function refresh(id = missionId) {
    if (!id.trim() || !authenticated) return;
    setBusy("refresh");
    setError("");
    try {
      const response = await request(`/v1/missions/${encodeURIComponent(id)}`);
      const next = (await response.json()) as Mission;
      setMission(next);
      setMissionId(next.mission_id);
      setVerifiedResult(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load Mission.");
    } finally {
      setBusy("");
    }
  }

  async function createMission(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authenticated) return;
    setBusy("create");
    setError("");
    try {
      const spec = JSON.parse(specText) as object;
      const response = await request("/v1/missions", {
        method: "POST",
        body: JSON.stringify({ spec }),
      });
      const created = (await response.json()) as { mission_id: string };
      setMissionId(created.mission_id);
      await refresh(created.mission_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Mission creation failed.");
      setBusy("");
    }
  }

  async function handoffProposal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authenticated) return;
    setBusy("handoff");
    setError("");
    try {
      const proposal = JSON.parse(proposalText) as object;
      const completion = JSON.parse(completionText) as object;
      const response = await request("/v1/proposals/handoff", {
        method: "POST",
        body: JSON.stringify({ proposal, completion }),
      });
      const handoff = (await response.json()) as { mission: { mission_id: string } };
      setMissionId(handoff.mission.mission_id);
      await refresh(handoff.mission.mission_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Proposal handoff failed.");
      setBusy("");
    }
  }

  async function cancelMission() {
    if (!mission || !authenticated) return;
    setBusy("cancel");
    setError("");
    try {
      await request(`/v1/missions/${encodeURIComponent(mission.mission_id)}/cancel`, { method: "POST" });
      await refresh(mission.mission_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Cancellation failed.");
      setBusy("");
    }
  }

  async function downloadBundle() {
    if (!mission?.bundle_sha256 || !authenticated) return;
    setBusy("bundle");
    setError("");
    try {
      const response = await request(`/v1/missions/${encodeURIComponent(mission.mission_id)}/bundle`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `research-forge-${mission.mission_id}.zip`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Bundle download failed.");
    } finally {
      setBusy("");
    }
  }

  async function loadVerifiedResult() {
    if (!mission || !authenticated) return;
    setBusy("verified");
    setError("");
    try {
      const response = await request(`/v1/missions/${encodeURIComponent(mission.mission_id)}/verified-result`);
      setVerifiedResult((await response.json()) as VerifiedResult);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "VerifiedResult is not available for this Mission.");
    } finally {
      setBusy("");
    }
  }

  async function decideApproval(approval: Approval, approved: boolean) {
    if (!mission || !authenticated) return;
    setBusy("approval");
    setError("");
    try {
      await request(`/v1/approvals/${encodeURIComponent(approval.approval_id)}/decide`, {
        method: "POST",
        body: JSON.stringify({ approved, decided_by: "local-reviewer" }),
      });
      await refresh(mission.mission_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Approval decision failed.");
      setBusy("");
    }
  }

  return (
    <main className="min-h-screen px-4 py-4 text-slate-100 sm:px-6 lg:px-8">
      <section className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-col gap-4 rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-4 shadow-xl shadow-black/20 backdrop-blur md:flex-row md:items-center md:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-cyan-300">
              <ShieldCheck className="h-4 w-4" /> Evidence-gated research reproduction
            </div>
            <h1 className="text-3xl font-semibold tracking-tight">Research Forge</h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-400">
              Immutable Spec → pinned execution → verified metric → reproducible Bundle.
            </p>
          </div>
          <div className="rounded-xl border border-cyan-300/15 bg-cyan-300/[.06] px-4 py-3 text-sm">
            <div className="text-slate-400">Current Mission</div>
            <code className="text-cyan-200">{mission?.mission_id ?? "not selected"}</code>
          </div>
        </header>

        <section className="relative overflow-hidden rounded-3xl border border-cyan-300/15 bg-slate-950/80 px-6 py-10 shadow-2xl shadow-black/30 sm:px-10 sm:py-14">
          <div className="pointer-events-none absolute -right-24 -top-36 h-96 w-96 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-36 left-1/3 h-80 w-80 rounded-full bg-indigo-500/15 blur-3xl" />
          <div className="relative max-w-3xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-1.5 text-xs font-medium text-emerald-200"><ShieldCheck className="h-3.5 w-3.5" /> Deterministic evidence gate · local-first control plane</div>
            <h2 className="mt-5 text-4xl font-semibold tracking-[-0.04em] text-white sm:text-6xl">Make every experiment <span className="text-cyan-300">auditable</span> before it ships.</h2>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300 sm:text-lg">Research Forge binds a paper, pinned Git commit, offline execution plan, and fixed metric into a reproducible evidence bundle. Completion is a proof, not a promise.</p>
            <div className="mt-7 flex flex-wrap gap-2">
              {['Pinned Git', 'Offline run', 'CAS verified', 'Lease safe'].map((capability) => <span key={capability} className="rounded-full border border-white/10 bg-white/[.045] px-3 py-1.5 text-xs text-slate-300">{capability}</span>)}
            </div>
            <a href="#mission" className="mt-8 inline-flex items-center gap-2 rounded-lg bg-cyan-300 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200">Launch a Mission <span aria-hidden>→</span></a>
          </div>
        </section>

        <form onSubmit={handoffProposal} className="rounded-2xl border border-violet-300/20 bg-slate-950/75 p-5 shadow-2xl shadow-black/20 sm:p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.16em] text-violet-200">Studio → Forge handoff</p>
              <h2 className="mt-2 text-xl font-semibold">Turn an unverified direction into a frozen Mission.</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">Paste the Proposal exported by Studio, then explicitly complete every paper pin, repository commit, image, command, metric, and budget. Forge performs its normal validation and prerequisite checks after this form.</p>
            </div>
            <span className="rounded-full border border-amber-300/20 bg-amber-300/10 px-3 py-1.5 text-xs font-medium text-amber-100">Proposal ≠ verified result</span>
          </div>
          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            <label className="block text-xs font-medium uppercase tracking-wider text-slate-400">
              Unverified ResearchProposal v1
              <textarea className="mt-2 h-64 w-full resize-y rounded-md border border-slate-700 bg-slate-950 p-3 font-mono text-xs normal-case leading-5 text-slate-100 outline-none ring-violet-500 focus:ring-2" value={proposalText} onChange={(event) => setProposalText(event.target.value)} spellCheck={false} />
            </label>
            <label className="block text-xs font-medium uppercase tracking-wider text-slate-400">
              Human-confirmed completion (ReproductionSpec fields)
              <textarea className="mt-2 h-64 w-full resize-y rounded-md border border-slate-700 bg-slate-950 p-3 font-mono text-xs normal-case leading-5 text-slate-100 outline-none ring-cyan-500 focus:ring-2" value={completionText} onChange={(event) => setCompletionText(event.target.value)} spellCheck={false} />
            </label>
          </div>
          <button className="mt-4 inline-flex items-center gap-2 rounded-md bg-violet-300 px-4 py-2.5 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40" disabled={!authenticated || busy !== ""}>
            {busy === "handoff" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
            Compile & create Forge Mission
          </button>
        </form>

        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <DashboardSignal label="Mission state" value={mission?.status ?? "NOT STARTED"} detail={mission ? "Durable source of truth" : "Create or load a Mission"} />
          <DashboardSignal label="Attempts" value={String(attemptCount)} detail="Lease-owned execution records" />
          <DashboardSignal label="Approvals" value={String(mission?.approvals.filter((approval) => approval.status === "PENDING").length ?? 0)} detail="Workers never wait in-process" />
          <DashboardSignal label="Research bundle" value={mission?.bundle_sha256 ? "SEALED" : "NOT SEALED"} detail={mission?.bundle_sha256 ? "Evidence artifact ready" : "Metric closure required"} />
        </section>

        <section id="mission" className="grid gap-4 lg:grid-cols-[1.15fr_.85fr]">
          <form onSubmit={createMission} className="rounded-2xl border border-white/10 bg-slate-950/75 p-5 shadow-2xl shadow-black/20 sm:p-6">
            <div className="mb-4 flex items-center gap-2">
              <ClipboardList className="h-5 w-5 text-cyan-300" />
              <h2 className="font-semibold">Mission</h2>
            </div>
            <label className="mb-2 block text-xs font-medium uppercase tracking-wider text-slate-400">Local API token</label>
            <div className="mb-4 flex gap-2">
              <KeyRound className="mt-2 h-4 w-4 text-slate-500" />
              <input
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none ring-cyan-500 focus:ring-2"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="Bearer token configured by the local API"
              />
            </div>
            <label className="mb-2 block text-xs font-medium uppercase tracking-wider text-slate-400">Frozen ReproductionSpec</label>
            <textarea
              className="h-80 w-full resize-y rounded-md border border-slate-700 bg-slate-950 p-3 font-mono text-xs leading-5 outline-none ring-cyan-500 focus:ring-2"
              value={specText}
              onChange={(event) => setSpecText(event.target.value)}
              spellCheck={false}
            />
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                className="inline-flex items-center gap-2 rounded-md bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!authenticated || busy !== ""}
              >
                {busy === "create" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                Validate & create
              </button>
              <input
                className="min-w-56 flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs"
                value={missionId}
                onChange={(event) => setMissionId(event.target.value)}
                placeholder="Mission ID"
              />
              <button
                type="button"
                onClick={() => refresh()}
                disabled={!authenticated || !missionId || busy !== ""}
                className="inline-flex items-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800 disabled:opacity-40"
              >
                <RefreshCw className={`h-4 w-4 ${busy === "refresh" ? "animate-spin" : ""}`} /> Refresh
              </button>
            </div>
          </form>

          <section className="space-y-4">
            <Panel icon={<GitBranch className="h-5 w-5" />} title="Workspace">
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <Metric label="Tasks" value={String(mission?.tasks.length ?? 0)} />
                <Metric label="Attempts" value={String(attemptCount)} />
                <Metric label="Spec hash" value={mission?.spec_sha256.slice(0, 12) ?? "—"} mono />
                <Metric label="Mode" value={mission?.tasks.some((task) => task.task_type === "REPAIR_CANDIDATE") ? "repair" : "reproduce"} />
              </dl>
              <p className="mt-4 text-xs leading-5 text-slate-400">
                Baseline and candidate worktrees are isolated. Candidate Commit and metric are tied by the operation ledger.
              </p>
            </Panel>

            <Panel icon={<Archive className="h-5 w-5" />} title="Bundle">
              {mission?.bundle_sha256 ? (
                <div className="space-y-3">
                  <code className="block break-all text-xs text-cyan-200">sha256:{mission.bundle_sha256}</code>
                  <button
                    onClick={downloadBundle}
                    disabled={busy !== ""}
                    className="inline-flex items-center gap-2 rounded-md border border-cyan-500/60 px-3 py-2 text-sm text-cyan-200 hover:bg-cyan-500/10 disabled:opacity-40"
                  >
                    {busy === "bundle" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} Download verified Bundle
                  </button>
                  {mission.proposal_id && (
                    <button
                      onClick={loadVerifiedResult}
                      disabled={busy !== ""}
                      className="inline-flex items-center gap-2 rounded-md border border-emerald-500/60 px-3 py-2 text-sm text-emerald-200 hover:bg-emerald-500/10 disabled:opacity-40"
                    >
                      {busy === "verified" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />} Read VerifiedResult
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-400">A Bundle becomes available only after deterministic metric and evidence closure.</p>
              )}
            </Panel>
          </section>
        </section>

        <Panel icon={<ShieldCheck className="h-5 w-5" />} title="Approvals">
          {mission?.approvals.length ? (
            <div className="space-y-3">
              {mission.approvals.map((approval) => (
                <article key={approval.approval_id} className="rounded-lg border border-slate-800 bg-slate-950/50 p-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <div className="flex items-center gap-2"><StatusBadge value={approval.status} detail={approval.scope} /><span className="text-sm font-medium">{approval.risk_level} risk · {approval.scope}</span></div>
                      <code className="mt-2 block break-all text-xs text-slate-400">{approval.approval_id} · sha256:{approval.action_hash}</code>
                      <p className="mt-2 text-xs text-slate-500">Requested {new Date(approval.requested_at).toLocaleString()} · expires {new Date(approval.expires_at).toLocaleString()}{approval.decided_by ? ` · decided by ${approval.decided_by}` : ""}</p>
                    </div>
                    {approval.status === "PENDING" && (
                      <div className="flex gap-2">
                        <button type="button" onClick={() => decideApproval(approval, false)} disabled={busy !== ""} className="rounded-md border border-rose-500/60 px-3 py-2 text-sm text-rose-200 hover:bg-rose-500/10 disabled:opacity-40">Reject</button>
                        <button type="button" onClick={() => decideApproval(approval, true)} disabled={busy !== ""} className="rounded-md border border-emerald-500/60 px-3 py-2 text-sm text-emerald-200 hover:bg-emerald-500/10 disabled:opacity-40">{busy === "approval" ? "Saving…" : "Approve & resume"}</button>
                      </div>
                    )}
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-400">No durable approval is waiting. Candidate work cannot be committed until its matching patch hash is approved.</p>
          )}
        </Panel>

        {verifiedResult && (
          <Panel icon={<ShieldCheck className="h-5 w-5" />} title="VerifiedResult v1 · read-only Forge evidence">
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-slate-300">
                <span>Proposal <code className="text-emerald-200">{verifiedResult.proposal_id}</code></span>
                <span>Completed {new Date(verifiedResult.completed_at).toLocaleString()}</span>
              </div>
              <code className="block break-all text-xs text-cyan-200">Bundle sha256:{verifiedResult.bundle_sha256}</code>
              <pre className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/70 p-3 text-xs leading-5 text-slate-300">{JSON.stringify(verifiedResult.metric, null, 2)}</pre>
              <p className="text-xs leading-5 text-slate-500">This panel displays Forge-issued evidence facts only; it does not turn the result into new Studio analysis.</p>
            </div>
          </Panel>
        )}

        <Panel icon={<ClipboardList className="h-5 w-5" />} title="Timeline">
          {mission ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-400">
                  <tr><th className="p-3">Task</th><th className="p-3">Type</th><th className="p-3">Attempt</th><th className="p-3">Lease epoch</th><th className="p-3">Status</th></tr>
                </thead>
                <tbody>
                  {mission.tasks.flatMap((task) => task.attempts.map((attempt) => (
                    <tr key={attempt.attempt_id} className="border-b border-slate-800/70">
                      <td className="p-3 font-mono text-xs text-slate-400">{task.task_id}</td>
                      <td className="p-3">{task.task_type}</td>
                      <td className="p-3 font-mono text-xs text-slate-400">{attempt.attempt_id}</td>
                      <td className="p-3">{attempt.lease_epoch}</td>
                      <td className="p-3"><StatusBadge value={attempt.status} detail={attempt.failure_code} /></td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-slate-400">Create or load a Mission to inspect its durable Task and Attempt timeline.</p>
          )}
          {mission && ["RUNNING", "VERIFYING", "READY"].includes(mission.status) && (
            <button
              onClick={cancelMission}
              disabled={busy !== ""}
              className="mt-4 inline-flex items-center gap-2 rounded-md border border-rose-500/60 px-3 py-2 text-sm text-rose-200 hover:bg-rose-500/10 disabled:opacity-40"
            >
              {busy === "cancel" ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />} Request cancellation
            </button>
          )}
        </Panel>

        {error && <p role="alert" className="rounded-md border border-rose-900 bg-rose-950/50 p-3 text-sm text-rose-200">{error}</p>}
      </section>
    </main>
  );
}

function Panel({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return <section className="rounded-2xl border border-white/10 bg-slate-950/75 p-5 shadow-2xl shadow-black/20"><h2 className="mb-4 flex items-center gap-2 font-semibold text-slate-100">{icon}{title}</h2>{children}</section>;
}

function DashboardSignal({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <section className="rounded-2xl border border-white/10 bg-slate-950/70 p-4">
    <p className="text-[11px] font-medium uppercase tracking-[.15em] text-slate-500">{label}</p>
    <p className="mt-2 font-mono text-lg text-cyan-100">{value}</p>
    <p className="mt-1 text-xs text-slate-500">{detail}</p>
  </section>;
}

function Metric({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div><dt className="text-xs uppercase tracking-wider text-slate-500">{label}</dt><dd className={`mt-1 ${mono ? "font-mono text-xs" : "font-medium"}`}>{value}</dd></div>;
}

function StatusBadge({ value, detail }: { value: string; detail: string | null }) {
  const good = value === "SUCCEEDED" || value === "COMPLETED";
  const bad = value === "FAILED" || value === "CANCELLED";
  return <span title={detail ?? undefined} className={`rounded-full px-2 py-1 text-xs ${good ? "bg-emerald-500/15 text-emerald-300" : bad ? "bg-rose-500/15 text-rose-300" : "bg-amber-500/15 text-amber-200"}`}>{value}</span>;
}
