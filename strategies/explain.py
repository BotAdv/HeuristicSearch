# -*- coding: utf-8 -*-
"""策略决策解释：把「这一步为什么这么选」变成结构化、可校验的数据。

设计原则
--------
1. **只读**：解释只在 ``Strategy.explain = True`` 时收集，且**不得**调用任何随机数、
   不得修改信念/计数。否则"解释"本身就会改变策略行为，分析结论自相矛盾。
   实现上靠"在既有计算路径上顺手记录"而不是"重算一遍"来保证（重算会多消耗 RNG）。
2. **共享**：任何持有 ``PositionBelief`` 的策略都能拿到通用的三档理由
   （已锁定 / 探测 / 候选内准则），策略只需补上"准则特有的数字"。
3. **可展示**：输出结构固定，前端与复盘分析器都用同一份 schema：

.. code-block:: python

    {
      "round": 3,               # 本轮是第几轮
      "strategy": "two_phase",
      "strategyName": "两阶段：先定支持集再定顺序",
      "phase": "A" | "B" | None, # 阶段性策略才会给出
      "support": {"inList": [...], "inCount": 9, "outCount": 31, "unknownCount": 5},
      "candidates": [            # 与位置一一对应
        {"position": 1, "size": 2, "chosen": [2, 7], "reason": "……",
         "topCandidates": [[1,10],[4,10]], "inCandidateSet": False},
        ...
      ],
      **extra                    # 各策略自报的额外统计（batch / budget / 准则分数…）
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from core.game import CANDIDATES, SEQ_LEN, Combo
from strategies.belief import IN, OUT, UNKNOWN


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def combo_label(combo: Combo) -> str:
    """统一的中文可读写法：``(3, 6)``。"""
    return f"({combo[0]}, {combo[1]})"


def combo_list(combos: Sequence[Combo], limit: int = 6) -> str:
    items = [combo_label(c) for c in list(combos)[:limit]]
    if len(combos) > limit:
        items.append(f"…（共 {len(combos)} 个）")
    return "、".join(items)


def membership_note(belief, combo: Combo) -> Optional[str]:
    """组合当前的支持集归属：给出「探测 / 已有定论」的定性说明。"""
    status = getattr(belief, "status", {})
    state = status.get(combo, UNKNOWN)
    if state == UNKNOWN:
        return "尚未判定它是否属于支持集（用它探测可拿到一次判定）"
    if state == IN:
        return "已确认它属于支持集（此刻用它是在定位，而不是探测）"
    if state == OUT:
        return "已判定它不属于支持集（仅在关闭 global_prune 时它还留在候选集合里）"
    return None


# --------------------------------------------------------------------------
# 快照
# --------------------------------------------------------------------------
def support_summary(belief) -> Dict[str, Any]:
    """支持集三分类统计（与前端"支持集已确认 x/10"一致）。"""
    status = getattr(belief, "status", {})
    known = list(getattr(belief, "domain", CANDIDATES))
    in_list = [c for c in known if status.get(c) == IN]
    out_list = [c for c in known if status.get(c) == OUT]
    unknown = [c for c in known if status.get(c) not in (IN, OUT)]
    return {
        "inList": in_list,
        "inCount": len(in_list),
        "outCount": len(out_list),
        "unknownCount": len(unknown),
        "outList": out_list,
        "unknownList": unknown,
    }


def candidates_snapshot(belief, seq_len: int = SEQ_LEN, top: int = 6) -> List[Dict[str, Any]]:
    """逐位置候选集快照（决策时刻的信念）。"""
    out: List[Dict[str, Any]] = []
    for i in range(seq_len):
        ci = sorted(belief.sets[i]) if belief is not None else []
        out.append({"position": i + 1, "size": len(ci), "topCandidates": ci[:top]})
    return out


def build_decision(
    strategy,
    guess: Sequence[Combo],
    round_index: int,
    reasons: Sequence[Optional[str]],
    extra: Dict[str, Any],
    phase: Optional[str] = None,
) -> Dict[str, Any]:
    """组装 ``last_decision``。``guess`` 与 ``reasons`` 都按位置对齐。"""
    belief = getattr(strategy, "belief", None)
    support = support_summary(belief) if belief is not None else {}
    support.pop("outList", None)
    support.pop("unknownList", None)
    tracker = getattr(strategy, "support", None)
    if support and tracker is not None and getattr(tracker, "count", None):
        support["counts"] = {str(c): tracker.count.get(c) for c in support.get("inList", [])}
    cells: List[Dict[str, Any]] = []
    for i in range(SEQ_LEN):
        combo = guess[i] if i < len(guess) else None
        ci = sorted(belief.sets[i]) if belief is not None else []
        cells.append(
            {
                "position": i + 1,
                "size": len(ci),
                "chosen": list(combo) if combo is not None else None,
                "reason": (reasons[i] if i < len(reasons) and reasons[i] else None),
                "topCandidates": [list(c) for c in ci[:6]],
                "inCandidateSet": bool(combo is not None and combo in belief.sets[i]) if belief is not None else None,
            }
        )
    decision: Dict[str, Any] = {
        "round": round_index,
        "strategy": getattr(strategy, "key", None),
        "strategyName": getattr(strategy, "name", None),
        "phase": phase,
        "support": support,
        "candidates": cells,
    }
    decision.update(extra or {})
    return decision


# --------------------------------------------------------------------------
# 通用理由
# --------------------------------------------------------------------------
def locked_reason(belief, i: int, combo: Combo) -> str:
    return f"已锁定：本位置候选集只剩 {combo_label(combo)} 这一个，直接沿用"


def criterion_reason(belief, i: int, combo: Combo, criterion: str, detail: str = "") -> str:
    """候选集内按照某准则取值的理由。"""
    ci = belief.sets[i]
    head = f"{criterion}：本位置候选 {len(ci)} 个"
    if combo not in ci:
        head = f"探测：{combo_label(combo)} 不在本位置候选集（{len(ci)} 个）内，本轮用它去换一次支持集判定"
    tail = f"，选 {combo_label(combo)}"
    note = membership_note(belief, combo)
    parts = [head + tail + (f"（{detail}）" if detail else "")]
    if note:
        parts.append(note)
    return "；".join(parts)


def generic_reason(belief, i: int, combo: Combo, criterion: str) -> str:
    """没有专属解释的策略用它兜底：锁定 / 探测 / 候选内取值三档。"""
    if belief is None:
        return f"{criterion}：本轮填 {combo_label(combo)}"
    ci = belief.sets[i]
    if len(ci) == 1 and combo in ci:
        return locked_reason(belief, i, combo)
    if combo in ci:
        return criterion_reason(belief, i, combo, criterion)
    return criterion_reason(belief, i, combo, criterion)


def fill_missing_reasons(strategy, guess: Sequence[Combo], reasons: List[Optional[str]], criterion: str) -> None:
    """把策略没写理由的位置补上通用理由（保证 10 个位置都有可展示的原因）。"""
    belief = getattr(strategy, "belief", None)
    for i in range(len(reasons)):
        if reasons[i] or i >= len(guess):
            continue
        reasons[i] = generic_reason(belief, i, guess[i], criterion)
