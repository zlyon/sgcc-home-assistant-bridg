import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from sgcc_ha_bridge.click_captcha_solver import (
    ClickCaptchaSolver,
    _MONTAGE_CELL,
    _build_symbol_montage,
    _parse_picks,
    _scale_to_height,
    _symbol_boxes,
)


class ClickCaptchaSolverTextReferenceTestCase(unittest.TestCase):
    def test_graphic_reference_keeps_single_model_call(self):
        ref = io.BytesIO()
        Image.new("RGB", (90, 30), "white").save(ref, format="PNG")
        main = io.BytesIO()
        Image.new("RGB", (330, 236), "white").save(main, format="PNG")

        solver = ClickCaptchaSolver(api_key="test")
        solver._download = Mock(side_effect=[ref.getvalue(), main.getvalue()])
        solver._find_all_icons = Mock(return_value=[(10, 20), (30, 40), (50, 60)])

        coords = solver.solve(
            "data:image/png;base64,ref",
            "data:image/png;base64,main",
            330,
            236,
        )

        self.assertEqual(coords, [(10, 20), (30, 40), (50, 60)])
        self.assertEqual(solver._find_all_icons.call_count, 1)
        self.assertNotIn(
            "reference_text",
            solver._find_all_icons.call_args.kwargs,
        )

    def test_text_reference_skips_reference_image_download(self):
        main = io.BytesIO()
        Image.new("RGB", (330, 236), "white").save(main, format="PNG")

        solver = ClickCaptchaSolver(api_key="test")
        solver._download = Mock(return_value=main.getvalue())
        solver._find_all_icons = Mock(return_value=[(10, 20), (30, 40), (50, 60)])

        coords = solver.solve(
            None,
            "data:image/png;base64,main",
            330,
            236,
            reference_text="爱 诧 畅",
        )

        self.assertEqual(coords, [(10, 20), (30, 40), (50, 60)])
        solver._download.assert_called_once_with("data:image/png;base64,main")
        self.assertEqual(
            solver._find_all_icons.call_args.kwargs["reference_text"],
            ["爱", "诧", "畅"],
        )
        self.assertEqual(solver._find_all_icons.call_count, 3)

    def test_text_reference_consensus_ignores_one_outlier(self):
        samples = [
            [(100, 100), (200, 100), (300, 100)],
            [(102, 98), (201, 102), (298, 101)],
            [(20, 20), (40, 40), (60, 60)],
        ]

        self.assertEqual(
            ClickCaptchaSolver._select_consensus_coordinates(samples, 330, 236),
            [(101, 99), (200, 101), (299, 100)],
        )

    def test_text_reference_without_consensus_is_rejected(self):
        samples = [
            [(20, 20), (40, 40), (60, 60)],
            [(100, 100), (120, 120), (140, 140)],
            [(200, 200), (220, 200), (240, 200)],
        ]

        self.assertEqual(
            ClickCaptchaSolver._select_consensus_coordinates(samples, 330, 236),
            [],
        )

    def test_text_coordinates_are_refined_to_colored_glyph_centers(self):
        image = Image.new("RGB", (330, 236), (120, 170, 200))
        draw = ImageDraw.Draw(image)
        glyphs = [
            (100, 60, 144, 90),
            (247, 158, 292, 188),
            (197, 60, 243, 91),
        ]
        for box in glyphs:
            draw.rectangle(box, fill=(255, 180, 0))
        raw = io.BytesIO()
        image.save(raw, format="PNG")

        refined = ClickCaptchaSolver._refine_text_coordinates(
            raw.getvalue(),
            [(119, 76), (271, 144), (214, 75)],
        )

        self.assertEqual(refined, [(122, 75), (270, 173), (220, 76)])

    @patch("sgcc_ha_bridge.click_captcha_solver.OpenAI")
    def test_text_reference_is_sent_in_order(self, openai):
        response = Mock()
        response.choices = [Mock(message=Mock(content='{"coords":[[0.1,0.2],[0.3,0.4],[0.5,0.6]]}'))]
        openai.return_value.chat.completions.create.return_value = response

        solver = ClickCaptchaSolver(api_key="test")
        coords = solver._find_all_icons(
            [],
            "data:image/png;base64,main",
            300,
            200,
            reference_text=["爱", "诧", "畅"],
        )

        self.assertEqual(coords, [(30, 40), (90, 80), (150, 120)])
        request = openai.return_value.chat.completions.create.call_args.kwargs
        prompt = request["messages"][1]["content"][-1]["text"]
        self.assertLess(prompt.index("“爱”"), prompt.index("“诧”"))
        self.assertLess(prompt.index("“诧”"), prompt.index("“畅”"))


class SymbolSegmentationTestCase(unittest.TestCase):
    """符号分割：坐标由连通域算出，模型只回答编号。"""

    @staticmethod
    def _canvas():
        """造一张「亮底 + 若干个黑色空心方框 + 一个干扰符号」的假验证码图。"""
        image = Image.new("RGB", (320, 200), (222, 176, 120))
        draw = ImageDraw.Draw(image)
        spots = [(30, 30), (140, 30), (250, 30), (80, 140)]
        for x, y in spots:
            draw.rectangle([x, y, x + 24, y + 24], outline=(0, 0, 0), width=3)
        return image, [(x + 12, y + 12) for x, y in spots]

    def test_boxes_are_found_without_numpy(self):
        image, centers = self._canvas()
        boxes = _symbol_boxes(image)

        self.assertEqual(len(boxes), len(centers))
        found = sorted((box["cx"], box["cy"]) for box in boxes)
        for (expect_x, expect_y), (got_x, got_y) in zip(sorted(centers), found):
            self.assertLessEqual(abs(got_x - expect_x), 4)
            self.assertLessEqual(abs(got_y - expect_y), 4)

    def test_boxes_are_numbered_in_reading_order(self):
        image, _ = self._canvas()
        boxes = _symbol_boxes(image)

        # 第一行三个从左到右，最后一个在第二行
        self.assertLess(boxes[0]["cx"], boxes[1]["cx"])
        self.assertLess(boxes[1]["cx"], boxes[2]["cx"])
        self.assertGreater(boxes[3]["cy"], boxes[0]["cy"])

    def test_dark_background_is_rejected(self):
        image = Image.new("RGB", (200, 120), (12, 12, 12))
        self.assertEqual(_symbol_boxes(image), [])

    def test_blank_image_yields_no_boxes(self):
        self.assertEqual(_symbol_boxes(Image.new("RGB", (200, 120), "white")), [])

    def test_montage_has_one_cell_per_symbol(self):
        image, _ = self._canvas()
        boxes = _symbol_boxes(image)
        montage = _build_symbol_montage(image, boxes)

        self.assertIsNotNone(montage)
        self.assertEqual(montage.width, len(boxes) * _MONTAGE_CELL)

    def test_solver_returns_blob_centers_for_picked_numbers(self):
        image, centers = self._canvas()
        boxes = _symbol_boxes(image)

        solver = ClickCaptchaSolver(api_key="test")
        solver._ask_symbol_picks = Mock(return_value=[2, 0, 1])

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        coords = solver._solve_by_symbols(buffer.getvalue(), ["1", "8", "9"])

        self.assertEqual(
            coords,
            [(boxes[2]["cx"], boxes[2]["cy"]),
             (boxes[0]["cx"], boxes[0]["cy"]),
             (boxes[1]["cx"], boxes[1]["cy"])],
        )

    def test_solver_gives_up_when_numbers_are_invalid(self):
        image, _ = self._canvas()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        solver = ClickCaptchaSolver(api_key="test")
        solver._ask_symbol_picks = Mock(return_value=[9, 0, 1])

        self.assertEqual(solver._solve_by_symbols(buffer.getvalue(), ["1", "8", "9"]), [])

    def test_solver_skips_when_too_few_symbols(self):
        image = Image.new("RGB", (200, 120), (222, 176, 120))
        ImageDraw.Draw(image).rectangle([20, 20, 60, 60], outline=(0, 0, 0), width=3)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        solver = ClickCaptchaSolver(api_key="test")
        solver._ask_symbol_picks = Mock(return_value=[0, 0, 0])

        self.assertEqual(solver._solve_by_symbols(buffer.getvalue(), ["1", "8", "9"]), [])
        solver._ask_symbol_picks.assert_not_called()


class ReferenceStripTestCase(unittest.TestCase):
    """参考目标是图片（标题条）时，走同一套「切符号 + 选编号」。"""

    @staticmethod
    def _canvas():
        image = Image.new("RGB", (320, 200), (222, 176, 120))
        draw = ImageDraw.Draw(image)
        for x, y in [(30, 30), (140, 30), (250, 30), (80, 140)]:
            draw.rectangle([x, y, x + 24, y + 24], outline=(0, 0, 0), width=3)
        return image

    @staticmethod
    def _strip():
        """假的标题条：三个字符挤在一条灰底里。"""
        strip = Image.new("RGB", (90, 30), (200, 200, 200))
        draw = ImageDraw.Draw(strip)
        for offset in (10, 40, 70):
            draw.rectangle([offset - 6, 6, offset + 6, 24], outline=(0, 0, 0), width=2)
        return strip

    @staticmethod
    def _png(image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_picks_map_to_blob_centers(self):
        image = self._canvas()
        boxes = _symbol_boxes(image)

        solver = ClickCaptchaSolver(api_key="test")
        solver._ask_strip_picks = Mock(return_value=[3, 0, 2])

        coords = solver._solve_by_reference_strip(
            self._png(image), self._png(self._strip())
        )
        self.assertEqual(
            coords,
            [(boxes[3]["cx"], boxes[3]["cy"]),
             (boxes[0]["cx"], boxes[0]["cy"]),
             (boxes[2]["cx"], boxes[2]["cy"])],
        )

    def test_missing_reference_is_rejected(self):
        solver = ClickCaptchaSolver(api_key="test")
        solver._ask_strip_picks = Mock(return_value=[0, 1, 2])

        self.assertEqual(
            solver._solve_by_reference_strip(self._png(self._canvas()), None), []
        )
        solver._ask_strip_picks.assert_not_called()

    def test_too_few_symbols_skips_model(self):
        image = Image.new("RGB", (200, 120), (222, 176, 120))
        ImageDraw.Draw(image).rectangle([20, 20, 60, 60], outline=(0, 0, 0), width=3)

        solver = ClickCaptchaSolver(api_key="test")
        solver._ask_strip_picks = Mock(return_value=[0, 1, 2])

        self.assertEqual(
            solver._solve_by_reference_strip(
                self._png(image), self._png(self._strip())
            ),
            [],
        )
        solver._ask_strip_picks.assert_not_called()

    def test_invalid_picks_are_rejected(self):
        solver = ClickCaptchaSolver(api_key="test")
        solver._ask_strip_picks = Mock(return_value=[0, 1])

        self.assertEqual(
            solver._solve_by_reference_strip(
                self._png(self._canvas()), self._png(self._strip())
            ),
            [],
        )

    def test_solve_uses_strip_path_when_reference_is_image(self):
        """targets 为空（标题是图片）时，不再让模型直接给坐标。"""
        image = self._canvas()
        strip = self._strip()

        solver = ClickCaptchaSolver(api_key="test")
        solver._download = Mock(side_effect=lambda url: strip_bytes if url == "ref" else image_bytes)
        solver._split_strip = Mock(return_value=["a", "b", "c"])
        solver._solve_by_reference_strip = Mock(return_value=[(1, 2), (3, 4), (5, 6)])
        solver._find_all_icons = Mock(return_value=[(9, 9)])

        strip_bytes = self._png(strip)
        image_bytes = self._png(image)

        coords = solver.solve("ref", "main", image.width, image.height)

        self.assertEqual(coords, [(1, 2), (3, 4), (5, 6)])
        solver._solve_by_reference_strip.assert_called_once()
        solver._find_all_icons.assert_not_called()

    def test_strip_is_upscaled_before_sending(self):
        """标题条只有二十来像素高，必须放大再送模型。"""
        strip = self._strip()
        scaled = _scale_to_height(strip, 200)

        self.assertEqual(scaled.height, 200)
        self.assertGreater(scaled.width, strip.width)
        # 已经很宽的图不应被缩小
        self.assertIs(_scale_to_height(scaled, 100), scaled)


class SymbolPickParsingTestCase(unittest.TestCase):
    def test_valid_picks(self):
        self.assertEqual(_parse_picks('{"picks": [1, 0, 4]}', 5, 3), [1, 0, 4])

    def test_duplicate_picks_are_rejected(self):
        self.assertEqual(_parse_picks('{"picks": [1, 1, 4]}', 5, 3), [])

    def test_out_of_range_picks_are_rejected(self):
        self.assertEqual(_parse_picks('{"picks": [1, 0, 7]}', 5, 3), [])

    def test_wrong_count_is_rejected(self):
        self.assertEqual(_parse_picks('{"picks": [1, 0]}', 5, 3), [])

    def test_text_around_json_is_tolerated(self):
        raw = '好的，结果是 {"picks": [2, 4, 0]} 完毕'
        self.assertEqual(_parse_picks(raw, 5, 3), [2, 4, 0])

    def test_empty_output(self):
        self.assertEqual(_parse_picks("", 5, 3), [])


if __name__ == "__main__":
    unittest.main()
