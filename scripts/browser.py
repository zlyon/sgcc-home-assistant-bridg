import logging
import os

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService

from config import FetcherConfig
from redact import redact_text


def build_driver(config: FetcherConfig):
    chrome_options = webdriver.ChromeOptions()

    # SGCC_REAL_BROWSER=1 + Docker 时是 attach 到 start-real-browser.sh 已启动的持久 Chromium。
    # 这种模式下 ChromeDriver 只应携带 debuggerAddress；excludeSwitches / prefs 等启动选项
    # 不能应用到已存在浏览器，会导致 invalid argument: unrecognized chrome option。
    real_browser = os.getenv("SGCC_REAL_BROWSER", "false").lower() in ("1", "true", "yes", "on")
    attach_existing_browser = real_browser and ('PYTHON_IN_DOCKER' in os.environ)

    # 可选：环境变量自定义反检测参数
    browser_lang = os.getenv("BROWSER_LANGUAGE", "zh-HK,zh,en-US,en")
    browser_ua = os.getenv("BROWSER_USER_AGENT", "")
    device_scale = os.getenv("BROWSER_DEVICE_SCALE_FACTOR", "2")
    window_size = os.getenv("BROWSER_WINDOW_SIZE", "1158,848")

    if not attach_existing_browser:
        # 基础参数
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--start-maximized")

        # 反检测核心参数（参考 ha-95598）
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        chrome_options.add_argument(f"--lang={browser_lang}")
        chrome_options.add_argument(f"--window-size={window_size}")
        chrome_options.add_argument(f"--force-device-scale-factor={device_scale}")
        chrome_options.add_argument("--high-dpi-support=1")
        if browser_ua:
            chrome_options.add_argument(f"user-agent={browser_ua}")

        chrome_options.add_experimental_option("prefs", {
            "intl.accept_languages": browser_lang,
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        })

    # Docker 环境默认 headless；SGCC_REAL_BROWSER=1 时连接 start-real-browser.sh 启动的持久 Chromium。
    if 'PYTHON_IN_DOCKER' in os.environ:
        chrome_options.binary_location = "/usr/bin/chromium"
        service = ChromeService(executable_path="/usr/bin/chromedriver")
        if real_browser:
            debugger_address = os.getenv("BROWSER_CDP_ADDRESS", "127.0.0.1:9222")
            chrome_options.debugger_address = debugger_address
            logging.info(f"使用持久真实浏览器会话: {debugger_address}")
        else:
            chrome_options.add_argument("--headless=new")

        def _setting_driver(driver):
            # 显式设置窗口大小（解决无头模式下 --window-size 不生效的问题）
            width, height = map(int, window_size.split(','))
            try:
                driver.set_window_size(width, height)
            except Exception as e:
                logging.warning(f"设置窗口大小失败: {e}")
            try:
                driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": int(device_scale),
                    "mobile": False,
                    "dontSetVisibleSize": False
                })
            except Exception as e:
                logging.warning(f"CDP 设置 viewport 失败: {e}")

    else:
        service = _find_chromedriver()
        if real_browser:
            profile_dir = os.getenv("SGCC_BROWSER_PROFILE", os.path.expanduser("~/.local/share/sgcc-electricity-arc/chrome-profile"))
            os.makedirs(profile_dir, exist_ok=True)
            chrome_options.add_argument(f"--user-data-dir={profile_dir}")
            logging.info(f"使用本机持久浏览器 profile: {profile_dir}")
        def _setting_driver(driver):
            driver.maximize_window()

    driver = webdriver.Chrome(options=chrome_options, service=service)
    driver.implicitly_wait(config.DRIVER_IMPLICITY_WAIT_TIME)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)

    _setting_driver(driver)

    return driver


def _find_chromedriver() -> ChromeService:
    """在非 Docker 环境中查找可用的 ChromeDriver。"""
    import shutil

    # 1) 尝试系统 PATH
    path = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
    if path:
        return ChromeService(executable_path=path)

    # 2) 尝试 CloakBrowser 缓存的 chromedriver（如果有）
    for base in [
        os.path.expanduser("~/.cloakbrowser"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), ".cloakbrowser"),
    ]:
        try:
            for root, dirs, files in os.walk(base):
                if "chromedriver.exe" in files or "chromedriver" in files:
                    fname = "chromedriver.exe" if "chromedriver.exe" in files else "chromedriver"
                    path = os.path.join(root, fname)
                    if os.path.isfile(path):
                        return ChromeService(executable_path=path)
                # 最多扫描两级目录
                if len(root) - len(base) > 200:
                    dirs.clear()
        except Exception:
            pass

    # 3) 尝试 Selenium Manager 自动下载
    try:
        return ChromeService()
    except Exception:
        pass

    raise RuntimeError(
        "ChromeDriver 未找到。请安装 ChromeDriver 或运行: pip install chromedriver-binary-auto"
    )


def _attach_existing_browser_enabled() -> bool:
    real_browser = os.getenv("SGCC_REAL_BROWSER", "false").lower() in ("1", "true", "yes", "on")
    return real_browser and ('PYTHON_IN_DOCKER' in os.environ)


def release_driver(driver) -> None:
    if _attach_existing_browser_enabled():
        logging.info("当前为持久 Chromium attach 模式，跳过 driver.quit()，保留登录会话。")
        return
    try:
        driver.quit()
        logging.info("数据抓取完成后浏览器驱动退出。")
    except Exception as e:
        logging.warning(f"浏览器驱动退出失败: {redact_text(e)}")
