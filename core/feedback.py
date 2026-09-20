# -*- coding: utf-8 -*-
"""反馈解析与一致性校验（「逐步猜测」模式专用）。

在逐步猜测模式里，**秘密序列是未知的**：玩家看着自己的真实秘密（或别人的秘密），
手工把每一轮的逐位反馈录进来。服务端无法用 :func:`core.game.evaluate` 校验，
但仍可以检查一批**与秘密无关的必然规律**，用来挡住绝大多数手滑录错：

1. **同轮重复组合的自洽性**：若某个组合在猜测里出现了两次以上，
   那么这些位置要么全是 `M`（该组合属于支持集但在别处），
   要么恰好一个是 `C` 且其余都是 `M`，要么全是 `P`/`W`（该组合不属于支持集）。
   绝不可能出现“两个 C”或“一个 C 加一个 P”。
2. **支持集归属的跨轮一致性**：某个组合只要在某轮出现过 `C` 或 `M`，
   它就属于支持集；此后任何一轮再猜它，就**不可能**出现 `P` 或 `W`。反之亦然。
3. **已定位组合的唯一性**：出现过的 `C` 把“组合 → 位置”钉死
   （位置 p 上曾出现 `C` 表示 ``x_p`` 就是它）；此后
   ①同一个组合不能再出现在别的位置的 `C` 上；
   ②位置 p 再出现 `C` 时必须是同一个组合。
4. **终局合法性**：一旦玩家把整轮都标成 `C`，该猜测就必须是一个合法秘密
   （在互异规则下即“10 个组合互不相同”）。

这些检查都是规则的必然推论，因此命中时几乎可以肯定是录入错误，
但实现上只把它作为**警告**返回，由调用方决定是拒绝还是接受。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.game import (
    CORRECT,
    FEEDBACK_KINDS,
    MISPLACED,
    PARTIAL,
    SECRET_DISTINCT,
    SEQ_LEN,
    WRONG,
    Combo,
    is_distinct_sequence,
)

#: 字母 / 别名 → 规范反馈名
_LETTER = {"C": CORRECT, "M": MISPLACED, "P": PARTIAL, "W": WRONG}

_ALIASES: Dict[str, str] = {
    "C": CORRECT,
    "CORRECT": CORRECT,
    "M": MISPLACED,
    "MISPLACED": MISPLACED,
    "P": PARTIAL,
    "PARTIAL": PARTIAL,
    "W": WRONG,
    "WRONG": WRONG,
}


def _canonical(token: str) -> str:
    key = token.strip().upper()
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(
        f"无法识别的反馈取值 {token!r}；应为 C/M/P/W 或 CORRECT/MISPLACED/PARTIAL/WRONG"
    )


def parse_feedback(value, expected_len: int = SEQ_LEN) -> List[str]:
    """把玩家输入的反馈解析成规范列表。

    支持三种写法：

    * 字母串：``"CMPWCMPWCM"``（可含空格或逗号分隔）；
    * 列表/元组：``["CORRECT", "MISPLACED", ...]`` 或 ``["C", "M", ...]``；
    * 下标映射：``{0: "C", 1: "M", ...}``。
    """
    if value is None:
        raise ValueError("缺少反馈内容")
    if isinstance(value, str):
        text = value.replace(",", " ").replace("|", " ").strip()
        if not text:
            raise ValueError("反馈内容为空")
        parts = text.split()
        if len(parts) == 1 and len(parts[0]) == expected_len:
            parts = list(parts[0])
        items = parts
    elif isinstance(value, dict):
        items = []
        for i in range(expected_len):
            if i not in value and str(i) not in value:
                raise ValueError(f"反馈缺少第 {i + 1} 位的取值")
            items.append(value.get(i, value.get(str(i))))
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f"无法解析的反馈类型：{type(value).__name__}")

    if len(items) != expected_len:
        raise ValueError(f"反馈长度必须为 {expected_len}，实际为 {len(items)}")
    return [_canonical(str(x)) for x in items]


def feedback_to_letters(feedback: Sequence[str]) -> str:
    return "".join({v: k for k, v in _LETTER.items()}[f] for f in feedback)


class FeedbackChecker:
    """跨轮校验玩家录入的反馈是否自相矛盾。

    典型用法::

        checker = FeedbackChecker()
        for guess, feedback in history:      # 回放历史，或 incremental observe
            checker.observe(guess, feedback)
        warnings = checker.check(new_guess, new_feedback)
    """

    def __init__(self, seq_len: int = SEQ_LEN, unique_values: Optional[bool] = None) -> None:
        self.seq_len = seq_len
        self.unique_values = SECRET_DISTINCT if unique_values is None else bool(unique_values)
        #: 已被证实属于支持集的组合
        self.known_in: set = set()
        #: 已被证实不属于支持集的组合
        self.known_out: set = set()
        #: 由 C 钉死的 “位置 -> 组合”
        self.placed: Dict[int, Combo] = {}
        #: 由 C 钉死的 “组合 -> 位置”
        self.combo_place: Dict[Combo, int] = {}

    # ------------------------------------------------------------------ 回放
    def observe(self, guess: Sequence[Combo], feedback: Sequence[str]) -> None:
        for i, (g, f) in enumerate(zip(guess, feedback)):
            if f == CORRECT:
                self.known_in.add(g)
                self.placed[i] = g
                self.combo_place.setdefault(g, i)
            elif f == MISPLACED:
                self.known_in.add(g)
            elif f in (PARTIAL, WRONG):
                self.known_out.add(g)

    @classmethod
    def from_history(cls, history, seq_len: int = SEQ_LEN, unique_values: Optional[bool] = None):
        checker = cls(seq_len=seq_len, unique_values=unique_values)
        for guess, feedback in history:
            checker.observe(guess, feedback)
        return checker

    # ------------------------------------------------------------------ 校验
    def check(self, guess: Sequence[Combo], feedback: Sequence[str]) -> List[str]:
        """返回不一致之处的中文说明；空列表表示未发现矛盾。"""
        problems: List[str] = []
        if len(guess) != len(feedback):
            return [f"猜测与反馈长度不一致：{len(guess)} vs {len(feedback)}"]

        # 1) 同轮重复组合的自洽性
        positions: Dict[Combo, List[int]] = {}
        for i, g in enumerate(guess):
            positions.setdefault(g, []).append(i)
        for combo, idxs in positions.items():
            if len(idxs) < 2:
                continue
            desc = "第 " + "、".join(str(i + 1) for i in idxs) + " 位"
            correct = [i for i in idxs if feedback[i] == CORRECT]
            if len(correct) > 1:
                problems.append(
                    f"{desc} 都猜了 {self._fmt(combo)}，却标了多个 CORRECT；"
                    "一个组合只能有一个正确位置"
                )
            elif len(correct) == 1:
                bad = [i for i in idxs if feedback[i] not in (CORRECT, MISPLACED)]
                if bad:
                    problems.append(
                        f"{desc} 都猜了 {self._fmt(combo)}，其中一位是 CORRECT，"
                        f"其余只能是 MISPLACED，但第 {'、'.join(str(i + 1) for i in bad)} 位标成了 "
                        + "/".join(feedback[i] for i in bad)
                    )
            else:
                # 没有 CORRECT 时：要么全是 MISPLACED（该组合在支持集但在别处），
                # 要么全是 PARTIAL/WRONG（该组合根本不在支持集，此时 P/W 可以任意混）。
                has_mis = any(feedback[i] == MISPLACED for i in idxs)
                has_weak = any(feedback[i] in (PARTIAL, WRONG) for i in idxs)
                if has_mis and has_weak:
                    problems.append(
                        f"{desc} 都猜了 {self._fmt(combo)} 且没有 CORRECT："
                        "这些位要么全是 MISPLACED、要么全是 PARTIAL/WRONG，但实际混用了"
                    )

        # 2) 支持集归属的跨轮一致性
        for i, (g, f) in enumerate(zip(guess, feedback)):
            if f in (CORRECT, MISPLACED) and g in self.known_out:
                problems.append(
                    f"第 {i + 1} 位的 {self._fmt(g)} 之前被标为 PARTIAL/WRONG"
                    f"（说明它不在秘密序列里），这一轮却标成了 {f}"
                )
            if f in (PARTIAL, WRONG) and g in self.known_in:
                problems.append(
                    f"第 {i + 1} 位的 {self._fmt(g)} 之前被标为 CORRECT/MISPLACED"
                    f"（说明它一定在秘密序列里），这一轮却标成了 {f}"
                )

        # 3) 已定位组合的唯一性
        for i, (g, f) in enumerate(zip(guess, feedback)):
            if f != CORRECT:
                continue
            if i in self.placed and self.placed[i] != g:
                problems.append(
                    f"第 {i + 1} 位之前已确认是 {self._fmt(self.placed[i])}，现在不可能又是 {self._fmt(g)}"
                )
            elif g in self.combo_place and self.combo_place[g] != i:
                problems.append(
                    f"{self._fmt(g)} 之前已确认在第 {self.combo_place[g] + 1} 位，不可能同时在第 {i + 1} 位"
                )

        # 4) 终局合法性
        if all(f == CORRECT for f in feedback):
            if self.unique_values and not is_distinct_sequence(guess):
                problems.append("整轮标为 CORRECT，但猜测里有重复组合，不满足“秘密组合互异”规则")
        return problems

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _fmt(combo: Combo) -> str:
        return f"({combo[0]},{combo[1]})"

    def support_summary(self) -> Dict[str, int]:
        return {
            "knownIn": len(self.known_in),
            "knownOut": len(self.known_out),
            "placed": len(self.placed),
        }


__all__ = ["parse_feedback", "feedback_to_letters", "FeedbackChecker", "FEEDBACK_KINDS"]
