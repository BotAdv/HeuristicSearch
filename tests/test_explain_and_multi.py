# -*- coding: utf-8 -*-
"""决策解释 + 多策略并排 + 历史交给策略预测：守门测试。

三块：

1. **解释必须只读、且对所有策略都完整**：打开 ``explain`` 不能改变任何一步下法
   （每个策略逐轮对比），每个位置都必须有点明本轮组合的中文理由，
   且 ``last_decision`` 能直接 JSON 序列化（前端/存档都用它）。
2. **``/api/advise``**：把一段历史交给策略，得到“它下一步会怎么猜”；
   0 轮历史等价于首轮出招，多轮历史等价于按同样历史回放。
3. **多策略并排**：同一秘密、共享轮次；每轮都带上解释；能各自单独推进。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.game import CANDIDATES, CORRECT, evaluate  # noqa: E402
from core.sequence_game import SequenceGame  # noqa: E402
from engine.simulator import make_secrets  # noqa: E402
from strategies import catalog, create  # noqa: E402

ALL_KEYS = [entry["key"] for entry in catalog()]


def play(strategy, secret, max_rounds: int = 40):
    """用真实反馈把一条秘密玩完，返回逐轮猜测。"""
    history, guesses = [], []
    for _ in range(max_rounds):
        guess = list(strategy.next_guess(history))
        feedback = list(evaluate(guess, secret))
        history.append((tuple(guess), tuple(feedback)))
        guesses.append(tuple(guess))
        if all(f == CORRECT for f in feedback):
            break
    return guesses


class TestExplainIsReadOnlyAndComplete(unittest.TestCase):
    """解释是“观察者”，不能变成“参与者”。"""

    def test_explain_does_not_change_play(self):
        secrets = make_secrets(3, seed=20260920)
        for key in ALL_KEYS:
            for idx, secret in enumerate(secrets):
                with self.subTest(strategy=key, game=idx):
                    quiet = create(key, seed=11)
                    loud = create(key, seed=11)
                    loud.explain = True
                    self.assertEqual(play(quiet, secret, 12), play(loud, secret, 12))

    def test_reasons_cover_all_positions_and_name_the_combo(self):
        secret = make_secrets(1, seed=99)[0]
        for key in ALL_KEYS:
            with self.subTest(strategy=key):
                strategy = create(key, seed=3)
                strategy.explain = True
                history = []
                for _ in range(4):
                    guess = list(strategy.next_guess(history))
                    decision = strategy.last_decision
                    self.assertIsNotNone(decision)
                    self.assertEqual(len(decision["candidates"]), 10)
                    for cell, combo in zip(decision["candidates"], guess):
                        self.assertTrue(cell["reason"], f"{key} 第 {cell['position']} 位没有理由")
                        self.assertIn(
                            f"({combo[0]}, {combo[1]})",
                            cell["reason"],
                            f"{key} 第 {cell['position']} 位的理由没点明本轮组合：{cell['reason']}",
                        )
                        self.assertEqual(cell["chosen"], list(combo))
                    feedback = list(evaluate(guess, secret))
                    history.append((tuple(guess), tuple(feedback)))
                    if all(f == CORRECT for f in feedback):
                        break

    def test_decision_is_json_serializable(self):
        strategy = create("two_phase", seed=1)
        strategy.explain = True
        secret = make_secrets(1, seed=5)[0]
        history = []
        for _ in range(3):
            guess = list(strategy.next_guess(history))
            text = json.dumps(strategy.last_decision, ensure_ascii=False)
            self.assertIn("candidates", text)
            feedback = list(evaluate(guess, secret))
            history.append((tuple(guess), tuple(feedback)))

    def test_phase_and_extra_fields(self):
        """two_phase 的阶段字段仍要给出（复盘脚本与前端都会读）。"""
        strategy = create("two_phase", seed=1)
        strategy.explain = True
        secret = make_secrets(1, seed=7)[0]
        history = []
        phases = set()
        for _ in range(6):
            guess = list(strategy.next_guess(history))
            decision = strategy.last_decision
            phases.add(decision["phase"])
            self.assertIn("phaseARounds", decision)
            self.assertIn("support", decision)
            self.assertGreaterEqual(decision["support"]["inCount"], 0)
            feedback = list(evaluate(guess, secret))
            history.append((tuple(guess), tuple(feedback)))
            if all(f == CORRECT for f in feedback):
                break
        self.assertIn("A", phases)


class TestAdviseEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.client = app.app.test_client()

    def test_empty_history_equals_first_guess(self):
        res = self.client.post("/api/advise", json={"strategy": "two_phase", "history": []})
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        first = [tuple(c) for c in create("two_phase", seed=None).next_guess([])]
        self.assertEqual([tuple(c) for c in body["guess"]], first)
        self.assertEqual(body["rounds"], 0)
        self.assertEqual(len(body["decision"]["candidates"]), 10)

    def test_history_replay_matches_direct_replay(self):
        secret = make_secrets(1, seed=20260920)[0]
        strategy = create("position_entropy", seed=5)
        strategy.explain = True
        history, payload = [], []
        for _ in range(2):
            guess = list(strategy.next_guess(history))
            feedback = list(evaluate(guess, secret))
            history.append((tuple(guess), tuple(feedback)))
            payload.append({"guess": [list(c) for c in guess], "letters": "".join(f[0] for f in feedback)})
        expected = [tuple(c) for c in strategy.next_guess(history)]
        expected_decision = strategy.last_decision
        res = self.client.post(
            "/api/advise",
            json={"strategy": "position_entropy", "seed": 5, "history": payload},
        )
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        self.assertEqual(body["rounds"], 2)
        # 重放要“一模一样”：不仅猜得要一样，解释里的信念统计也要一样
        self.assertEqual([tuple(c) for c in body["guess"]], expected)
        self.assertEqual(body["decision"]["support"]["inCount"], expected_decision["support"]["inCount"])
        self.assertEqual(
            [c["size"] for c in body["decision"]["candidates"]],
            [c["size"] for c in expected_decision["candidates"]],
        )

    def test_feedback_accepts_letters_and_full_names(self):
        secret = make_secrets(1, seed=3)[0]
        guess = [list(c) for c in create("two_phase", seed=1).next_guess([])]
        feedback = list(evaluate([tuple(c) for c in guess], secret))
        letters = "".join(f[0] for f in feedback)
        a = self.client.post("/api/advise", json={
            "strategy": "naive_position", "seed": 4,
            "history": [{"guess": guess, "letters": letters}]}).get_json()
        b = self.client.post("/api/advise", json={
            "strategy": "naive_position", "seed": 4,
            "history": [{"guess": guess, "feedback": feedback}]}).get_json()
        self.assertEqual(a["guess"], b["guess"])

    def test_bad_input_rejected(self):
        bad_key = self.client.post("/api/advise", json={"strategy": "nope", "history": []})
        self.assertEqual(bad_key.status_code, 400)
        bad_hist = self.client.post("/api/advise", json={"strategy": "two_phase", "history": [{"guess": [[1, 1]]}]})
        self.assertEqual(bad_hist.status_code, 400)
        bad_fb = self.client.post("/api/advise", json={
            "strategy": "two_phase",
            "history": [{"guess": [[1, 1]] * 10, "letters": "XXXX"}]})
        self.assertEqual(bad_fb.status_code, 400)


class TestMultiStrategySession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.client = app.app.test_client()

    def _create(self, keys, seed=7):
        res = self.client.post("/api/games", json={
            "mode": "multi", "seed": seed, "maxRounds": 20,
            "plan": [{"key": k} for k in keys],
        })
        self.assertEqual(res.status_code, 200, res.get_json())
        return res.get_json()

    def test_same_secret_for_all_and_shared_rounds(self):
        state = self._create(["two_phase", "naive_position"])
        self.assertEqual(state["mode"], "multi")
        gid = state["gameId"]
        stepped = self.client.post(f"/api/games/{gid}/step", json={})
        self.assertEqual(stepped.status_code, 200, stepped.get_json())
        st = stepped.get_json()["state"]
        self.assertEqual([e["rounds"] for e in st["entries"]], [1, 1])
        # 同一个秘密：开 reveal 后各条对局的秘密必须完全一致
        revealed = self.client.get(f"/api/games/{gid}?reveal=1").get_json()
        secrets = [tuple(tuple(c) for c in e["secret"]) for e in revealed["entries"]]
        self.assertEqual(len(set(secrets)), 1)
        self.assertEqual(len(set(secrets[0])), 10, "秘密组合应当互异")

    def test_each_round_carries_explanations(self):
        gid = self._create(["two_phase", "position_entropy"])["gameId"]
        st = self.client.post(f"/api/games/{gid}/step", json={}).get_json()["state"]
        for entry in st["entries"]:
            decision = entry["history"][-1]["decision"]
            self.assertIsNotNone(decision, f"{entry['strategy']} 的这一轮没有解释")
            self.assertEqual(len(decision["candidates"]), 10)
            self.assertTrue(all(c["reason"] for c in decision["candidates"]))

    def test_single_step_only_touches_one(self):
        gid = self._create(["two_phase", "naive_position"])["gameId"]
        st = self.client.post(f"/api/games/{gid}/step", json={"only": 1}).get_json()["state"]
        self.assertEqual([e["rounds"] for e in st["entries"]], [0, 1])

    def test_play_solves_all_entries(self):
        gid = self._create(["two_phase", "position_entropy", "naive_position"])["gameId"]
        res = self.client.post(f"/api/games/{gid}/play", json={"reveal": 1})
        self.assertEqual(res.status_code, 200, res.get_json())
        st = res.get_json()["state"]
        self.assertTrue(st["solved"])
        for entry in st["entries"]:
            self.assertTrue(entry["solved"], entry["strategy"])
            self.assertLessEqual(entry["rounds"], 20)


class TestHumanDecisions(unittest.TestCase):
    """逐步猜测模式：出招就记录解释，撤销要与历史对齐。"""

    @classmethod
    def setUpClass(cls):
        import app
        cls.client = app.app.test_client()

    def setUp(self):
        self.state = self.client.post("/api/human/games", json={"strategy": "two_phase"}).get_json()

    def test_pending_decision_present(self):
        self.assertIsNotNone(self.state["pendingDecision"])
        self.assertEqual(len(self.state["pendingDecision"]["candidates"]), 10)

    def test_feedback_records_decision_and_undo_rolls_back(self):
        gid = self.state["gameId"]
        guess = self.state["pendingGuess"]
        res = self.client.post(f"/api/human/games/{gid}/feedback",
                               json={"feedback": ["WRONG"] * 10}).get_json()
        self.assertEqual(len(res["state"]["decisions"]), 1)
        self.assertEqual(res["state"]["decisions"][0]["candidates"][0]["chosen"], guess[0])
        undone = self.client.post(f"/api/human/games/{gid}/undo", json={}).get_json()
        self.assertTrue(undone["undone"])
        self.assertEqual(len(undone["state"]["decisions"]), 0)
        self.assertIsNotNone(undone["state"]["pendingDecision"])


class TestStrategyState(unittest.TestCase):
    """同一秘密下可复现：同一猜测得到同一反馈。"""

    def test_sequence_game_reproducible(self):
        secret = make_secrets(1, seed=42)[0]
        a = SequenceGame(secret=list(secret))
        b = SequenceGame(secret=list(secret))
        strategy = create("two_phase", seed=1)
        guess = strategy.next_guess([])
        self.assertEqual(a.submit(guess), b.submit(guess))
        self.assertEqual(len(CANDIDATES), 45)
        self.assertTrue(a.solved or a.rounds == 1)
        self.assertEqual(len(set(secret)), 10, "当前规则下秘密组合互异")


if __name__ == "__main__":
    unittest.main()
