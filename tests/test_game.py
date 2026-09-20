# -*- coding: utf-8 -*-
"""规则层单元测试。

运行::

    conda activate flask_env
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.game import (  # noqa: E402
    ALL_COMBOS,
    CANDIDATES,
    CORRECT,
    MISPLACED,
    PARTIAL,
    REMOVED_COMBOS,
    SECRET_DISTINCT,
    SEQ_LEN,
    WRONG,
    combo_str,
    evaluate,
    is_distinct_sequence,
    parse_combo,
    sample_secret,
    shares,
    validate_sequence,
    validate_secret,
)
from core.sequence_game import SequenceGame  # noqa: E402

#: 一个合法的秘密序列（10 个互不相同的组合）
DISTINCT_SECRET = list(CANDIDATES[:SEQ_LEN])


class TestCombinationSpace(unittest.TestCase):
    def test_all_combos_count(self):
        self.assertEqual(len(ALL_COMBOS), 55)
        self.assertEqual(len(set(ALL_COMBOS)), 55)

    def test_removed_combos(self):
        self.assertEqual(len(REMOVED_COMBOS), 10)
        for c in REMOVED_COMBOS:
            self.assertIn(c, ALL_COMBOS)
            self.assertNotIn(c, CANDIDATES)

    def test_candidate_count(self):
        self.assertEqual(len(CANDIDATES), 45)
        self.assertEqual(sorted(CANDIDATES), sorted(set(CANDIDATES)))
        self.assertEqual(len(set(ALL_COMBOS) - set(REMOVED_COMBOS)), 45)

    def test_self_combos_present(self):
        for k in range(1, 11):
            self.assertIn((k, k), CANDIDATES)

    def test_parse_combo(self):
        self.assertEqual(parse_combo([4, 3]), (3, 4))
        self.assertEqual(parse_combo("(3,4)"), (3, 4))
        self.assertEqual(parse_combo("3-4"), (3, 4))
        with self.assertRaises(ValueError):
            parse_combo([1, 5])  # 被删除的组合
        with self.assertRaises(ValueError):
            parse_combo([0, 3])


class TestFeedback(unittest.TestCase):
    def test_all_correct(self):
        secret = [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10)]
        self.assertEqual(evaluate(secret, secret), [CORRECT] * SEQ_LEN)

    def test_priority_misplaced_over_partial(self):
        """g_i 出现在别处时，即使与 x_i 共享元素，也必须判 MISPLACED。"""
        secret = [(1, 2), (1, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (1, 1)]
        guess = [(1, 2), (1, 2), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (1, 1)]
        fb = evaluate(guess, secret)
        self.assertEqual(fb[0], CORRECT)
        # 位置 1 猜 (1,2)，x_1=(1,3) 与其共享 1，但 (1,2) 出现在位置 0 -> MISPLACED
        self.assertEqual(fb[1], MISPLACED)

    def test_partial_when_unique_and_shared(self):
        secret = [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10)]
        guess = [(1, 2), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10)]
        fb = evaluate(guess, secret)
        self.assertEqual(fb[0], PARTIAL)
        self.assertEqual(fb[1], CORRECT)

    def test_wrong_when_no_share(self):
        secret = [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10)]
        guess = [(3, 4), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10)]
        fb = evaluate(guess, secret)
        self.assertEqual(fb[0], WRONG)

    def test_partial_requires_unique_value(self):
        """秘密中某组合重复出现时，猜该组合的位置一律判 MISPLACED，永远不会是 PARTIAL。"""
        secret = [(1, 2), (3, 3), (1, 2)] + [(4, 4)] * 7
        guess = [(3, 3), (1, 2), (3, 3)] + [(4, 4)] * 7
        fb = evaluate(guess, secret)
        self.assertEqual(fb[:3], [MISPLACED, MISPLACED, MISPLACED])
        self.assertNotIn(PARTIAL, fb)
        self.assertEqual(fb[3:], [CORRECT] * 7)

    def test_no_partial_when_value_in_support(self):
        """只要 g_i 属于支持集且 g_i != x_i，就一定是 MISPLACED。"""
        secret = [(1, 2)] * SEQ_LEN
        guess = [(1, 2)] * 5 + [(1, 3)] * 5
        fb = evaluate(guess, secret)
        # (1,3) 不在支持集中，且与 (1,2) 共享元素 1 -> PARTIAL
        self.assertEqual(fb[:5], [CORRECT] * 5)
        self.assertEqual(fb[5:], [PARTIAL] * 5)

    def test_guess_length_mismatch(self):
        with self.assertRaises(ValueError):
            evaluate([(1, 1)], [(1, 1)] * 10)

    def test_shares(self):
        self.assertTrue(shares((1, 2), (2, 3)))
        self.assertTrue(shares((1, 1), (1, 5)) if (1, 5) in CANDIDATES else True)
        self.assertFalse(shares((1, 2), (3, 4)))


class TestSequenceGame(unittest.TestCase):
    def test_seed_reproducible(self):
        a = SequenceGame(seed=42)
        b = SequenceGame(seed=42)
        self.assertEqual(a.secret, b.secret)
        self.assertEqual(len(a.secret), SEQ_LEN)
        for c in a.secret:
            self.assertIn(c, CANDIDATES)

    def test_submit_and_solve(self):
        game = SequenceGame(secret=DISTINCT_SECRET)
        fb = game.submit(DISTINCT_SECRET)
        self.assertEqual(fb, [CORRECT] * SEQ_LEN)
        self.assertTrue(game.solved)
        self.assertEqual(game.rounds, 1)
        with self.assertRaises(RuntimeError):
            game.submit(DISTINCT_SECRET)

    def test_state_serialization(self):
        game = SequenceGame(seed=1)
        game.submit(list(CANDIDATES[:SEQ_LEN]))
        state = game.state()
        self.assertEqual(state["rounds"], 1)
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(len(state["history"][0]["guess"]), SEQ_LEN)
        self.assertNotIn("secret", state)
        self.assertTrue(state["secretDistinct"])
        self.assertIn("secret", game.state(reveal_secret=True))

    def test_validate_sequence_length(self):
        with self.assertRaises(ValueError):
            validate_sequence([(1, 1)] * 9)

    def test_combo_str(self):
        self.assertEqual(combo_str((3, 4)), "(3,4)")


class TestSecretDistinctRule(unittest.TestCase):
    """新规则：秘密序列中的组合不允许重复。"""

    def test_rule_flag_on(self):
        self.assertTrue(SECRET_DISTINCT)

    def test_generated_secrets_are_distinct(self):
        import random

        rng = random.Random(20260920)
        for _ in range(200):
            secret = sample_secret(rng, SEQ_LEN)
            self.assertEqual(len(secret), SEQ_LEN)
            self.assertTrue(is_distinct_sequence(secret), f"出现了重复组合：{secret}")
            for c in secret:
                self.assertIn(c, CANDIDATES)

    def test_game_secret_is_distinct(self):
        for seed in range(50):
            game = SequenceGame(seed=seed)
            self.assertTrue(is_distinct_sequence(game.secret), f"seed={seed} 的秘密有重复")

    def test_duplicate_secret_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            SequenceGame(secret=[(1, 1)] * SEQ_LEN)
        self.assertIn("互不相同", str(ctx.exception))
        with self.assertRaises(ValueError):
            validate_secret(list(CANDIDATES[:9]) + [CANDIDATES[0]])

    def test_valid_distinct_secret_accepted(self):
        secret = SequenceGame(secret=DISTINCT_SECRET).secret
        self.assertEqual(len(set(secret)), SEQ_LEN)

    def test_guess_may_repeat(self):
        """规则只约束秘密序列：猜测序列允许重复，重复的组合只会得到 MISPLACED。"""
        secret = [(1, 1), (2, 2), (3, 3)] + list(CANDIDATES[3:10])
        guess = [(1, 1)] * SEQ_LEN
        fb = evaluate(guess, secret)
        self.assertEqual(fb[0], CORRECT)
        self.assertEqual(fb[1], MISPLACED)
        self.assertNotIn(PARTIAL, fb)

    def test_no_partial_when_guess_is_permutation(self):
        """若互异猜测的 10 个组合全部属于支持集，则反馈只含 C/M，说明猜测是一个排列。"""
        secret = list(CANDIDATES[:SEQ_LEN])
        guess = list(reversed(secret))
        fb = evaluate(guess, secret)
        self.assertTrue(all(f in (CORRECT, MISPLACED) for f in fb))

    def test_partial_means_not_in_support(self):
        """互异规则下，出现 PARTIAL 说明该组合不在支持集中。"""
        secret = list(CANDIDATES[:SEQ_LEN])
        # 选一个不在秘密里、但与 secret[idx] 共享元素的外部组合
        idx = SEQ_LEN - 1
        outsider = next(c for c in CANDIDATES if c not in secret and shares(c, secret[idx]))
        guess = secret[:idx] + [outsider] + secret[idx + 1 :]
        self.assertEqual(evaluate(guess, secret)[idx], PARTIAL)


if __name__ == "__main__":
    unittest.main()
