# -*- coding: utf-8 -*-
"""策略层冒烟测试。

重点验证：
1. 所有策略都能在有限轮次内解出随机秘密（或至少在轮次上限内不出异常）；
2. 策略输出的猜测始终是合法候选组合；
3. 同一批秘密下策略结果可复现。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.game import CANDIDATE_SET, SEQ_LEN  # noqa: E402
from core.sequence_game import SequenceGame  # noqa: E402
from engine.simulator import make_secrets, play_one  # noqa: E402
from strategies import CLASSES  # noqa: E402

#: 需要能在该轮次内解出的策略（不含纯随机/纯固定基线）
SOLVABLE = {"naive_position", "two_phase", "position_entropy", "adaptive_hybrid", "particle_entropy"}


class TestStrategyContract(unittest.TestCase):
    def test_registry_unique_keys(self):
        keys = [c.key for c in CLASSES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_params_spec_unique(self):
        for cls in CLASSES:
            names = [p.name for p in cls.params_spec]
            self.assertEqual(len(names), len(set(names)), f"{cls.key} 参数名重复")

    def test_guess_is_legal(self):
        secrets = make_secrets(5, 777)
        for cls in CLASSES:
            for secret in secrets:
                game = SequenceGame(secret=secret)
                strategy = cls(seed=1)
                for _ in range(3):
                    guess = strategy.next_guess(game.history)
                    self.assertEqual(len(guess), SEQ_LEN, f"{cls.key} 猜测长度错误")
                    for combo in guess:
                        self.assertIn(combo, CANDIDATE_SET, f"{cls.key} 输出了非法组合 {combo}")
                    game.submit(guess)


class TestStrategyPerformance(unittest.TestCase):
    def test_solvable_strategies(self):
        secrets = make_secrets(20, 20260920)
        for cls in CLASSES:
            if cls.key not in SOLVABLE:
                continue
            solved = 0
            with self.subTest(strategy=cls.key):
                for index, secret in enumerate(secrets):
                    result = play_one(cls.key, {}, secret, strategy_seed=index, max_rounds=40)
                    self.assertIsNone(result["error"], f"{cls.key} 抛出异常：{result['error']}")
                    solved += int(result["solved"])
                # 20 局中至少解出 18 局（允许个别难例）
                self.assertGreaterEqual(solved, 18, f"{cls.key} 仅解出 {solved}/20 局")

    def test_reproducible(self):
        secret = make_secrets(1, 5)[0]
        a = play_one("particle_entropy", {"particles": 32}, secret, strategy_seed=7, max_rounds=40)
        b = play_one("particle_entropy", {"particles": 32}, secret, strategy_seed=7, max_rounds=40)
        self.assertEqual(a["rounds"], b["rounds"])
        self.assertEqual(a["solved"], b["solved"])


if __name__ == "__main__":
    unittest.main()
