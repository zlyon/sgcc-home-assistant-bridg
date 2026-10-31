"""国家电网自有图形点选验证码（非腾讯 tcaptcha）的视觉解算。

背景
----
95598 短信登录点「获取验证码」后，弹出的是国网自研的图形点选浮层：
顶部一行「请依次点击：□ 城堡 9」，下面是一张 AI 生成的立体底图，其上散布若干
黑色描边符号，底部是蓝色「确定」按钮。

它的 DOM 与腾讯 tcaptcha 毫无关系，`captcha_selenium.has_captcha_in_browser` 只查
腾讯选择器，因此对这类浮层恒为 False。发码状态机等不到 `captcha`，只会在 pending
上耗到超时，最后以 `phone_code_request_failed` 收场——这就是「发送验证码时不走大模型
校验」的直接原因。

为什么不复用 click_captcha_solver
--------------------------------
`ClickCaptchaSolver` 依赖两个可下载的图片地址（参考图标 URL + 主图 URL）。国网浮层的
标题行和底图常由 canvas / CSS background 渲染，取不到 URL；而且它的标题是一整行三个
字符，不是三个独立图标。这里改为「截屏 + 裁剪 + 放大」，与前端实现方式解耦。

设计约束
--------
* 只负责「识别 + 点击」，是否需要解算由调用方判断；
* 模型配置全部来自 ``const.LLM_*``，不在此处硬编码任何接入点或密钥；
* 单次调用出结果，不做多轮投票，避免无谓消耗。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from typing import Any, Optional

from . import const
from .llm_config import thinking_extra_body

logger = logging.getLogger(__name__)

# 浮层裁剪后的放大倍数。底图里的符号很小，不放大模型容易漏检或认错。
_OVERLAY_ZOOM = 2.5
# 标题行单独裁出来再放大：整页里这三个字符只有十几像素高，模型会把「方框+横线」
# 读成「8」、把「方框+竖线+圆点」读成「山」，于是点错旁边的干扰符号。实测放大 6 倍
# 后连测三轮坐标完全一致。
_TITLE_ZOOM = 6.0
# 标题行在浮层内的相对位置（左, 上, 宽, 高），按国网当前版式取值。
_TITLE_BAND = (0.02, 0.03, 0.70, 0.16)
# 单张图送模型前的最大边长，避免超出模型侧的大小限制。
_MAX_SIDE = 1600
# 输出预算。强制思考的模型（如 GLM-5.3-Flash）把推理 token 也算在输出里，
# 2048 容易被推理吃光而留下空正文，这里放宽到 4096；按实际输出计费，不设成本的
# 模型不会因此多花钱。若换用推理很长的模型仍返回空内容，可再上调。
_MAX_TOKENS = 4096

_SYSTEM_PROMPT = (
    "你是图形验证码识别器。只输出一个 JSON 对象，不要 markdown、不要解释。"
)

_USER_PROMPT = """图1 是验证码浮层的放大截图，尺寸 {w}x{h} 像素，包含标题提示行、大图区（散落多个黑色描边符号）、底部按钮。
图2 是标题提示行的放大图，只显示「请依次点击：」后面的目标符号，清晰得多，请以图2 为准读取目标。

【最重要的一条规则】全程禁止给符号起名字，禁止猜测它是哪个数字或汉字。
这些符号是 AI 生成的变形字形，按常见字符去套必然出错。你只能描述**几何结构**：
- 外轮廓：闭合方框 / 开口边框 / 圆形 / 三角形 / 无框自由线条
- 内部线条：有几条、是横线还是竖线、把内部分成几格
- 圆点：有没有、在内部还是外部、在上/下/左/右/正中
- 其它：开口朝哪个方向、有无环状闭合、有无尖角

必须严格按以下顺序推理，不要跳步：

第一步（读要求）：只看图2，逐个写出目标符号的结构描述（严禁出现"8"、"日"、"山"、"B" 这类字符名）。

第二步（全部枚举）：看整张图1 中间那片大图区域，把**所有**叠加的黑色描边符号逐个找出来并给出坐标（坐标按整张图1 算，不是按图片区域算）。注意：
- 这些符号是描边线条，不是照片里的实物，别把动物、树木、云朵当符号；
- 干扰符号（笑脸最常见）也要枚举出来并给坐标，不要跳过；
- 一般有 4~6 个，容易漏掉贴近画面边缘或被物体挡住的那个；
- 每个符号只记中心点，并用同样格式给出结构描述。

第三步（逐一匹配）：按结构描述逐项比对，只允许结构完全吻合的配对，并按顺序写出**选中的是第几个符号**（picks，从 0 开始，对应 symbols 数组的下标）。
「方框内一条横线分成上下两格」与「两个竖直堆叠的闭合圆环」是**不同**符号；
「方框内 3 条竖线 + 底部圆点」与「方框内 2 条竖线 + 右侧小点」也是**不同**符号。

严格输出 JSON：
{{
  "has_captcha": true/false,
  "kind": "click" | "none",
  "instruction": "标题行完整文字",
  "targets": ["目标1的结构描述", "目标2", "目标3"],
  "symbols": [{{"desc": "符号的结构描述", "x": 0.0, "y": 0.0}}],
  "picks": [2, 0, 3],
  "confirm": [0.0, 0.0]
}}

坐标规则（必须遵守）：
- 一律为相对**整张图1**的比例坐标：整张图1（包含顶部标题行、中间大图区、底部按钮和空白边距）
  的左上角是 (0,0)，右下角是 (1,1)。
- 严禁以「中间那张图片区域」为基准。图片区域只占图1 的中部，按它算出来的坐标会整体偏移，
  直接导致点错。
- 判断方法：图1 最顶边是 (0,0) 那条线，最底边是 (1,1) 那条线。一个符号若在图片区域垂直
  居中，它的 y 大约就是 0.5。
- picks 按标题要求的顺序排列，坐标取自 symbols 里对应的那一项，不要另外手写。
- 只输出画面里真实存在的位置；不存在的字段给 null。
- 若 has_captcha 为 false，则 kind 必须是 "none"，其余字段均为 null。
"""

# 探测浮层用的 JS。以「验证语境文案」为主判据，class/id 命名作为兜底：
# 国网前端改版通常不会改「请依次点击」这几个字，但会改 class 名。
_OVERLAY_JS = """
const vw = window.innerWidth || document.documentElement.clientWidth;
const vh = window.innerHeight || document.documentElement.clientHeight;

const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 150 && rect.height > 120
        && rect.right > 0 && rect.bottom > 0
        && rect.left < vw && rect.top < vh
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity || 1) > 0.1;
};

// 登录表单本身也可能居中、含 logo 图片并出现「验证码」字样，必须排除，
// 否则会把正常表单误判成浮层，裁出来的图交给模型就是白跑一次。
const looksLikeLoginForm = (el) => {
    if (el.querySelector('input[type=password], input[type=tel], input[type=text], input[type=number], textarea')) return true;
    const idOrClass = ((el.id || '') + ' ' + (el.className || '')).toLowerCase();
    return /login|signin|account|form|panel|qr_code/.test(idOrClass);
};

const looksLikeCaptcha = (el) => {
    const text = (el.innerText || '').replace(/\\s+/g, '');
    return /依次点击|请点击图片|请点击下方|安全验证|人机验证|拖动滑块|拖动下方|完成拼图|滑动验证/.test(text);
};

const hasMedia = (el) => {
    if (el.querySelector('canvas, img, svg, video')) return true;
    // 部分验证码大图走 CSS background-image，DOM 里没有真实 img/canvas 节点，
    // 只查标签会漏判，这里把计算样式里的背景图也算作「含图像」。
    const nodes = [el].concat(Array.from(el.querySelectorAll('div, span, i')).slice(0, 30));
    return nodes.some((node) => {
        const bg = window.getComputedStyle(node).backgroundImage || '';
        return bg !== 'none' && /url\\(/.test(bg);
    });
};

const namedCaptcha = (el) => {
    const idOrClass = ((el.id || '') + ' ' + (el.className || '')).toLowerCase();
    return /captcha|verify|yzm|geetest|checkcode|slideverif/.test(idOrClass);
};

const highLayer = (el) => {
    const style = window.getComputedStyle(el);
    const z = Number(style.zIndex);
    return style.position === 'fixed' || (!Number.isNaN(z) && z >= 60);
};

const limit = vw * vh * 0.85;
const hits = [];
for (const el of document.querySelectorAll('div, section, dialog')) {
    if (el === document.body || el === document.documentElement) continue;
    if (!visible(el) || !hasMedia(el) || looksLikeLoginForm(el) || !highLayer(el)) continue;
    if (!looksLikeCaptcha(el) && !namedCaptcha(el)) continue;
    const rect = el.getBoundingClientRect();
    const area = rect.width * rect.height;
    if (area > limit) continue;
    hits.push({
        x: rect.left, y: rect.top, w: rect.width, h: rect.height, area: area,
        cx: rect.left + rect.width / 2,
    });
}
if (!hits.length) return null;

// 优先取居中的（浮层几乎总是居中）；同组里取面积最大的，也就是包含标题行、
// 图片区和底部按钮的最外层框，裁出来的图才完整。
const centered = hits.filter((h) => Math.abs(h.cx - vw / 2) < vw * 0.3);
const pool = centered.length ? centered : hits;
pool.sort((a, b) => b.area - a.area);
const best = pool[0];
return {x: best.x, y: best.y, w: best.w, h: best.h};
"""


# ═══════════════════════════════════════════════════════════════════
# 对外接口
# ═══════════════════════════════════════════════════════════════════

def overlay_box(driver) -> Optional[dict]:
    """返回非腾讯验证码浮层的可视矩形（CSS 像素），没有则返回 None。"""
    try:
        box = driver.execute_script(_OVERLAY_JS)
    except Exception as error:  # 页面切换中执行 JS 可能抛异常，按「无浮层」处理
        logger.debug("浮层探测脚本执行失败: %s", error)
        return None
    if not box:
        return None
    try:
        return {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "w": float(box["w"]),
            "h": float(box["h"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def overlay_present(driver) -> bool:
    """页面当前是否存在国网自有结构的验证码浮层。"""
    return overlay_box(driver) is not None


def solve_overlay_captcha(
    driver,
    max_attempts: Optional[int] = None,
    settle_seconds: float = 2.0,
) -> bool:
    """识别并点掉非腾讯结构的验证码浮层。

    返回 True 表示浮层已消失（含「本来就没有」的情况）。点错时国网会换一张图，
    因此允许重试；每次重试都重新截图，不复用上一轮的坐标。
    """
    attempts = max_attempts
    if attempts is None:
        attempts = _env_int("SGCC_OVERLAY_CAPTCHA_ATTEMPTS", 2, 1, 4)

    for attempt in range(1, attempts + 1):
        box = overlay_box(driver)
        if box is None:
            logger.info("通用验证码：当前没有需要处理的浮层")
            return True

        logger.info(
            "通用验证码：识别到非腾讯浮层 %dx%d @(%d,%d)，第 %d/%d 轮",
            int(box["w"]), int(box["h"]), int(box["x"]), int(box["y"]),
            attempt, attempts,
        )

        screenshot, dpr = _screenshot(driver)
        if screenshot is None:
            logger.warning("通用验证码：截图失败")
            return False

        overlay_png = _crop(
            screenshot, dpr,
            box["x"], box["y"], box["w"], box["h"],
            _OVERLAY_ZOOM,
        )
        title_png = _crop_title(screenshot, box, dpr)
        if overlay_png is None:
            logger.warning("通用验证码：浮层裁剪失败")
            return False

        try:
            plan = _ask_model(overlay_png, title_png)
        except Exception as error:
            logger.error("通用验证码：模型调用失败（%s: %s）", type(error).__name__, error)
            return False

        if not plan or not plan.get("has_captcha"):
            # DOM 探测可能误报（例如页面上还留着上个流程的隐藏容器）。模型看到的是
            # 真实画面，它说没有就没有，按「无需处理」返回，交给调用方继续等发码结果。
            logger.info("通用验证码：模型判定画面中没有验证码，视为探测误报")
            return True

        points = _plan_to_page_points(plan, box)
        if not points:
            logger.warning("通用验证码：模型没有给出可用坐标（原始返回见上）")
            return False

        logger.info(
            "通用验证码：目标=%s，点击 %d 个位置",
            plan.get("targets"), len(points),
        )
        for index, (x, y) in enumerate(points):
            logger.info("通用验证码：点击 #%d -> (%.0f, %.0f)", index + 1, x, y)
            _click(driver, x, y)
            time.sleep(0.35)

        confirm = _ratio_point(plan.get("confirm"), box)
        if confirm:
            logger.info("通用验证码：点击确定 (%.0f, %.0f)", confirm[0], confirm[1])
            _click(driver, confirm[0], confirm[1])
        else:
            logger.info("通用验证码：模型未给出确定按钮位置，跳过")

        time.sleep(settle_seconds)
        if not overlay_present(driver):
            logger.info("通用验证码：浮层已消失，解算完成")
            return True
        logger.warning("通用验证码：浮层仍在，可能答案有误，准备重试")

    logger.error("通用验证码：%d 轮后仍未通过", attempts)
    return False


# ═══════════════════════════════════════════════════════════════════
# 截屏与裁剪
# ═══════════════════════════════════════════════════════════════════

def _screenshot(driver):
    """抓整页截图，返回 (PIL.Image, devicePixelRatio)。"""
    from PIL import Image

    png = driver.get_screenshot_as_png()
    if not png:
        return None, 1.0
    try:
        metrics = driver.execute_script(
            "return {dpr: window.devicePixelRatio || 1};"
        )
        dpr = float((metrics or {}).get("dpr") or 1.0)
    except Exception:
        dpr = 1.0
    try:
        image = Image.open(io.BytesIO(png)).convert("RGB")
    except Exception as error:
        logger.debug("截图解码失败: %s", error)
        return None, dpr
    return image, dpr


def _crop(image, dpr, left, top, width, height, zoom):
    """按页面 CSS 坐标裁剪并放大，返回 PNG 字节。"""
    from PIL import Image

    l = max(0, int(left * dpr))
    t = max(0, int(top * dpr))
    r = min(image.width, int((left + width) * dpr))
    b = min(image.height, int((top + height) * dpr))
    if r - l < 40 or b - t < 40:
        return None
    region = image.crop((l, t, r, b))
    region = _resize(region, zoom)
    buffer = io.BytesIO()
    region.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _crop_title(image, box, dpr):
    """把浮层顶部的标题行单独裁出来放大，作为第二张图。"""
    if box["w"] <= 0 or box["h"] <= 0:
        return None
    rx, ry, rw, rh = _TITLE_BAND
    return _crop(
        image, dpr,
        box["x"] + box["w"] * rx,
        box["y"] + box["h"] * ry,
        box["w"] * rw,
        box["h"] * rh,
        _TITLE_ZOOM,
    )


def _resize(image, zoom):
    """放大到目标倍数，同时把最长边压到 _MAX_SIDE 以内。"""
    from PIL import Image as PILImage

    width = max(1, int(image.width * zoom))
    height = max(1, int(image.height * zoom))
    longest = max(width, height)
    if longest > _MAX_SIDE:
        scale = _MAX_SIDE / float(longest)
        width = max(1, int(width * scale))
        height = max(1, int(height * scale))
    return image.resize((width, height), PILImage.LANCZOS)


# ═══════════════════════════════════════════════════════════════════
# 模型调用
# ═══════════════════════════════════════════════════════════════════

def _client():
    from openai import OpenAI

    api_key = getattr(const, "LLM_API_KEY", "") or ""
    if not api_key:
        raise RuntimeError("LLM_API_KEY 未设置，无法解算图形验证码")
    timeout = float(_env_int("SGCC_LLM_TIMEOUT", 60, 10, 180))
    return OpenAI(
        base_url=getattr(const, "LLM_BASE_URL", None) or None,
        api_key=api_key,
        timeout=timeout,
    )


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _image_part(png: bytes) -> dict:
    """构造一张图片消息块。

    `detail` 是 OpenAI 的嵌套字段，火山方舟认，智谱等厂商不保证认，因此可用
    `SGCC_LLM_IMAGE_DETAIL=off` 去掉；请求被拒时也会自动去掉再试一次。
    """
    image: dict[str, Any] = {"url": _data_uri(png)}
    detail = (os.getenv("SGCC_LLM_IMAGE_DETAIL") or "high").strip().lower()
    if detail not in {"", "off", "none", "false", "0"}:
        image["detail"] = detail
    return {"type": "image_url", "image_url": image}


def _without_detail(content: list[dict]) -> list[dict]:
    """去掉所有图片块里的 detail 字段，厂商不认这个嵌套参数时用。"""
    cleaned: list[dict] = []
    for part in content:
        if part.get("type") == "image_url" and isinstance(part.get("image_url"), dict):
            image = {k: v for k, v in part["image_url"].items() if k != "detail"}
            cleaned.append({"type": "image_url", "image_url": image})
        else:
            cleaned.append(part)
    return cleaned


def _extra_body() -> dict:
    """厂商私有的思考模式开关，实现见 ``llm_config.thinking_extra_body``。"""
    return thinking_extra_body()



def _ask_model(overlay_png: bytes, title_png: Optional[bytes]) -> dict:
    """把浮层截图与标题放大图一起送模型，返回解析后的方案。"""
    from PIL import Image

    width, height = 0, 0
    try:
        with Image.open(io.BytesIO(overlay_png)) as image:
            width, height = image.size
    except Exception:
        pass

    content = [_image_part(overlay_png)]
    if title_png:
        content.append(_image_part(title_png))
    content.append({
        "type": "text",
        "text": _USER_PROMPT.format(w=width or "?", h=height or "?"),
    })

    kwargs: dict[str, Any] = {
        "model": getattr(const, "LLM_MODEL", None) or None,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "max_tokens": _MAX_TOKENS,
    }

    effort = (os.getenv("SGCC_LLM_REASONING_EFFORT") or "").strip()
    extra = _extra_body()
    client = _client()

    # 兼容层差异：不同厂商支持的开关不同，按「最可能成功」的顺序逐级降级，
    # 任何一级成功就返回，全部失败才把最后一个异常抛出。
    attempts: list[dict[str, Any]] = []
    if effort:
        attempts.append({"reasoning_effort": effort, "extra_body": extra})
    attempts.append({"response_format": {"type": "json_object"}, "extra_body": extra})
    attempts.append({"extra_body": extra})

    for options in attempts:
        try:
            response = client.chat.completions.create(**options, **kwargs)
            _log_usage(response)
            return _parse(_content_of(response))
        except Exception as error:
            logger.info("通用验证码：这组参数被拒（%s），继续降级", error)

    # 最后一搏：去掉 image_url.detail 与厂商私有开关，只留最朴素的 OpenAI 字段。
    kwargs["messages"][1]["content"] = _without_detail(content)
    response = client.chat.completions.create(**kwargs)
    _log_usage(response)
    return _parse(_content_of(response))


def _content_of(response) -> str:
    try:
        return response.choices[0].message.content or ""
    except Exception:
        return ""


def _log_usage(response) -> None:
    """打印 token 用量，便于盯住这部分的成本。"""
    try:
        usage = response.usage
        reasoning = 0
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            reasoning = getattr(details, "reasoning_tokens", 0) or 0
        logger.info(
            "通用验证码用量: 输入=%s 输出=%s 思考=%s",
            getattr(usage, "prompt_tokens", "?"),
            getattr(usage, "completion_tokens", "?"),
            reasoning,
        )
    except Exception:
        pass


def _parse(text: str) -> dict:
    """从模型输出里取出 JSON，容忍 ```json 包裹和前后杂字。"""
    if not text:
        logger.warning("通用验证码：模型返回空内容")
        return {}
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    try:
        plan = json.loads(raw)
    except Exception as error:
        logger.warning("通用验证码：JSON 解析失败（%s），原始返回: %s", error, text[:400])
        return {}
    logger.info("通用验证码：模型返回 %s", json.dumps(plan, ensure_ascii=False)[:400])
    return plan if isinstance(plan, dict) else {}


# ═══════════════════════════════════════════════════════════════════
# 坐标换算与点击
# ═══════════════════════════════════════════════════════════════════

def _plan_to_page_points(plan: dict, box: dict) -> list[tuple[float, float]]:
    """把方案转成页面 CSS 坐标，按点击顺序返回。

    优先用 ``picks``（选中符号的下标）去 ``symbols`` 里取坐标：模型直接手写坐标时
    偶发把 x 写反（0.09 写成 0.89），而它枚举符号本身的定位是准的，用下标反查能
    把「选对符号」和「写对坐标」这两件事解耦。
    """
    symbols = plan.get("symbols") or []
    picks = plan.get("picks")

    points: list[tuple[float, float]] = []
    if isinstance(picks, list) and picks:
        for index in picks:
            try:
                item = symbols[int(index)]
            except (IndexError, TypeError, ValueError):
                continue
            point = _ratio_point([item.get("x"), item.get("y")], box)
            if point:
                points.append(point)
        if points:
            return points

    # 回退：模型直接给了 clicks 就用它
    for entry in plan.get("clicks") or []:
        point = _ratio_point(entry, box)
        if point:
            points.append(point)
    return points


def _ratio_point(value, box: dict) -> Optional[tuple[float, float]]:
    """把「相对浮层裁剪图」的比例坐标换算成页面 CSS 坐标。"""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        rx, ry = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0):
        return None
    # 裁剪图是浮层按 zoom 放大后的结果，比例坐标乘回浮层尺寸即可回到页面坐标
    return box["x"] + rx * box["w"], box["y"] + ry * box["h"]


def _click(driver, x: float, y: float) -> None:
    """用 CDP 发真实鼠标事件。

    国网浮层对 `element.click()` 这类合成事件不响应，必须是带坐标的真实按压，
    所以走 Input.dispatchMouseEvent。
    """
    try:
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": float(x), "y": float(y)},
        )
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": float(x),
                "y": float(y),
                "button": "left",
                "clickCount": 1,
            },
        )
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": float(x),
                "y": float(y),
                "button": "left",
                "clickCount": 1,
            },
        )
    except Exception as error:
        logger.warning("通用验证码：CDP 点击失败（%s），回退到 ActionChains", error)
        from selenium.webdriver import ActionChains

        ActionChains(driver).move_by_offset(int(x), int(y)).click().perform()
        ActionChains(driver).move_by_offset(-int(x), -int(y)).perform()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))
