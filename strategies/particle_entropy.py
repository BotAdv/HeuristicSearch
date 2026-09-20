# -*- coding: utf-8 -*-
"""粒子滤波 + 全局最大熵（向量化实现）。

与“逐位置熵”不同，这里对**整条序列**的信念做建模：

1. 用一个粒子集合表示“所有与历史反馈一致的候选秘密序列”；
2. 每轮生成一个候选猜测池（粒子本身 / 逐位置众数 / 常数探针 / 随机边缘采样）；
3. 用粒子集合估计每个候选猜测的**反馈分布**，选择期望信息熵最大的那个。

该策略是理论上最强的对照之一：它显式建模 MISPLACED 所依赖的“全局支持集”结构，
因此能正确处理“某组合在别的位置出现过”这类非局部信息。

实现要点：反馈判定与熵估计全部用 NumPy 向量化，
单个候选猜测的评分成本约 ``O(P · 10)``（P 为粒子数）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.game import CANDIDATES, COMBO_INDEX, FEEDBACK_CODE, SECRET_DISTINCT, SEQ_LEN, Combo
from strategies.base import History, ParamSpec, Strategy
from strategies.belief import PositionBelief

N_COMBOS = len(CANDIDATES)
_ARR = np.asarray(CANDIDATES, dtype=np.int64)  # (45, 2)
#: 两组合是否共享至少一个对象
SHARE = (
    (_ARR[:, None, 0] == _ARR[None, :, 0])
    | (_ARR[:, None, 0] == _ARR[None, :, 1])
    | (_ARR[:, None, 1] == _ARR[None, :, 0])
    | (_ARR[:, None, 1] == _ARR[None, :, 1])
)
_WEIGHTS = np.asarray([4 ** (SEQ_LEN - 1 - k) for k in range(SEQ_LEN)], dtype=np.int64)
_ALL_CORRECT_CODE = 0  # CORRECT 的编码为 0，全 0 即“全部正确”


def evaluate_particles(particles: np.ndarray, guess_idx: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """向量化计算每个粒子对 ``guess_idx`` 的反馈编码与 CORRECT 位数。"""
    eq = particles == guess_idx[None, :]
    total = eq.sum(axis=1)
    mis = (~eq) & (total[:, None] > 0)
    sh = SHARE[guess_idx[None, :], particles]
    par = (~eq) & (~mis) & sh
    codes = np.where(eq, 0, np.where(mis, 1, np.where(par, 2, 3))).astype(np.int64)
    return codes @ _WEIGHTS, eq.sum(axis=1)


def guess_codes(particles: np.ndarray, guess_idx: np.ndarray) -> np.ndarray:
    return evaluate_particles(particles, guess_idx)[0]


def encode_feedback(feedback: Sequence[str]) -> int:
    return int(sum(FEEDBACK_CODE[f] * int(w) for f, w in zip(feedback, _WEIGHTS)))


def distribution_entropy(codes: np.ndarray) -> Tuple[float, np.ndarray, np.ndarray]:
    """返回经验分布的香农熵（bit）、取值与计数。"""
    values, counts = np.unique(codes, return_counts=True)
    p = counts.astype(np.float64) / counts.sum()
    h = float(-(p * np.log2(p)).sum())
    return h, values, counts


class ParticleEntropyStrategy(Strategy):
    key = "particle_entropy"
    name = "粒子滤波 + 全局最大熵"
    description = (
        "维护一组与历史反馈完全一致的候选秘密序列（粒子），"
        "每轮在候选猜测池中挑选“反馈分布期望熵最大”的猜测。"
        "整序列建模可以正确利用 MISPLACED 带来的非局部信息，是理论最强的对照策略。"
    )
    tags = ("entropy", "adaptive", "global", "expensive")
    params_spec = (
        ParamSpec("particles", "粒子数", default=64, type="int", min=16, max=2048,
                  help="越大越接近精确后验，但每轮计算量线性增长。"
                       "实测 32→512 之间平均步数几乎不变，仅耗时线性增加。"),
        ParamSpec("candidate_pool", "候选猜测池大小", default=32, type="int", min=8, max=512,
                  help="每轮评估多少个候选猜测。"),
        ParamSpec("min_particles", "触发重采样的粒子数下限", default=24, type="int", min=1, max=1024),
        ParamSpec("refill_attempts", "重采样尝试次数", default=4, type="int", min=1, max=200,
                  help="每次最多做几轮“提议 + 用完整历史过滤”；互异规则下过滤通过率会明显下降。"),
        ParamSpec("finish_particles", "直接猜共识的粒子数阈值", default=1, type="int", min=0, max=64,
                  help="当剩余不同粒子数不超过该值时，直接猜出现次数最多的粒子，确保收尾。"),
        ParamSpec("objective", "候选评分目标", default="entropy_correct", type="choice",
                  choices=["entropy", "entropy_correct", "expected_remaining", "expected_correct"],
                  help="entropy：最大化反馈分布熵；entropy_correct：熵 + 收尾激励×期望正确位数；"
                       "expected_remaining：最小化期望剩余候选序列数；expected_correct：最大化期望正确位数。"),
        ParamSpec("finish_bonus", "收尾激励系数", default=0.35, type="float", min=0.0, max=5.0, step=0.05,
                  help="用于 entropy / entropy_correct：奖励“本轮能直接锁定更多位置”的猜测。"),
        ParamSpec("assume_distinct", "假定秘密组合互异", default=SECRET_DISTINCT, type="bool",
                  help="当前规则下应为 True（粒子也必须由互异组合构成）；"
                       "仅在用规则外秘密做对照实验时关闭。"),
    )

    # ------------------------------------------------------------------ 状态
    def reset(self) -> None:
        super().reset()
        #: 新规则下秘密序列的组合互不重复，粒子也必须满足该约束，否则会污染后验
        self.unique_values = bool(self.params["assume_distinct"])
        self.belief = PositionBelief(global_prune=True, unique_values=self.unique_values)
        self._obs: List[Tuple[np.ndarray, int]] = []
        n = int(self.params["particles"])
        self.particles = np.asarray(self._sample_prior(n), dtype=np.int64)

    def _sample_prior(self, count: int) -> List[List[int]]:
        """从先验采样粒子：规则内时保证一条序列内组合互异。"""
        if self.unique_values:
            return [list(self.rng.sample(range(N_COMBOS), SEQ_LEN)) for _ in range(count)]
        return [[self.rng.randrange(N_COMBOS) for _ in range(SEQ_LEN)] for _ in range(count)]

    # ------------------------------------------------------------------ 更新
    def observe(self, guess: Sequence[Combo], feedback: Sequence[str]) -> None:
        self.belief.observe(guess, feedback)
        g = np.asarray([COMBO_INDEX[c] for c in guess], dtype=np.int64)
        code = encode_feedback(feedback)
        self._obs.append((g, code))

        if len(self.particles):
            keep = guess_codes(self.particles, g) == code
            self.particles = self.particles[keep]
        if len(self.particles) < int(self.params["min_particles"]):
            self._refill()
        cap = int(self.params["particles"]) * 4
        if len(self.particles) > cap:
            idx = list(range(len(self.particles)))
            self.rng.shuffle(idx)
            self.particles = self.particles[idx[:cap]]

    def _sample_from_belief(self, count: int) -> np.ndarray:
        """从逐位置候选集合采样（作为提议分布，保证可行解一定可被采到）。

        规则内（组合互异）时逐位置抽不重复的组合；若某个位置实在无新组合可用，
        则退回该位置的候选池，避免取样失败。
        """
        pools: List[List[int]] = []
        for i in range(SEQ_LEN):
            pool = [COMBO_INDEX[c] for c in sorted(self.belief.sets[i])]
            pools.append(pool or list(range(N_COMBOS)))
        rows: List[List[int]] = []
        for _ in range(count):
            row = [0] * SEQ_LEN
            used: set = set()
            order = list(range(SEQ_LEN))
            self.rng.shuffle(order)
            for i in order:
                pool = pools[i]
                if self.unique_values:
                    available = [k for k in pool if k not in used]
                    if not available:
                        available = pool
                else:
                    available = pool
                pick = self.rng.choice(available)
                row[i] = pick
                used.add(pick)
            rows.append(row)
        return np.asarray(rows, dtype=np.int64)

    def _refill(self) -> None:
        target = int(self.params["particles"])
        need = target - len(self.particles)
        if need <= 0:
            return
        collected: List[np.ndarray] = []
        attempts = 0
        got = 0
        attempts_max = int(self.params["refill_attempts"])
        while got < need and attempts < attempts_max:
            attempts += 1
            batch = self._sample_from_belief(max(need * 2, 128))
            ok = np.ones(len(batch), dtype=bool)
            for g, code in self._obs:
                if not ok.any():
                    break
                ok &= guess_codes(batch, g) == code
            if ok.any():
                picked = batch[ok]
                collected.append(picked)
                got += len(picked)
        if collected:
            fresh = np.concatenate(collected)[: max(need, 1) * 4]
        else:
            # 兜底：宁可放宽信念也不能让粒子集合为空
            fresh = self._sample_from_belief(need)
        if len(self.particles):
            self.particles = np.concatenate([self.particles, fresh])
        else:
            self.particles = fresh
        if len(self.particles) > target * 4:
            self.particles = self.particles[: target * 4]

    # ------------------------------------------------------------------ 决策
    def next_guess(self, history: History) -> List[Combo]:
        self._sync(history)
        P = self.particles
        if len(P) == 0:
            return self._random_guess()

        finish_threshold = int(self.params["finish_particles"])
        if finish_threshold > 0:
            uniq, counts = np.unique(P, axis=0, return_counts=True)
            if len(uniq) <= finish_threshold:
                row = uniq[int(np.argmax(counts))]
                return [CANDIDATES[int(k)] for k in row]

        pool = self._candidate_guesses(P)
        objective = self.params["objective"]
        bonus = float(self.params["finish_bonus"])
        n = float(len(P))
        best_idx: Optional[np.ndarray] = None
        best_score = -float("inf")
        for guess_idx in pool:
            codes, n_correct = evaluate_particles(P, guess_idx)
            if objective in ("entropy", "entropy_correct"):
                values, counts = np.unique(codes, return_counts=True)
                probs = counts.astype(np.float64) / counts.sum()
                score = float(-(probs * np.log2(probs)).sum())
                if objective == "entropy":
                    hits = counts[values == _ALL_CORRECT_CODE]
                    score += bonus * (float(hits[0]) / n if len(hits) else 0.0)
                else:
                    score += bonus * float(n_correct.mean())
            elif objective == "expected_remaining":
                _, counts = np.unique(codes, return_counts=True)
                score = -float((counts.astype(np.float64) ** 2).sum()) / n
            else:  # expected_correct
                score = float(n_correct.mean())
            if score > best_score + 1e-12:
                best_score = score
                best_idx = guess_idx
        if best_idx is None:
            best_idx = P[0]
        return [CANDIDATES[int(k)] for k in best_idx]

    def _candidate_guesses(self, P: np.ndarray) -> List[np.ndarray]:
        limit = int(self.params["candidate_pool"])
        out: List[np.ndarray] = []
        seen = set()

        def add(row: Sequence[int]) -> None:
            key = tuple(int(x) for x in row)
            if key not in seen:
                seen.add(key)
                out.append(np.asarray(key, dtype=np.int64))

        # 1) 粒子代表（最可能的候选）
        order = list(range(len(P)))
        self.rng.shuffle(order)
        for j in order[: max(4, limit // 4)]:
            add(P[j])

        # 2) 逐位置众数 / 次众数构成的组合
        col_rank: List[List[int]] = []
        for i in range(SEQ_LEN):
            values, counts = np.unique(P[:, i], return_counts=True)
            idx = np.argsort(-counts)
            col_rank.append([int(values[k]) for k in idx[:3]])
        add([col_rank[i][0] for i in range(SEQ_LEN)])
        for variant in (1, 2):
            add([col_rank[i][min(variant, len(col_rank[i]) - 1)] for i in range(SEQ_LEN)])

        # 3) 常数探针：快速判定某个高频组合在支持集中的重数
        flat = P.reshape(-1)
        values, counts = np.unique(flat, return_counts=True)
        for k in np.argsort(-counts)[:3]:
            add([int(values[k])] * SEQ_LEN)

        # 4) 按逐位置边缘分布随机采样
        for _ in range(max(4, limit // 2)):
            add([self.rng.choice(col_rank[i]) for i in range(SEQ_LEN)])

        # 5) 纯随机组合（保证探索性）
        for _ in range(max(4, limit // 6)):
            add([self.rng.randrange(N_COMBOS) for _ in range(SEQ_LEN)])

        return out[:limit]
