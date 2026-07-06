import importlib.util
import os
import sys
import time
import types
import unittest


def _install_selenium_stub_if_missing():
    if importlib.util.find_spec("selenium") is not None:
        return

    class TimeoutException(Exception):
        pass

    class By:
        XPATH = "xpath"
        CSS_SELECTOR = "css selector"
        CLASS_NAME = "class name"
        TAG_NAME = "tag name"

    class Keys:
        ESCAPE = "\ue00c"

    class WebDriverWait:
        def __init__(self, driver, timeout, poll_frequency=0.5):
            self.driver = driver
            self.timeout = timeout
            self.poll_frequency = poll_frequency

        def until(self, method):
            deadline = time.monotonic() + max(0, self.timeout)
            while True:
                value = method(self.driver)
                if value:
                    return value
                if time.monotonic() >= deadline:
                    raise TimeoutException()
                time.sleep(max(0, self.poll_frequency))

    def element_to_be_clickable(locator):
        def predicate(driver):
            elements = driver.find_elements(*locator)
            for element in elements:
                try:
                    if element.is_displayed():
                        return element
                except Exception:
                    continue
            return False
        return predicate

    module_names = [
        "selenium",
        "selenium.common",
        "selenium.common.exceptions",
        "selenium.webdriver",
        "selenium.webdriver.common",
        "selenium.webdriver.common.by",
        "selenium.webdriver.common.keys",
        "selenium.webdriver.support",
        "selenium.webdriver.support.expected_conditions",
        "selenium.webdriver.support.ui",
    ]
    for name in module_names:
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["selenium.common.exceptions"].TimeoutException = TimeoutException
    sys.modules["selenium.webdriver.common.by"].By = By
    sys.modules["selenium.webdriver.common.keys"].Keys = Keys
    sys.modules["selenium.webdriver.support.expected_conditions"].element_to_be_clickable = element_to_be_clickable
    sys.modules["selenium.webdriver.support.ui"].WebDriverWait = WebDriverWait


_install_selenium_stub_if_missing()

from sgcc_ha_bridge.model import Account, AccountData
from sgcc_ha_bridge.scraper import Scraper


class FetchAllAccountEnumerationTestCase(unittest.TestCase):
    def test_fetch_all_keeps_duplicate_option_text_and_adds_current_account(self):
        scraper = Scraper(driver=object(), wait_seconds=1, settle_seconds=0)
        calls = []
        scraper._navigate = lambda url, label: None
        scraper._visible_account_options = lambda: ["民用", "民用"]

        def fake_fetch_account(selection_index=None):
            calls.append(selection_index)
            account_no = {
                None: "1234567891705",
                0: "1234567891703",
                1: "1234567891704",
            }[selection_index]
            return AccountData(account=Account(account_no=account_no))

        scraper._fetch_account = fake_fetch_account

        results = scraper.fetch_all()

        self.assertEqual(calls, [None, 0, 1])
        self.assertEqual([item.account.account_no for item in results], [
            "1234567891705",
            "1234567891703",
            "1234567891704",
        ])

    def test_fetch_all_deduplicates_current_account_when_selector_includes_it(self):
        scraper = Scraper(driver=object(), wait_seconds=1, settle_seconds=0)
        scraper._navigate = lambda url, label: None
        scraper._visible_account_options = lambda: ["当前", "其他"]

        def fake_fetch_account(selection_index=None):
            account_no = {
                None: "1234567891705",
                0: "1234567891705",
                1: "1234567891703",
            }[selection_index]
            return AccountData(account=Account(account_no=account_no))

        scraper._fetch_account = fake_fetch_account

        results = scraper.fetch_all()

        self.assertEqual([item.account.account_no for item in results], [
            "1234567891705",
            "1234567891703",
        ])

    def test_visible_account_options_does_not_deduplicate_equal_text(self):
        class FakeElement:
            def __init__(self, text):
                self.text = text

            def get_attribute(self, name):
                return ""

            def is_displayed(self):
                return True

        class FakeDriver:
            def find_elements(self, by, value):
                return [FakeElement("民用"), FakeElement("民用")]

            def find_element(self, by, value):
                raise Exception("no body")

        scraper = Scraper(driver=FakeDriver(), wait_seconds=1, settle_seconds=0)
        scraper._open_account_selector = lambda: True

        self.assertEqual(scraper._visible_account_options(), ["民用", "民用"])

    def test_fetch_selected_account_does_not_reselect_when_route_preserves_account(self):
        scraper = Scraper(driver=object(), wait_seconds=1, settle_seconds=0)
        selected = []
        scraper._navigate = lambda url, label: None
        scraper._select_account = lambda index: selected.append(index) or True
        scraper._click_tab = lambda text: True
        scraper._expand_daily_range_to_30_days = lambda: True
        scraper._current_account_no = lambda: "1234567891703"
        snapshots = iter([
            AccountData(account=Account(account_no="1234567891703")),
            AccountData(account=Account(account_no="1234567891703")),
            AccountData(account=Account(account_no="1234567891703")),
        ])
        scraper._parse_current_page = lambda *args, **kwargs: next(snapshots)

        data = scraper._fetch_selected_account(1)

        self.assertEqual(data.account.account_no, "1234567891703")
        self.assertEqual(selected, [1])


class DailyRangeWaitTestCase(unittest.TestCase):
    def test_daily_count_looks_expanded_accepts_30_day_or_more_than_7(self):
        self.assertTrue(Scraper._daily_count_looks_expanded(31, previous_count=7))
        self.assertTrue(Scraper._daily_count_looks_expanded(8, previous_count=7))
        self.assertFalse(Scraper._daily_count_looks_expanded(7, previous_count=7))
        self.assertFalse(Scraper._daily_count_looks_expanded(8, previous_count=8))

    def test_wait_for_daily_range_expansion_polls_until_count_grows(self):
        scraper = Scraper(driver=object(), wait_seconds=1, settle_seconds=0)
        counts = iter([7, 7, 31])
        scraper._current_daily_count = lambda: next(counts)

        self.assertTrue(
            scraper._wait_for_daily_range_expansion(
                previous_count=7,
                min_expected_count=20,
                timeout_seconds=1,
                poll_frequency=0,
            )
        )

    def test_expand_daily_range_is_best_effort_when_button_missing(self):
        scraper = Scraper(driver=object(), wait_seconds=1, settle_seconds=0)
        scraper._current_daily_count = lambda: 7
        scraper._click_daily_range = lambda text: False

        self.assertFalse(scraper._expand_daily_range_to_30_days())


if __name__ == "__main__":
    unittest.main()
