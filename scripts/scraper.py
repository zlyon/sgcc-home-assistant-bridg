"""Path B scraper: read decrypted SGCC data from live Vue/Vuex state.

The caller owns the Selenium driver.  This module never quits/closes the driver
and assumes the browser is already authenticated.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict
from typing import Any, Optional

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from const import BALANCE_URL, ELECTRIC_USAGE_URL
from model import AccountData, mask_account_no
from parser import merge_account_data, parse_account_data
import vue_state


class Scraper:
    """Scrape AccountData from an attached, logged-in Selenium driver."""

    def __init__(self, driver, wait_seconds: int = 12, settle_seconds: float = 3.0):
        self.driver = driver
        self.wait_seconds = wait_seconds
        self.settle_seconds = settle_seconds

    def fetch_all(self, max_accounts: Optional[int] = None) -> list[AccountData]:
        """Navigate balance/usage views and return one AccountData per account.

        Multi-account handling is index-based: discover visible account choices,
        then select the same index on each business view.  For single-account
        sessions it performs one bounded pass without opening selectors again.
        """
        self._navigate(BALANCE_URL, "账户余额")
        account_count = max(1, len(self._visible_account_options()))
        if max_accounts is not None:
            account_count = min(account_count, max_accounts)

        results: list[AccountData] = []
        for index in range(account_count):
            partials: list[AccountData] = []

            self._navigate(BALANCE_URL, "账户余额")
            if account_count > 1:
                self._select_account(index)
            partials.append(self._parse_current_page())

            self._navigate(ELECTRIC_USAGE_URL, "电量电费查询")
            if account_count > 1:
                self._select_account(index)
            self._click_tab("月度电费")
            partials.append(self._parse_current_page())
            self._click_tab("日用电量")
            partials.append(self._parse_current_page())

            data = merge_account_data(*partials)
            results.append(data)
        return results

    def fetch_one(self) -> AccountData:
        """Convenience wrapper for the current/default account."""
        items = self.fetch_all(max_accounts=1)
        return items[0] if items else AccountData(account=parse_account_data().account)

    def _parse_current_page(self) -> AccountData:
        snapshot = self._snapshot()
        return parse_account_data(store=snapshot.get("store"), components=snapshot.get("components"))

    def _snapshot(self) -> dict[str, Any]:
        store = {}
        try:
            if hasattr(vue_state, "selected_store_snapshot"):
                store = vue_state.selected_store_snapshot(self.driver) or {}
            else:
                store = {"state": vue_state.selected_store_state(self.driver)}
        except Exception:
            store = {}
        try:
            components = vue_state.selected_vue_data(self.driver) or []
        except Exception:
            components = []
        return {"store": store, "components": components, "url": self.driver.current_url}

    def _navigate(self, url: str, label: str) -> None:
        self.driver.execute_script("window.location.href = arguments[0];", url)
        try:
            WebDriverWait(self.driver, self.wait_seconds).until(
                lambda d: url.split("/osgweb", 1)[-1] in (d.current_url or "")
                or d.execute_script("return document.readyState") in ("interactive", "complete")
            )
        except TimeoutException:
            pass
        time.sleep(self.settle_seconds)
        try:
            self.driver.execute_script("window.stop();")
        except Exception:
            pass

    def _click_tab(self, tab_text: str) -> bool:
        xpaths = [
            f"//div[contains(@class,'el-tabs__item') and contains(normalize-space(.), '{tab_text}')]",
            f"//*[contains(normalize-space(.), '{tab_text}')]",
        ]
        for xpath in xpaths:
            try:
                element = WebDriverWait(self.driver, 4).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                self.driver.execute_script("arguments[0].click();", element)
                time.sleep(self.settle_seconds)
                return True
            except Exception:
                continue
        return False

    def _visible_account_options(self) -> list[str]:
        """Return visible account selector option texts, without exposing them in logs."""
        if not self._open_account_selector():
            return []
        time.sleep(1)
        options = self.driver.find_elements(
            By.XPATH,
            "//ul[contains(@class,'el-dropdown-menu')]//li"
            " | //div[contains(@class,'el-select-dropdown')]//li",
        )
        texts: list[str] = []
        for option in options:
            try:
                klass = option.get_attribute("class") or ""
                text = (option.text or "").strip()
                if option.is_displayed() and text and "disabled" not in klass and "is-disabled" not in klass:
                    texts.append(text)
            except Exception:
                continue
        self._close_popups()
        # De-duplicate while preserving order.
        result: list[str] = []
        for text in texts:
            if text not in result:
                result.append(text)
        return result

    def _select_account(self, index: int) -> bool:
        if not self._open_account_selector():
            return False
        time.sleep(1)
        options = [
            option
            for option in self.driver.find_elements(
                By.XPATH,
                "//ul[contains(@class,'el-dropdown-menu')]//li"
                " | //div[contains(@class,'el-select-dropdown')]//li",
            )
            if self._is_enabled_visible(option)
        ]
        if index >= len(options):
            self._close_popups()
            return False
        self.driver.execute_script("arguments[0].click();", options[index])
        time.sleep(self.settle_seconds)
        return True

    def _open_account_selector(self) -> bool:
        selectors = [
            (By.XPATH, "//span[contains(normalize-space(.), '切换用户')]"),
            (By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner"),
            (By.CSS_SELECTOR, ".houseNum .el-select .el-input__suffix"),
            (By.CSS_SELECTOR, ".el-dropdown > span"),
            (By.CLASS_NAME, "el-input__suffix"),
        ]
        for by, value in selectors:
            try:
                elements = self.driver.find_elements(by, value)
                for element in elements:
                    if element.is_displayed():
                        self.driver.execute_script("arguments[0].click();", element)
                        return True
            except Exception:
                continue
        return False

    def _is_enabled_visible(self, element) -> bool:
        try:
            klass = element.get_attribute("class") or ""
            return element.is_displayed() and "disabled" not in klass and "is-disabled" not in klass
        except Exception:
            return False

    def _close_popups(self) -> None:
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass


def redact_account_data(data: AccountData) -> dict[str, Any]:
    """Return a log-safe dict for live verification output."""
    raw = asdict(data)
    account_no = data.account.account_no
    masked = mask_account_no(account_no)
    raw["account"]["account_no"] = masked
    raw["account"]["display_name"] = ""
    raw["account"]["address"] = ""
    if raw.get("balance"):
        raw["balance"]["account_no"] = masked
    for key in ("yearly",):
        if raw.get(key):
            raw[key]["account_no"] = masked
    for key in ("monthly", "daily"):
        for row in raw.get(key) or []:
            row["account_no"] = masked
    return raw
