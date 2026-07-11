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
                'screenshot_saved': _env_truthy("SGCC_ERROR_SCREENSHOT"),
            })
            meta_path = capture_dir / "meta.redacted.json"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            meta_path.chmod(0o600)
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
