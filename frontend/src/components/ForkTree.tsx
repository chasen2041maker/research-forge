"use client";

/**
 * ============================================================
 *  ForkTreeView 组件(整理版 Phase E)
 * ============================================================
 *
 * 🎓 教学目标
 *   把 GET /api/forks/tree 返回的"父→子映射"递归渲染成研究树。
 *   - mainline 高亮(整理版 §9.5 第一阶段:winner fork 标记 mainline)
 *   - abandoned 灰显
 *   - 点击 fork 行调 GET /api/forks/{fork_id} 拿单条详情侧栏展示
 *   - 选中多条 fork 后调 POST /api/branches/merge 触发 LLM 综合评分
 *
 * 📌 设计取舍
 *   不引 react-flow / d3,直接用 UL/LI 嵌套渲染。Phase E MVP 够用,
 *   未来要复杂交互再换图形库。
 *
 * ------------------------------------------------------------
 */

import { useEffect, useState } from "react";
import { Loader2, GitBranch, Check, X, Crown, Eye } from "lucide-react";

interface ForkRow {
  fork_id: string;
  parent_fork_id: string;
  branch_node: string;
  description: string;
  created_at: number;
  final_rating: number;
  status: string; // running / done / abandoned / mainline
  topic_id: string;
}

interface TreeMap {
  [parentId: string]: string[];
}

export default function ForkTreeView() {
  const [tree, setTree] = useState<TreeMap>({});
  const [forks, setForks] = useState<Record<string, ForkRow>>({});
  const [loading, setLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<any | null>(null);
  const [mergeResult, setMergeResult] = useState<any | null>(null);
  const [mergeLoading, setMergeLoading] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const resp = await fetch("/api/forks/tree");
      const data = await resp.json();
      setTree(data.tree || {});
      const map: Record<string, ForkRow> = {};
      for (const f of data.forks ?? []) map[f.fork_id] = f;
      setForks(map);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function viewDetail(forkId: string) {
    setDetail({ loading: true, fork_id: forkId });
    const resp = await fetch(`/api/forks/${forkId}`);
    if (!resp.ok) {
      setDetail({ error: `HTTP ${resp.status}`, fork_id: forkId });
      return;
    }
    const data = await resp.json();
    setDetail({ ...data, fork_id: forkId });
  }

  function toggleSelect(forkId: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(forkId)) next.delete(forkId);
      else next.add(forkId);
      return next;
    });
    setMergeResult(null);
  }

  async function mergeSelected(useLlm: boolean) {
    if (selectedIds.size < 2) return;
    setMergeLoading(true);
    setMergeResult(null);
    try {
      const resp = await fetch("/api/branches/merge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fork_ids: Array.from(selectedIds),
          use_llm_compare: useLlm,
        }),
      });
      const data = await resp.json();
      setMergeResult(data);
      await refresh();
    } finally {
      setMergeLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-gray-400">
        <Loader2 className="w-4 h-4 animate-spin" /> 加载 fork 树...
      </div>
    );
  }

  const rootChildren = tree["root"] ?? [];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-4">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm text-gray-400">
            选中 {selectedIds.size} 条 / 共 {Object.keys(forks).length} 条
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => mergeSelected(false)}
              disabled={selectedIds.size < 2 || mergeLoading}
              className="px-3 py-1.5 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-40 rounded text-sm flex items-center gap-1"
            >
              {mergeLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Crown className="w-4 h-4" />}
              merge(规则版)
            </button>
            <button
              onClick={() => mergeSelected(true)}
              disabled={selectedIds.size < 2 || mergeLoading}
              className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 disabled:opacity-40 rounded text-sm flex items-center gap-1"
            >
              {mergeLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Crown className="w-4 h-4" />}
              merge(LLM critical)
            </button>
            <button
              onClick={refresh}
              className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-sm"
            >
              刷新
            </button>
          </div>
        </div>

        {mergeResult && (
          <div className="p-3 bg-cyan-950/40 border border-cyan-800 rounded text-sm">
            {mergeResult.winner ? (
              <>
                <div className="font-semibold text-cyan-200">
                  🏆 Winner: {mergeResult.winner.fork_id}
                </div>
                <div className="text-xs text-gray-300">
                  {mergeResult.winner.description} · rating {mergeResult.winner.final_rating} ·
                  status {mergeResult.winner.status}
                </div>
              </>
            ) : (
              <div className="text-amber-300">
                未选出 winner:{mergeResult.reason}
              </div>
            )}
          </div>
        )}

        {rootChildren.length === 0 ? (
          <div className="text-gray-500 text-sm">还没有任何 fork。先在研究视图启动一次研究。</div>
        ) : (
          <ul className="space-y-2">
            {rootChildren.map((id) => (
              <ForkNode
                key={id}
                forkId={id}
                tree={tree}
                forks={forks}
                depth={0}
                selectedIds={selectedIds}
                onSelect={toggleSelect}
                onView={viewDetail}
              />
            ))}
          </ul>
        )}
      </div>

      {/* 侧栏:单条 fork 详情 */}
      <aside className="lg:sticky lg:top-4 self-start">
        <div className="p-4 bg-gray-900 border border-gray-800 rounded-lg min-h-[200px]">
          <h3 className="font-semibold text-cyan-300 mb-2">Fork 详情</h3>
          {!detail && <p className="text-sm text-gray-500">点击 👁 查看一条 fork。</p>}
          {detail?.loading && (
            <div className="text-sm text-gray-400">
              <Loader2 className="w-4 h-4 animate-spin inline" /> 加载中...
            </div>
          )}
          {detail?.error && (
            <div className="text-sm text-red-400">错误:{detail.error}</div>
          )}
          {detail?.meta && (
            <div className="text-sm space-y-2">
              <div className="text-cyan-200 font-mono text-xs">{detail.fork_id}</div>
              <div className="text-gray-300">{detail.meta.description}</div>
              <div className="text-xs text-gray-400">
                from {detail.meta.parent_fork_id || "root"} @ {detail.meta.branch_node}
                <br />
                rating {detail.meta.final_rating?.toFixed?.(1) ?? "-"} · status{" "}
                {detail.meta.status}
              </div>
              {detail.snapshot && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-cyan-400">展开 snapshot</summary>
                  <pre className="mt-1 max-h-72 overflow-auto bg-gray-950 p-2 rounded">
                    {JSON.stringify(detail.snapshot, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function ForkNode({
  forkId,
  tree,
  forks,
  depth,
  selectedIds,
  onSelect,
  onView,
}: {
  forkId: string;
  tree: TreeMap;
  forks: Record<string, ForkRow>;
  depth: number;
  selectedIds: Set<string>;
  onSelect: (id: string) => void;
  onView: (id: string) => void;
}) {
  const fork = forks[forkId];
  if (!fork) {
    return null;
  }
  const children = tree[forkId] ?? [];
  const selected = selectedIds.has(forkId);
  const isMainline = fork.status === "mainline";
  const isAbandoned = fork.status === "abandoned";

  return (
    <li>
      <div
        className={`flex items-center gap-2 p-2 rounded border ${
          isMainline
            ? "border-amber-500 bg-amber-950/30"
            : selected
            ? "border-cyan-500 bg-cyan-950/30"
            : isAbandoned
            ? "border-gray-800 bg-gray-950 opacity-60"
            : "border-gray-700 bg-gray-950"
        }`}
        style={{ marginLeft: depth * 16 }}
      >
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onSelect(forkId)}
          className="cursor-pointer"
        />
        <GitBranch className={`w-4 h-4 ${isMainline ? "text-amber-400" : "text-gray-500"}`} />
        <code className="text-xs text-cyan-200">{forkId.slice(0, 8)}</code>
        <span className="text-sm text-gray-200 flex-1 truncate">{fork.description}</span>
        <span className="text-xs text-gray-400">
          @ {fork.branch_node}
          {fork.topic_id && (
            <span className="ml-2 px-1.5 py-0.5 bg-gray-800 rounded font-mono">{fork.topic_id}</span>
          )}
        </span>
        <span className="text-xs text-gray-400">
          rating {fork.final_rating?.toFixed?.(1) ?? "-"}
        </span>
        <StatusPill status={fork.status} />
        <button
          onClick={() => onView(forkId)}
          className="p-1 hover:bg-gray-800 rounded"
          title="查看详情"
        >
          <Eye className="w-4 h-4 text-gray-400" />
        </button>
      </div>
      {children.length > 0 && (
        <ul className="space-y-2 mt-2">
          {children.map((cid) => (
            <ForkNode
              key={cid}
              forkId={cid}
              tree={tree}
              forks={forks}
              depth={depth + 1}
              selectedIds={selectedIds}
              onSelect={onSelect}
              onView={onView}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function StatusPill({ status }: { status: string }) {
  const cls =
    status === "mainline"
      ? "bg-amber-700"
      : status === "done"
      ? "bg-green-700"
      : status === "running"
      ? "bg-blue-700"
      : status === "abandoned"
      ? "bg-red-900"
      : "bg-gray-800";
  const Icon =
    status === "mainline" ? Crown : status === "done" ? Check : status === "abandoned" ? X : null;
  return (
    <span className={`px-2 py-0.5 rounded text-xs flex items-center gap-1 ${cls}`}>
      {Icon && <Icon className="w-3 h-3" />}
      {status}
    </span>
  );
}
