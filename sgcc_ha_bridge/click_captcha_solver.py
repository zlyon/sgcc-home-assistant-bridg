"""
点击验证码 LLM 解算器

使用视觉大模型识别腾讯图形或文字点选验证码，按参考目标顺序返回点击坐标。

策略:
1. 图形题下载参考图标条并拆分；文字题直接读取 DOM 中的有序目标
2. 下载主图并以 data URI 发送（确保 LLM 能访问）
3. 文字题使用多次识别一致性和字形中心校正，图形题保持单次识别
"""

import base64
import io
import json
import logging
import math
import os
import re
import statistics
import time
from itertools import permutations
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageDraw
from openai import OpenAI

from . import const
from .llm_config import thinking_extra_body

logger = logging.getLogger(__name__)

_TEXT_REFERENCE_SAMPLE_COUNT = 3
_TEXT_REFERENCE_CONSENSUS_RATIO = 0.06

# 符号分割参数。国网目下的点选题是「亮色照片底图 + 纯黑线描符号」，
# 用亮度阈值就能干净地切出来；切不出来说明换了题型，交给坐标直读兜底。
_MASK_MAX_CHANNEL = 95      # 三通道都低于它才算描边
_MASK_MAX_MEAN = 80
_SYMBOL_MIN_AREA = 60       # 小于这个像素数当作噪点
_SYMBOL_MIN_SIDE = 9        # 描边符号的最短边
_SYMBOL_MIN_BLOBS = 3
_SYMBOL_MAX_BLOBS = 10
_BLOB_WORK_SIDE = 520       # 分割前把图缩到这个尺寸内，纯 Python 也够快
_MONTAGE_CELL = 150         # 单格边长，够大模型才看得清笔画走向
# 腾讯点选把「请依次点击」后面的目标字符渲染成灰底图片条，DOM 里没有文字。
# 这条固定是三个字符，整条只有八十来像素宽，必须放大到与候选格接近的尺度。
_REFERENCE_STRIP_TARGETS = 3
_STRIP_VIEW_HEIGHT = 200


class ClickCaptchaSolver:
    """基于大模型的点击验证码解算器。"""

    def __init__(self,
                 api_key: Optional[str] = None,
                 model: Optional[str] = None,
                 base_url: Optional[str] = None):
        self.api_key = api_key or const.LLM_API_KEY
        self.model = model or const.LLM_MODEL
        self.base_url = base_url or const.LLM_BASE_URL
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("LLM_API_KEY 未设置，验证码解算将失败")
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def solve(self, ref_url: Optional[str], main_url: str,
              main_width: int, main_height: int,
              reference_text: Optional[str] = None) -> List[Tuple[int, int]]:
        """准备图形或文字参考目标，单次调用 LLM 返回有序坐标。"""
        # 1. 准备图形或文字参考目标
        targets = self._normalize_reference_text(reference_text)
        icon_uris = []
        if not targets:
            ref_raw = self._download(ref_url)
            if not ref_raw:
                return []
            icon_uris = self._split_strip(ref_raw)
            if len(icon_uris) < 3:
                return []

        # 2. 下载主图并转为 data URI（确保LLM能访问）
        main_raw = self._download(main_url)
        if not main_raw:
            return []
        main_uri = "data:image/png;base64," + base64.b64encode(main_raw).decode("ascii")

        # 3. 文字点选题优先走「切图定坐标 + 模型只认形状」：坐标由连通域算出，
        #    模型只回答第几号，彻底避开它对小字形定位不准的问题（实测同一张图
        #    直接问坐标，三个点里有一个偏了 167px，够点空）。
        coords: List[Tuple[int, int]] = []
        if targets:
            coords = self._solve_by_symbols(main_raw, targets)
            if len(coords) < len(targets):
                logger.info("符号分割法未给出完整答案，回退坐标直读路径")
        elif icon_uris:
            # 国网这套腾讯点选会把标题里的目标字符渲染成图片（灰底条），DOM 里
            # 取不到文字，reference_text 为空、targets 也为空。此前这种题会直接
            # 让模型给坐标，同样存在上百像素的定位误差；这里把标题条当作第二张
            # 图送进去，模型只做形状对照并报编号，坐标仍由连通域给出。
            coords = self._solve_by_reference_strip(main_raw, ref_raw)
            if len(coords) < _REFERENCE_STRIP_TARGETS:
                logger.info("参考图选号未给出完整答案，回退坐标直读路径")

        # 4. 回退路径：图形题单次识别；文字题用三次结果做一致性裁决
        if targets and len(coords) < len(targets):
            samples = []
            for sample_index in range(_TEXT_REFERENCE_SAMPLE_COUNT):
                sample = self._find_all_icons(
                    icon_uris,
                    main_uri,
                    main_width,
                    main_height,
                    reference_text=targets,
                )
                if len(sample) >= len(targets):
                    samples.append(sample[:len(targets)])
                logger.info(
                    "文字点选识别样本 %s/%s: 坐标数=%s",
                    sample_index + 1,
                    _TEXT_REFERENCE_SAMPLE_COUNT,
                    len(sample),
                )
            coords = self._select_consensus_coordinates(
                samples,
                main_width,
                main_height,
            )
            coords = self._refine_text_coordinates(main_raw, coords)
        elif not targets and len(coords) < _REFERENCE_STRIP_TARGETS:
            coords = self._find_all_icons(
                icon_uris,
                main_uri,
                main_width,
                main_height,
            )
        if len(coords) < 2:
            return []

        # 钳制到主图范围内
        return [
            (max(0, min(x, main_width - 1)), max(0, min(y, main_height - 1)))
            for x, y in coords
        ]

    @staticmethod
    def _normalize_reference_text(reference_text: Optional[str]) -> List[str]:
        if not reference_text:
            return []
        return re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+", reference_text)[:3]

    def _solve_by_symbols(self, main_raw: bytes,
                          targets: List[str]) -> List[Tuple[int, int]]:
        """先切符号再让模型选编号，完全不依赖模型输出坐标。

        直接把整图丢给模型问坐标时，它对小字形的定位误差能到上百像素——实测
        一张 338×247 的点选题，三个目标里有一个偏了 167px，点下去就是空的，
        而「确定」要三次都点中才会点亮，于是整轮验证从来没有提交过。
        这里改用连通域把画面里的黑色线描符号切出来（坐标是算出来的，精确到
        像素），再拼成带编号的图让模型只回答「第几号」。模型不再输出任何坐标，
        定位误差这个误差源就消失了，调用次数也从三次采样降到一到两轮。
        """
        try:
            image = Image.open(io.BytesIO(main_raw)).convert("RGB")
        except Exception:
            return []

        boxes = _symbol_boxes(image)
        need = max(_SYMBOL_MIN_BLOBS, len(targets))
        if len(boxes) < need or len(boxes) > _SYMBOL_MAX_BLOBS:
            logger.info(
                "符号分割：候选 %s 个（需要 %s~%s），改走坐标直读",
                len(boxes), need, _SYMBOL_MAX_BLOBS,
            )
            return []

        montage = _build_symbol_montage(image, boxes)
        if montage is None:
            return []

        picks = self._ask_symbol_picks(montage, targets, len(boxes))
        if len(picks) != len(targets):
            return []

        coords: List[Tuple[int, int]] = []
        for pick in picks:
            if not 0 <= pick < len(boxes):
                return []
            box = boxes[pick]
            coords.append((int(box["cx"]), int(box["cy"])))
        logger.info("符号分割选号: 目标=%s 编号=%s 坐标=%s", targets, picks, coords)
        return coords

    def _solve_by_reference_strip(self, main_raw: bytes,
                                  ref_raw: Optional[bytes]) -> List[Tuple[int, int]]:
        """图形点选题：目标字符本身是图片时，同样走「切符号 + 选编号」。

        腾讯点选把标题里的目标字符渲染成灰底图片条，DOM 里没有文字，
        ``reference_text`` 取不到，于是 targets 为空——这类题以前只能让模型
        直接读整图给坐标，误差和文字题一样大。这里把标题条当作第二张图送进
        模型，让它只做「形状对照 + 报第几号」，坐标依旧由连通域算出。
        """
        if not ref_raw:
            return []
        try:
            image = Image.open(io.BytesIO(main_raw)).convert("RGB")
            strip = Image.open(io.BytesIO(ref_raw)).convert("RGB")
        except Exception as error:
            logger.info("参考图选号：图片解码失败（%s）", error)
            return []

        # 只有「三个目标横排」的条才适合这套提示词。单个图标的参考图宽高比接近 1，
        # 送进去模型会按「三个目标」硬答，白烧一次调用。
        if strip.width < strip.height * 2:
            logger.info(
                "参考图不是横排目标条（%dx%d），改走坐标直读",
                strip.width, strip.height,
            )
            return []

        boxes = _symbol_boxes(image)
        need = max(_SYMBOL_MIN_BLOBS, _REFERENCE_STRIP_TARGETS)
        if len(boxes) < need or len(boxes) > _SYMBOL_MAX_BLOBS:
            logger.info(
                "符号分割：候选 %s 个（需要 %s~%s），改走坐标直读",
                len(boxes), need, _SYMBOL_MAX_BLOBS,
            )
            return []

        montage = _build_symbol_montage(image, boxes)
        if montage is None:
            return []

        picks = self._ask_strip_picks(montage, strip, len(boxes))
        if len(picks) != _REFERENCE_STRIP_TARGETS:
            return []

        coords: List[Tuple[int, int]] = []
        for pick in picks:
            if not 0 <= pick < len(boxes):
                return []
            box = boxes[pick]
            coords.append((int(box["cx"]), int(box["cy"])))
        logger.info("符号分割选号（参考图）: 编号=%s 坐标=%s", picks, coords)
        return coords

    def _ask_strip_picks(self, montage: Image.Image, strip: Image.Image,
                         count: int) -> List[int]:
        """把候选拼图与标题条一起送模型，按标题顺序取编号。

        标题条在整页里只有八十来像素宽，三个字符挤在一起，不放大模型看不清
        笔画；放大到固定高度后与候选格的尺度接近，同形不同形的判断才稳定。
        """
        strip = _scale_to_height(strip, _STRIP_VIEW_HEIGHT)
        prompt = (
            f"图1 是从验证码图片里切出来的 {count} 个候选符号，每格左上角标了编号（#0、#1 …）。\n"
            "图2 是题目的目标条，从左到右就是要点击的顺序；目标可能是字符，也可能是小图标。\n"
            "请在图1 里找出与图2 三个目标形状完全一致的格子，按图2 的顺序给出编号。\n"
            "注意：图1 里可能有形状相近的干扰符号（比如顶部带圆弧的方块 vs 带尖顶的方块、"
            "栅栏、笑脸），不要选错；也要留意只有局部被裁到的边缘符号。\n"
            # 别写 [0, 1, 2] 这类顺序示例，模型会顺着示例去挑前几个格子。
            f'只输出 JSON，形如 {{"picks": [2, 0, 1]}}，方括号里按顺序填 {_REFERENCE_STRIP_TARGETS} 个编号，'
            f"编号互不相同且都在 0~{count - 1} 之间。"
        )
        return self._vote_picks(
            [_image_uri(montage), _image_uri(strip)],
            prompt,
            count,
            _REFERENCE_STRIP_TARGETS,
        )

    def _ask_symbol_picks(self, montage: Image.Image, targets: List[str],
                          count: int) -> List[int]:
        """让模型在带编号的符号拼图里选出目标，返回编号列表。

        单次采样偶尔会把形近的两个字符（比如 1 和 8）顺序对调，所以问两轮：
        两轮一致直接采用，不一致再问一轮取多数，仍无多数就放弃本轮、让上层换图。
        """
        prompt = (
            f"这是从验证码图片里裁出来的 {count} 个符号，每格左上角标了编号（#0、#1 …）。\n"
            f"需要按顺序点击的字符是：{'、'.join(targets)}。\n"
            "这些字符是黑色线描的空心字形，画面里通常还混着干扰符号（猫脸、书本等），"
            "干扰符号不能选。\n"
            "请按要求的顺序，给出每个字符所在格子的编号（从 0 开始，对应 #号）。\n"
            # 别在这里写 [0, 1, 2] 这类顺序示例：模型会顺着示例去挑前几个格子。
            f'只输出 JSON，形如 {{"picks": [2, 0, 1]}}，方括号里按顺序填 {len(targets)} 个编号，'
            f"编号互不相同且都在 0~{count - 1} 之间。"
        )
        return self._vote_picks(
            [_image_uri(montage)], prompt, count, len(targets)
        )

    def _vote_picks(self, uris: List[str], prompt: str, count: int,
                    expected: int) -> List[int]:
        """多轮投票。两轮一致直接采用；只有一轮有效（免费档常 429）也采用。"""
        votes: List[List[int]] = []
        # 只问两轮：免费档是账户级限流，问得越多越容易连续撞 429，反而把
        # 整轮时间耗光。第一轮有答案、第二轮失败时直接采用第一轮。
        for _ in range(2):
            picks = self._request_picks(uris, prompt, count, expected)
            if not picks:
                continue
            votes.append(picks)
            if votes.count(picks) >= 2:
                return picks

        if len(votes) == 1:
            logger.info("符号选号：仅一轮有效结果，直接采用")
            return votes[0]
        if len(votes) >= 2:
            logger.warning("符号选号：多轮结果不一致 %s，放弃本轮", votes)
        return []

    def _request_picks(self, uris: List[str], prompt: str, count: int,
                       expected: int) -> List[int]:
        """问一轮编号，遇到限流退避重试。

        单轮最多问两次：免费档模型 429 很频繁，而外层还有整题重试与换图逻辑，
        这里退避太久只会把整轮时间拖没。
        """
        content = [
            {"type": "image_url", "image_url": {"url": uri}}
            for uri in uris
        ]
        content.append({"type": "text", "text": prompt})

        for attempt in range(2):
            try:
                _throttle_llm()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system",
                         "content": "Output valid JSON only. No markdown, no explanation."},
                        {"role": "user", "content": content},
                    ],
                    max_tokens=512,
                    response_format={"type": "json_object"},
                    extra_body=thinking_extra_body() or None,
                )
                output = response.choices[0].message.content or ""
                logger.info("符号选号响应: %s", output[:200])
                return _parse_picks(output, count, expected)
            except Exception as error:
                logger.warning("符号选号模型调用失败(%s/2): %s", attempt + 1, error)
                if attempt == 1:
                    return []
                # 免费档是账户级限流，一秒级的退避压不住，直接退到 5 秒
                time.sleep(5.0 * (attempt + 1))
        return []

    @staticmethod
    def _select_consensus_coordinates(
        samples: List[List[Tuple[int, int]]],
        main_width: int,
        main_height: int,
    ) -> List[Tuple[int, int]]:
        """选择至少两组相互接近的结果，并按目标顺序取中位数。"""
        if len(samples) < 2:
            logger.warning("文字点选有效识别样本不足，取消本次点击")
            return []

        tolerance = max(
            12.0,
            max(main_width, main_height) * _TEXT_REFERENCE_CONSENSUS_RATIO,
        )
        agreeing_groups = []
        for index, sample in enumerate(samples):
            group = [sample]
            for other_index, other in enumerate(samples):
                if other_index == index or len(other) != len(sample):
                    continue
                distances = [
                    math.dist((x1, y1), (x2, y2))
                    for (x1, y1), (x2, y2) in zip(sample, other)
                ]
                if distances and max(distances) <= tolerance:
                    group.append(other)
            if len(group) >= 2:
                agreeing_groups.append(group)

        if not agreeing_groups:
            logger.warning("文字点选多次识别结果无一致结论，取消本次点击")
            return []

        group = max(agreeing_groups, key=len)
        result = []
        for target_index in range(len(group[0])):
            xs = [sample[target_index][0] for sample in group]
            ys = [sample[target_index][1] for sample in group]
            result.append((
                round(statistics.median(xs)),
                round(statistics.median(ys)),
            ))
        logger.info(
            "文字点选一致性通过: 样本=%s/%s, 容差=%.1fpx",
            len(group),
            len(samples),
            tolerance,
        )
        return result

    @staticmethod
    def _refine_text_coordinates(
        main_raw: bytes,
        coords: List[Tuple[int, int]],
    ) -> List[Tuple[int, int]]:
        """把模型粗坐标吸附到腾讯文字点选题的黄/橙色字形中心。"""
        if not coords:
            return []
        try:
            image = Image.open(io.BytesIO(main_raw)).convert("HSV")
        except Exception:
            return coords

        width, height = image.size
        raw_mask = set()
        pixels = image.load()
        for y in range(height):
            for x in range(width):
                hue, saturation, value = pixels[x, y]
                if 8 <= hue <= 48 and saturation >= 110 and value >= 150:
                    raw_mask.add((x, y))
        if not raw_mask:
            return coords

        # 轻微膨胀，把同一汉字中相邻但不接触的笔画连成一个组件。
        dilated = set(raw_mask)
        for x, y in tuple(raw_mask):
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        dilated.add((nx, ny))

        components = []
        while dilated:
            seed = dilated.pop()
            queue = [seed]
            expanded = [seed]
            for x, y in queue:
                for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if neighbor in dilated:
                        dilated.remove(neighbor)
                        queue.append(neighbor)
                        expanded.append(neighbor)

            points = [point for point in expanded if point in raw_mask]
            if len(points) < 30:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            if max_x - min_x < 10 or max_y - min_y < 10:
                continue
            components.append((
                round((min_x + max_x) / 2),
                round((min_y + max_y) / 2),
            ))

        max_distance = max(28.0, max(width, height) * 0.16)
        candidates = [
            center
            for center in components
            if any(math.dist(center, coord) <= max_distance for coord in coords)
        ]
        if len(candidates) < len(coords):
            return coords

        best_assignment = None
        best_score = float("inf")
        for assignment in permutations(candidates, len(coords)):
            distances = [
                math.dist(coord, center)
                for coord, center in zip(coords, assignment)
            ]
            if max(distances) > max_distance:
                continue
            score = sum(distances)
            if score < best_score:
                best_score = score
                best_assignment = assignment

        if best_assignment is None:
            return coords
        refined = list(best_assignment)
        logger.info(
            "文字点选字形中心校正: 最大移动=%.1fpx",
            max(math.dist(before, after) for before, after in zip(coords, refined)),
        )
        return refined

    def _download(self, url: Optional[str]) -> Optional[bytes]:
        """下载图片，支持 http 和 data URI。"""
        if not url:
            return None
        try:
            if url.startswith("data:"):
                _, encoded = url.split(",", 1)
                try:
                    return base64.b64decode(encoded)
                except Exception:
                    return base64.b64decode(__import__('urllib.parse').unquote(encoded))
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.content
            logger.error(f"下载失败: HTTP {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"下载错误: {e}")
            return None

    def _split_strip(self, raw: bytes) -> List[str]:
        """将参考图标条三等分为独立图标的 data URI。"""
        try:
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
            logger.info(f"参考图标条: {w}x{h}")

            part_w = w // 3
            uris = []
            for i in range(3):
                left = i * part_w
                right = (i + 1) * part_w if i < 2 else w
                icon = img.crop((left, 0, right, h))
                # 放大图标以便LLM看清细节
                icon = icon.resize((icon.width * 3, icon.height * 3), Image.LANCZOS)
                buf = io.BytesIO()
                icon.save(buf, format="PNG")
                uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                uris.append(uri)
                logger.info(f"图标 #{i + 1}: {icon.width}x{icon.height}")
            return uris
        except Exception as e:
            logger.error(f"拆分错误: {e}")
            return []

    def _find_all_icons(self, icon_uris: List[str], main_uri: str,
                        main_width: int, main_height: int,
                        reference_text: Optional[List[str]] = None) -> List[Tuple[int, int]]:
        """单次 API 调用，让 LLM 找到所有有序目标。"""
        content = []
        if reference_text:
            ordered = "、".join(f"“{item}”" for item in reference_text)
            prompt = (
                f"大图（{main_width}×{main_height}像素）是一张文字点选验证码。\n"
                f"请严格按顺序找到文字：{ordered}。\n"
                "文字可能使用艺术字体、旋转、扭曲、缩放或颜色干扰。\n\n"
                '输出JSON：{"coords":[[x1,y1],[x2,y2],[x3,y3]]}\n'
                "其中x、y为每个目标文字中心的比例坐标（0~1）。"
            )
        else:
            prompt = (
                f"大图（{main_width}×{main_height}像素）是一个图标网格。\n"
                "找到3个参考图标(A, B, C)各自在大图网格中的位置。\n"
                "匹配规则：形状和颜色必须一致，空心/实心、线条粗细是关键区分点，允许旋转。\n\n"
                '输出JSON：{"coords":[[xA,yA],[xB,yB],[xC,yC]]}\n'
                "其中x、y为图标中心的比例坐标（0~1）。"
            )
            labels = ["A", "B", "C"]
            for i, uri in enumerate(icon_uris[:3]):
                content.append({"type": "image_url", "image_url": {"url": uri}})
                content.append({"type": "text", "text": f"参考图标{labels[i]}"})

        content.append({"type": "image_url", "image_url": {"url": main_uri}})
        content.append({"type": "text", "text": prompt})

        try:
            _throttle_llm()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Output valid JSON only. No markdown, no explanation."},
                    {"role": "user", "content": content},
                ],
                max_tokens=4096,
                response_format={"type": "json_object"},
                extra_body=thinking_extra_body() or None,
            )
            output = response.choices[0].message.content or ""
            logger.info(f"大模型响应: {output[:400]}")
            return self._parse_coordinates(output, main_width, main_height)
        except Exception as e:
            logger.error(f"大模型错误: {e}")
            return []

    def _parse_coordinates(self, text: str,
                           main_width: int, main_height: int) -> List[Tuple[int, int]]:
        """从LLM返回文本中提取JSON坐标并转为像素。"""
        # 优先尝试JSON解析
        match = re.search(r'\{.*"coords"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                result = []
                for x, y in data["coords"]:
                    x, y = float(x), float(y)
                    if max(x, y) <= 1.5:
                        result.append((round(x * main_width), round(y * main_height)))
                    else:
                        result.append((round(x), round(y)))
                return result
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

        # 回退：正则解析
        coords = []
        paren_pairs = re.findall(r'\(\s*(\d+\.?\d*)\s*[,，]\s*(\d+\.?\d*)\s*\)', text)
        for x_str, y_str in paren_pairs:
            x, y = float(x_str), float(y_str)
            coords.append((x, y))

        if not coords:
            # 宽松匹配
            nums = re.findall(r'(\d+\.?\d+)', text)
            for i in range(0, len(nums) - 1, 2):
                coords.append((float(nums[i]), float(nums[i + 1])))

        result = []
        for x, y in coords[:3]:
            max_val = max(x, y)
            if max_val <= 1.5:
                result.append((round(x * main_width), round(y * main_height)))
            else:
                result.append((round(x), round(y)))
        return result


# ═══════════════════════════════════════════════════════════
# 符号分割：把黑色线描符号切成独立块，坐标由连通域算出
# ═══════════════════════════════════════════════════════════

def _image_uri(image: Image.Image) -> str:
    """把 PIL 图转成 data URI，供模型直接读取。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


# 免费档视觉模型按「每分钟请求数」限流，连着打几乎必 429（实测线上日志里
# 一分钟内能撞三次）。每次请求前强制间隔这么久，把突发流量抹平。
_LLM_MIN_INTERVAL = 2.0
_llm_last_call_at = 0.0


def _throttle_llm() -> None:
    """在发起模型请求前拉开最小间隔。"""
    global _llm_last_call_at
    now = time.monotonic()
    wait = _LLM_MIN_INTERVAL - (now - _llm_last_call_at)
    if wait > 0:
        time.sleep(wait)
        now = time.monotonic()
    _llm_last_call_at = now


def _scale_to_height(image: Image.Image, height: int) -> Image.Image:
    """等比放大到指定高度；本来就更高时原样返回，不做无意义的缩放。"""
    if image.height <= 0 or image.height >= height:
        return image
    factor = height / float(image.height)
    return image.resize(
        (max(1, int(image.width * factor)), height),
        Image.LANCZOS,
    )


def _symbol_boxes(image: Image.Image) -> List[dict]:
    """找出图中所有黑色线描符号，返回它们的外接框与中心点（原图像素坐标）。

    阈值分割 + 4 邻域连通域，先对掩码做一次 1 像素膨胀，把断开的笔画接上。
    亮度太高或太低（纯黑照片）时直接返回空，让调用方回退到坐标直读。
    """
    width, height = image.size
    if width <= 0 or height <= 0:
        return []

    work = image
    ratio = 1.0
    if max(width, height) > _BLOB_WORK_SIDE:
        ratio = _BLOB_WORK_SIDE / float(max(width, height))
        work = image.resize(
            (max(1, int(width * ratio)), max(1, int(height * ratio))),
            Image.LANCZOS,
        )

    w, h = work.size
    pixels = work.load()
    dark = set()
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y][:3]
            if max(r, g, b) <= _MASK_MAX_CHANNEL and (r + g + b) // 3 <= _MASK_MAX_MEAN:
                dark.add((x, y))

    if not dark:
        return []
    # 大面积暗色说明底图本身就是深色照片，阈值切不出符号，别硬凑候选。
    if len(dark) > w * h * 0.35:
        logger.info("符号分割：暗像素占 %.0f%%，底图过暗，放弃分割",
                    len(dark) * 100.0 / (w * h))
        return []

    mask = set(dark)
    for x, y in dark:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                mask.add((x + dx, y + dy))

    boxes = []
    remaining = set(mask)
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        blob = [seed]
        while stack:
            x, y = stack.pop()
            for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbor in remaining:
                    remaining.discard(neighbor)
                    stack.append(neighbor)
                    blob.append(neighbor)

        core = [point for point in blob if point in dark]
        if len(core) < _SYMBOL_MIN_AREA:
            continue
        xs = [point[0] for point in core]
        ys = [point[1] for point in core]
        box_w = max(xs) - min(xs)
        box_h = max(ys) - min(ys)
        if box_w < _SYMBOL_MIN_SIDE or box_h < _SYMBOL_MIN_SIDE:
            continue
        # 面积占满整图的块是背景，不是符号
        if box_w * box_h > w * h * 0.6:
            continue
        boxes.append({
            "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
            "cx": (min(xs) + max(xs)) // 2,
            "cy": (min(ys) + max(ys)) // 2,
            "area": len(core),
        })

    if not boxes:
        return []

    # 按阅读顺序（先分行、再按 x）编号，让编号看起来稳定
    band = max(18, h // 6)
    boxes.sort(key=lambda box: (box["cy"] // band, box["cx"]))

    # 换算回原图坐标
    if ratio != 1.0:
        for box in boxes:
            for key in ("x0", "y0", "x1", "y1", "cx", "cy"):
                box[key] = int(round(box[key] / ratio))
    return boxes


def _build_symbol_montage(image: Image.Image, boxes: List[dict]) -> Optional[Image.Image]:
    """把每个符号裁出来拼成一张带编号的图，供模型比对。"""
    if not boxes:
        return None

    pad = max(3, int(max(image.size) * 0.02))
    cols = min(len(boxes), 6)
    rows = (len(boxes) + cols - 1) // cols
    cell = _MONTAGE_CELL
    label_height = 22
    canvas = Image.new("RGB", (cols * cell, rows * (cell + label_height)), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    for index, box in enumerate(boxes):
        left = max(0, box["x0"] - pad)
        top = max(0, box["y0"] - pad)
        right = min(image.width, box["x1"] + pad)
        bottom = min(image.height, box["y1"] + pad)
        if right - left < 2 or bottom - top < 2:
            continue
        crop = image.crop((left, top, right, bottom))
        factor = min((cell - 10) / float(crop.width), (cell - 10) / float(crop.height))
        crop = crop.resize(
            (max(1, int(crop.width * factor)), max(1, int(crop.height * factor))),
            Image.LANCZOS,
        )
        offset_x = (index % cols) * cell
        offset_y = (index // cols) * (cell + label_height)
        canvas.paste(
            crop,
            (offset_x + (cell - crop.width) // 2, offset_y + (cell - crop.height) // 2),
        )
        draw.text((offset_x + 4, offset_y + cell + 4), f"#{index}", fill=(0, 0, 0))
        draw.rectangle(
            [offset_x, offset_y, offset_x + cell - 1, offset_y + cell - 1],
            outline=(210, 210, 210),
        )
    return canvas


def _parse_picks(text: str, count: int, expected: int) -> List[int]:
    """从模型输出里取出编号列表，并校验可点性。"""
    if not text:
        return []

    picks: List[int] = []
    match = re.search(r'"picks"\s*:\s*\[([^\]]*)\]', text, re.DOTALL)
    if match:
        picks = [int(value) for value in re.findall(r"-?\d+", match.group(1))]
    if not picks:
        # 回退：整段文本里只出现了少量整数时，按出现顺序取
        numbers = [int(value) for value in re.findall(r"-?\d+", text)]
        if len(numbers) <= count:
            picks = numbers

    if len(picks) != expected:
        logger.warning("符号选号：返回 %s 个编号，期望 %s 个", len(picks), expected)
        return []
    if len(set(picks)) != len(picks):
        logger.warning("符号选号：编号重复 %s", picks)
        return []
    if any(pick < 0 or pick >= count for pick in picks):
        logger.warning("符号选号：编号越界 %s（候选 %s 个）", picks, count)
        return []
    return picks
