# -*- coding: utf-8 -*-
"""「逐步猜测」模式测试。

覆盖三块：

1. 反馈解析（字符串 / 列表 / 映射 / 错误输入）；
2. 一致性校验器（规则必然推论，既要能抓到矛盾，也**绝不能误报**真实反馈）；
3. 端到端流程：策略出招 → 人工反馈 → 下一轮，直到全 CORRECT 后落盘并记录。

其中第 2 块的误报检查最关键：测试里让一个「隐藏秘密」用 :func:`core.game.evaluate`
逐轮计算出**真实反馈**喂进接口，如果有任何一条警告，就说明校验器太激进。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core import journal  # noqa: E402
from core.feedback import FeedbackChecker, feedback_to_letters, parse_feedback  # noqa: E402
from core.game import CANDIDATES, CORRECT, MISPLACED, PARTIAL, SEQ_LEN, WRONG, evaluate  # noqa: E402
from core.sequence_game import HumanFeedbackGame  # noqa: E402
from engine.simulator import make_secrets  # noqa: E402


class TestParseFeedback(unittest.TestCase):
    def test_letter_string(self):
        self.assertEqual(parse_feedback("CMPWCMPWCM"), [CORRECT, MISPLACED, PARTIAL, WRONG] * 2 + [CORRECT, MISPLACED])

    def test_with_separators(self):
        self.assertEqual(parse_feedback("C,M,P,W, C,M,P,W,C,M")[0], CORRECT)
        self.assertEqual(parse_feedback("c m p w c m p w c m")[3], WRONG)

    def test_full_names_and_list(self):
        self.assertEqual(parse_feedback(["CORRECT", "MISPLACED"] + ["WRONG"] * 8)[1], MISPLACED)
        self.assertEqual(parse_feedback(["c"] * 10)[0], CORRECT)

    def test_mapping(self):
        value = {i: "C" for i in range(SEQ_LEN)}
        self.assertEqual(parse_feedback(value), [CORRECT] * SEQ_LEN)
        with self.assertRaises(ValueError):
            parse_feedback({0: "C"})

    def test_errors(self):
        with self.assertRaises(ValueError):
            parse_feedback("CMPW")
        with self.assertRaises(ValueError):
            parse_feedback("XXXXXXXXXX")
        with self.assertRaises(ValueError):
            parse_feedback(None)
        with self.assertRaises(ValueError):
            parse_feedback(123)

    def test_round_trip_letters(self):
        letters = "CMPWCMPWCM"
        self.assertEqual(feedback_to_letters(parse_feedback(letters)), letters)


class TestFeedbackChecker(unittest.TestCase):
    def setUp(self):
        # 取 10 个互异组合作为“猜测”，便于构造同轮重复的情形
        self.distinct = list(CANDIDATES[:SEQ_LEN])
        self.dup = list(CANDIDATES[:SEQ_LEN])
        self.dup[5] = self.dup[0]  # 第 1、6 位猜同一个组合

    def test_clean_round_has_no_warning(self):
        checker = FeedbackChecker()
        guess = self.distinct
        feedback = [CORRECT] + [PARTIAL] * (SEQ_LEN - 1)
        self.assertEqual(checker.check(guess, feedback), [])

    def test_duplicate_positions_all_partial(self):
        checker = FeedbackChecker()
        feedback = [PARTIAL] * SEQ_LEN
        self.assertEqual(checker.check(self.dup, feedback), [])

    def test_duplicate_positions_one_correct_rest_misplaced(self):
        checker = FeedbackChecker()
        feedback = [CORRECT] + [PARTIAL] * 4 + [MISPLACED] + [PARTIAL] * 4
        self.assertEqual(checker.check(self.dup, feedback), [])

    def test_duplicate_positions_two_correct_is_rejected(self):
        checker = FeedbackChecker()
        feedback = [CORRECT] + [PARTIAL] * 4 + [CORRECT] + [PARTIAL] * 4
        self.assertTrue(any("多个 CORRECT" in w for w in checker.check(self.dup, feedback)))

    def test_duplicate_correct_with_partial_is_rejected(self):
        checker = FeedbackChecker()
        feedback = [CORRECT] + [PARTIAL] * 4 + [PARTIAL] + [PARTIAL] * 4
        self.assertTrue(any("只能是 MISPLACED" in w for w in checker.check(self.dup, feedback)))

    def test_duplicate_without_correct_must_not_mix(self):
        checker = FeedbackChecker()
        feedback = [MISPLACED] + [PARTIAL] * 4 + [PARTIAL] + [PARTIAL] * 4
        self.assertTrue(any("混用" in w for w in checker.check(self.dup, feedback)))

    def test_cross_round_support_membership(self):
        checker = FeedbackChecker()
        c = self.distinct[0]
        # 第 1 轮：c 被判为 W -> c 不在支持集里
        checker.observe(self.distinct, [WRONG] + [PARTIAL] * (SEQ_LEN - 1))
        guess2 = list(CANDIDATES[20:30])
        guess2[3] = c
        feedback2 = [PARTIAL] * SEQ_LEN
        feedback2[3] = CORRECT
        self.assertTrue(any("不在秘密序列里" in w for w in checker.check(guess2, feedback2)))

        # 反向：c 被判为 M -> 一定在支持集里，后面不可能再判 P/W
        checker2 = FeedbackChecker()
        checker2.observe(self.distinct, [MISPLACED] + [PARTIAL] * (SEQ_LEN - 1))
        feedback3 = [PARTIAL] * SEQ_LEN
        guess3 = list(CANDIDATES[20:30])
        guess3[2] = c
        self.assertTrue(any("一定在秘密序列里" in w for w in checker2.check(guess3, feedback3)))

    def test_placed_positions_are_pinned(self):
        checker = FeedbackChecker()
        c0 = self.distinct[0]
        checker.observe(self.distinct, [CORRECT] + [PARTIAL] * (SEQ_LEN - 1))
        # 同一个组合不可能同时出现在另一位
        guess = list(CANDIDATES[20:30])
        guess[4] = c0
        feedback = [PARTIAL] * SEQ_LEN
        feedback[4] = CORRECT
        self.assertTrue(any("不可能同时在第" in w for w in checker.check(guess, feedback)))
        # 第 1 位已被钉死，不能又变成别的组合
        guess2 = list(CANDIDATES[20:30])
        feedback2 = [PARTIAL] * SEQ_LEN
        feedback2[0] = CORRECT
        self.assertTrue(any("之前已确认是" in w for w in checker.check(guess2, feedback2)))

    def test_all_correct_requires_distinct_guess(self):
        checker = FeedbackChecker()
        self.assertEqual(checker.check(self.distinct, [CORRECT] * SEQ_LEN), [])
        self.assertTrue(any("互异" in w for w in checker.check(self.dup, [CORRECT] * SEQ_LEN)))

    def test_rebuild_from_history(self):
        history = [
            (self.distinct, [CORRECT] + [PARTIAL] * (SEQ_LEN - 1)),
            (list(CANDIDATES[20:30]), [PARTIAL] * SEQ_LEN),
        ]
        checker = FeedbackChecker.from_history(history)
        self.assertEqual(checker.placed, {0: self.distinct[0]})


class TestHumanFeedbackGame(unittest.TestCase):
    def setUp(self):
        self.secret = list(CANDIDATES[:SEQ_LEN])
        self.game = HumanFeedbackGame(label="单测")

    def test_flow_until_solved(self):
        self.assertEqual(self.game.status, "awaiting_guess")
        guess = list(CANDIDATES[10:20])
        self.game.set_pending(guess)
        self.assertEqual(self.game.status, "awaiting_feedback")
        truth = evaluate(guess, self.secret)
        self.assertEqual(self.game.submit_feedback(truth), [])   # 真实反馈不应产生警告
        self.assertEqual(self.game.rounds, 1)
        self.assertFalse(self.game.solved)

        self.game.set_pending(self.secret)
        self.game.submit_feedback([CORRECT] * SEQ_LEN)
        self.assertTrue(self.game.solved)
        self.assertEqual(self.game.status, "solved")
        self.assertIsNotNone(self.game.finished_at)

    def test_pending_guard(self):
        self.game.set_pending(list(CANDIDATES[10:20]))
        with self.assertRaises(RuntimeError):
            self.game.set_pending(list(CANDIDATES[20:30]))
        with self.assertRaises(RuntimeError):
            HumanFeedbackGame().submit_feedback([CORRECT] * SEQ_LEN)

    def test_feedback_length(self):
        self.game.set_pending(list(CANDIDATES[10:20]))
        with self.assertRaises(ValueError):
            self.game.submit_feedback([CORRECT] * 3)

    def test_illegal_guess_rejected(self):
        with self.assertRaises(ValueError):
            self.game.set_pending([(1, 5)] * SEQ_LEN)  # 已被删除的组合

    def test_undo_rebuilds_checker(self):
        g1 = list(CANDIDATES[10:20])
        self.game.set_pending(g1)
        self.game.submit_feedback([CORRECT] + [PARTIAL] * (SEQ_LEN - 1))
        self.assertIn(g1[0], self.game.checker.known_in)
        self.assertTrue(self.game.undo())
        self.assertEqual(self.game.rounds, 0)
        self.assertEqual(self.game.checker.known_in, set())
        self.assertFalse(self.game.undo())

    def test_record_shape(self):
        g1 = list(CANDIDATES[10:20])
        self.game.set_pending(g1)
        self.game.submit_feedback(evaluate(g1, self.secret))
        record = self.game.record(strategy_key="two_phase", params={"count_policy": "skip"})
        self.assertEqual(record["mode"], "human-feedback")
        self.assertFalse(record["secretKnown"])
        self.assertEqual(record["rounds"], 1)
        self.assertEqual(len(record["trace"]), 1)
        self.assertEqual(record["trace"][0]["letters"], feedback_to_letters(evaluate(g1, self.secret)))


class TestJournalHumanGame(unittest.TestCase):
    def test_record_writes_markdown_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_md, old_jsonl, old_flag = journal.LOG_MD, journal.EVENTS_JSONL, config.DOC_LOG_ENABLED
            journal.LOG_MD = Path(tmp) / "log.md"
            journal.EVENTS_JSONL = Path(tmp) / "events.jsonl"
            config.DOC_LOG_ENABLED = True
            try:
                record = {
                    "gameId": "unit-test",
                    "mode": "human-feedback",
                    "label": "单测对局",
                    "strategy": "two_phase",
                    "params": {},
                    "assumeDistinct": True,
                    "solved": True,
                    "rounds": 2,
                    "support": {"knownIn": 10, "knownOut": 3, "placed": 10},
                    "trace": [
                        {"index": 1, "guess": [[1, 1]] * SEQ_LEN, "feedback": [CORRECT] * SEQ_LEN,
                         "letters": "CCCCCCCCCC", "warnings": []},
                    ],
                }
                journal.record_human_game(record)
                text = journal.LOG_MD.read_text(encoding="utf-8")
                self.assertIn("逐步猜测", text)
                self.assertIn("单测对局", text)
                jsonl = journal.EVENTS_JSONL.read_text(encoding="utf-8").strip().splitlines()
                self.assertTrue(any('"human_game"' in line for line in jsonl))
            finally:
                journal.LOG_MD, journal.EVENTS_JSONL, config.DOC_LOG_ENABLED = old_md, old_jsonl, old_flag


class TestHumanApi(unittest.TestCase):
    """端到端：用隐藏秘密算出真实反馈，喂给接口，验证零误报 + 自动落盘。"""

    @classmethod
    def setUpClass(cls):
        import app  # 延迟导入，避免测试收集阶段就启动 Flask
        cls.app_module = app

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._games_dir = config.GAMES_DIR
        self._doc_flag = config.DOC_LOG_ENABLED
        config.GAMES_DIR = Path(self.tmp.name)
        config.DOC_LOG_ENABLED = False          # 测试不要污染 Doc/
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        config.GAMES_DIR = self._games_dir
        config.DOC_LOG_ENABLED = self._doc_flag
        self.tmp.cleanup()

    def _play_as_human(self, strategy_key, secret, max_rounds=25):
        created = self.client.post("/api/human/games", json={"strategy": strategy_key, "label": strategy_key})
        self.assertEqual(created.status_code, 200, created.get_json())
        state = created.get_json()
        gid = state["gameId"]
        self.assertEqual(state["status"], "awaiting_feedback")
        self.assertIsNotNone(state["pendingGuess"])

        for _ in range(max_rounds):
            current = self.client.get(f"/api/human/games/{gid}").get_json()
            guess = [tuple(c) for c in current["pendingGuess"]]
            truth = evaluate(guess, secret)
            res = self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": truth})
            self.assertEqual(res.status_code, 200, res.get_json())
            body = res.get_json()
            self.assertEqual(body["warnings"], [], f"真实反馈被误报：{body['warnings']}")
            if body["state"]["solved"]:
                return gid, body["state"]
            nxt = self.client.post(f"/api/human/games/{gid}/next", json={})
            self.assertEqual(nxt.status_code, 200, nxt.get_json())
        self.fail(f"{strategy_key} 在 {max_rounds} 轮内没有解出")

    def test_full_flow_all_strategies(self):
        secrets = make_secrets(4, 987654)
        for key in ("two_phase", "position_entropy", "adaptive_hybrid", "particle_entropy"):
            for secret in secrets:
                gid, state = self._play_as_human(key, secret)
                self.assertTrue(state["solved"], key)
                self.assertTrue(state["saved"], f"{key} 解出后应自动落盘")
                self.assertTrue(state["savedPath"] and state["savedPath"].endswith(f"{gid}.json"))
                self.assertLessEqual(state["rounds"], 20)

    def test_archive_contains_saved_game(self):
        gid, _ = self._play_as_human("two_phase", make_secrets(1, 5)[0])
        listing = self.client.get("/api/human/archive").get_json()
        self.assertTrue(any(g["gameId"] == gid for g in listing["games"]))
        detail = self.client.get(f"/api/human/archive/{gid}").get_json()
        self.assertEqual(detail["gameId"], gid)
        self.assertEqual(len(detail["trace"]), detail["rounds"])
        self.assertFalse(detail["secretKnown"])
        self.assertEqual(self.client.get("/api/human/archive/not-exist").status_code, 404)

    def test_undo_and_resume(self):
        created = self.client.post("/api/human/games", json={"strategy": "two_phase"}).get_json()
        gid = created["gameId"]
        secret = make_secrets(1, 11)[0]
        guess = [tuple(c) for c in created["pendingGuess"]]
        self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": evaluate(guess, secret)})
        after = self.client.get(f"/api/human/games/{gid}").get_json()
        self.assertEqual(after["rounds"], 1)

        undo = self.client.post(f"/api/human/games/{gid}/undo", json={}).get_json()
        self.assertTrue(undo["undone"])
        self.assertEqual(undo["state"]["rounds"], 0)
        self.assertEqual(undo["state"]["history"], [])
        # 撤销后，被撤销的那一轮猜测回到“等待反馈”，可以直接重录（修正录入错误）
        self.assertEqual(undo["state"]["status"], "awaiting_feedback")
        self.assertEqual(undo["state"]["pendingGuess"], created["pendingGuess"])
        # 再撤一次：已经无轮次可撤，退化为丢弃待反馈的猜测，等策略重新出招
        discarded = self.client.post(f"/api/human/games/{gid}/undo", json={}).get_json()
        self.assertFalse(discarded["undone"])
        self.assertEqual(discarded["state"]["status"], "awaiting_guess")
        self.assertEqual(self.client.post(f"/api/human/games/{gid}/next", json={}).status_code, 200)

    def test_manual_save_before_solving(self):
        created = self.client.post("/api/human/games", json={"strategy": "two_phase", "autoSave": False}).get_json()
        gid = created["gameId"]
        secret = make_secrets(1, 3)[0]
        guess = [tuple(c) for c in created["pendingGuess"]]
        self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": evaluate(guess, secret)})
        saved = self.client.post(f"/api/human/games/{gid}/save", json={})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertTrue(saved.get_json()["saved"])
        self.assertTrue(Path(saved.get_json()["path"]).exists())

    def test_bad_requests(self):
        created = self.client.post("/api/human/games", json={"strategy": "two_phase"}).get_json()
        gid = created["gameId"]
        # 长度不对
        self.assertEqual(
            self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": "CCC"}).status_code, 400)
        # 非法字母
        self.assertEqual(
            self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": "XXXXXXXXXX"}).status_code, 400)
        # 未知策略
        self.assertEqual(self.client.post("/api/human/games", json={"strategy": "nope"}).status_code, 400)
        # 不存在的对局
        self.assertEqual(self.client.get("/api/human/games/not-exist").status_code, 404)
        self.assertEqual(self.client.post("/api/human/games/not-exist/next", json={}).status_code, 404)

    def test_strict_mode_rejects_contradiction(self):
        created = self.client.post(
            "/api/human/games", json={"strategy": "two_phase", "strict": True}).get_json()
        gid = created["gameId"]
        first = [tuple(c) for c in created["pendingGuess"]]
        c0 = first[0]
        # 第 1 轮：第 1 位标为 CORRECT（即宣布 x_1 = c0），其余标为 PARTIAL
        res = self.client.post(f"/api/human/games/{gid}/feedback",
                               json={"feedback": ["C"] + ["P"] * 9})
        self.assertEqual(res.status_code, 200, res.get_json())
        nxt = self.client.post(f"/api/human/games/{gid}/next", json={}).get_json()
        self.assertEqual(tuple(nxt["pendingGuess"][0]), c0, "已锁定的位置应保持同一个组合")
        # 第 2 轮把已经证明在支持集里的 c0 标成 PARTIAL -> 自相矛盾，严格模式应拒绝
        bad = self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": ["P"] * 10})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("矛盾", bad.get_json()["error"])
        # 被拒绝的那一轮不能留在历史里
        state = self.client.get(f"/api/human/games/{gid}").get_json()
        self.assertEqual(state["rounds"], 1)
        self.assertIsNotNone(state["pendingGuess"])

    def test_warn_but_accept_when_not_strict(self):
        created = self.client.post("/api/human/games", json={"strategy": "two_phase"}).get_json()
        gid = created["gameId"]
        first = [tuple(c) for c in created["pendingGuess"]]
        c0 = first[0]
        self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": ["C"] + ["P"] * 9})
        self.client.post(f"/api/human/games/{gid}/next", json={})
        res = self.client.post(f"/api/human/games/{gid}/feedback", json={"feedback": ["P"] * 10})
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertFalse(body["consistent"])
        self.assertTrue(any("一定在秘密序列里" in w for w in body["warnings"]))
        self.assertEqual(body["state"]["rounds"], 2)


if __name__ == "__main__":
    unittest.main()
