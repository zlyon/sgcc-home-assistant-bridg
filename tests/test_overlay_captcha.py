"""overlay_captcha 的离线测试：坐标换算、方案解析、浮层探测的容错。

这些用例不启动浏览器、不调用模型，跑起来没有外部依赖。
"""

import unittest
from unittest.mock import patch

from sgcc_ha_bridge import overlay_captcha as oc

BOX = {"x": 455.0, "y": 158.0, "w": 390.0, "h": 430.0}


class RatioPointTestCase(unittest.TestCase):
    def test_corners_map_to_overlay_corners(self):
        self.assertEqual(oc._ratio_point([0, 0], BOX), (455.0, 158.0))
        self.assertEqual(oc._ratio_point([1, 1], BOX), (845.0, 588.0))

    def test_center_maps_to_overlay_center(self):
        self.assertEqual(oc._ratio_point([0.5, 0.5], BOX), (650.0, 373.0))

    def test_out_of_range_is_rejected(self):
        self.assertIsNone(oc._ratio_point([1.2, 0.5], BOX))
        self.assertIsNone(oc._ratio_point([-0.1, 0.5], BOX))

    def test_malformed_input_is_rejected(self):
        for value in (None, [], [0.5], ["x", 0.5], "abc"):
            self.assertIsNone(oc._ratio_point(value, BOX))


class PlanToPointsTestCase(unittest.TestCase):
    def test_picks_resolve_through_symbols(self):
        plan = {
            "symbols": [
                {"desc": "数字一", "x": 0.10, "y": 0.66},
                {"desc": "数字七", "x": 0.80, "y": 0.46},
                {"desc": "票据形", "x": 0.70, "y": 0.56},
            ],
            "picks": [0, 1, 2],
        }
        points = oc._plan_to_page_points(plan, BOX)
        self.assertEqual(
            points,
            [
                (455.0 + 0.10 * 390, 158.0 + 0.66 * 430),
                (455.0 + 0.80 * 390, 158.0 + 0.46 * 430),
                (455.0 + 0.70 * 390, 158.0 + 0.56 * 430),
            ],
        )

    def test_picks_keep_title_order(self):
        plan = {
            "symbols": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}, {"x": 0.3, "y": 0.3}],
            "picks": [2, 0, 1],
        }
        points = oc._plan_to_page_points(plan, BOX)
        self.assertEqual(
            points,
            [(455.0 + 0.3 * 390, 158.0 + 0.3 * 430),
             (455.0 + 0.1 * 390, 158.0 + 0.1 * 430),
             (455.0 + 0.2 * 390, 158.0 + 0.2 * 430)],
        )

    def test_falls_back_to_clicks_without_picks(self):
        plan = {"clicks": [[0.2, 0.3], [0.4, 0.5]]}
        self.assertEqual(
            oc._plan_to_page_points(plan, BOX),
            [(455.0 + 0.2 * 390, 158.0 + 0.3 * 430),
             (455.0 + 0.4 * 390, 158.0 + 0.5 * 430)],
        )

    def test_invalid_clicks_are_dropped(self):
        plan = {"clicks": [[1.8, 0.3], [0.2, 0.3], None, ["x", 1]]}
        self.assertEqual(len(oc._plan_to_page_points(plan, BOX)), 1)

    def test_out_of_range_picks_are_skipped(self):
        plan = {"symbols": [{"x": 0.1, "y": 0.1}], "picks": [0, 9, 99]}
        self.assertEqual(len(oc._plan_to_page_points(plan, BOX)), 1)

    def test_empty_plan_yields_no_points(self):
        self.assertEqual(oc._plan_to_page_points({}, BOX), [])


class ParseTestCase(unittest.TestCase):
    def test_plain_json(self):
        self.assertTrue(oc._parse('{"has_captcha": true}')["has_captcha"])

    def test_fenced_json(self):
        self.assertTrue(oc._parse('```json\n{"has_captcha": true}\n```')["has_captcha"])

    def test_json_with_surrounding_text(self):
        plan = oc._parse('识别结果如下 {"has_captcha": false} 以上')
        self.assertFalse(plan["has_captcha"])

    def test_empty_response(self):
        self.assertEqual(oc._parse(""), {})

    def test_broken_json(self):
        self.assertEqual(oc._parse("{not json"), {})


class OverlayBoxTestCase(unittest.TestCase):
    class _Driver:
        def __init__(self, value=None, error=None):
            self.value = value
            self.error = error

        def execute_script(self, *args, **kwargs):
            if self.error:
                raise self.error
            return self.value

    def test_returns_none_when_no_overlay(self):
        self.assertIsNone(oc.overlay_box(self._Driver(None)))

    def test_returns_none_on_script_error(self):
        self.assertIsNone(oc.overlay_box(self._Driver(error=RuntimeError("no such window"))))

    def test_parses_rect(self):
        box = oc.overlay_box(self._Driver({"x": 1, "y": 2, "w": 3, "h": 4}))
        self.assertEqual(box, {"x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0})

    def test_ignores_malformed_rect(self):
        self.assertIsNone(oc.overlay_box(self._Driver({"x": "a"})))


class PromptTestCase(unittest.TestCase):
    """提示词里这几条是实测调出来的，改坏了准确率会明显下降。"""

    def setUp(self):
        self.prompt = oc._USER_PROMPT.format(w=975, h=1075)

    def test_forbids_naming_symbols(self):
        self.assertIn("禁止给符号起名字", self.prompt)

    def test_warns_against_image_area_origin(self):
        self.assertIn("严禁以「中间那张图片区域」为基准", self.prompt)

    def test_requires_pick_index(self):
        self.assertIn("picks", self.prompt)

    def test_can_be_formatted(self):
        self.assertIn("975", self.prompt)


class EnvIntTestCase(unittest.TestCase):
    def test_default_when_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(oc._env_int("MISSING", 2, 1, 4), 2)

    def test_clamped_to_range(self):
        with patch.dict("os.environ", {"X": "99"}, clear=True):
            self.assertEqual(oc._env_int("X", 2, 1, 4), 4)
        with patch.dict("os.environ", {"X": "-5"}, clear=True):
            self.assertEqual(oc._env_int("X", 2, 1, 4), 1)

    def test_invalid_value_falls_back(self):
        with patch.dict("os.environ", {"X": "abc"}, clear=True):
            self.assertEqual(oc._env_int("X", 3, 1, 4), 3)


class VendorCompatTestCase(unittest.TestCase):
    """各厂商 OpenAI 兼容层的差异：图文块字段与思考开关。"""

    def test_image_part_has_detail_by_default(self):
        part = oc._image_part(b"png")
        self.assertEqual(part["type"], "image_url")
        self.assertEqual(part["image_url"]["detail"], "high")
        self.assertTrue(part["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_part_can_drop_detail(self):
        with patch.dict("os.environ", {"SGCC_LLM_IMAGE_DETAIL": "off"}, clear=True):
            part = oc._image_part(b"png")
        self.assertNotIn("detail", part["image_url"])

    def test_without_detail_strips_only_images(self):
        content = [
            oc._image_part(b"png"),
            {"type": "text", "text": "hi"},
        ]
        cleaned = oc._without_detail(content)
        self.assertNotIn("detail", cleaned[0]["image_url"])
        self.assertEqual(cleaned[1], {"type": "text", "text": "hi"})

    def test_extra_body_disables_thinking(self):
        with patch.dict("os.environ", {"SGCC_LLM_THINKING": "disabled"}, clear=True):
            self.assertEqual(oc._extra_body(), {"thinking": {"type": "disabled"}})
        with patch.dict("os.environ", {"SGCC_LLM_THINKING": "none"}, clear=True):
            self.assertEqual(oc._extra_body(), {"thinking": {"type": "disabled"}})
        with patch.dict("os.environ", {"SGCC_LLM_THINKING": "enabled"}, clear=True):
            self.assertEqual(oc._extra_body(), {"thinking": {"type": "enabled"}})

    def test_extra_body_empty_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(oc._extra_body(), {})


if __name__ == "__main__":
    unittest.main()
