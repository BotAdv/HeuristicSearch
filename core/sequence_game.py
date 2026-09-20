# -*- coding: utf-8 -*-
"""单局游戏状态机。

两种模式共用同一套历史/序列化约定：

* :class:`SequenceGame`    —— 秘密由服务端生成或指定，反馈由规则自动判定（沙盒 / 批量模拟）；
* :class:`HumanFeedbackGame` —— **逐步猜测**：秘密未知，逐位反馈由玩家手工录入。

策略层只通过 ``history``（``[(guess, feedback), ...]``）观察游戏，两种模式完全一致。
"""
from __future__ import annotations

import random
import time
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

from core.feedback import FeedbackChecker
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
        rounds = rounds_payload(self.history)
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


# --------------------------------------------------------------------------
# 逐步猜测模式（秘密未知，反馈由玩家手工录入）
# --------------------------------------------------------------------------
def rounds_payload(history: Sequence[GuessFeedback]) -> List[Dict]:
    """把 ``[(guess, feedback), ...]`` 转成前端与日志共用的轮次列表。"""
    out: List[Dict] = []
    for idx, (guess, feedback) in enumerate(history, start=1):
        out.append(
            {
                "index": idx,
                "guess": serialize_sequence(guess),
                "feedback": list(feedback),
                "letters": feedback_letters(feedback),
                "summary": summarize(feedback),
            }
        )
    return out


class HumanFeedbackGame:
    """逐步猜测对局：**秘密未知**，逐位反馈由玩家手工录入。

    与 :class:`SequenceGame` 的区别在于服务端不持有秘密，
    因此只能用与秘密无关的必然规律做一致性校验（``core.feedback.FeedbackChecker``），
    并把发现的问题作为**警告**返回给调用方。

    流程::

        game.set_pending(strategy.next_guess(game.history))   # 策略给出这一轮猜测
        warnings = game.submit_feedback(parse_feedback(text)) # 玩家录入反馈 -> 进入 history
        ...                                                   # 全部 CORRECT 则 game.solved
    """

    def __init__(
        self,
        seq_len: int = SEQ_LEN,
        game_id: Optional[str] = None,
        assume_distinct: Optional[bool] = None,
        label: str = "",
        note: str = "",
    ) -> None:
        self.seq_len = seq_len
        self.game_id = game_id or uuid.uuid4().hex[:12]
        #: 玩家声明的规则：秘密组合是否必须互异（默认取当前正式规则）
        self.assume_distinct = SECRET_DISTINCT if assume_distinct is None else bool(assume_distinct)
        self.label = label or ""
        self.note = note or ""
        self.history: List[GuessFeedback] = []
        self.rounds = 0
        self.checker = FeedbackChecker(seq_len=seq_len, unique_values=self.assume_distinct)
        self.pending_guess: Optional[Tuple[Combo, ...]] = None
        #: 每一轮的一致性警告（与 history 一一对应）
        self.round_warnings: List[List[str]] = []
        self.created = time.time()
        self.finished_at: Optional[float] = None
        self.saved_path: Optional[str] = None

    # ---------------------------------------------------------------- 状态
    @property
    def solved(self) -> bool:
        return bool(self.history) and is_solved(self.history[-1][1])

    @property
    def status(self) -> str:
        """``awaiting_guess``（该策略出招）/ ``awaiting_feedback``（该玩家录入）/ ``solved``。"""
        if self.solved:
            return "solved"
        return "awaiting_feedback" if self.pending_guess is not None else "awaiting_guess"

    @property
    def guess_history(self) -> List[Tuple[Combo, ...]]:
        return [g for g, _ in self.history]

    # ---------------------------------------------------------------- 操作
    def set_pending(self, guess: Sequence) -> Tuple[Combo, ...]:
        """由策略给出（或外部指定）本轮猜测，等待玩家录入反馈。"""
        if self.solved:
            raise RuntimeError("本局已经结束（全部位置 CORRECT）")
        if self.pending_guess is not None:
            raise RuntimeError("上一轮猜测还没有录入反馈")
        g = validate_sequence(guess, self.seq_len)
        self.pending_guess = g
        return g

    def submit_feedback(self, feedback: Sequence[str]) -> List[str]:
        """录入本轮逐位反馈，返回一致性警告（空列表表示未发现矛盾）。"""
        if self.pending_guess is None:
            raise RuntimeError("还没有待反馈的猜测，请先让策略给出本轮猜测")
        if len(feedback) != self.seq_len:
            raise ValueError(f"反馈长度必须为 {self.seq_len}，实际为 {len(feedback)}")
        warnings = self.checker.check(self.pending_guess, feedback)
        self.history.append((self.pending_guess, tuple(feedback)))
        self.round_warnings.append(list(warnings))
        self.checker.observe(self.pending_guess, feedback)
        self.rounds = len(self.history)
        self.pending_guess = None
        if self.solved:
            self.finished_at = time.time()
        return warnings

    def undo(self) -> bool:
        """撤销上一轮，并把那一轮的猜测恢复成“等待反馈”状态。

        这样玩家发现录错时可以立即重录同一个猜测；若已经无轮次可撤，
        则退化为“丢弃当前待反馈的猜测”。返回是否真的撤销了轮次。
        """
        if not self.history:
            self.pending_guess = None
            return False
        return self._pop_last_round()

    def reject_last_round(self) -> bool:
        """退回刚录入的一轮（严格模式拒绝时用），行为与 :meth:`undo` 一致。"""
        if not self.history:
            return False
        return self._pop_last_round()

    def _pop_last_round(self) -> bool:
        guess, _ = self.history.pop()
        self.round_warnings.pop()
        self.rounds = len(self.history)
        self.checker = FeedbackChecker.from_history(
            self.history, seq_len=self.seq_len, unique_values=self.assume_distinct
        )
        self.finished_at = time.time() if self.solved else None
        self.pending_guess = guess
        return True

    def set_label(self, label: str, note: str = "") -> None:
        self.label = label or ""
        if note:
            self.note = note

    # ---------------------------------------------------------------- 序列化
    def state(self) -> Dict:
        return {
            "gameId": self.game_id,
            "mode": "human",
            "seqLen": self.seq_len,
            "rounds": self.rounds,
            "solved": self.solved,
            "status": self.status,
            "history": rounds_payload(self.history),
            "roundWarnings": self.round_warnings,
            "pendingGuess": serialize_sequence(self.pending_guess) if self.pending_guess else None,
            "candidateCount": len(CANDIDATES),
            "assumeDistinct": self.assume_distinct,
            "label": self.label,
            "note": self.note,
            "createdAt": self.created,
            "finishedAt": self.finished_at,
            "savedPath": self.saved_path,
            "support": self.checker.support_summary(),
        }

    def record(self, strategy_key: Optional[str] = None, params: Optional[Dict] = None) -> Dict:
        """落盘用的完整对局记录（秘密未知，只留猜测与反馈）。"""
        trace = []
        for idx, (guess, feedback) in enumerate(self.history, start=1):
            trace.append(
                {
                    "index": idx,
                    "guess": serialize_sequence(guess),
                    "feedback": list(feedback),
                    "letters": feedback_letters(feedback),
                    "warnings": self.round_warnings[idx - 1] if idx - 1 < len(self.round_warnings) else [],
                }
            )
        return {
            "gameId": self.game_id,
            "mode": "human-feedback",
            "label": self.label,
            "note": self.note,
            "strategy": strategy_key,
            "params": dict(params or {}),
            "assumeDistinct": self.assume_distinct,
            "secretKnown": False,
            "solved": self.solved,
            "rounds": self.rounds,
            "createdAt": self.created,
            "finishedAt": self.finished_at,
            "support": self.checker.support_summary(),
            "trace": trace,
        }
