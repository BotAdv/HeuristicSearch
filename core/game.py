# -*- coding: utf-8 -*-
"""组合猜序列游戏的核心规则层。

本模块**只**定义规则（候选集合、反馈判定、胜利条件），
不包含任何策略逻辑与 IO，保证所有策略面对完全相同的判定标准。

规则摘要
--------
1. 对象集合 U = {1, 2, ..., 10}；
2. 组合 (a, b) 允许重复取对象、不计顺序，即 1 <= a <= b <= 10；
3. 全部组合 C(11, 2) = 55 个（含 10 个自组合）；
4. 本游戏预先删除 10 个组合，实际候选集合 |S| = 45；
5. **秘密序列 x 由 10 个互不相同的组合构成**（x 中不允许出现重复组合）；
6. 猜测序列 g 长度为 10、元素取自 S，**允许重复**（规则只限制秘密序列）；
7. 逐位反馈优先级：CORRECT > MISPLACED > PARTIAL > WRONG；
8. 当 10 个位置全部返回 CORRECT 时游戏结束。

关于“秘密组合互异”带来的额外推理
----------------------------------
既然秘密序列的 10 个组合互不相同，则每个属于支持集的组合**重数恒为 1**，于是：

* 一旦某个组合被确定“落在某个位置”（观察到 CORRECT，或某位置候选集收缩为单元素），
  它就可以从**所有其它位置**的候选集合中删除 —— 这是本规则下收益最大的一条传播；
* 若某一轮使用的 10 个组合互异且反馈中**没有** PARTIAL/WRONG，
  说明这 10 个组合全部属于支持集；而支持集恰好只有 10 个元素，
  因此该猜测就是秘密序列的一个**排列**，问题退化为“纯置换求解”。

关于 MISPLACED 的“掩盖效应”
---------------------------
若 g_i 在秘密序列的**其它位置**出现过，则该位一律返回 MISPLACED，
此时玩家无法从本次反馈得知 g_i 与 x_i 的任何关系（除 g_i != x_i 之外）。
这是本游戏区别于经典位置独立猜测游戏的关键难点。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Combo = Tuple[int, int]

# --------------------------------------------------------------------------
# 1. 组合空间
# --------------------------------------------------------------------------
OBJECTS: Tuple[int, ...] = tuple(range(1, 11))
OBJECT_SET = frozenset(OBJECTS)
SEQ_LEN: int = 10

ALL_COMBOS: List[Combo] = [(a, b) for a in OBJECTS for b in OBJECTS if a <= b]

REMOVED_COMBOS: List[Combo] = [
    (1, 5),
    (2, 4),
    (3, 7),
    (4, 6),
    (5, 6),
    (6, 8),
    (7, 10),
    (8, 9),
    (8, 10),
    (9, 10),
]
_REMOVED_SET = frozenset(REMOVED_COMBOS)

CANDIDATES: List[Combo] = [c for c in ALL_COMBOS if c not in _REMOVED_SET]
CANDIDATE_SET = frozenset(CANDIDATES)

#: 组合 -> 下标（策略与向量化计算常用）
COMBO_INDEX: Dict[Combo, int] = {c: i for i, c in enumerate(CANDIDATES)}

assert len(ALL_COMBOS) == 55, "组合总数应为 55"
assert len(CANDIDATES) == 45, "候选组合数应为 45"

#: 规则开关：秘密序列中的组合是否必须互异。
#: 当前规则为 **True** —— 秘密序列的 10 个位置由 10 个不同的组合构成。
#: 置为 False 可回到“允许重复”的旧规则（仅用于对照实验）。
SECRET_DISTINCT: bool = True

# --------------------------------------------------------------------------
# 2. 反馈取值
# --------------------------------------------------------------------------
CORRECT = "CORRECT"
MISPLACED = "MISPLACED"
PARTIAL = "PARTIAL"
WRONG = "WRONG"

FEEDBACK_KINDS: Tuple[str, ...] = (CORRECT, MISPLACED, PARTIAL, WRONG)
#: 数值编码，便于向量化与紧凑存储
FEEDBACK_CODE: Dict[str, int] = {k: i for i, k in enumerate(FEEDBACK_KINDS)}
#: 单字母，便于前端与日志展示
FEEDBACK_LETTER: Dict[str, str] = {CORRECT: "C", MISPLACED: "M", PARTIAL: "P", WRONG: "W"}
FEEDBACK_MEANING: Dict[str, str] = {
    CORRECT: "完全正确：g_i == x_i",
    MISPLACED: "正确但位置错误：g_i != x_i 且 g_i 出现在其它位置（会掩盖 x_i 的信息）",
    PARTIAL: "部分正确：g_i 是独苗且与 x_i 至少共享一个元素",
    WRONG: "完全错误：以上都不满足",
}


def shares(a: Combo, b: Combo) -> bool:
    """两个组合是否至少共享一个对象。"""
    return a[0] == b[0] or a[0] == b[1] or a[1] == b[0] or a[1] == b[1]


def share_count(a: Combo, b: Combo) -> int:
    """共享对象个数（自组合与自身共享 2 个元素仍按集合语义计 1 种，此处返回元素级别计数）。"""
    sa, sb = set(a), set(b)
    return len(sa & sb)


# --------------------------------------------------------------------------
# 3. 反馈判定
# --------------------------------------------------------------------------
def evaluate(guess: Sequence[Combo], secret: Sequence[Combo]) -> List[str]:
    """按优先级判定每个位置的反馈。

    :param guess: 猜测序列（长度 n）
    :param secret: 秘密序列（长度 n）
    :return: 长度为 n 的反馈列表，取值见 ``FEEDBACK_KINDS``
    """
    n = len(secret)
    if len(guess) != n:
        raise ValueError(f"猜测长度 {len(guess)} 与秘密长度 {n} 不一致")
    counts = Counter(secret)
    out: List[str] = []
    for g, x in zip(guess, secret):
        if g == x:
            out.append(CORRECT)
        elif counts[g] > 0:
            # g != x 且 g 在秘密序列的其它位置出现 -> 掩盖效应
            out.append(MISPLACED)
        elif shares(g, x):
            out.append(PARTIAL)
        else:
            out.append(WRONG)
    return out


def evaluate_codes(guess: Sequence[Combo], secret: Sequence[Combo]) -> Tuple[int, ...]:
    """返回数值编码形式的反馈，便于向量化与哈希。"""
    return tuple(FEEDBACK_CODE[f] for f in evaluate(guess, secret))


def is_solved(feedback: Sequence[str]) -> bool:
    return all(f == CORRECT for f in feedback)


def feedback_letters(feedback: Sequence[str]) -> str:
    return "".join(FEEDBACK_LETTER[f] for f in feedback)


def summarize(feedback: Sequence[str]) -> Dict[str, int]:
    """统计各类反馈数量。"""
    counter = Counter(feedback)
    return {k: counter.get(k, 0) for k in FEEDBACK_KINDS}


# --------------------------------------------------------------------------
# 4. 解析与校验
# --------------------------------------------------------------------------
def parse_combo(value) -> Combo:
    """把 ``(a, b)`` / ``[a, b]`` / ``"3-4"`` / ``"3,4"`` 解析为规范化组合。"""
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError(f"组合必须恰好包含两个对象：{value!r}")
        a, b = int(value[0]), int(value[1])
    elif isinstance(value, str):
        text = value.strip().strip("()").replace("-", ",").replace(" ", ",")
        parts = [p for p in text.split(",") if p != ""]
        if len(parts) != 2:
            raise ValueError(f"无法解析组合：{value!r}")
        a, b = int(parts[0]), int(parts[1])
    else:
        raise ValueError(f"无法解析组合：{value!r}")
    if a > b:
        a, b = b, a
    if (a, b) not in CANDIDATE_SET:
        raise ValueError(f"组合 {combo_str((a, b))} 不在候选集合 S 中（可能属于被删除的组合或超出 U 范围）")
    return (a, b)


def combo_str(c: Combo) -> str:
    return f"({c[0]},{c[1]})"


def validate_sequence(seq: Iterable, expected_len: int = SEQ_LEN) -> Tuple[Combo, ...]:
    """校验并规范化一个序列（只检查长度与元素是否属于 S）。"""
    items = list(seq)
    if len(items) != expected_len:
        raise ValueError(f"序列长度必须为 {expected_len}，实际为 {len(items)}")
    return tuple(parse_combo(x) for x in items)


def is_distinct_sequence(seq: Sequence[Combo]) -> bool:
    """序列中的组合是否两两互不相同。"""
    return len(set(seq)) == len(seq)


def validate_secret(
    seq: Iterable,
    expected_len: int = SEQ_LEN,
    distinct: Optional[bool] = None,
) -> Tuple[Combo, ...]:
    """校验一个**秘密**序列：除长度/取值范围外，还按当前规则检查组合是否互异。"""
    combo = validate_sequence(seq, expected_len)
    if (SECRET_DISTINCT if distinct is None else distinct) and not is_distinct_sequence(combo):
        raise ValueError(
            "按当前规则，秘密序列中的组合必须互不相同（不允许重复）。"
            f"收到的序列存在重复组合，共 {expected_len - len(set(combo))} 处重复。"
        )
    return combo


def serialize_combo(c: Combo) -> List[int]:
    return [int(c[0]), int(c[1])]


def serialize_sequence(seq: Sequence[Combo]) -> List[List[int]]:
    return [serialize_combo(c) for c in seq]


def random_sequence(rng, length: int = SEQ_LEN) -> List[Combo]:
    """从候选集合中独立均匀采样一个序列（**允许重复**，可用作猜测）。"""
    return [rng.choice(CANDIDATES) for _ in range(length)]


def sample_secret(rng, length: int = SEQ_LEN, distinct: Optional[bool] = None) -> List[Combo]:
    """按当前规则采样一个秘密序列。

    ``distinct=True``（默认，规则内）：从 45 个候选中不重复地取 ``length`` 个并打乱顺序；
    ``distinct=False``：独立均匀采样（允许重复，属规则外对照）。
    """
    if distinct is None:
        distinct = SECRET_DISTINCT
    if not distinct:
        return random_sequence(rng, length)
    if length > len(CANDIDATES):
        raise ValueError(f"要求 {length} 个互异组合，但候选集合只有 {len(CANDIDATES)} 个")
    pool = list(CANDIDATES)
    rng.shuffle(pool)
    return pool[:length]


def random_distinct_sequence(rng, length: int = SEQ_LEN) -> List[Combo]:
    """采样一个由互异组合构成的序列（猜测也可用，便于做“无 P/W ⇒ 即排列”的推断）。"""
    return sample_secret(rng, length, distinct=True)


def constant_sequence(combo: Combo, length: int = SEQ_LEN) -> List[Combo]:
    """由同一个组合构成的常数序列（用于探测该组合的重数，仅作猜测使用）。"""
    return [combo] * length
