"""
评测数据集加载与校验

数据集以 JSON 文件存放（scripts/eval_data/*.json），零第三方依赖
（刻意不用 YAML：pyproject 未声明 pyyaml，引入会逼着改依赖并重建镜像）。

一条 case 的字段约定见 eval_data/core_v1.json 顶部 _schema 说明。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

EVAL_ROOT = Path(__file__).resolve().parent.parent          # -> scripts/
DATA_DIR = EVAL_ROOT / "eval_data"

# 断言字段允许的键（未知键视为数据集写错，校验时报错）
KNOWN_EXPECTED_KEYS = {
    "steps_path",            # 期望经过的步骤序列（按状态转移工具调用推断）
    "final_step",            # 期望终态步骤（从图状态读取）
    "tools_expected",        # 期望【全部】被调用的工具名
    "tools_expected_any",    # 期望【至少一个】被调用的工具名
    "tools_forbidden",       # 明确不该被调用的工具名
    "requirement_fields",    # 期望被填写的 user_requirement 字段
    "must_contain_any",      # 正文应包含的关键词（任一命中即可）
    "must_not_contain",      # 禁用话术（出现即扣分）
    "long_output_allowed",   # 是否豁免「单轮正文 ≤8 行」约束
}

REQUIRED_KEYS = ("id", "title", "category", "turns")
VALID_TIERS = ("quick", "deep")


@dataclass
class Case:
    """一条评测用例"""
    id: str
    title: str
    category: str
    turns: list[str]
    expected: dict[str, Any] = field(default_factory=dict)
    tier: str = "quick"
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    external_dependent: bool = True
    note: str = ""

    def expectations(self, key: str, default=None):
        return self.expected.get(key, default)


@dataclass
class Dataset:
    name: str
    version: str
    description: str
    cases: list[Case]

    def select(self, tier: str = "all", case_ids: list[str] | None = None) -> list[Case]:
        """按档位 / 指定 id 过滤（只返回 enabled 的用例）"""
        picked = [c for c in self.cases if c.enabled]
        if case_ids:
            wanted = {c.strip() for c in case_ids if c.strip()}
            picked = [c for c in picked if c.id in wanted]
            # 保持数据集里的原始顺序
        elif tier and tier != "all":
            picked = [c for c in picked if c.tier == tier]
        return picked


def dataset_path(name: str) -> Path:
    return DATA_DIR / f"{name}.json"


def load_dataset(name: str = "core_v1") -> Dataset:
    """加载数据集（不做校验，先能读出来）"""
    path = dataset_path(name)
    if not path.exists():
        raise FileNotFoundError(f"数据集不存在: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [Case(**{k: v for k, v in item.items() if k in Case.__dataclass_fields__})
             for item in raw.get("cases", [])]

    return Dataset(
        name=raw.get("name", name),
        version=str(raw.get("version", "0")),
        description=raw.get("description", ""),
        cases=cases,
    )


def validate_dataset(ds: Dataset) -> list[str]:
    """返回问题列表（空列表 = 校验通过）"""
    problems: list[str] = []
    seen: set[str] = set()

    for c in ds.cases:
        prefix = f"[{c.id or '<无 id>'}]"
        if not c.id:
            problems.append(f"{prefix} 缺少 id")
            continue
        if c.id in seen:
            problems.append(f"{prefix} id 重复")
        seen.add(c.id)

        for key in REQUIRED_KEYS:
            if not getattr(c, key, None):
                problems.append(f"{prefix} 缺少必填字段 {key}")
        if c.tier not in VALID_TIERS:
            problems.append(f"{prefix} tier 非法: {c.tier}（应为 {'/'.join(VALID_TIERS)}）")
        if not isinstance(c.turns, list) or not c.turns:
            problems.append(f"{prefix} turns 必须是非空字符串数组")

        unknown = set(c.expected) - KNOWN_EXPECTED_KEYS
        if unknown:
            problems.append(f"{prefix} expected 含未知键: {sorted(unknown)}")
        if not c.expected:
            problems.append(f"{prefix} expected 为空 —— 无法判定，用例不合格")

        # 步骤名合法性（防止拼错导致指标永远算不对）
        from scripts.eval_lib.metrics import VALID_STEPS
        for step in list(c.expected.get("steps_path") or []):
            if step not in VALID_STEPS:
                problems.append(f"{prefix} steps_path 含未知步骤: {step}")
        final_step = c.expected.get("final_step")
        if final_step and final_step not in VALID_STEPS:
            problems.append(f"{prefix} final_step 未知步骤: {final_step}")

    if not ds.cases:
        problems.append("数据集为空")
    return problems


def iter_cases(ds: Dataset) -> Iterator[Case]:
    return iter(ds.cases)
