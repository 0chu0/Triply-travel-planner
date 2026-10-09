"""
评测报告：把一次 run 的结果落成 JSON（机器可读）+ Markdown（人可读）

产物目录：scripts/eval_out/
    {run_id}.json    完整结果（含每轮 transcript 摘要、每项指标 detail）
    {run_id}.md      Markdown 报告（总览 + 用例明细 + 失败详情）
    latest.json      最近一次 run 的指针（方便 --baseline 自动对比）
    latest.md
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_lib.metrics import GATE_KEYS

OUT_DIR = Path(__file__).resolve().parent.parent / "eval_out"

_STATUS_ICON = {"success": "✅", "error": "❌", "timeout": "⏱️"}
_KIND_LABEL = {"gate": "门禁", "core": "核心", "info": "观察"}


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


# ---------------------------------------------------------------- JSON

def build_payload(
    *,
    run_id: str,
    dataset: str,
    dataset_version: str,
    tier: str,
    temperature,
    concurrency: int,
    timeout_sec: int,
    started_at: str,
    finished_at: str,
    rows: list[dict],
    summary: dict,
    comparison: list[dict] | None = None,
    baseline_run_id: str | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "dataset": dataset,
        "dataset_version": dataset_version,
        "tier": tier,
        "started_at": started_at,
        "finished_at": finished_at,
        "config": {
            "temperature": temperature,
            "concurrency": concurrency,
            "case_timeout_sec": timeout_sec,
        },
        "summary": summary,
        "baseline_run_id": baseline_run_id,
        "comparison": comparison or [],
        "cases": rows,
    }


def write_json(payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{payload['run_id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_run(run_id_or_path: str) -> dict:
    """按 run_id 或文件路径读取一次 run 的结果"""
    path = Path(run_id_or_path)
    if not path.exists():
        path = OUT_DIR / f"{run_id_or_path}.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到 run 结果: {run_id_or_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_latest() -> dict | None:
    path = OUT_DIR / "latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- Markdown

def render_markdown(payload: dict) -> str:
    s = payload["summary"]
    cfg = payload["config"]
    lines: list[str] = []

    verdict = "✅ 通过" if s["verdict"] == "passed" else "❌ 未通过"
    lines.append(f"# 评测报告 · {payload['run_id']}")
    lines.append("")
    lines.append(f"- 数据集：`{payload['dataset']}` v{payload['dataset_version']}（档位 `{payload['tier']}`）")
    lines.append(f"- 用例：{s['case_success']}/{s['case_total']} 成功执行，{s['case_failed']} 条异常")
    lines.append(
        f"- 判定：**{verdict}**"
        + (f"（{len(s['gate_failures'])} 条门禁未通过）" if s["gate_failures"] else "")
    )
    lines.append(
        f"- 配置：temperature={cfg['temperature']}，并发={cfg['concurrency']}，单轮超时={cfg['case_timeout_sec']}s"
    )
    lines.append(f"- 时间：{payload['started_at']} → {payload['finished_at']}")

    # 链路缺陷提示：子图输出混入正文（会让 format_clean 等指标失真）
    leak = (s["metrics"].get("subgraph_leak") or {})
    if leak.get("mean") is not None and leak["mean"] < 1:
        lines.append("")
        lines.append(
            "> ⚠️ **链路缺陷**：检测到子图（如目的地 Router 的 explore 子 Agent）的流式输出"
            "混入正文 —— 它也构成 `create_agent` 图、节点名同为 `model`，"
            "且绑定 runtime 调用时未传 config，因此 `checkpoint_ns` 带 `|` 的事件穿透了"
            " `chat.py` 的正文白名单（详见指标 `subgraph_leak`）。"
            "下面的 `format_clean` 失败由它引起，**不是模型输出格式问题**。"
        )
    lines.append("")

    # —— 指标总览 ——
    lines.append("## 指标总览")
    lines.append("")
    lines.append("| 指标 | 类型 | 均值 | 通过率 | 样本 |")
    lines.append("|---|---|---|---|---|")
    order = {"gate": 0, "core": 1, "info": 2}
    for key, m in sorted(s["metrics"].items(), key=lambda kv: (order.get(kv[1]["kind"], 9), kv[0])):
        lines.append(
            f"| {m['title']} `{key}` | {_KIND_LABEL.get(m['kind'], m['kind'])} "
            f"| {_fmt(m['mean'])} | {_fmt(m['pass_rate'])} | {m['samples']} |"
        )
    lines.append("")

    # —— 与基线对比 ——
    if payload.get("comparison"):
        lines.append(f"## 与基线对比（baseline: `{payload.get('baseline_run_id')}`）")
        lines.append("")
        lines.append("| 指标 | 基线 | 本次 | 变化 | 结论 |")
        lines.append("|---|---|---|---|---|")
        for c in payload["comparison"]:
            if c["kind"] == "info":
                continue
            arrow = "↓" if c["delta"] < 0 else ("↑" if c["delta"] > 0 else "=")
            flag = "⚠️ 变差" if c["worse"] else "正常"
            lines.append(
                f"| {c['title']} `{c['key']}` | {_fmt(c['base'])} | {_fmt(c['cur'])} "
                f"| {arrow} {abs(c['delta'])} | {flag} |"
            )
        lines.append("")

    # —— 用例明细 ——
    lines.append("## 用例明细")
    lines.append("")
    lines.append("| 用例 | 分类 | 状态 | 步骤路径 | 轮次 | 耗时 | 未通过指标 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in payload["cases"]:
        failed = [m for m in row["metrics"] if m.get("passed") is False]
        failed_txt = "、".join(f"{m['title']}" for m in failed) or "—"
        icon = _STATUS_ICON.get(row["status"], "❔")
        path = " → ".join(row.get("steps_path") or []) or "—"
        turns = len(row.get("messages") or [])
        latency = f"{row.get('latency_ms', 0) / 1000:.1f}s"
        lines.append(
            f"| `{row['case_id']}` {row['title']} | {row.get('category', '—')} | {icon} {row['status']} "
            f"| {path} | {turns} | {latency} | {failed_txt} |"
        )
    lines.append("")

    # —— 失败详情 ——
    bad_rows = [
        r for r in payload["cases"]
        if r["status"] != "success" or any(m.get("passed") is False for m in r["metrics"])
    ]
    if bad_rows:
        lines.append("## 失败详情")
        lines.append("")
        for row in bad_rows:
            lines.append(f"### `{row['case_id']}` {row['title']}")
            lines.append("")
            lines.append(f"- 分类：{row.get('category', '—')}　状态：{row['status']}"
                         + (f"（{row.get('error')}）" if row.get("error") else ""))
            if row.get("expected_path"):
                lines.append(f"- 期望步骤：{' → '.join(row['expected_path'])}")
            lines.append(f"- 实际步骤：{' → '.join(row.get('steps_path') or []) or '—'}"
                         f"　终态：`{row.get('final_step') or '—'}`")
            for m in row["metrics"]:
                if m.get("passed") is False:
                    lines.append(f"- ❌ {m['title']}：{m['detail']}")
            for msg in row.get("messages") or []:
                lines.append("")
                lines.append(f"  用户：{msg['user']}")
                preview = (msg["assistant"] or "").strip().replace("\n", " ")
                lines.append(f"  助手：{preview[:300]}{'…' if len(preview) > 300 else ''}")
                if msg.get("tools"):
                    lines.append(f"  （调用工具：{'、'.join(msg['tools'])}）")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"完整结果（含每轮 transcript）见 `scripts/eval_out/{payload['run_id']}.json`")
    return "\n".join(lines)


def write_markdown(payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = render_markdown(payload)
    path = OUT_DIR / f"{payload['run_id']}.md"
    path.write_text(text, encoding="utf-8")
    (OUT_DIR / "latest.md").write_text(text, encoding="utf-8")
    return path


def summarize_console(payload: dict) -> str:
    """控制台一行结论（跑完立刻能看）"""
    s = payload["summary"]
    icon = "✅" if s["verdict"] == "passed" else "❌"
    gate = f"，门禁未通过 {len(s['gate_failures'])} 条" if s["gate_failures"] else ""
    return (
        f"{icon} run={payload['run_id']} 用例 {s['case_success']}/{s['case_total']} 成功"
        f"{gate}　判定：{s['verdict']}"
    )
