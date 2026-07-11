import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "convert_state_grid_lovelace", ROOT / "tools" / "convert_state_grid_lovelace.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class ConvertStateGridLovelaceTests(unittest.TestCase):
    def test_replaces_state_grid_entities_with_sgcc_entities(self):
        src = """
entity: sensor.state_grid_123456_balance
entity: sensor.state_grid_123456_daily_ele_num
entity: sensor.state_grid_123456_month_p_ele_num
entity: sensor.state_grid_yearly_ele
"""
        out, counts = module.convert_text(src, "4840_0123456789")
        self.assertIn("sensor.sgcc_4840_0123456789_balance", out)
        self.assertIn("sensor.sgcc_4840_0123456789_last_daily_usage", out)
        self.assertIn("sensor.sgcc_4840_0123456789_month_peak", out)
        self.assertIn("sensor.sgcc_4840_0123456789_year_usage", out)
        self.assertEqual(counts["entity"], 4)

    def test_replaces_graph_attribute_by_series_context(self):
        src = """
series:
  - entity: sensor.state_grid_123456_recent_30_daily_ele_list
    data_generator: |
      return entity.attributes.graph;
  - entity: sensor.state_grid_123456_recent_12_monthly_ele_list
    data_generator: |
      return entity.attributes["graph"];
"""
        out, counts = module.convert_text(src, "4840_0123456789")
        self.assertEqual(out.count("sensor.sgcc_4840_0123456789_history"), 2)
        self.assertIn("entity.attributes.daily", out)
        self.assertIn('entity.attributes["monthly"]', out)
        self.assertEqual(counts["daily_graph"], 1)
        self.assertEqual(counts["monthly_graph"], 1)

    def test_cli_accepts_account_number_and_output_positional(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "input.yaml"
            dst = Path(tmp) / "output.yaml"
            src.write_text("entity: sensor.state_grid_123456_balance\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "convert_state_grid_lovelace.py"),
                    str(src),
                    str(dst),
                    "--account-no",
                    "1234567890123",
                    "--quiet",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.stderr, "")
            self.assertIn(
                "sensor.sgcc_0123_e2161a7e19_balance",
                dst.read_text(encoding="utf-8"),
            )

    def test_shipped_lovelace_examples_use_current_entity_identity(self):
        example_files = [
            ROOT / "examples/basic/lovelace-sgcc-electricity.yaml",
            *sorted((ROOT / "examples/lovelace-cards").glob("*.yaml")),
        ]

        for path in example_files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("sensor.guo_wang_dian_fei_4840", text, path)
            self.assertIn("sensor.sgcc_4840_0123456789_", text, path)


if __name__ == "__main__":
    unittest.main()
