"""
评测指标（规则型 / 确定性）

设计原则
--------
- 全部指标都是「可复现的规则计算」，不调用任何模型 —— 零成本、可进 CI、结果稳定。
- 每个指标带 kind：
    gate  门禁：不过 → 整次 run 判定失败
    core  核心：看数值与通过率，用于回归对比
    info  观察项：只展示，不参与判定
- 期望字段缺省时该指标返回 skip，不计入通过率（避免"没写期望却算满分/零分"）。

主观质量（是否编造、是否像真人）不在本模块职责内，交给人工抽查，
本模块只做「能写死规则的部分」，这也是精简闭环的取舍。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# 与 app/core/state.py 的 PlanningStep 保持同步（优先直接读取，读不到时用兜底常量）
try:  # pragma: no cover - 取决于运行环境
    from typing import get_args

    from app.core.state import PlanningStep

    VALID_STEPS: tuple[str, ...] = tuple(get_args(PlanningStep))
except Exception:  # pragma: no cover
    VALID_STEPS = (
        "requirement_collection",
        "destination_recommendation",
        "transport_planning",
        "accommodation_planning",
        "food_planning",
        "itinerary_generation",
        "budget_summarization",
        "report_generation",
    )

# 状态转移工具 → 它把流程推进到的目标步骤（用于从工具调用推断步骤路径）
STEP_TRANSITIONS = {
    "record_requirement_tool": "destination_recommendation",
    "select_destination_tool": "transport_planning",
    "select_transport_tool": "accommodation_planning",
    "select_accommodation_tool": "food_planning",
    "select_food_tool": "itinerary_generation",
    "generate_itinerary_tool": "budget_summarization",
    "summarize_budget_tool": "order_generation",
    "generate_order_tool": "order_generation",
    "go_back_to_requirement": "requirement_collection",
    "go_back_to_destination": "destination_recommendation",
    "go_back_to_transport": "transport_planning",
    "go_back_to_accommodation": "accommodation_planning",
    "go_back_to_food": "food_planning",
    "go_back_to_itinerary": "itinerary_generation",
    "go_back_to_budget": "budget_summarization",
}

# 正文里不允许出现的 Markdown 标记（前端不渲染 Markdown，用户会看到裸符号）
_MD_MARKS = ("**", "##", "`", "~~")
# 行首单个 * 当列表符（GLOBAL_OUTPUT_RULES 明确禁止；"- " 与 "1)" 是允许的）
_LINE_STAR = re.compile(r"(?m)^[ \t]*\*[ \t]+")
# 「小标题 / 标签」独占一行（如【3 天西安行程框架】）按 GLOBAL_OUTPUT_RULES 不计入行数
_TITLE_ONLY_LINE = re.compile(r"^【[^】]{0,40}】[:：]?$")

MAX_LINES = 8  # GLOBAL_OUTPUT_RULES 的单轮正文硬约束


@dataclass
class Metric:
    key: str
    title: str
    kind: str                      # gate | core | info
    value: Optional[float]         # 0~1 或原始数值；None = 本用例未定义该期望（skip）
    passed: Optional[bool] = None  # None = 不判定
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- 基础工具

def lcs_len(a: list[str], b: list[str]) -> int:
    """最长公共子序列长度（用于步骤路径的相似度）"""
    if not a or not b:
        return 0
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if x == y else max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1]


def _all_answers(run: dict) -> list[str]:
    return [t.get("assistant") or "" for t in run.get("turns") or []]


def _flat_tools(run: dict) -> list[str]:
    names: list[str] = []
    for t in run.get("turns") or []:
        names.extend(t.get("tools") or [])
    return names


def _flat_tool_errors(run: dict) -> list[str]:
    names: list[str] = []
    for t in run.get("turns") or []:
        names.extend(t.get("tool_errors") or [])
    return names


# ---------------------------------------------------------------- 各指标计算

def _m_answer_non_empty(run: dict) -> Metric:
    answers = _all_answers(run)
    if not answers:
        return Metric("answer_non_empty", "有正文回复", "gate", 0.0, False, "本轮没有任何助手回复")
    ok = [bool(a.strip()) for a in answers]
    ratio = sum(ok) / len(ok)
    bad = [i + 1 for i, v in enumerate(ok) if not v]
    return Metric(
        "answer_non_empty", "有正文回复", "gate", round(ratio, 3), ratio == 1.0,
        "全部轮次都有正文" if not bad else f"第 {bad} 轮正文为空",
    )


def _m_format_clean(case, run: dict) -> Metric:
    allowed = bool(case.expectations("long_output_allowed", False))
    problems: list[str] = []
    for idx, text in enumerate(_all_answers(run), 1):
        hits = [m for m in _MD_MARKS if m in text]
        if hits:
            problems.append(f"第{idx}轮含 Markdown 标记 {hits}")
        if _LINE_STAR.search(text):
            problems.append(f"第{idx}轮用 * 当列表符")
        if not allowed:
            # 行数统计遵循 prompt 原文口径：「小标题/标签不计」
            lines = [
                ln for ln in text.splitlines()
                if ln.strip() and not _TITLE_ONLY_LINE.match(ln.strip())
            ]
            if len(lines) > MAX_LINES:
                problems.append(f"第{idx}轮正文 {len(lines)} 行（>{MAX_LINES}）")
    return Metric(
        "format_clean", "输出格式合规", "gate",
        0.0 if problems else 1.0, not problems,
        "；".join(problems) if problems else "无 Markdown 标记、行数合规",
    )


def _m_forbidden_phrase(case, run: dict) -> Metric:
    banned = case.expectations("must_not_contain") or []
    if not banned:
        return Metric("forbidden_phrase_clean", "无禁用话术", "gate", None, None, "用例未定义")
    joined = "\n".join(_all_answers(run))
    hit = [b for b in banned if b and b in joined]
    return Metric(
        "forbidden_phrase_clean", "无禁用话术", "gate",
        0.0 if hit else 1.0, not hit,
        f"命中禁用话术 {hit}" if hit else "未出现禁用话术",
    )


def _m_must_contain(case, run: dict) -> Metric:
    wanted = case.expectations("must_contain_any") or []
    if not wanted:
        return Metric("must_contain_hit", "关键内容覆盖", "core", None, None, "用例未定义")
    joined = "\n".join(_all_answers(run))
    hit = [w for w in wanted if w and w in joined]
    return Metric(
        "must_contain_hit", "关键内容覆盖", "core",
        1.0 if hit else 0.0, bool(hit),
        f"命中 {hit}" if hit else f"未命中任一关键词 {wanted}",
    )


def _m_step_path(case, run: dict) -> Metric:
    expected = case.expectations("steps_path") or []
    if not expected:
        return Metric("step_path_score", "步骤路径符合预期", "core", None, None, "用例未定义")
    actual = run.get("steps_path") or []
    score = lcs_len(actual, expected) / len(expected)
    return Metric(
        "step_path_score", "步骤路径符合预期", "core",
        round(score, 3), score >= 0.8,
        f"实际 {actual} vs 期望 {expected}",
        extra={"actual_path": actual, "expected_path": expected},
    )


def _m_final_step(case, run: dict) -> Metric:
    expected = case.expectations("final_step")
    if not expected:
        return Metric("final_step_match", "终态步骤正确", "core", None, None, "用例未定义")
    actual = run.get("final_step")
    return Metric(
        "final_step_match", "终态步骤正确", "core",
        1.0 if actual == expected else 0.0, actual == expected,
        f"实际 {actual} vs 期望 {expected}",
    )


def _m_tools_recall(case, run: dict) -> Metric:
    expected = case.expectations("tools_expected") or []
    if not expected:
        return Metric("tools_recall", "期望工具全部被调用", "core", None, None, "用例未定义")
    actual = set(_flat_tools(run))
    hit = [t for t in expected if t in actual]
    score = len(hit) / len(expected)
    missing = [t for t in expected if t not in actual]
    return Metric(
        "tools_recall", "期望工具全部被调用", "core",
        round(score, 3), score == 1.0,
        f"已调 {hit}" + (f"，缺 {missing}" if missing else ""),
    )


def _m_tools_any(case, run: dict) -> Metric:
    wanted = case.expectations("tools_expected_any") or []
    if not wanted:
        return Metric("tools_any_hit", "期望工具至少调用一个", "core", None, None, "用例未定义")
    actual = set(_flat_tools(run))
    hit = [t for t in wanted if t in actual]
    return Metric(
        "tools_any_hit", "期望工具至少调用一个", "core",
        1.0 if hit else 0.0, bool(hit),
        f"命中 {hit}" if hit else f"未调用 {wanted}（实际调用 {sorted(actual)}）",
    )


def _m_tools_forbidden(case, run: dict) -> Metric:
    banned = case.expectations("tools_forbidden") or []
    if not banned:
        return Metric("tools_forbidden_clean", "未调用禁用的工具", "gate", None, None, "用例未定义")
    actual = set(_flat_tools(run))
    violated = [t for t in banned if t in actual]
    return Metric(
        "tools_forbidden_clean", "未调用禁用的工具", "gate",
        0.0 if violated else 1.0, not violated,
        f"违规调用了 {violated}" if violated else "未越权调用工具",
    )


def _m_tool_error_rate(run: dict) -> Metric:
    total = len(_flat_tools(run))
    errors = len(_flat_tool_errors(run))
    if total == 0:
        return Metric("tool_error_rate", "工具调用失败率", "info", 0.0, None, "本次未调用工具")
    rate = errors / total
    return Metric(
        "tool_error_rate", "工具调用失败率", "info", round(rate, 3), rate <= 0.2,
        f"{errors}/{total} 次调用报错" + (f"：{_flat_tool_errors(run)}" if errors else ""),
    )


def _m_requirement_fill(case, run: dict) -> Metric:
    fields = case.expectations("requirement_fields") or []
    if not fields:
        return Metric("requirement_fill_rate", "需求字段填写率", "core", None, None, "用例未定义")
    req = (run.get("state") or {}).get("user_requirement") or {}
    if not isinstance(req, dict):
        req = {}
    filled = [f for f in fields if req.get(f) not in (None, "", [], {})]
    score = len(filled) / len(fields)
    missing = [f for f in fields if f not in filled]
    return Metric(
        "requirement_fill_rate", "需求字段填写率", "core",
        round(score, 3), score >= 0.8,
        f"已填 {filled}" + (f"，缺 {missing}" if missing else ""),
    )


def _m_subgraph_leak(run: dict) -> Metric:
    """
    正文来源纯净度：是否混入了子图（如目的地 Router 的 explore 子 Agent）的输出。

    为什么单列一个指标：子 Agent 也用 create_agent 构成，节点名同为 "model"，
    它在绑定 runtime 调用（不传 config）时 checkpoint_ns 会变成
    `tools:xxx|explore:xxx|model:xxx`；若正文判据只看 langgraph_node，这些内部
    文本会被当成给用户的正文（还会被存库）。本指标把这类事件单独统计出来，
    让失败归因一眼可见 —— 是链路缺陷，不是模型输出问题。
    """
    turns = run.get("turns") or []
    total = sum(int(t.get("subgraph_events") or 0) for t in turns)
    if total == 0:
        return Metric("subgraph_leak", "正文未混入子图输出", "core", 1.0, True, "正文来源均为主图 model 节点")
    samples = [t.get("subgraph_sample") for t in turns if t.get("subgraph_events")]
    return Metric(
        "subgraph_leak", "正文未混入子图输出", "core", 0.0, False,
        f"检测到 {total} 条来自子图的流式片段混入正文（样本 {samples[0]!r}…）",
    )


def _m_info_run(run: dict) -> list[Metric]:
    turns = len(run.get("turns") or [])
    lat = run.get("latency_ms") or 0
    pt = run.get("prompt_tokens") or 0
    ct = run.get("completion_tokens") or 0
    return [
        Metric("turns_used", "实际轮次", "info", float(turns), None, f"{turns} 轮"),
        Metric("latency_ms", "端到端耗时", "info", float(lat), None, f"{round(lat / 1000, 1)} s"),
        Metric("total_tokens", "token 消耗", "info", float(pt + ct), None, f"入 {pt} / 出 {ct}"),
    ]


def compute_metrics(case, run: dict) -> list[Metric]:
    """计算一条用例的全部指标"""
    metrics = [
        _m_answer_non_empty(run),
        _m_format_clean(case, run),
        _m_forbidden_phrase(case, run),
        _m_must_contain(case, run),
        _m_step_path(case, run),
        _m_final_step(case, run),
        _m_tools_recall(case, run),
        _m_tools_any(case, run),
        _m_tools_forbidden(case, run),
        _m_tool_error_rate(run),
        _m_requirement_fill(case, run),
        _m_subgraph_leak(run),
    ]
    metrics.extend(_m_info_run(run))
    return metrics


# ---------------------------------------------------------------- 聚合

GATE_KEYS = ("answer_non_empty", "format_clean", "forbidden_phrase_clean", "tools_forbidden_clean")


def aggregate(rows: list[dict]) -> dict:
    """
    rows: [{"case_id","title","status","metrics":[{key,kind,value,passed,detail}...]}...]
    返回 run 级汇总：每个指标的均值 / 通过率 + 门禁判定。
    """
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        for m in row["metrics"]:
            by_key.setdefault(m["key"], []).append(m)

    metrics_summary: dict[str, dict] = {}
    for key, items in by_key.items():
        scored = [m for m in items if m.get("value") is not None]
        judged = [m for m in items if m.get("passed") is not None]
        metrics_summary[key] = {
            "title": items[0].get("title", key),
            "kind": items[0].get("kind", "info"),
            "samples": len(scored),
            "mean": round(sum(m["value"] for m in scored) / len(scored), 3) if scored else None,
            "pass_rate": round(sum(1 for m in judged if m["passed"]) / len(judged), 3) if judged else None,
        }

    # 门禁：任一 gate 指标出现「未通过」即判定失败
    gate_failures: list[str] = []
    for key in GATE_KEYS:
        for row in rows:
            for m in row["metrics"]:
                if m["key"] == key and m.get("passed") is False:
                    gate_failures.append(f"{row['case_id']} · {m['title']}：{m['detail']}")

    errored = [r for r in rows if r["status"] != "success"]
    verdict = "passed"
    if gate_failures or errored:
        verdict = "failed"

    return {
        "case_total": len(rows),
        "case_success": len(rows) - len(errored),
        "case_failed": len(errored),
        "metrics": metrics_summary,
        "gate_failures": gate_failures,
        "case_errors": [{"case_id": r["case_id"], "status": r["status"], "error": r.get("error")} for r in errored],
        "verdict": verdict,
    }


def compare_runs(current: dict, baseline: dict) -> list[dict]:
    """
    与基线 run 对比：返回 [{key, title, base, cur, delta, worse}]。
    worse=True 表示该指标相对基线下降（用于"改完有没有变差"的判断）。
    """
    out: list[dict] = []
    for key, cur in (current.get("metrics") or {}).items():
        base = (baseline.get("metrics") or {}).get(key)
        if not base or base.get("mean") is None or cur.get("mean") is None:
            continue
        delta = round(cur["mean"] - base["mean"], 3)
        out.append({
            "key": key,
            "title": cur.get("title", key),
            "kind": cur.get("kind", "info"),
            "base": base["mean"],
            "cur": cur["mean"],
            "delta": delta,
            "worse": delta < 0,
        })
    out.sort(key=lambda x: x["delta"])
    return out
