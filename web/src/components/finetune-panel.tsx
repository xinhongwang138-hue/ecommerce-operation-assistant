"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Database, Download, Loader2, Play, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { FinetuneDataset, FinetuneJob } from "@/types";

const QUALITY_LABEL: Record<string, string> = {
  positive: "正向",
  boundary: "边界",
  needs_fix: "待修",
};

const QUALITY_STYLE: Record<string, string> = {
  positive: "bg-[var(--primary-soft)] text-[var(--primary)]",
  boundary: "bg-[var(--muted)] text-[var(--foreground)]",
  needs_fix: "bg-[var(--warning-soft)] text-[var(--warning)]",
};

const JOB_LABEL: Record<string, string> = {
  registered: "已登记",
  running: "训练中",
  succeeded: "已完成",
  failed: "失败",
};

/** 没进数据集的原因。这些不显示出来，"这次怎么只有 13 条"就永远查不出来。 */
const SKIP_LABEL: Record<string, string> = {
  qa_unscored: "质检会话还没评分",
  qa_too_short_or_unfinished: "质检会话过短或没等到坐席回话",
  cs_missing_track: "客服会话缺轨道标记（旧数据）",
  cs_too_short_or_unfinished: "客服会话过短或没等到机器人回话",
};

/**
 * 训练台账。
 *
 * 页面上不出现进度条和 loss 曲线——本平台不执行训练，画一条假的进度条
 * 比不画更糟：它会让人以为在等一个结果。
 */
export function FinetunePanel({
  datasets,
  jobs,
}: {
  datasets: FinetuneDataset[];
  jobs: FinetuneJob[];
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [sources, setSources] = useState<string[]>(["qa", "cs"]);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [jobName, setJobName] = useState("sft-v1");
  const [baseModel, setBaseModel] = useState("qwen-plus");
  // 选中项不能只在挂载时算一次：建完新数据集会 router.refresh()，props 换成新的数组，
  // 而 useState 的初值不会重算——下拉还指着上一条，登记出来的作业挂错数据集，且不报错。
  // 所以存"人选了哪个"，实际用哪个由当前列表推导：选项消失就退回最新一条。
  const [pickedDatasetId, setPickedDatasetId] = useState<number | null>(null);
  const datasetId = datasets.some((item) => item.id === pickedDatasetId)
    ? pickedDatasetId
    : (datasets[0]?.id ?? null);

  const [pickedJobId, setPickedJobId] = useState<number | null>(null);
  const evalJobId = jobs.some((item) => item.id === pickedJobId)
    ? pickedJobId
    : (jobs[0]?.id ?? null);
  const [evalRows, setEvalRows] = useState("还有货吗,60,85\n能退吗,70,65");

  function flash(message: string | null, kind: "ok" | "err" = "ok") {
    setNotice(kind === "ok" ? message : null);
    setError(kind === "err" ? message : null);
  }

  async function build() {
    if (!name.trim()) {
      flash("给数据集起个名字", "err");
      return;
    }
    setBusy("build");
    flash(null);
    const response = await fetch("/finetune/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, sources, limit: 200 }),
    }).catch(() => null);
    const payload = (await response?.json().catch(() => null)) as
      | { error?: string; sample_count?: number; stats?: FinetuneDataset["stats"] }
      | null;
    setBusy(null);
    if (!response?.ok) {
      flash(payload?.error ?? "构建失败", "err");
      return;
    }
    const dropped = Object.values(payload?.stats?.skipped ?? {}).reduce((sum, n) => sum + n, 0);
    const dup = payload?.stats?.duplicates ?? 0;
    const extra = [
      dup > 0 ? `去重丢 ${dup} 条` : "",
      dropped > 0 ? `另有 ${dropped} 条不符合条件未入库` : "",
    ]
      .filter(Boolean)
      .join("，");
    flash(`建成：${payload?.sample_count ?? 0} 条样本${extra ? `（${extra}）` : ""}`);
    setName("");
    router.refresh();
  }

  async function register() {
    if (!datasetId) {
      flash("先建一个数据集", "err");
      return;
    }
    setBusy("register");
    flash(null);
    const response = await fetch("/finetune/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "register",
        dataset_id: datasetId,
        name: jobName,
        base_model: baseModel,
      }),
    }).catch(() => null);
    const payload = (await response?.json().catch(() => null)) as
      | { error?: string; id?: number }
      | null;
    setBusy(null);
    if (!response?.ok) {
      flash(payload?.error ?? "登记失败", "err");
      return;
    }
    // 登记完直接给出开跑命令：平台不训练，但得让人知道下一步敲什么
    flash(
      `已登记 #${payload?.id ?? "?"}。开跑：python api/scripts/run_finetune_job.py --job ${payload?.id ?? "<id>"}`,
    );
    router.refresh();
  }

  async function setStatus(job: FinetuneJob, status: string) {
    setBusy(`status-${job.id}`);
    flash(null);
    const response = await fetch("/finetune/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "status", job_id: job.id, status, note: "" }),
    }).catch(() => null);
    const payload = (await response?.json().catch(() => null)) as { error?: string } | null;
    setBusy(null);
    if (!response?.ok) {
      flash(payload?.error ?? "更新失败", "err");
      return;
    }
    flash(`状态已改为「${JOB_LABEL[status] ?? status}」`);
    router.refresh();
  }

  async function recordEval() {
    if (!evalJobId) {
      flash("先登记一个任务", "err");
      return;
    }
    const cases = evalRows
      .split("\n")
      .map((line) => line.split(",").map((cell) => cell.trim()))
      .filter((cells) => cells.length >= 3 && cells[0])
      .map((cells) => ({
        question: cells[0],
        base_score: Number(cells[1]),
        tuned_score: Number(cells[2]),
        note: cells[3] ?? "",
      }))
      .filter((item) => Number.isFinite(item.base_score) && Number.isFinite(item.tuned_score));

    if (cases.length === 0) {
      flash("评测用例格式：问题,base分,tuned分（每行一条）", "err");
      return;
    }
    setBusy("eval");
    flash(null);
    const response = await fetch("/finetune/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "eval", job_id: evalJobId, cases, note: "人工打分" }),
    }).catch(() => null);
    const payload = (await response?.json().catch(() => null)) as { error?: string } | null;
    setBusy(null);
    if (!response?.ok) {
      flash(payload?.error ?? "记录失败", "err");
      return;
    }
    flash(`已记录 ${cases.length} 条对比`);
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--foreground)]">
            <Database className="h-4 w-4" /> 建数据集
          </h2>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
            从 P09 质检会话和 P01 客服会话回流。入库前会去重、按长度过滤、按质量分层——
            这些不能指望训练框架替你做。
          </p>
          <div className="mt-2 flex flex-wrap gap-3">
            {(["qa", "cs"] as const).map((key) => (
              <label key={key} className="flex items-center gap-1.5 text-[11px] text-[var(--foreground)]">
                <input
                  type="checkbox"
                  checked={sources.includes(key)}
                  onChange={(event) =>
                    setSources((prev) =>
                      event.target.checked ? [...prev, key] : prev.filter((item) => item !== key),
                    )
                  }
                />
                {key === "qa" ? "销售考核会话" : "AI 客服会话"}
              </label>
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="数据集名称，如「10 月回流」"
              className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-xs text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
            />
            <Button size="sm" onClick={build} disabled={busy !== null}>
              {busy === "build" ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Plus className="mr-1 h-3.5 w-3.5" />
              )}
              构建
            </Button>
          </div>
        </div>

        <div className="card p-4">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--foreground)]">
            <Play className="h-4 w-4" /> 登记训练任务
          </h2>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
            本平台不执行训练。登记之后由外部服务去跑，跑完把状态和指标填回来。
          </p>
          <div className="mt-2 flex flex-col gap-2">
            <select
              value={datasetId ?? ""}
              onChange={(event) => setPickedDatasetId(Number(event.target.value) || null)}
              className="rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-xs text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
            >
              <option value="">选择数据集</option>
              {datasets.map((item) => (
                <option key={item.id} value={item.id}>
                  #{item.id} {item.name}（{item.sample_count} 条）
                </option>
              ))}
            </select>
            <div className="flex items-center gap-2">
              <input
                value={jobName}
                onChange={(event) => setJobName(event.target.value)}
                placeholder="任务名"
                className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-xs text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
              />
              <input
                value={baseModel}
                onChange={(event) => setBaseModel(event.target.value)}
                placeholder="底座模型"
                className="w-28 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-xs text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
              />
              <Button size="sm" onClick={register} disabled={busy !== null}>
                {busy === "register" ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : null}
                登记
              </Button>
            </div>
          </div>
        </div>
      </section>

      <div className="card overflow-hidden">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">数据集</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            待修样本不进导出——把它们拿去训，等于教模型学错误的回答
          </p>
        </div>
        {datasets.length === 0 ? (
          <p className="px-4 py-6 text-center text-xs text-[var(--muted-foreground)]">
            还没有数据集。先去 P09 导入会话或用 P01 聊几句，再回来构建。
          </p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {datasets.map((item) => {
              const byQuality = item.stats?.by_quality ?? {};
              const skipped = Object.entries(item.stats?.skipped ?? {}).filter(
                ([, count]) => count > 0,
              );
              return (
                <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 text-xs font-medium text-[var(--foreground)]">
                      #{item.id} {item.name}
                      <span className="rounded-full bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
                        {item.status}
                      </span>
                    </p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--muted-foreground)]">
                      <span>{item.sample_count} 条</span>
                      <span>· 平均 {item.stats?.average_chars ?? 0} 字</span>
                      {Object.entries(byQuality).map(([key, count]) => (
                        <span
                          key={key}
                          className={`rounded-full px-1.5 py-0.5 ${QUALITY_STYLE[key] ?? "bg-[var(--muted)]"}`}
                        >
                          {QUALITY_LABEL[key] ?? key} {count}
                        </span>
                      ))}
                      {(item.stats?.duplicates ?? 0) > 0 ? (
                        <span>
                          · 合并重复 {item.stats?.duplicates} 条
                          {Object.entries(item.stats?.duplicates_by_source ?? {}).length > 0
                            ? `（${Object.entries(item.stats?.duplicates_by_source ?? {})
                                .map(([key, count]) => `${key === "qa" ? "质检" : "客服"} ${count}`)
                                .join(" / ")}）`
                            : ""}
                        </span>
                      ) : null}
                    </p>
                    {skipped.length > 0 ? (
                      <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--warning)]">
                        <span>未入库</span>
                        {skipped.map(([key, count]) => (
                          <span key={key} className="rounded-full bg-[var(--warning-soft)] px-1.5 py-0.5">
                            {SKIP_LABEL[key] ?? key} {count}
                          </span>
                        ))}
                      </p>
                    ) : null}
                  </div>
                  <a
                    href={`/finetune/export/${item.id}`}
                    className="flex items-center gap-1 rounded-md border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--foreground)] hover:bg-[var(--muted)]"
                  >
                    <Download className="h-3 w-3" /> JSONL
                  </a>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="card overflow-hidden">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">训练任务与评测</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            评测比的是同一批题在 base 与 tuned 上的平均分差——换一批题，「变好了」可以归因到任何东西
          </p>
        </div>
        {jobs.length === 0 ? (
          <p className="px-4 py-6 text-center text-xs text-[var(--muted-foreground)]">
            还没有任务。
          </p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {jobs.map((job) => (
              <li key={job.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-xs font-medium text-[var(--foreground)]">
                    #{job.id} {job.name} · {job.base_model}
                    <span className="ml-2 text-[10px] text-[var(--muted-foreground)]">
                      {JOB_LABEL[job.status] ?? job.status} · 数据集 #{job.dataset_id}
                    </span>
                  </p>
                  <div className="flex items-center gap-1.5">
                    {(["running", "succeeded", "failed"] as const).map((status) => (
                      <Button
                        key={status}
                        size="sm"
                        variant="outline"
                        onClick={() => setStatus(job, status)}
                        disabled={busy !== null}
                      >
                        {busy === `status-${job.id}` ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          JOB_LABEL[status]
                        )}
                      </Button>
                    ))}
                  </div>
                </div>
                {job.eval_summary?.cases ? (
                  <p className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="text-[var(--muted-foreground)]">
                      {job.eval_summary.cases} 条用例
                    </span>
                    <span className="text-[var(--muted-foreground)]">
                      base {job.eval_summary.base_average} → tuned {job.eval_summary.tuned_average}
                    </span>
                    <span
                      className={
                        (job.eval_summary.delta ?? 0) >= 0
                          ? "text-[var(--primary)]"
                          : "text-[var(--danger)]"
                      }
                    >
                      {(job.eval_summary.delta ?? 0) >= 0 ? "+" : ""}
                      {job.eval_summary.delta}
                    </span>
                    <span className="text-[var(--muted-foreground)]">
                      胜 {job.eval_summary.wins ?? 0} / 负 {job.eval_summary.losses ?? 0} / 平{" "}
                      {job.eval_summary.ties ?? 0}
                    </span>
                  </p>
                ) : (
                  <p className="mt-1.5 text-[11px] text-[var(--muted-foreground)]">还没有评测记录</p>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="border-t border-[var(--border)] px-4 py-3">
          <h3 className="text-xs font-semibold text-[var(--foreground)]">记一批评测</h3>
          <div className="mt-2 flex flex-wrap items-start gap-2">
            <select
              value={evalJobId ?? ""}
              onChange={(event) => setPickedJobId(Number(event.target.value) || null)}
              className="rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-xs text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
            >
              <option value="">选择任务</option>
              {jobs.map((item) => (
                <option key={item.id} value={item.id}>
                  #{item.id} {item.name}
                </option>
              ))}
            </select>
            <textarea
              value={evalRows}
              onChange={(event) => setEvalRows(event.target.value)}
              rows={3}
              className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-[11px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
            />
            <Button size="sm" onClick={recordEval} disabled={busy !== null}>
              {busy === "eval" ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
              记录
            </Button>
          </div>
          <p className="mt-1 text-[10px] text-[var(--muted-foreground)]">
            每行一条：问题,base分,tuned分
          </p>
        </div>
      </div>

      {notice ? (
        <p className="rounded-md bg-[var(--primary-soft)] px-3 py-2 text-xs text-[var(--primary)]">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="rounded-md bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
