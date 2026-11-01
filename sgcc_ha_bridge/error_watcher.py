"""Bounded, redacted browser incident records for SGCC failures."""

import functools
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .diag import redact_structure
from .redact import redact_text, redact_url


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


class ErrorWatcher:

    @classmethod
    def init(cls, **kwargs):
        """
        初始化 ErrorWatcher 单例实例。
        在使用 ErrorWatcher 之前应先调用此方法。
        可接受以下关键字参数：
        - root_dir: 保存脱敏事件记录的根目录（默认为当前工作目录）。
        - driver: 用于提取最小现场信息的驱动实例（默认为 None）。
        """
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def instance(cls):
        if cls._instance is None:
            raise ValueError("ErrorWatcher has not been initialized. Call init() first.")
        return cls._instance

    @classmethod
    def watch(cls, func: Optional[Callable] = None, **options) -> Callable:
        """
        装饰器，用于包装函数并记录异常现场。

        用法：
        1. @ErrorWatcher.watch
        2. @ErrorWatcher.watch(driver=my_driver)
        3. @ErrorWatcher.watch(error_type=ValueError)
        """

        def decorator(f):
            @functools.wraps(f)
            def wrapped(*args, **kwargs):
                instance = cls.instance()
                return instance._watch_impl(f, *args, **kwargs)
            return wrapped

        if func is not None:
            # 如果直接传入了函数，则返回包装后的函数
            return decorator(func)
        else:
            # 如果没有传入函数，则返回装饰器
            return decorator

    def set_driver(self, driver):
        """
        设置用于提取最小现场信息的驱动。
        """
        self.driver = driver

    def watch_this(self, func, **options):
        """
        装饰器，用于包装函数并捕获异常。
        """
        error_type = options.get('error_type', Exception)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except error_type as e:
                self.__handle_error(e, options)
                raise
        return wrapper


    # 以下为私有方法

    def __init__(self, **kwargs):
        self.root_dir = Path(kwargs.get("root_dir", os.getcwd()))
        self.root_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root_dir.chmod(0o700)
        except OSError:
            pass
        self.driver = kwargs.get('driver', None)

    _instance = None

    def _watch_impl(self, func, *args, **options):
        error_type = options.get('error_type', Exception)
        try:
            return func(*args, **options)
        except error_type as e:
            self.__handle_error(e, **options)
            raise e

    def capture(self, label: str, error: Exception | str | None = None) -> str | None:
        """Save a bounded, redacted incident record for failure branches.

        Raw HTML and browser logs are never written. Screenshots are opt-in
        because rendered account names/addresses cannot be reliably redacted.
        """
        driver = self.driver
        if not driver:
            logging.error("未设置截图驱动。")
            return None

        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)[:80] or "capture"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        capture_dir = self.root_dir / f"{safe_label}_{timestamp}"
        try:
            capture_dir.mkdir(parents=True, exist_ok=False, mode=0o700)

            try:
                browser_logs = driver.get_log('browser')[-50:]
            except Exception as log_error:
                browser_logs = [{"error": f"{type(log_error).__name__}: {redact_text(log_error)}"}]
            log_levels: dict[str, int] = {}
            for item in browser_logs:
                level = (
                    str(item.get("level") or "UNKNOWN").upper()
                    if isinstance(item, dict)
                    else "UNKNOWN"
                )
                log_levels[level] = log_levels.get(level, 0) + 1

            meta = redact_structure({
                'label': label,
                'error': redact_text(error) if error is not None else '',
                'current_url': redact_url(str(getattr(driver, 'current_url', '') or '')),
                'browser_log_summary': {
                    'count': len(browser_logs),
                    'levels': log_levels,
                },
                'raw_html_saved': False,
                'ui_state_saved': False,
                'screenshot_saved': _env_truthy("SGCC_ERROR_SCREENSHOT"),
            })
            meta_path = capture_dir / "meta.redacted.json"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            meta_path.chmod(0o600)

            # 结构现场：SGCC_ERROR_HTML=1 时额外落一份「登录框 HTML + 浮层清单」。
            # 只留结构不留内容——输入框的值、图片 base64 全部抹掉，避免把手机号、
            # 密码之类写进磁盘。排查「有没有弹浮层 / 选择器还对不对」靠这个就够。
            if _env_truthy("SGCC_ERROR_HTML"):
                try:
                    snapshot = self._ui_snapshot()
                    if snapshot:
                        html_path = capture_dir / "login_box.html"
                        html_path.write_text(
                            snapshot.get("login_box_html", ""),
                            encoding="utf-8",
                        )
                        html_path.chmod(0o600)
                        state_path = capture_dir / "ui-state.json"
                        state_path.write_text(
                            json.dumps(
                                {k: v for k, v in snapshot.items() if k != "login_box_html"},
                                ensure_ascii=False,
                                indent=2,
                            ) + "\n",
                            encoding="utf-8",
                        )
                        state_path.chmod(0o600)
                        meta["raw_html_saved"] = True
                        meta["ui_state_saved"] = True
                        meta_path.write_text(
                            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                except Exception as snapshot_error:
                    logging.warning(
                        "保存登录框结构失败: %s",
                        redact_text(snapshot_error)[:200],
                    )
            if _env_truthy("SGCC_ERROR_SCREENSHOT"):
                screenshot_path = capture_dir / "screenshot.png"
                driver.save_screenshot(str(screenshot_path))
                screenshot_path.chmod(0o600)
                logging.warning(
                    "SGCC_ERROR_SCREENSHOT 已开启；截图可能包含页面可见个人信息，请勿直接上传。"
                )
            self._prune()
            logging.error(f"已保存浏览器现场至 {capture_dir}")
            return str(capture_dir)
        except Exception as e:
            logging.error(f"保存浏览器现场失败: {redact_text(e)}")
            return None

    def _ui_snapshot(self) -> dict | None:
        """收集登录页结构快照（只留结构，抹掉输入值与图片数据）。"""
        driver = self.driver
        if not driver:
            return None
        return driver.execute_script("""
            const box = document.querySelector('#login_box');
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && el.offsetParent !== null;
            };
            const desc = (el) => ({
                tag: el.tagName,
                cls: String(el.className || '').slice(0, 160),
                text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
                visible: visible(el),
            });

            const inputs = Array.from(
                document.querySelectorAll('#login_box input')
            ).map((el) => ({
                placeholder: el.getAttribute('placeholder') || '',
                visible: visible(el),
                value_len: String(el.value || '').length,
            }));

            const captchaNodes = Array.from(document.querySelectorAll(
                '#tCaptchaDyContent, [class*="tencent-captcha"]'
            )).filter(visible).map(desc);

            const modals = Array.from(document.querySelectorAll('div'))
                .filter((el) => visible(el)
                    && /确定|请依次点击|拖动下方|安全验证/.test(el.innerText || '')
                    && el.getBoundingClientRect().width < window.innerWidth * 0.9)
                .slice(0, 8)
                .map(desc);

            let html = box ? box.outerHTML : '';
            html = html.replace(/data:image\\/[a-z+]+;base64,[A-Za-z0-9+/=]+/g, 'data:image/...');
            html = html.replace(/(<input[^>]*?)\\svalue="[^"]*"/g, '$1');
            html = html.slice(0, 40000);

            return {
                current_url: location.href,
                tabs: Array.from(document.querySelectorAll(
                    '#login_box .password_login, #login_box .code_login, #login_box .qr_code'
                )).map(desc),
                inputs: inputs,
                captcha_visible: captchaNodes,
                modal_like: modals,
                errmsgs: Array.from(document.querySelectorAll('.errmsg-tip'))
                    .map(desc),
                sms_form_visible: !!Array.from(
                    document.querySelectorAll("input[placeholder='手机号码']")
                ).some(visible),
                qr_panel_visible: visible(document.querySelector('.sweepCodePic')),
                login_box_html: html,
            };
        """)

    def _prune(self) -> None:
        try:
            keep = max(1, int(os.getenv("SGCC_ERROR_MAX_CAPTURES", "10")))
        except (TypeError, ValueError):
            keep = 10
        captures = sorted(
            (path for path in self.root_dir.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in captures[keep:]:
            shutil.rmtree(path, ignore_errors=True)

    def __handle_error(self, error, **options):
        self.capture(type(error).__name__ or "error", error)
