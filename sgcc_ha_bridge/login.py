import base64
import logging
import os
import random
import time
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.common.exceptions import TimeoutException

from .captcha_selenium import solve_captcha_in_browser
from .login_guard import LoginFailure, classify_login_failure
from .config import FetcherConfig
from .const import LOGIN_URL
from .error_watcher import ErrorWatcher
from .redact import mask_secret


class SgccLogin:
    def __init__(self, driver, username: str, password: str, config: FetcherConfig):
        self.driver = driver
        self._username = username
        self._password = password
        self.config = config

    @staticmethod
    def is_logged_in_page(driver) -> bool:
        try:
            try:
                if driver.execute_script("return !!sessionStorage.getItem('accessToken')"):
                    return True
            except Exception:
                pass

            current_url = driver.current_url or ""
            if "/osgweb/login" not in current_url and "/osgweb/" in current_url:
                return True
            return bool(driver.execute_script("""
                return !!(
                    document.querySelector('.el-dropdown') ||
                    document.querySelector('.userName') ||
                    document.body.innerText.includes('我的') ||
                    document.body.innerText.includes('安全退出')
                );
            """))
        except Exception:
            return False

    @staticmethod
    def auth_evidence(driver) -> tuple[bool, str]:
        try:
            try:
                if driver.execute_script("return !!sessionStorage.getItem('accessToken')"):
                    return True, "token"
            except Exception:
                pass

            current_url = driver.current_url or ""
            if "/osgweb/login" not in current_url and "/osgweb/" in current_url:
                return True, "url_dom"
            if driver.execute_script("""
                return !!(
                    document.querySelector('.el-dropdown') ||
                    document.querySelector('.userName') ||
                    document.body.innerText.includes('我的') ||
                    document.body.innerText.includes('安全退出')
                );
            """):
                return True, "url_dom"
            return False, "none"
        except Exception:
            return False, "error"

    @ErrorWatcher.watch
    def login(self, phone_code=False, allow_fallback: bool = True) -> bool:
        driver = self.driver
        try:
            self._safe_get(driver, LOGIN_URL, "登录页面")
            if self.is_logged_in_page(driver):
                logging.info(f"打开登录页后检测到已登录态: {driver.current_url}")
                return True
            try:
                WebDriverWait(driver, self.config.DRIVER_IMPLICITY_WAIT_TIME * 3).until(
                    EC.visibility_of_element_located((By.CLASS_NAME, "user"))
                )
            except Exception as wait_error:
                ErrorWatcher.instance().capture("login_page_load_failed", wait_error)
                logging.error(f"登录页面加载失败: {LOGIN_URL}")
                raise LoginFailure("page_load_failed", "登录页面加载失败")
        except Exception as e:
            ErrorWatcher.instance().capture("login_page_open_failed", e)
            logging.error(f"登录页面加载失败: {LOGIN_URL}")
            raise LoginFailure("page_load_failed", "登录页面加载失败")
        logging.info(f"打开登录页面: {LOGIN_URL}。\r")
        time.sleep(self.config.RETRY_WAIT_TIME_OFFSET_UNIT*2)
        # swtich to username-password login page
        # 临时关闭隐式等待，避免与 WebDriverWait 叠加导致超时
        driver.implicitly_wait(0)
        try:
            WebDriverWait(driver, 10).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, 'el-loading-mask')))
        finally:
            driver.implicitly_wait(self.config.DRIVER_IMPLICITY_WAIT_TIME)  # 恢复隐式等待

        element = WebDriverWait(driver, self.config.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'user')))
        driver.execute_script("arguments[0].click();", element)
        logging.info("已找到 'user' 元素。\r")
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
        time.sleep(self.config.RETRY_WAIT_TIME_OFFSET_UNIT)
        # 点击同意按钮
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
        logging.info("已点击同意选项。\r")
        time.sleep(self.config.RETRY_WAIT_TIME_OFFSET_UNIT)
        if phone_code:
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[3]/span')
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[2].send_keys(self._username)
            logging.info(f"已输入用户名: {mask_secret(self._username)}\r")
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[1]/div[2]/div[2]/div/a')
            code = input("请输入手机验证码: ")
            input_elements[3].send_keys(code)
            logging.info(f"已输入验证码: {code}。\r")
            # 点击登录按钮
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[2]/div/button/span')
            time.sleep(self.config.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            logging.info("已点击登录按钮。\r")

            return True
        # 增加判空校验便于测试备用方案
        elif self._password is not None and len(self._password) > 0:
            # 输入用户名和密码
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[0].send_keys(self._username)
            logging.info(f"已输入用户名: {mask_secret(self._username)}\r")
            input_elements[1].send_keys(self._password)
            logging.info("已输入密码: ***MASKED***\r")

            # 点击登录按钮
            self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
            time.sleep(self.config.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
            logging.info("已点击登录按钮。\r")

            # 快速检查：如果已经跳转离开登录页，说明无需验证码，直接成功
            if driver.current_url != LOGIN_URL:
                logging.info("无需验证码登录成功 (已被重定向)。\r")
                return True

            # 会出现点击登录直接失败（账号被限制登录）
            error = self._get_error_message(driver, "//div[@class='errmsg-tip']//span")
            if error is None:
                # 处理腾讯点击验证码
                captcha_passed = solve_captcha_in_browser(driver, max_retries=self.config.RETRY_TIMES_LIMIT)
                if captcha_passed:
                    time.sleep(self.config.RETRY_WAIT_TIME_OFFSET_UNIT)
                    if driver.current_url != LOGIN_URL:
                        logging.info("通过点击验证码登录成功。\r")
                        return True
                    else:
                        error = self._get_error_message(driver, "//div[@class='errmsg-tip']//span")
                        if error:
                            logging.info(f"验证码通过但登录失败: [{error}]\r")
                        else:
                            error = "验证码已通过但仍停留在登录页面。"
                            logging.error(error)
                        category = classify_login_failure(error, captcha_passed=True)
                        ErrorWatcher.instance().capture(f"login_failed_{category}", error)
                        if allow_fallback and self._fallback_login(driver, error):
                            return True
                        raise LoginFailure(category, error)
                else:
                    error = self._get_error_message(driver, "//div[@class='errmsg-tip']//span") or "点击验证码识别在所有重试后均失败。"
                    logging.error("点击验证码识别在所有重试后均失败。")
                    category = classify_login_failure(error, captcha_failed=True)
                    ErrorWatcher.instance().capture(f"login_failed_{category}", error)
                    if allow_fallback and self._fallback_login(driver, error):
                        return True
                    raise LoginFailure(category, error)
            else:
                logging.error(f"登录失败: [{error}]\r")
                category = classify_login_failure(error)
                ErrorWatcher.instance().capture(f"login_failed_{category}", error)
                if allow_fallback and self._fallback_login(driver, error):
                    return True
                raise LoginFailure(category, error)
        raise LoginFailure("login_failed", "登录失败")

    def _safe_get(self, driver, url: str, label: str = "页面", fast: bool = False):
        """Navigate with a bounded page-load timeout.

        95598 pages may keep long-polling or hold subresources open. Selenium's
        default get() waits for full document load and can block the whole fetch
        job. For post-login SPA pages, use JS navigation and stop loading after
        the route/DOM becomes observable.
        """
        logging.info(f"正在打开{label}: {url}")
        if fast:
            old_wait = self.config.DRIVER_IMPLICITY_WAIT_TIME
            try:
                driver.implicitly_wait(0)
                driver.execute_script("window.location.href = arguments[0];", url)
                deadline = int(os.getenv("FAST_NAV_WAIT", 20))
                WebDriverWait(driver, deadline).until(
                    lambda d: url.split('/osgweb')[-1] in (d.current_url or '')
                    or (d.execute_script("return document.readyState") in ("interactive", "complete"))
                )
            except TimeoutException as e:
                logging.warning(f"快速打开{label}等待超时，执行 window.stop() 后继续: {e}")
            except Exception as e:
                logging.warning(f"快速打开{label}异常，继续使用当前页面: {e}")
            finally:
                try:
                    driver.execute_script("window.stop();")
                except Exception as stop_error:
                    logging.warning(f"{label} window.stop() 失败: {stop_error}")
                driver.implicitly_wait(old_wait)
            return

        try:
            driver.get(url)
        except TimeoutException as e:
            logging.warning(f"打开{label}超时({self.config.PAGE_LOAD_TIMEOUT}s)，执行 window.stop() 后继续: {e}")
            try:
                driver.execute_script("window.stop();")
            except Exception as stop_error:
                logging.warning(f"{label} window.stop() 失败: {stop_error}")
        except Exception as e:
            logging.warning(f"打开{label}异常，继续使用当前页面: {e}")

    def _click_button(self, driver, button_search_type, button_search_key):
        '''封装点击函数，仅在元素可点击时点击'''
        click_element = driver.find_element(button_search_type, button_search_key)
        WebDriverWait(driver, self.config.DRIVER_IMPLICITY_WAIT_TIME).until(EC.element_to_be_clickable(click_element))
        driver.execute_script("arguments[0].click();", click_element)
        # 点击后添加微小随机暂停，模拟人工操作
        time.sleep(random.uniform(0.1, 0.5))

    def _get_error_message(self, driver, path) -> Optional[str]:
        """获取错误信息，如果不存在则返回 None"""
        # 关闭隐式等待
        driver.implicitly_wait(0)
        try:
            element = driver.find_element(By.XPATH, path)
            return element.text
        except Exception:
            return None
        finally:
            driver.implicitly_wait(self.config.DRIVER_IMPLICITY_WAIT_TIME)  # 恢复隐式等待

    def _fallback_login(self, driver, reason: str) -> bool:
        """使用备用方案登录"""
        fallback = os.getenv("LOGIN_FALLBACK")
        if fallback == 'qrcode':
            return self._qr_login(driver, reason)
        return False

    def _qr_login(self, driver, reason: str) -> bool:
        logging.info("二维码登录开始")
        # 切换验证码
        element = WebDriverWait(driver, self.config.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'qr_code')))
        driver.execute_script("arguments[0].click();", element)
        logging.info("已切换到二维码模式")

        time.sleep(self.config.RETRY_WAIT_TIME_OFFSET_UNIT)
        # 获取登录二维码
        qrElement = WebDriverWait(driver, self.config.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.visibility_of_element_located((By.XPATH, "//div[@class='sweepCodePic']//img")))
        logging.info("已找到二维码图片元素")

        img_src = qrElement.get_attribute('src')

        if img_src.startswith('data:image'):
            base64_data = img_src.split(',')[1]
            img_screenshot = base64.b64decode(base64_data)
        else:
          logging.info('二维码图片源不是 base64 格式')
          img_screenshot = qrElement.screenshot_as_png

        with open("/data/login_qr_code.png", "wb") as f:
            f.write(img_screenshot)
            logging.info("已将二维码保存到 /data/login_qr_code.png")

        from .notify import UrlLoginQrCodeNotify
        notifyFunc = UrlLoginQrCodeNotify()
        notifyFunc(img_screenshot, reason)
        for i in range(1, self.config.QR_CODE_LOGIN_WAIT_COUNT + 1):
            logging.info(f'二维码登录等待检查[{self.config.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT}] 次数[{i}]')
            time.sleep(self.config.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT)
            if (driver.current_url != LOGIN_URL):
                logging.info("二维码登录成功")
                return True
            else:
                error = self._get_error_message(driver, "//div[@class='sweepCodePic']//div[@class='erwBg']//p")
                if error is not None:
                    logging.error(f'二维码登录错误[{error}]')
                    return False

        logging.warning("二维码登录超时")

        return False

    def _random_delay(self, min_seconds=0.5, max_seconds=3.0):
        """添加随机延迟，使自动化操作更难被检测。"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)
