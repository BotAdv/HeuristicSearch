# -*- coding: utf-8 -*-
"""单局游戏状态机。

负责：生成/指定秘密序列、接收猜测、记录历史、判定胜负。
策略层只通过 ``history`` 观察游戏，不接触秘密序列。
"""
from __future__ import annotations

import random
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

from core.game import (
    CANDIDATES,
    CORRECT,
    SECRET_DISTINCT,
    SEQ_LEN,
    Combo,
    evaluate,
    feedback_letters,
    is_solved,
    sample_secret,
    serialize_sequence,
    summarize,
    validate_secret,
    validate_sequence,
)

GuessFeedback = Tuple[Tuple[Combo, ...], Tuple[str, ...]]


class SequenceGame:
    """一局「组合猜序列」游戏。

    秘密序列按当前规则生成：**10 个互不相同的组合**（``SECRET_DISTINCT = True``）。
    猜测序列不受此限制（允许重复），因为规则只约束秘密序列。
    """

    def __init__(
        self,
        secret: Optional[Sequence] = None,
        seed: Optional[int] = None,
        seq_len: int = SEQ_LEN,
        game_id: Optional[str] = None,
        distinct: Optional[bool] = None,
    ) -> None:
        self.seq_len = seq_len
        #: 本局采用的规则：True = 秘密组合必须互异（当前正式规则）
        self.secret_distinct = SECRET_DISTINCT if distinct is None else bool(distinct)
        self.game_id = game_id or uuid.uuid4().hex[:12]
        if secret is None:
            rng = random.Random(seed)
            self.seed = seed
            # 注意：sample_secret 从候选表中**不重复地**取 10 个并打乱顺序
            self.secret: Tuple[Combo, ...] = tuple(
                sample_secret(rng, seq_len, distinct=self.secret_distinct)
            )
        else:
            self.seed = seed
            self.secret = validate_secret(secret, seq_len, distinct=self.secret_distinct)
        self.history: List[GuessFeedback] = []
        self.rounds = 0

    # ---------------------------------------------------------------- 状态
    @property
    def solved(self) -> bool:
        return bool(self.history) and is_solved(self.history[-1][1])

    @property
    def guess_history(self) -> List[Tuple[Combo, ...]]:
        return [g for g, _ in self.history]

    # ---------------------------------------------------------------- 操作
    def submit(self, guess: Sequence) -> List[str]:
        if self.solved:
            raise RuntimeError("本局已经结束（全部位置 CORRECT）")
        g = validate_sequence(guess, self.seq_len)
        feedback = evaluate(g, self.secret)
        self.history.append((g, tuple(feedback)))
        self.rounds += 1
        return feedback

    def reset(self) -> None:
        self.history.clear()
        self.rounds = 0

    # ---------------------------------------------------------------- 序列化
    def state(self, reveal_secret: bool = False) -> Dict:
        rounds = []
        for idx, (guess, feedback) in enumerate(self.history, start=1):
            rounds.append(
                {
                    "index": idx,
                    "guess": serialize_sequence(guess),
                    "feedback": list(feedback),
                    "letters": feedback_letters(feedback),
                    "summary": summarize(feedback),
                }
            )
        state: Dict = {
            "gameId": self.game_id,
            "seqLen": self.seq_len,
            "rounds": self.rounds,
            "solved": self.solved,
            "history": rounds,
            "candidateCount": len(CANDIDATES),
            "secretDistinct": self.secret_distinct,
        }
        if reveal_secret:
            state["secret"] = serialize_sequence(self.secret)
        return state


def play_auto(
    strategy,
    game: SequenceGame,
    max_rounds: int = 120,
    on_round=None,
) -> Dict:
    """用给定策略自动玩完一局，返回对局报告（含逐步轨迹）。"""
    trace = []
    for _ in range(max_rounds):
        guess = strategy.next_guess(game.history)
        feedback = game.submit(guess)
        trace.append({"guess": serialize_sequence(game.history[-1][0]), "feedback": list(feedback)})
        if on_round is not None:
            on_round(game.history[-1])
        if is_solved(feedback):
            break
    return {
        "gameId": game.game_id,
        "solved": game.solved,
        "rounds": game.rounds,
        "trace": trace,
        "secret": serialize_sequence(game.secret),
    }
