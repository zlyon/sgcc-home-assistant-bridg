import unittest

from sgcc_ha_bridge.vue_state import (
    FULL_VUE_DATA_SCRIPT,
    SELECTED_VUE_DATA_SCRIPT,
    _selected_vue_data_script,
    selected_vue_data,
    selected_vue_debug_data,
)


class VueStateScriptTestCase(unittest.TestCase):
    def test_balance_page_local_fields_are_collected(self):
        for key in ("accountBalance", "queryTime", "accountNo", "address"):
            self.assertIn(f'"{key}"', SELECTED_VUE_DATA_SCRIPT)

    def test_diag_only_money_fields_are_not_collected_by_default(self):
        for key in ("balance", "bal"):
            self.assertNotIn(f'"{key}"', SELECTED_VUE_DATA_SCRIPT)

    def test_diag_fields_are_collected_when_requested(self):
        script = _selected_vue_data_script(include_diag_fields=True)

        for key in ("balance", "bal"):
            self.assertIn(f'"{key}"', script)

    def test_selected_vue_data_passes_diag_fields_flag(self):
        class FakeDriver:
            def __init__(self):
                self.script = ""

            def execute_script(self, script):
                self.script = script
                return []

        driver = FakeDriver()

        selected_vue_data(driver, include_diag_fields=True)

        self.assertIn('"balance"', driver.script)

    def test_debug_capture_has_hard_global_and_component_budgets(self):
        self.assertIn("maxNodesPerComponent", FULL_VUE_DATA_SCRIPT)
        self.assertIn("maxMillis", FULL_VUE_DATA_SCRIPT)
        self.assertIn("<truncated:time-budget>", FULL_VUE_DATA_SCRIPT)
        self.assertNotIn("safeClone(vm.$route, 0)", FULL_VUE_DATA_SCRIPT)

    def test_selected_vue_debug_data_passes_safe_defaults(self):
        class FakeDriver:
            def execute_script(self, script, limits):
                self.script = script
                self.limits = limits
                return []

        driver = FakeDriver()
        selected_vue_debug_data(driver)

        self.assertEqual(driver.limits["maxDepth"], 6)
        self.assertEqual(driver.limits["maxNodes"], 12000)
        self.assertEqual(driver.limits["maxNodesPerComponent"], 800)
        self.assertEqual(driver.limits["maxMillis"], 1500)


if __name__ == "__main__":
    unittest.main()
