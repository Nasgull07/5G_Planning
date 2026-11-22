import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.s2.bbu_initial_assignment_kmedian import (
    resolve_output_directories,
)


class BBUOutputPathTests(unittest.TestCase):
    def test_default_csv_and_html_directories_share_k_specific_folder(self):
        with TemporaryDirectory() as temp_dir:
            data_dir, html_dir = resolve_output_directories(temp_dir, 4)

            expected = Path(temp_dir) / "output" / "BBU-pool-allocation_k4"
            self.assertEqual(data_dir, expected)
            self.assertEqual(html_dir, expected)

    def test_custom_output_directories_override_defaults_independently(self):
        with TemporaryDirectory() as temp_dir:
            csv_override = Path(temp_dir) / "csv"
            data_dir, html_dir = resolve_output_directories(
                temp_dir,
                3,
                data_output_dir=csv_override,
            )

            self.assertEqual(data_dir, csv_override)
            self.assertEqual(
                html_dir,
                Path(temp_dir) / "output" / "BBU-pool-allocation_k3",
            )


if __name__ == "__main__":
    unittest.main()
