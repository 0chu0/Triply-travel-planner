"""
评测 CLI —— 本地跑一遍 Golden Case，回答「这次改动有没有把别的地方改坏」

用法示例
--------
    # 列出数据集里的用例（不执行）
    python scripts/run_eval.py --list

    # 只校验数据集格式（1 秒内完成，改完数据集先跑这个）
    python scripts/run_eval.py --validate

    # 跑 quick 档（8 条，不需要深推进，约几分钟）
    python scripts/run_eval.py

    # 跑全部 12 条（含 deep：高铁边界/回退/行程/预算）
    python scripts/run_eval.py --tier all

    # 只跑指定用例
    python scripts/run_eval.py --cases req_full_info_advances,format_no_markdown

    # 与上一次 run 对比（判断是否变差）
    python scripts/run_eval.py --tier all --baseline latest

设计约定
--------
- 复用生产判据（`app.api.v1.chat.is_answer_stream_event`），不改动 app/ 下任何生产代码；
- 默认 temperature=0（可复现）；`--temperature prod` 表示与生产逐字一致（0.7）；
- 结果落文件：scripts/eval_out/{run_id}.json 与 .md，不写业务库表；
- 会话数据写进 checkpoints 表（thread_id 前缀 eval-），可用 --cleanup 清理。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ⚠️ 必须在 import app.mcp_core.client 之前执行：
# MCPClientManager 在【模块导入时】就把 os.environ 快照成子进程环境变量，
# 而 SERVER_CONFIGS 里两个自建 stdio 服务的 command 是裸 "python"。
# 本地跑评测时若不把当前虚拟环境的 bin 目录加到 PATH 前面，子进程会用到系统
# python（没有装 dotenv 等依赖）→ weather / search 服务加载失败、工具为空，
# 评测结果与线上不一致。这里只影响评测进程，不动生产代码。
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from scripts.eval_lib import collector, report                      # noqa: E402
from scripts.eval_lib.dataset import load_dataset, validate_dataset  # noqa: E402
from scripts.eval_lib.metrics import aggregate, compare_runs, compute_metrics  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_eval",
        description="Triply 评测 CLI（Golden Case + 规则指标 + Markdown 报告）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dataset", default="core_v1", help="数据集名（对应 scripts/eval_data/{name}.json）")
    p.add_argument("--tier", default="quick", choices=["quick", "deep", "all"], help="档位（默认 quick）")
    p.add_argument("--cases", default="", help="只跑指定用例 id，逗号分隔")
    p.add_argument("--concurrency", type=int, default=2, help="并发用例数（默认 2，别打满小机器）")
    p.add_argument("--timeout", type=int, default=300, help="单轮超时秒数（默认 300）")
    p.add_argument("--max-turns", type=int, default=0, help="覆盖每条用例的最大轮次（0=用例自身设定）")
    p.add_argument(
        "--temperature", default="0",
        help="评测温度：数字（默认 0，可复现）或 prod（不覆盖，与生产一致 0.7）",
    )
    p.add_argument("--baseline", default="none", help="对比基线：none | latest | <run_id>")
    p.add_argument("--cleanup", action="store_true", help="跑完清理本次 checkpoints 与评测用户记忆")
    p.add_argument("--list", action="store_true", help="只列出用例，不执行")
    p.add_argument("--validate", action="store_true", help="只校验数据集，不执行")
    return p.parse_args(argv)


def print_cases(cases) -> None:
    print(f"\n共 {len(cases)} 条用例：")
    print(f"{'id':<34}{'档位':<8}{'分类':<24}标题")
    print("-" * 110)
    for c in cases:
        print(f"{c.id:<34}{c.tier:<8}{c.category:<24}{c.title}")
    print()


async def main_async(args: argparse.Namespace) -> int:
    ds = load_dataset(args.dataset)

    problems = validate_dataset(ds)
    if problems:
        print("❌ 数据集校验未通过：")
        for p in problems:
            print("   -", p)
        return 2

    if args.validate:
        print(f"✅ 数据集 {ds.name} v{ds.version} 校验通过（{len(ds.cases)} 条用例）")
        return 0

    cases = ds.select(
        tier=args.tier,
        case_ids=[c for c in args.cases.split(",") if c.strip()] or None,
    )
    if args.list:
        print_cases(cases)
        return 0
    if not cases:
        print("❌ 没有匹配的用例（检查 --tier / --cases）")
        return 2

    # —— 温度覆盖（仅评测进程内生效） ——
    temperature = None if str(args.temperature).lower() == "prod" else float(args.temperature)
    max_turns = args.max_turns or None
    run_id = collector.new_run_id()
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 78)
    print(f"评测开始  run={run_id}")
    print(f"数据集：{ds.name} v{ds.version}　档位：{args.tier}　用例：{len(cases)} 条")
    print(f"参数：temperature={args.temperature}　并发={args.concurrency}　单轮超时={args.timeout}s")
    print("=" * 78)

    await collector.prepare_environment()
    collector.install_temperature_override(temperature)

    try:
        case_runs = await collector.run_cases(
            cases, run_id,
            concurrency=max(1, args.concurrency),
            timeout_sec=args.timeout,
            max_turns=max_turns,
        )
    finally:
        await collector.shutdown_environment()
        try:
            from app.core.tracing import flush_langfuse

            flush_langfuse()
        except Exception:
            pass

    # —— 打分 ——
    case_by_id = {c.id: c for c in cases}
    rows: list[dict] = []
    for run in case_runs:
        case = case_by_id[run.case_id]
        metrics = compute_metrics(case, run.as_dict())
        rows.append({
            "case_id": run.case_id,
            "title": run.title,
            "category": case.category,
            "tier": case.tier,
            "status": run.status,
            "error": run.error,
            "steps_path": run.steps_path,
            "final_step": run.final_step,
            "expected_path": case.expectations("steps_path") or [],
            "latency_ms": run.latency_ms,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "thread_id": run.thread_id,
            "metrics": [asdict(m) for m in metrics],
            "messages": [
                {
                    "user": t["user"],
                    "assistant": t["assistant"],
                    "tools": t["tools"],
                    "tool_errors": t["tool_errors"],
                }
                for t in run.turns
            ],
        })

    summary = aggregate(rows)

    # —— 与基线对比 ——
    comparison: list[dict] = []
    baseline_run_id: str | None = None
    if args.baseline and args.baseline != "none":
        try:
            base = (
                report.load_latest() if args.baseline == "latest"
                else report.load_run(args.baseline)
            )
            if base:
                baseline_run_id = base.get("run_id")
                comparison = compare_runs(summary, base.get("summary") or {})
        except FileNotFoundError as exc:
            print(f"⚠️ 找不到基线，跳过对比：{exc}")

    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = report.build_payload(
        run_id=run_id,
        dataset=ds.name,
        dataset_version=ds.version,
        tier=args.tier,
        temperature=args.temperature,
        concurrency=max(1, args.concurrency),
        timeout_sec=args.timeout,
        started_at=started_at,
        finished_at=finished_at,
        rows=rows,
        summary=summary,
        comparison=comparison,
        baseline_run_id=baseline_run_id,
    )

    json_path = report.write_json(payload)
    md_path = report.write_markdown(payload)

    print()
    print("=" * 78)
    print(report.summarize_console(payload))
    if summary["gate_failures"]:
        print("\n门禁未通过：")
        for item in summary["gate_failures"]:
            print("  -", item)
    if summary["case_errors"]:
        print("\n执行异常：")
        for item in summary["case_errors"]:
            print(f"  - {item['case_id']} ({item['status']}): {item['error']}")
    if comparison:
        worse = [c for c in comparison if c["worse"] and c["kind"] != "info"]
        print(f"\n与基线 {baseline_run_id} 对比："
              + ("指标下降 " + "、".join(f"{c['key']} {c['delta']}" for c in worse) if worse else "无指标下降"))
    print(f"\n报告：{md_path}")
    print(f"数据：{json_path}")
    print("=" * 78)

    # —— 收尾清理 ——
    if args.cleanup:
        n = collector.cleanup_checkpoints(run_id)
        print(f"\n🧹 已清理 {n} 条本 run 的 checkpoints 记录")
        await collector.cleanup_eval_memory()
    else:
        print(f"\nℹ️ 本次会话保留在 checkpoints（thread_id 前缀 eval-{run_id}-）；"
              f"如要清理：python scripts/run_eval.py --cleanup（需重跑）或手动按前缀删除")

    return 0 if summary["verdict"] == "passed" else 1


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
