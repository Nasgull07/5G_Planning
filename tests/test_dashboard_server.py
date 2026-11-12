import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.dashboard_server import assignment_metadata, build_commands


class DashboardCommandTests(unittest.TestCase):
    def test_scenario_runs_generation_before_map_creation(self):
        label, commands = build_commands(
            "scenario",
            {
                "real_count": 120,
                "synthetic_count": 25,
                "min_distance": 150,
                "seed": 7,
            },
        )

        self.assertEqual(label, "Generating scenario and map")
        self.assertEqual(len(commands), 2)
        self.assertIn("--real-count", commands[0])
        self.assertIn("--count", commands[0])
        self.assertEqual(Path(commands[1][-1]).name, "create_cell_sites_map.py")

    def test_invalid_synthetic_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Synthetic RRHs"):
            build_commands("scenario", {"real_count": 10, "synthetic_count": -1})

    def test_download_worker_count_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "Workers"):
            build_commands("download", {"workers": 0})

    def test_boolean_spacing_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Minimum distance"):
            build_commands(
                "scenario",
                {"real_count": 1, "synthetic_count": 0, "min_distance": True},
            )

    def test_assignment_metadata_counts_real_synthetic_and_pools(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "assignments.csv"
            path.write_text(
                "rrh_id,bbu_pool,is_synthetic\n"
                "RRH_1,BBU_01,False\n"
                "RRH_SYN_1,BBU_02,True\n"
                "RRH_2,BBU_02,False\n",
                encoding="utf-8",
            )

            metadata = assignment_metadata(path, fallback_k=3)

        self.assertEqual(metadata, {
            "k": 3,
            "rrhs": 3,
            "real_rrhs": 2,
            "synthetic_rrhs": 1,
        })


if __name__ == "__main__":
    unittest.main()
