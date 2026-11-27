from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.s4.balance_traffic import (
    assign_rrhs,
    build_connection_records,
    build_pool_summary,
    plot_pool_comparison,
    plot_ranked_pool_loads,
    _rank_pool_loads,
)


class TrafficLoadBalancingTests(unittest.TestCase):
    def setUp(self):
        assignments = pd.DataFrame([
            {
                "rrh_id": "RRH_001",
                "site_id": "site-1",
                "bbu_pool": "BBU_A",
                "feasible_bbu_pools": '["BBU_A"]',
                "feasible_distances_km": "[1.0]",
            },
            {
                "rrh_id": "RRH_002",
                "site_id": "site-2",
                "bbu_pool": "BBU_A",
                "feasible_bbu_pools": '["BBU_A", "BBU_B"]',
                "feasible_distances_km": "[2.0, 1.0]",
            },
            {
                "rrh_id": "RRH_003",
                "site_id": "site-3",
                "bbu_pool": "BBU_B",
                "feasible_bbu_pools": '["BBU_A", "BBU_B"]',
                "feasible_distances_km": "[1.0, 2.0]",
            },
        ])
        self.records = build_connection_records(assignments)
        self.loads = {
            "RRH_001": 10.0,
            "RRH_002": 5.0,
            "RRH_003": 1.0,
        }

    def test_traffic_method_keeps_forced_rrhs_and_uses_least_loaded_pool(self):
        result = assign_rrhs(self.records, self.loads, method="traffic")
        by_id = result.set_index("rrh_id")

        self.assertEqual(by_id.loc["RRH_001", "bbu_pool_id"], "BBU_A")
        self.assertTrue(by_id.loc["RRH_001", "is_forced"])
        self.assertEqual(by_id.loc["RRH_002", "bbu_pool_id"], "BBU_B")
        self.assertEqual(by_id.loc["RRH_003", "bbu_pool_id"], "BBU_B")
        self.assertAlmostEqual(result["traffic_load_mbph"].sum(), 16)

    def test_count_method_balances_rrh_counts_and_keeps_links_feasible(self):
        result = assign_rrhs(self.records, self.loads, method="rrh_count")
        counts = result.groupby("bbu_pool_id")["rrh_id"].count()

        self.assertEqual(counts.to_dict(), {"BBU_A": 2, "BBU_B": 1})
        self.assertEqual(len(result), 3)
        self.assertEqual(result["rrh_id"].nunique(), 3)
        self.assertEqual(
            result.loc[result["rrh_id"] == "RRH_001", "bbu_pool_id"].iloc[0],
            "BBU_A",
        )

    def test_pool_summary_compares_traffic_and_count_assignments(self):
        traffic = assign_rrhs(self.records, self.loads, method="traffic")
        count = assign_rrhs(self.records, self.loads, method="rrh_count")

        summary = build_pool_summary(traffic, count).set_index("bbu_pool_id")

        self.assertEqual(
            summary.loc["BBU_B", "traffic_rrh_count"], 2
        )
        self.assertAlmostEqual(
            summary["traffic_traffic_load_mbph"].sum(), 16
        )

    def test_pool_comparison_plot_is_created(self):
        traffic = assign_rrhs(self.records, self.loads, method="traffic")
        count = assign_rrhs(self.records, self.loads, method="rrh_count")
        summary = build_pool_summary(traffic, count)

        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "comparison.png"
            plot_pool_comparison(summary, output_file, hour="21:20")

            self.assertTrue(output_file.is_file())
            self.assertGreater(output_file.stat().st_size, 0)

    def test_ranked_pool_loads_are_descending_and_plot_is_created(self):
        traffic = assign_rrhs(self.records, self.loads, method="traffic")
        count = assign_rrhs(self.records, self.loads, method="rrh_count")
        summary = build_pool_summary(traffic, count)
        traffic_loads, count_loads = _rank_pool_loads(summary)

        self.assertTrue((traffic_loads[:-1] >= traffic_loads[1:]).all())
        self.assertTrue((count_loads[:-1] >= count_loads[1:]).all())

        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "ranked.png"
            plot_ranked_pool_loads(summary, output_file, hour="21:20")

            self.assertTrue(output_file.is_file())
            self.assertGreater(output_file.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
