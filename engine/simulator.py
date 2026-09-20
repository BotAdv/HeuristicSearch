# -*- coding: utf-8 -*-
"""批量模拟引擎。

设计要点
--------
* **配对设计**：同一批秘密序列喂给所有策略，因此两两比较是逐局配对比较，方差更小；
* **可复现**：秘密序列由 ``random.Random(seed)`` 生成，策略自身的随机种子由
  ``seed * 1000003 + game_index`` 派生，同样的 ``(plan, games, seed)`` 必然得到同样的结果；
* **可中断**：通过 ``cancel_event`` 支持前端取消长跑任务。

秘密序列默认按当前规则生成（**10 个互不相同的组合**），详见 :data:`SECRET_MODES`。
"""
from __future__ import annotations

import random
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.game import CANDIDATES, SEQ_LEN, Combo, is_solved, serialize_sequence
from core.sequence_game import SequenceGame
from engine.metrics import pairwise_compare, summarize
from strategies import create as create_strategy
from strategies import get_strategy_class


# --------------------------------------------------------------------------
# 秘密序列生成
# --------------------------------------------------------------------------
#: 秘密生成方式。前两种属于规则内/规则外对照，第三种是规则外的极端压力测试。
SECRET_MODES: Dict[str, Dict[str, Any]] = {
    "distinct": {
        "label": "互异组合（规则内）",
        "inRule": True,
        "help": "从 45 个候选中不重复地取 10 个并打乱顺序——当前规则下的真实分布。",
    },
    "repeated": {
        "label": "允许重复（规则外对照）",
        "inRule": False,
        "help": "每个位置独立均匀采样，允许重复组合。旧规则，仅作对照实验。",
    },
    "constant": {
        "label": "常数序列（规则外压力测试）",
        "inRule": False,
        "help": "同一条序列全为同一个组合，已不符合当前规则，仅用于压测重数相关推理。",
    },
}


def make_secrets(
    count: int,
    seed: int,
    length: int = SEQ_LEN,
    mode: str = "distinct",
) -> List[Tuple[Combo, ...]]:
    """生成 ``count`` 条秘密序列。

    * ``distinct``（默认，规则内）：10 个互不相同的组合，等概率；
    * ``repeated``：允许重复（旧规则，规则外对照，``random`` 作为别名）；
    * ``constant``：常数序列（规则外压力测试）。
    """
    rng = random.Random(seed)
    if mode == "constant":
        pool = list(CANDIDATES)
        rng.shuffle(pool)
        if count <= len(pool):
            return [tuple([c] * length) for c in pool[:count]]
        return [tuple([rng.choice(CANDIDATES)] * length) for _ in range(count)]
    if mode in ("repeated", "random"):
        return [tuple(rng.choice(CANDIDATES) for _ in range(length)) for _ in range(count)]
    if mode == "distinct":
        if length > len(CANDIDATES):
            raise ValueError(f"要求 {length} 个互异组合，但候选集合只有 {len(CANDIDATES)} 个")
        out: List[Tuple[Combo, ...]] = []
        for _ in range(count):
            pool = list(CANDIDATES)
            rng.shuffle(pool)
            out.append(tuple(pool[:length]))
        return out
    raise ValueError(f"未知的秘密生成方式：{mode}（可用：{sorted(SECRET_MODES)}）")


# --------------------------------------------------------------------------
# 单局
# --------------------------------------------------------------------------
def play_one(
    strategy_key: str,
    params: Dict[str, Any],
    secret: Sequence[Combo],
    strategy_seed: Optional[int] = None,
    max_rounds: int = 120,
    collect_trace: bool = False,
    rule_distinct: bool = True,
) -> Dict[str, Any]:
    """用指定策略玩一局，返回结果字典。

    ``rule_distinct=False``（即用规则外秘密做对照）时，会自动把策略的
    ``assume_distinct`` 置为 False——否则策略会套用“重数恒为 1”的推理，
    在那个分布上是不成立的。
    """
    started = time.perf_counter()
    trace: List[Dict[str, Any]] = []
    error: Optional[str] = None
    game = SequenceGame(secret=secret, distinct=rule_distinct)
    effective = dict(params or {})
    effective.setdefault("assume_distinct", bool(rule_distinct))
    try:
        strategy = create_strategy(strategy_key, seed=strategy_seed, **effective)
        for _ in range(max_rounds):
            guess = strategy.next_guess(game.history)
            feedback = game.submit(guess)
            if collect_trace:
                trace.append(
                    {
                        "guess": serialize_sequence(game.history[-1][0]),
                        "feedback": list(feedback),
                    }
                )
            if is_solved(feedback):
                break
    except Exception as exc:  # noqa: BLE001 - 沙盒需要把策略异常记录下来而不是中断整批
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "solved": game.solved,
        "rounds": game.rounds,
        "seconds": elapsed_ms / 1000.0,
        "ms": elapsed_ms,
        "error": error,
        "trace": trace if collect_trace else None,
    }


# --------------------------------------------------------------------------
# 批量模拟
# --------------------------------------------------------------------------
def assign_plan_ids(plan: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给计划条目分配唯一 id。

    同一个策略可以带不同参数出现多次（例如 ``two_phase`` 的 ``exact`` / ``skip`` 两种模式），
    因此不能直接用策略 key 当作字典键，否则结果会互相覆盖。
    """
    counter: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for item in plan:
        key = str(item["key"])
        counter[key] = counter.get(key, 0) + 1
        ident = key if counter[key] == 1 else f"{key}#{counter[key]}"
        out.append({"id": ident, "key": key, "params": dict(item.get("params") or {})})
    return out


def run_benchmark(
    plan: Sequence[Dict[str, Any]],
    games: int = 100,
    seed: int = 20260920,
    max_rounds: int = 120,
    secret_mode: str = "distinct",
    secrets: Optional[Sequence[Sequence[Combo]]] = None,
    progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event=None,
) -> Dict[str, Any]:
    """跑完整套对比实验，返回报告。"""
    plan = assign_plan_ids(plan)
    if secrets is None:
        secrets = make_secrets(games, seed, mode=secret_mode)
    secrets = [tuple(s) for s in secrets]

    total_steps = len(plan) * len(secrets)
    done_steps = 0
    rule_distinct = secret_mode == "distinct"
    report: Dict[str, Any] = {
        "meta": {
            "seed": seed,
            "games": len(secrets),
            "maxRounds": max_rounds,
            "secretMode": secret_mode,
            "plan": [dict(p) for p in plan],
        },
        "strategies": {},
        "pairwise": [],
    }
    rounds_by_key: Dict[str, List[int]] = {}
    started = time.perf_counter()

    for entry in plan:
        ident = entry["id"]
        key = entry["key"]
        params = dict(entry.get("params") or {})
        cls = get_strategy_class(key)
        rounds_list: List[int] = []
        solved_list: List[bool] = []
        times_ms: List[float] = []
        errors: Dict[str, int] = {}
        cancelled = False

        for index, secret in enumerate(secrets):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            strategy_seed = (seed * 1000003 + index) & 0x7FFFFFFF
            result = play_one(
                key,
                params,
                secret,
                strategy_seed=strategy_seed,
                max_rounds=max_rounds,
                rule_distinct=rule_distinct,
            )
            rounds_list.append(result["rounds"])
            solved_list.append(result["solved"])
            times_ms.append(result["ms"])
            if result["error"]:
                errors[result["error"]] = errors.get(result["error"], 0) + 1
            done_steps += 1
            if progress is not None:
                progress(
                    {
                        "done": done_steps,
                        "total": total_steps,
                        "current": ident,
                        "doneInStrategy": index + 1,
                        "gamesInStrategy": len(secrets),
                    }
                )

        aggregate = summarize(rounds_list, solved_list, times_ms, max_rounds)
        rounds_by_key[ident] = rounds_list
        report["strategies"][ident] = {
            "id": ident,
            "key": key,
            "name": cls.name,
            "params": params,
            "aggregate": aggregate,
            "errors": errors,
            "cancelled": cancelled,
            "rounds": rounds_list,
            "solvedFlags": solved_list,
            "timesMs": times_ms,
        }
        if cancelled:
            break

    report["pairwise"] = pairwise_compare(rounds_by_key)
    report["meta"]["elapsedSec"] = round(time.perf_counter() - started, 3)
    report["meta"]["completed"] = done_steps
    report["meta"]["totalSteps"] = total_steps
    return report


def replay_transcript(
    strategy_key: str,
    params: Dict[str, Any],
    secret: Sequence[Combo],
    max_rounds: int = 120,
    strategy_seed: Optional[int] = None,
    rule_distinct: bool = True,
) -> Dict[str, Any]:
    """回放一局并返回完整轨迹（供前端逐步展示）。"""
    return play_one(
        strategy_key,
        params,
        secret,
        strategy_seed=strategy_seed,
        max_rounds=max_rounds,
        collect_trace=True,
        rule_distinct=rule_distinct,
    )
