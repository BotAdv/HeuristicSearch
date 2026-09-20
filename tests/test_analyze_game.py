# -*- coding: utf-8 -*-
"""对局复盘分析器与策略解释钩子的测试。

三件事必须成立，否则「复盘结论」就是编造出来的：

1. **解释钩子不改变行为**：``strategy.explain = True`` 只允许写 ``last_decision`` /
   理由字符串，不能影响 ``next_guess`` 返回的猜测（逐轮对比开/关两种情况）；
2. **解释是完整的**：每一轮的 10 个位置都要有非空理由，且理由里提到的组合必须
   真的出现在本轮猜测里（不然就是串了位置）；
3. **回放与自检可信**：用存档对局回放能复现记录里的猜测，
   ``check_after`` 既能对真实回放「零误报」，也能抓到人为制造的矛盾。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.game import CORRECT, SEQ_LEN, evaluate  # noqa: E402
from engine.simulator import make_secrets  # noqa: E402
from experiments.analyze_game import (  # noqa: E402
    check_after,
    load_record,
    render_report,
    replay,
    snapshot,
)
from strategies import create  # noqa: E402

ENGINE = "two_phase"
PARAMS = {
    "count_policy": "auto_exact",
    "probe_order": "heuristic",
    "assume_distinct": True,
    "lock_known_positions": True,
}


def play(strategy, secret, max_rounds: int = 30):
    """用真实反馈把一个策略在这条秘密序列上玩完，返回 (逐轮猜测, 逐轮反馈)。"""
    history = []
    guesses, feedbacks = [], []
    for _ in range(max_rounds):
        guess = list(strategy.next_guess(history))
        feedback = list(evaluate(guess, secret))
        history.append((tuple(guess), tuple(feedback)))
        guesses.append(tuple(guess))
        feedbacks.append(tuple(feedback))
        if all(f == CORRECT for f in feedback):
            break
    return guesses, feedbacks


class TestExplainHook(unittest.TestCase):
    """解释钩子必须只读：开了解释与不开解释，策略的下法要逐轮完全一致。"""

    def test_explain_does_not_change_guesses(self):
        secrets = make_secrets(6, seed=20260920)
        for idx, secret in enumerate(secrets):
            with self.subTest(game=idx):
                plain = create(ENGINE, seed=7, **PARAMS)
                loud = create(ENGINE, seed=7, **PARAMS)
                loud.explain = True
                g_plain, _ = play(plain, secret)
                g_loud, _ = play(loud, secret)
                self.assertEqual(g_plain, g_loud)

    def test_every_position_has_a_reason(self):
        secret = make_secrets(1, seed=99)[0]
        strategy = create(ENGINE, seed=3, **PARAMS)
        strategy.explain = True
        history = []
        for _ in range(30):
            guess = list(strategy.next_guess(history))
            decision = strategy.last_decision
            self.assertIsNotNone(decision)
            self.assertEqual(len(decision["candidates"]), SEQ_LEN)
            reasons = [cell["reason"] for cell in decision["candidates"]]
            self.assertTrue(all(r for r in reasons), f"有位置没有理由：{reasons}")
            for cell, combo in zip(decision["candidates"], guess):
                self.assertIn(
                    f"({combo[0]}, {combo[1]})",
                    cell["reason"],
                    f"第 {cell['position']} 位的理由与本轮猜测不一致：{cell['reason']}",
                )
                self.assertEqual(cell["chosen"], combo)
            feedback = list(evaluate(guess, secret))
            history.append((tuple(guess), tuple(feedback)))
            if all(f == CORRECT for f in feedback):
                break
        else:
            self.fail("策略没有在 30 轮内解出")

    def test_phase_budget_is_reported(self):
        secret = make_secrets(1, seed=99)[0]
        strategy = create(ENGINE, seed=3, **PARAMS)
        strategy.explain = True
        history = []
        seen_phase_b = False
        for _ in range(30):
            guess = list(strategy.next_guess(history))
            decision = strategy.last_decision
            if decision["phase"] == "B":
                seen_phase_b = True
                budget = decision["budget"]
                self.assertEqual(set(budget["remainingBefore"]), set(budget["remainingOfSupport"]))
                self.assertTrue(all(v >= 0 for v in budget["remainingBefore"].values()))
            feedback = list(evaluate(guess, secret))
            history.append((tuple(guess), tuple(feedback)))
            if all(f == CORRECT for f in feedback):
                break
        self.assertTrue(seen_phase_b, "这条秘密序列应该会进入阶段 B")

    def test_count_lower_ignores_repeated_correct(self):
        """同一位置连续两轮 CORRECT，不能把「已落位副本数」算成 2。"""
        secret = make_secrets(1, seed=99)[0]
        strategy = create(ENGINE, seed=3, **PARAMS)
        history = []
        seen = set()
        for _ in range(30):
            guess = list(strategy.next_guess(history))
            feedback = list(evaluate(guess, secret))
            history.append((tuple(guess), tuple(feedback)))
            # 先把本轮反馈喂给策略，再检查计数（否则查的是“还没观察到”的状态）
            strategy._sync(history)
            for combo, f in zip(guess, feedback):
                if f == CORRECT:
                    seen.add(combo)
                    self.assertEqual(
                        strategy.belief.count_lower.get(combo),
                        1,
                        f"{combo} 被同一位置重复确认，计数不该超过 1",
                    )
            if all(f == CORRECT for f in feedback):
                break
        self.assertEqual(len(seen), SEQ_LEN)


class TestCheckAfter(unittest.TestCase):
    def test_no_false_positive_on_real_replay(self):
        """真实对局回放：每一轮的反馈必然推论都必须已被信念吸收。"""
        for seed in (None, 3, 11):
            with self.subTest(seed=seed):
                secret = make_secrets(1, seed=20260920)[0]
                strategy = create(ENGINE, seed=seed, **PARAMS)
                history = []
                for _ in range(30):
                    strategy._sync(history)
                    guesses = [tuple(c) for c in strategy.next_guess(history)]
                    feedback = tuple(evaluate(guesses, secret))
                    strategy._sync(history + [(tuple(guesses), feedback)])
                    problems = check_after(snapshot(strategy), guesses, feedback)
                    self.assertEqual(problems, [], f"种子 {seed} 出现误报：{problems}")
                    history.append((tuple(guesses), feedback))
                    if all(f == CORRECT for f in feedback):
                        break

    def test_detects_contradiction(self):
        """人为制造矛盾时必须报错，否则这个自检就是摆设。"""
        secret = make_secrets(1, seed=20260920)[0]
        strategy = create(ENGINE, seed=0, **PARAMS)
        history = []
        strategy._sync(history)
        guess = [tuple(c) for c in strategy.next_guess(history)]
        feedback = tuple(evaluate(guess, secret))
        strategy._sync(history + [(tuple(guess), feedback)])
        after = snapshot(strategy)
        # 把某一位的候选集人为改掉，制造「反馈说 WRONG，候选集却还留着共享元素的组合」
        broken = dict(after)
        wrong_at = next(i for i, f in enumerate(feedback) if f != CORRECT)
        sets = [list(s) for s in after["sets"]]
        sets[wrong_at] = sorted(set(after["sets"][wrong_at]) | {guess[wrong_at]})
        broken["sets"] = sets
        problems = check_after(broken, guess, feedback)
        self.assertTrue(problems, "自检没有发现人为制造的矛盾")


class TestReplayRecordedGame(unittest.TestCase):
    """存档对局：回放要能复现，报告要能生成。"""

    RECORD = config.GAMES_DIR / "cf2d34c94be5.json"

    def setUp(self):
        if not self.RECORD.exists():
            self.skipTest(f"找不到存档 {self.RECORD}")

    def test_replay_reproduces_recorded_guesses(self):
        record = load_record(str(self.RECORD))
        rounds, _ = replay(record, seed=1, explain=True)
        made = [r["madeGuess"] for r in rounds if r["match"] is not None]
        self.assertEqual(len(made), record["rounds"])
        self.assertTrue(all(r["match"] for r in rounds if r["match"] is not None))

    def test_replay_consistency(self):
        record = load_record(str(self.RECORD))
        rounds, _ = replay(record, seed=1, explain=True)
        for idx, r in enumerate(rounds):
            if r["decision"] is None or idx + 1 >= len(rounds):
                continue
            problems = check_after(rounds[idx + 1]["before"], r["recordedGuess"], r["feedback"])
            self.assertEqual(problems, [], f"第 {r['index']} 轮自检失败：{problems}")

    def test_report_has_all_rounds(self):
        record = load_record(str(self.RECORD))
        rounds, _ = replay(record, seed=1, explain=True)
        report = render_report(record, rounds, [("1", [True] * record["rounds"])])
        for idx in range(1, record["rounds"] + 1):
            self.assertIn(f"## 第 {idx} 轮", report)
        self.assertIn("## 1. 回放校验", report)
        self.assertIn("## 2. 各轮反馈对信念的实际收缩", report)
        self.assertIn("## 3. 决策构成统计", report)

    def test_report_written_to_file(self):
        """端到端：CLI 入口能跑通并把报告写到指定位置。"""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.md"
            proc = subprocess.run(
                [sys.executable, str(config.BASE_DIR / "experiments" / "analyze_game.py"),
                 str(self.RECORD), "--out", str(out)],
                cwd=str(config.BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("一致性自检：通过", proc.stdout)
            text = out.read_text(encoding="utf-8")
            self.assertIn("对局复盘", text)
            payload = json.loads(self.RECORD.read_text(encoding="utf-8"))
            self.assertIn(payload["gameId"], text)


if __name__ == "__main__":
    unittest.main()
