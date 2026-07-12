"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Circle,
  Clock,
  Eye,
  FileText,
  GitBranch,
  Layers,
  Loader2,
  Play,
  Sparkles,
} from "lucide-react";
import ForkTreeView from "../components/ForkTree";

const API_PORT = process.env.NEXT_PUBLIC_API_PORT ?? "8001";

interface TopicCard {
  topic_id?: string;
  title?: string;
  research_direction?: string;
  candidate_question?: string;
  suspected_gap?: string;
  key_evidence?: string[];
  novelty_rationale?: string;
  feasibility_rationale?: string;
  risk_factors?: string[];
  score?: number;
}

interface GapCard {
  gap_id?: string;
  title?: string;
  problem?: string;
  missing_piece?: string;
  datasets?: string[];
  baselines?: string[];
  metrics?: string[];
  novelty_score?: number;
  feasibility_score?: number;
  evidence_level?: string;
  evidence_papers?: string[];
}

interface AccessStatus {
  paper_id?: string;
  access_status?: string;
  evidence_level?: string;
  has_code?: boolean;
  has_dataset?: boolean;
  has_benchmark?: boolean;
  notes?: string[];
}

interface DecisionCard {
  passed?: boolean;
  decision?: string;
  final_rating?: number;
  recommended_action?: string;
  target_node?: string;
  branch_count?: number;
  branch_variants?: string[];
  blocking_issues?: string[];
  required_fixes?: string[];
  reason?: string;
}

interface ResearchGate {
  gate_decision?: string;
  rationale?: string;
  blocking_issues?: string[];
  required_fixes?: string[];
}

interface Critique {
  reviewer?: string;
  rating?: number;
  rationale?: string;
}

interface Experiment {
  name?: string;
  baselines?: string[];
  metrics?: string[];
  datasets?: any[];
  _missing?: string[];
}

interface Artifact {
  path: string;
  name: string;
  kind: string;
  size: number;
  updated_at?: number;
}

interface Snapshot {
  pico?: any;
  papers_count?: number;
  critiques?: Critique[];
  meta_decision?: any;
  paper_latex_path?: string;
  errors?: string[];
  topic_cards?: TopicCard[];
  current_topic_id?: string;
  evidence_access_status?: AccessStatus[];
  gap_cards?: GapCard[];
  current_gap_id?: string;
  decision_card?: DecisionCard;
  research_gate?: ResearchGate;
  m1_pending_clarification?: string;
  experiment_plan?: Experiment;
  paper_title?: string;
  artifacts?: Artifact[];
}

interface ProgressNode {
  id: string;
  label: string;
  status: "pending" | "running" | "done" | "error" | "skipped";
  error?: string;
}

interface ProgressState {
  nodes?: ProgressNode[];
  current_node?: string;
  events?: any[];
}

interface RunState {
  forkId: string;
  status: string;
  progress?: ProgressState | null;
  snapshot?: Snapshot | null;
  error?: string;
}

interface Clarification {
  q: string;
  a: string;
}

type StartIntent =
  | { kind: "single"; question: string; topicId?: string }
  | { kind: "branches"; cards: TopicCard[] };

export default function HomePage() {
  const [view, setView] = useState<"research" | "tree">("research");
  const [question, setQuestion] = useState("");
  const [intakeMode, setIntakeMode] = useState<"m0" | "direct">("m0");
  const [topicCards, setTopicCards] = useState<TopicCard[]>([]);
  const [selectedTopicIds, setSelectedTopicIds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<"discovering" | "clarifying" | "starting" | "branching" | "">("");
  const [m1Clarifications, setM1Clarifications] = useState<Clarification[]>([]);
  const [m1FollowUp, setM1FollowUp] = useState("");
  const [m1Answer, setM1Answer] = useState("");
  const [pendingIntent, setPendingIntent] = useState<StartIntent | null>(null);
  const [clarifyError, setClarifyError] = useState("");
  const [activeRun, setActiveRun] = useState<RunState | null>(null);
  const [branchRuns, setBranchRuns] = useState<Record<string, RunState>>({});
  const socketsRef = useRef<Record<string, WebSocket>>({});

  useEffect(() => {
    return () => {
      for (const ws of Object.values(socketsRef.current)) ws.close();
    };
  }, []);

  async function discoverTopics() {
    if (!question.trim()) return;
    setBusy("discovering");
    setTopicCards([]);
    setSelectedTopicIds(new Set());
    try {
      const resp = await fetch("/api/topics/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, k: 3 }),
      });
      const data = await resp.json();
      const cards = data.topic_cards ?? [];
      setTopicCards(cards);
      if (cards[0]?.topic_id) setSelectedTopicIds(new Set([cards[0].topic_id]));
    } finally {
      setBusy("");
    }
  }

  async function startDirectRun(
    runQuestion: string,
    topicId: string | undefined,
    clarifications: Clarification[],
  ) {
    if (!runQuestion.trim()) return;
    setBusy("starting");
    setActiveRun(null);
    try {
      const resp = await fetch("/api/research/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: runQuestion,
          execution_mode: "generate_only",
          skip_m0: true,
          selected_topic_id: topicId ?? null,
          clarifications,
        }),
      });
      const data = await resp.json();
      const run: RunState = { forkId: data.fork_id, status: "running" };
      setActiveRun(run);
      subscribeRun(data.fork_id, "single");
    } finally {
      setBusy("");
    }
  }

  async function beginSingleRun(runQuestion: string, topicId?: string) {
    await requestM1Clarification({ kind: "single", question: runQuestion, topicId }, []);
  }

  async function runSelectedSingle() {
    const selected = selectedCards()[0];
    if (!selected) return;
    await beginSingleRun(
      selected.candidate_question || selected.title || question,
      selected.topic_id,
    );
  }

  async function startBranchRuns(cards: TopicCard[], clarifications: Clarification[]) {
    if (cards.length === 0) return;
    setBusy("branching");
    setBranchRuns({});
    try {
      const resp = await fetch("/api/branches/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          raw_question: question,
          topic_cards: cards,
          execution_mode: "generate_only",
          clarifications,
        }),
      });
      const data = await resp.json();
      const runs: Record<string, RunState> = {};
      for (const fid of data.fork_ids ?? []) {
        runs[fid] = { forkId: fid, status: "running" };
        subscribeRun(fid, "branch");
      }
      setBranchRuns(runs);
      setView("research");
    } finally {
      setBusy("");
    }
  }

  async function runSelectedBranches() {
    const cards = selectedCards();
    if (cards.length === 0) return;
    await requestM1Clarification({ kind: "branches", cards }, []);
  }

  async function requestM1Clarification(intent: StartIntent, clarifications: Clarification[]) {
    const m1Question = intent.kind === "single" ? intent.question : question;
    if (!m1Question.trim()) return;
    setBusy("clarifying");
    setClarifyError("");
    try {
      const resp = await fetch("/api/m1/clarify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: m1Question,
          clarifications,
          max_turns: 3,
        }),
      });
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      setM1Clarifications(data.clarifications ?? clarifications);
      if (data.ready || !data.follow_up) {
        setPendingIntent(null);
        setM1FollowUp("");
        setM1Answer("");
        if (intent.kind === "single") {
          await startDirectRun(intent.question, intent.topicId, data.clarifications ?? clarifications);
        } else {
          await startBranchRuns(intent.cards, data.clarifications ?? clarifications);
        }
        return;
      }
      setPendingIntent(intent);
      setM1FollowUp(data.follow_up);
      setM1Answer("");
    } catch (err) {
      setClarifyError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy((prev) => (prev === "clarifying" ? "" : prev));
    }
  }

  async function submitM1Answer() {
    if (!pendingIntent || !m1FollowUp.trim() || !m1Answer.trim()) return;
    const next = [...m1Clarifications, { q: m1FollowUp, a: m1Answer.trim() }];
    await requestM1Clarification(pendingIntent, next);
  }

  async function continueWithoutM1Answer() {
    if (!pendingIntent) return;
    const intent = pendingIntent;
    setPendingIntent(null);
    setM1FollowUp("");
    setM1Answer("");
    if (intent.kind === "single") {
      await startDirectRun(intent.question, intent.topicId, m1Clarifications);
    } else {
      await startBranchRuns(intent.cards, m1Clarifications);
    }
  }

  function subscribeRun(forkId: string, kind: "single" | "branch") {
    socketsRef.current[forkId]?.close();
    const ws = new WebSocket(`ws://localhost:${API_PORT}/ws/research/${forkId}`);
    socketsRef.current[forkId] = ws;
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      const patch: Partial<RunState> = {
        status: msg.status,
        progress: msg.progress,
        snapshot: msg.snapshot,
        error: msg.error,
      };
      if (kind === "single") {
        setActiveRun((prev) => ({
          ...(prev ?? { forkId, status: "running" }),
          ...patch,
          forkId,
          status: patch.status ?? prev?.status ?? "running",
        }));
      } else {
        setBranchRuns((prev) => ({
          ...prev,
          [forkId]: {
            ...(prev[forkId] ?? { forkId, status: "running" }),
            ...patch,
            forkId,
            status: patch.status ?? prev[forkId]?.status ?? "running",
          },
        }));
      }
    };
    ws.onclose = () => {
      delete socketsRef.current[forkId];
    };
  }

  function toggleTopic(id: string) {
    setSelectedTopicIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectedCards() {
    return topicCards.filter((c) => c.topic_id && selectedTopicIds.has(c.topic_id));
  }

  return (
    <main className="min-h-screen p-8 max-w-6xl mx-auto">
      <header className="mb-6 flex items-center gap-3">
        <Sparkles className="w-8 h-8 text-cyan-400" />
        <h1 className="text-3xl font-bold">AI Co-Scientist</h1>
        <span className="text-sm text-gray-400">多路线科研工作流</span>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setView("research")}
            className={`px-3 py-1.5 rounded-lg text-sm flex items-center gap-1 ${
              view === "research" ? "bg-cyan-600" : "bg-gray-800 hover:bg-gray-700"
            }`}
          >
            <Eye className="w-4 h-4" /> 研究视图
          </button>
          <button
            onClick={() => setView("tree")}
            className={`px-3 py-1.5 rounded-lg text-sm flex items-center gap-1 ${
              view === "tree" ? "bg-cyan-600" : "bg-gray-800 hover:bg-gray-700"
            }`}
          >
            <GitBranch className="w-4 h-4" /> Fork 树
          </button>
        </div>
      </header>

      {view === "tree" ? (
        <ForkTreeView />
      ) : (
        <div className="space-y-5">
          <Card title="Direction Intake">
            <div className="space-y-4">
              <textarea
                className="w-full p-4 bg-gray-950 border border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-500"
                rows={3}
                placeholder='输入研究兴趣或明确问题,如:"我想做 RAG 减少幻觉方向"'
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => setIntakeMode("m0")}
                  className={`px-3 py-2 rounded text-sm flex items-center gap-2 ${
                    intakeMode === "m0" ? "bg-cyan-700" : "bg-gray-800 hover:bg-gray-700"
                  }`}
                >
                  <Layers className="w-4 h-4" /> M0 生成候选课题
                </button>
                <button
                  onClick={() => setIntakeMode("direct")}
                  className={`px-3 py-2 rounded text-sm flex items-center gap-2 ${
                    intakeMode === "direct" ? "bg-cyan-700" : "bg-gray-800 hover:bg-gray-700"
                  }`}
                >
                  <Play className="w-4 h-4" /> 已有明确课题
                </button>
              </div>

              {intakeMode === "m0" ? (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={discoverTopics}
                    disabled={!question.trim() || busy === "discovering"}
                    className="px-4 py-2 rounded bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 flex items-center gap-2"
                  >
                    {busy === "discovering" ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                    生成 TopicCard
                  </button>
                  <button
                    onClick={runSelectedSingle}
                    disabled={selectedCards().length < 1 || busy !== ""}
                    className="px-4 py-2 rounded bg-green-700 hover:bg-green-600 disabled:opacity-50"
                  >
                    跑选中主线
                  </button>
                  <button
                    onClick={runSelectedBranches}
                    disabled={selectedCards().length < 2 || busy !== ""}
                    className="px-4 py-2 rounded bg-purple-700 hover:bg-purple-600 disabled:opacity-50"
                  >
                    多分支探索({selectedCards().length})
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => beginSingleRun(question)}
                  disabled={!question.trim() || busy !== ""}
                  className="px-4 py-2 rounded bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 flex items-center gap-2"
                >
                  {busy === "starting" || busy === "clarifying" ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : null}
                  直接启动研究
                </button>
              )}
            </div>
          </Card>

          {(m1FollowUp || m1Clarifications.length > 0 || clarifyError) && (
            <Card title="M1 澄清">
              <div className="space-y-3">
                {m1Clarifications.length > 0 && (
                  <div className="space-y-2">
                    {m1Clarifications.map((item, i) => (
                      <div key={`${item.q}-${i}`} className="rounded border border-gray-800 bg-gray-950 p-3 text-sm">
                        <div className="text-cyan-300">{item.q}</div>
                        <div className="mt-1 text-gray-300">{item.a}</div>
                      </div>
                    ))}
                  </div>
                )}
                {m1FollowUp && (
                  <div className="space-y-2">
                    <div className="text-sm text-cyan-200">{m1FollowUp}</div>
                    <textarea
                      className="w-full p-3 bg-gray-950 border border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-500"
                      rows={2}
                      value={m1Answer}
                      onChange={(e) => setM1Answer(e.target.value)}
                    />
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={submitM1Answer}
                        disabled={!m1Answer.trim() || busy !== ""}
                        className="px-4 py-2 rounded bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 flex items-center gap-2"
                      >
                        {busy === "clarifying" ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                        提交回答
                      </button>
                      <button
                        onClick={continueWithoutM1Answer}
                        disabled={busy !== ""}
                        className="px-4 py-2 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-50"
                      >
                        直接继续
                      </button>
                    </div>
                  </div>
                )}
                {clarifyError && <div className="text-sm text-red-300">{clarifyError}</div>}
              </div>
            </Card>
          )}

          {topicCards.length > 0 && (
            <Card title={`M0 候选课题(${topicCards.length})`}>
              <TopicCardChooser
                cards={topicCards}
                selectedIds={selectedTopicIds}
                onToggle={toggleTopic}
              />
            </Card>
          )}

          {activeRun && (
            <Card title={`当前主线 Fork: ${activeRun.forkId}`}>
              <RunProgress run={activeRun} />
            </Card>
          )}

          {Object.keys(branchRuns).length > 0 && (
            <Card title={`M8 多分支进度(${Object.keys(branchRuns).length})`}>
              <div className="space-y-4">
                {Object.values(branchRuns).map((run) => (
                  <div key={run.forkId} className="border border-gray-800 rounded p-3 bg-gray-950">
                    <div className="mb-2 flex items-center justify-between">
                      <code className="text-xs text-cyan-300">{run.forkId}</code>
                      <StatusText status={run.status} />
                    </div>
                    <RunProgress run={run} compact />
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}
    </main>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="p-4 bg-gray-900 border border-gray-800 rounded-lg">
      <h2 className="font-semibold text-cyan-300 mb-3">{title}</h2>
      {children}
    </section>
  );
}

function TopicCardChooser({
  cards,
  selectedIds,
  onToggle,
}: {
  cards: TopicCard[];
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      {cards.map((card, i) => {
        const id = card.topic_id ?? `topic-${i}`;
        const selected = selectedIds.has(id);
        return (
          <button
            key={id}
            onClick={() => onToggle(id)}
            className={`text-left p-3 rounded border min-h-52 ${
              selected
                ? "border-cyan-500 bg-cyan-950/30"
                : "border-gray-700 bg-gray-950 hover:border-gray-500"
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="font-semibold text-cyan-200">{card.title}</div>
              <input type="checkbox" checked={selected} readOnly className="mt-1" />
            </div>
            <div className="mt-2 text-xs text-gray-400">
              score {(card.score ?? 0).toFixed(1)}
            </div>
            {card.candidate_question && (
              <p className="mt-2 text-sm text-gray-300">{card.candidate_question}</p>
            )}
            {card.suspected_gap && (
              <p className="mt-2 text-xs text-amber-300">Gap: {card.suspected_gap}</p>
            )}
            {card.risk_factors && card.risk_factors.length > 0 && (
              <p className="mt-2 text-xs text-red-300">{card.risk_factors.join(" / ")}</p>
            )}
          </button>
        );
      })}
    </div>
  );
}

function RunProgress({ run, compact = false }: { run: RunState; compact?: boolean }) {
  const snapshot = run.snapshot;
  return (
    <div className="space-y-4">
      <ProgressTimeline progress={run.progress} compact={compact} />
      {run.error && <div className="text-sm text-red-300">{run.error}</div>}
      {snapshot && !compact && <SnapshotView snapshot={snapshot} />}
    </div>
  );
}

function ProgressTimeline({
  progress,
  compact = false,
}: {
  progress?: ProgressState | null;
  compact?: boolean;
}) {
  const nodes = progress?.nodes ?? [];
  if (nodes.length === 0) {
    return <div className="text-sm text-gray-500">等待后端进度事件...</div>;
  }
  return (
    <div className={compact ? "grid grid-cols-1 md:grid-cols-2 gap-2" : "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2"}>
      {nodes.map((node) => (
        <div
          key={node.id}
          className={`flex items-center gap-2 rounded border px-3 py-2 text-sm ${progressClass(node.status)}`}
        >
          <ProgressIcon status={node.status} />
          <span className="truncate">{node.label}</span>
        </div>
      ))}
    </div>
  );
}

function ProgressIcon({ status }: { status: ProgressNode["status"] }) {
  if (status === "running") return <Loader2 className="w-4 h-4 animate-spin text-cyan-300" />;
  if (status === "done") return <CheckCircle2 className="w-4 h-4 text-green-400" />;
  if (status === "error") return <AlertCircle className="w-4 h-4 text-red-400" />;
  if (status === "skipped") return <Clock className="w-4 h-4 text-gray-500" />;
  return <Circle className="w-4 h-4 text-gray-600" />;
}

function progressClass(status: ProgressNode["status"]) {
  if (status === "running") return "border-cyan-600 bg-cyan-950/30 text-cyan-100";
  if (status === "done") return "border-green-800 bg-green-950/20 text-green-100";
  if (status === "error") return "border-red-800 bg-red-950/30 text-red-100";
  if (status === "skipped") return "border-gray-800 bg-gray-950 text-gray-500";
  return "border-gray-800 bg-gray-950 text-gray-400";
}

function StatusText({ status }: { status: string }) {
  const cls =
    status === "done"
      ? "text-green-300"
      : status === "error"
      ? "text-red-300"
      : status === "running"
      ? "text-cyan-300"
      : "text-gray-400";
  return <span className={`text-xs ${cls}`}>{status}</span>;
}

function SnapshotView({ snapshot }: { snapshot: Snapshot }) {
  return (
    <div className="space-y-3">
      <MiniBlock title="PICO" value={snapshot.pico} />
      {snapshot.m1_pending_clarification && (
        <div className="rounded border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-100">
          <span className="text-amber-300">M1 待补充:</span>{" "}
          {snapshot.m1_pending_clarification}
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Metric label="Papers" value={snapshot.papers_count ?? 0} />
        <Metric label="Reviewer Cards" value={(snapshot.critiques ?? []).length} />
        <Metric label="GapCards" value={(snapshot.gap_cards ?? []).length} />
      </div>
      {snapshot.evidence_access_status && snapshot.evidence_access_status.length > 0 && (
        <AccessStatusSummary statuses={snapshot.evidence_access_status} />
      )}
      {snapshot.gap_cards && snapshot.gap_cards.length > 0 && (
        <GapCardList cards={snapshot.gap_cards} currentId={snapshot.current_gap_id} />
      )}
      {snapshot.decision_card && Object.keys(snapshot.decision_card).length > 0 && (
        <DecisionCardView card={snapshot.decision_card} />
      )}
      {snapshot.experiment_plan && Object.keys(snapshot.experiment_plan).length > 0 && (
        <MiniBlock title="ExperimentPlan" value={snapshot.experiment_plan} />
      )}
      {snapshot.research_gate && Object.keys(snapshot.research_gate).length > 0 && (
        <ResearchGateView gate={snapshot.research_gate} />
      )}
      {snapshot.artifacts && snapshot.artifacts.length > 0 && (
        <ArtifactList artifacts={snapshot.artifacts} />
      )}
      {snapshot.paper_latex_path && (
        <div className="text-sm">
          <span className="text-gray-400">Paper:</span>{" "}
          <code className="text-green-300">{snapshot.paper_latex_path}</code>
        </div>
      )}
    </div>
  );
}

function ArtifactList({ artifacts }: { artifacts: Artifact[] }) {
  const [selected, setSelected] = useState<Artifact | null>(artifacts[0] ?? null);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function openArtifact(artifact: Artifact) {
    setSelected(artifact);
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(`/api/artifacts/content?path=${encodeURIComponent(artifact.path)}`);
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      setContent(data.content ?? "");
    } catch (err) {
      setContent("");
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (artifacts[0]) void openArtifact(artifacts[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifacts.map((a) => a.path).join("|")]);

  return (
    <div className="rounded border border-gray-800 bg-gray-950 p-3">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-cyan-300">
        <FileText className="h-4 w-4" />
        生成物
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-[260px_1fr]">
        <div className="space-y-2">
          {artifacts.map((artifact) => (
            <button
              key={artifact.path}
              onClick={() => openArtifact(artifact)}
              className={`w-full rounded border px-3 py-2 text-left text-sm ${
                selected?.path === artifact.path
                  ? "border-cyan-600 bg-cyan-950/30 text-cyan-100"
                  : "border-gray-800 bg-gray-900 text-gray-300 hover:border-gray-600"
              }`}
            >
              <div className="truncate font-medium">{artifact.name}</div>
              <div className="mt-1 text-xs text-gray-500">
                {artifact.kind} · {formatBytes(artifact.size)}
              </div>
            </button>
          ))}
        </div>
        <div className="min-h-64 rounded border border-gray-800 bg-black/30 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <code className="truncate text-xs text-cyan-300">{selected?.path ?? ""}</code>
            {loading && <Loader2 className="h-4 w-4 animate-spin text-cyan-300" />}
          </div>
          {error ? (
            <div className="text-sm text-red-300">{error}</div>
          ) : (
            <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap text-xs text-gray-300">
              {content || "选择左侧生成物查看内容"}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border border-gray-800 bg-gray-950 p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-xl font-semibold text-cyan-200">{value}</div>
    </div>
  );
}

function MiniBlock({ title, value }: { title: string; value: any }) {
  return (
    <details className="rounded border border-gray-800 bg-gray-950 p-3">
      <summary className="cursor-pointer text-sm text-cyan-300">{title}</summary>
      <pre className="mt-2 max-h-72 overflow-auto text-xs text-gray-300">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function AccessStatusSummary({ statuses }: { statuses: AccessStatus[] }) {
  const counts: Record<string, number> = {};
  let withCode = 0;
  let withDataset = 0;
  for (const s of statuses) {
    const key = s.access_status || "unknown";
    counts[key] = (counts[key] ?? 0) + 1;
    if (s.has_code) withCode++;
    if (s.has_dataset) withDataset++;
  }
  return (
    <div className="rounded border border-gray-800 bg-gray-950 p-3 text-sm text-gray-300">
      <div>Access: {Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(" / ")}</div>
      <div className="text-xs text-gray-500">code {withCode} · dataset {withDataset}</div>
    </div>
  );
}

function GapCardList({ cards, currentId }: { cards: GapCard[]; currentId?: string }) {
  return (
    <div className="space-y-2">
      {cards.map((card, i) => {
        const selected = card.gap_id && card.gap_id === currentId;
        return (
          <div
            key={card.gap_id ?? i}
            className={`rounded border p-3 ${selected ? "border-cyan-600 bg-cyan-950/20" : "border-gray-800 bg-gray-950"}`}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="font-semibold text-cyan-200">{card.title}</div>
              <div className="text-xs text-gray-500">{card.evidence_level ?? "medium"}</div>
            </div>
            {card.problem && <p className="mt-1 text-sm text-gray-300">{card.problem}</p>}
            <div className="mt-2 text-xs text-gray-500">
              datasets {(card.datasets ?? []).join(", ") || "-"} · baselines{" "}
              {(card.baselines ?? []).join(", ") || "-"} · metrics{" "}
              {(card.metrics ?? []).join(", ") || "-"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DecisionCardView({ card }: { card: DecisionCard }) {
  return (
    <div className="rounded border border-gray-800 bg-gray-950 p-3 text-sm">
      <div className="flex flex-wrap gap-3 text-gray-300">
        <span className={card.passed ? "text-green-300" : "text-amber-300"}>
          {card.decision ?? "unknown"}
        </span>
        <span>rating {(card.final_rating ?? 0).toFixed(1)}</span>
        <span>{card.recommended_action} → {card.target_node}</span>
      </div>
      {card.reason && <p className="mt-2 text-xs text-gray-500">{card.reason}</p>}
    </div>
  );
}

function ResearchGateView({ gate }: { gate: ResearchGate }) {
  const ok = gate.gate_decision === "continue_to_m6";
  return (
    <div className="rounded border border-gray-800 bg-gray-950 p-3 text-sm">
      <span className={ok ? "text-green-300" : "text-amber-300"}>
        {gate.gate_decision ?? "unknown"}
      </span>
      {gate.rationale && <p className="mt-2 text-xs text-gray-500">{gate.rationale}</p>}
    </div>
  );
}
