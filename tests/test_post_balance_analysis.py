import unittest

import pandas as pd

from scripts.s4.generate_post_balance_analysis import (
    aggregate_pool_metrics,
    build_distance_distribution,
    hourly_assignment_changes,
    make_distance_assignment,
)


class PostBalanceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.loads = {
            "RRH_001": 10.0,
            "RRH_002": 5.0,
            "RRH_003": 1.0,
        }
        self.capacities = {
            "RRH_001": 100.0,
            "RRH_002": 200.0,
            "RRH_003": 300.0,
        }

    def test_distance_assignment_maps_pool_and_load_without_losing_rrhs(self):
        source = pd.DataFrame({
            "rrh_id": ["RRH_001", "RRH_002", "RRH_003"],
            "site_id": ["site-1", "site-2", "site-3"],
            "bbu_pool": ["BBU_A", "BBU_B", "BBU_B"],
            "distance_to_bbu_km": [0.5, 1.5, 2.5],
        })

        result = make_distance_assignment(source, self.loads)

        self.assertEqual(len(result), 3)
        self.assertEqual(result["traffic_load_mbph"].sum(), 16.0)
        self.assertEqual(result.loc[0, "bbu_pool_id"], "BBU_A")

    def test_pool_metrics_conserve_load_and_capacity_and_keep_empty_pools(self):
        assignment = pd.DataFrame({
            "rrh_id": ["RRH_001", "RRH_002", "RRH_003"],
            "bbu_pool_id": ["BBU_A", "BBU_B", "BBU_B"],
            "distance_km": [1.0, 2.0, 3.0],
        })

        result = aggregate_pool_metrics(
            assignment,
            self.loads,
            self.capacities,
            ["BBU_A", "BBU_B", "BBU_C"],
        )

        self.assertEqual(result["rrh_count"].sum(), 3)
        self.assertEqual(result["traffic_load_mbph"].sum(), 16.0)
        self.assertEqual(result["capacity_mbph"].sum(), 600.0)
        self.assertEqual(result.loc["BBU_C", "rrh_count"], 0)
        self.assertEqual(result.loc["BBU_C", "capacity_mbph"], 0.0)

    def test_hourly_change_and_distance_bins_account_for_every_rrh(self):
        peak = pd.DataFrame({
            "rrh_id": ["RRH_001", "RRH_002", "RRH_003"],
            "bbu_pool_id": ["BBU_A", "BBU_A", "BBU_B"],
            "distance_km": [0.0, 1.0, 4.5],
        })
        other_hour = peak.copy()
        other_hour.loc[1, "bbu_pool_id"] = "BBU_B"

        change = hourly_assignment_changes(peak, other_hour, "08:00", 2)
        self.assertEqual(change["changed_rrhs"], 1)
        self.assertAlmostEqual(change["changed_percent"], 100 / 3)

        distribution, labels = build_distance_distribution({
            2: {
                "distance": peak,
                "traffic": peak,
                "rrh_count": peak,
            }
        })
        self.assertEqual(labels, [
            "0-1 km",
            "1-2 km",
            "2-3 km",
            "3-4 km",
            "4-5 km",
        ])
        self.assertTrue((distribution.groupby("method")["rrh_count"].sum() == 3).all())
        self.assertEqual(
            int(distribution.loc[
                (distribution["method"] == "Solo distancia")
                & (distribution["distance_bin"] == "4-5 km"),
                "rrh_count",
            ].iloc[0]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
