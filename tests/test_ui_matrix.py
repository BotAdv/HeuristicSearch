# -*- coding: utf-8 -*-
"""候选组合矩阵（10×10）与状态着色的守门测试。

矩阵本身跑在浏览器里（`static/js/combogrid.js`），Python 侧能且只能守住三类**契约**；
它们恰好也是最容易被后续改动悄悄破坏的东西：

1. **数据契约**：`/api/meta` 必须继续提供矩阵需要的字段，且
   "10 个被删除的组合都是 a<b" —— 这是「10 个组合在表里占 20 格灰色」的前提；
2. **装配契约**：页面上有矩阵挂载点，且矩阵组件脚本必须在页面脚本**之前**加载；
3. **配色契约**：文档与用户要求的颜色映射（C 绿 / M 蓝 / P 紫 / W 红）写在 CSS 变量里，
   改色必须同步改这里，避免"文档说蓝、界面是橙"。
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.game import ALL_COMBOS, CANDIDATES, OBJECTS, REMOVED_COMBOS, SEQ_LEN  # noqa: E402

STATIC = config.BASE_DIR / "static"
TEMPLATES = config.BASE_DIR / "templates"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMatrixDataContract(unittest.TestCase):
    """矩阵尺寸与灰色格数量的前提条件。"""

    def test_matrix_is_10x10(self):
        self.assertEqual(len(OBJECTS), 10, "矩阵是 10×10，对象集合必须是 10 个")
        self.assertEqual(SEQ_LEN, 10)

    def test_all_removed_combos_are_off_diagonal(self):
        """被删除的 10 个组合必须都是 a<b：这样它们在表里各占 2 格 = 20 格灰色。"""
        for a, b in REMOVED_COMBOS:
            self.assertNotEqual(a, b, f"({a},{b}) 删了对角线格，灰色格数就不再是 20")
            self.assertLess(a, b, f"({a},{b}) 不是规范形式 (a<b)")
        self.assertEqual(len(REMOVED_COMBOS), 10)
        cells = sum(1 if a == b else 2 for a, b in REMOVED_COMBOS)
        self.assertEqual(cells, 20, "10×10 表里的灰色格数应为 20")

    def test_candidate_count_and_diagonals(self):
        self.assertEqual(len(ALL_COMBOS), 55)
        self.assertEqual(len(CANDIDATES), 45)
        diagonals = [c for c in ALL_COMBOS if c[0] == c[1]]
        self.assertEqual(len(diagonals), 10, "对角线 10 格，且都不在被删除集合里")
        for c in diagonals:
            self.assertIn(c, CANDIDATES)

    def test_meta_exposes_matrix_inputs(self):
        import app

        client = app.app.test_client()
        meta = client.get("/api/meta").get_json()
        for key in ("objects", "allCombos", "removedCombos", "candidates", "candidateCount"):
            self.assertIn(key, meta, f"/api/meta 缺少矩阵需要的 {key}")
        self.assertEqual(list(meta["objects"]), list(OBJECTS))
        self.assertEqual(len(meta["allCombos"]), 55)
        self.assertEqual(len(meta["removedCombos"]), 10)
        self.assertEqual(meta["candidateCount"], 45)
        # 前端用 comboKey(c) = "a-b" 判灰，这里核对序列化形态
        for combo in meta["removedCombos"]:
            self.assertEqual(len(combo), 2)
            self.assertLess(combo[0], combo[1])


class TestMatrixWiring(unittest.TestCase):
    """脚本加载顺序与挂载点。"""

    def test_pages_have_matrix_host(self):
        for name in ("index.html", "human.html"):
            text = read(TEMPLATES / name)
            self.assertIn('id="comboMatrix"', text, f"{name} 缺少矩阵挂载点")
            self.assertIn("palette-wrap", text, f"{name} 缺少横向滚动容器")

    def test_base_loads_combogrid_before_page_scripts(self):
        base = read(TEMPLATES / "base.html")
        self.assertIn("js/combogrid.js", base)
        self.assertIn("js/api.js", base)
        # combogrid.js 必须排在 {% block scripts %} 之前，页面脚本才能用到这些函数
        self.assertLess(base.index("js/combogrid.js"), base.index("{% block scripts %}"))

    def test_combogrid_defines_expected_api(self):
        js = read(STATIC / "js" / "combogrid.js")
        for fn in ("canonicalCombo", "comboKey", "accumulateMarks", "overlayMarks",
                   "renderComboMatrix", "removedKeySet"):
            self.assertRegex(js, rf"function\s+{fn}\s*\(", f"combogrid.js 缺少 {fn}()")
        for kind in ("CORRECT", "MISPLACED", "PARTIAL", "WRONG"):
            self.assertIn(kind, js)
        # 优先级表必须覆盖四种反馈，否则 accumulateMarks 会漏掉标记
        ranks = re.search(r"const MARK_RANK = \{([^}]*)\}", js)
        self.assertIsNotNone(ranks, "找不到 MARK_RANK")
        for kind in ("CORRECT", "MISPLACED", "PARTIAL", "WRONG"):
            self.assertIn(kind, ranks.group(1))

    def test_both_pages_use_the_matrix(self):
        for name in ("sandbox.js", "human.js"):
            js = read(STATIC / "js" / name)
            self.assertIn("renderComboMatrix(", js, f"{name} 没有调用矩阵组件")
            self.assertIn("accumulateMarks(", js, f"{name} 没有累积反馈标记")


class TestFeedbackColors(unittest.TestCase):
    """配色映射：CORRECT 绿 / MISPLACED 蓝 / PARTIAL 紫 / WRONG 红。"""

    EXPECTED = {"--c": "#2ecc71", "--m": "#4d9fff", "--p": "#a06bff", "--w": "#ff5c5c"}

    def setUp(self):
        self.css = read(STATIC / "css" / "app.css")

    def test_variables(self):
        for name, value in self.EXPECTED.items():
            match = re.search(rf"^\s*{re.escape(name)}:\s*([^;]+);", self.css, re.M)
            self.assertIsNotNone(match, f"app.css 缺少 {name}")
            self.assertEqual(match.group(1).strip(), value,
                             f"{name} 应为 {value}（改色请同步 Doc/12 与本测试）")

    def test_matrix_mark_classes_exist(self):
        for kind in ("correct", "misplaced", "partial", "wrong"):
            self.assertIn(f".cg-cell.mark-{kind}", self.css, f"缺少 .cg-cell.mark-{kind} 样式")

    def test_removed_and_picked_classes_exist(self):
        for cls in (".cg-removed", ".cg-picked", ".cg-diag", ".cg-table"):
            self.assertIn(cls, self.css, f"缺少 {cls} 样式")

    def test_legend_uses_same_variables(self):
        """页脚与图例必须引用同一组变量，避免出现两套颜色。"""
        base = read(TEMPLATES / "base.html")
        self.assertIn("fb-c", base)
        self.assertRegex(self.css, r"\.fb-c\s*\{\s*color:\s*var\(--c\)")


if __name__ == "__main__":
    unittest.main()
