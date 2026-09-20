# -*- coding: utf-8 -*-
"""跑全量单元测试，只输出结果摘要（失败时输出测试名 + 首行断言）。

用法： python tools/run_tests_quiet.py
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(TESTS, pattern="test_*.py", top_level_dir=TESTS)

    class Quiet(unittest.TextTestResult):
        def addFailure(self, test, err):  # noqa: D102
            super().addFailure(test, err)
            self._brief("FAIL", test, err)

        def addError(self, test, err):  # noqa: D102
            super().addError(test, err)
            self._brief("ERROR", test, err)

        @staticmethod
        def _brief(kind, test, err):
            text = str(err[1]).splitlines()
            head = text[0] if text else ""
            print(f"{kind}: {test.id()} -> {head[:200]}", flush=True)

    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=0, resultclass=Quiet)
    result = runner.run(suite)
    total = result.testsRun
    bad = len(result.failures) + len(result.errors)
    print(f"总计 {total} 个用例：通过 {total - bad}，失败 {bad}", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
