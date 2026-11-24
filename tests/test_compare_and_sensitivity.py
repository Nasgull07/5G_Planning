import unittest

import pandas as pd

from scripts.s4.compare_and_sensitivity import (
    aggregate_pool_metrics,
    assert_load_conservation,
    distance_bin_counts,
    hourly_assignment_changes,
    _ranked_pool_counts,
)


class CompareAndSensitivityTests(unittest.TestCase):
    def test_pool_counts_are_ranked_descending_independently_per_method(self):
        counts = pd.DataFrame({
            "k": [3] * 6,
            "method": [
                "Distance-only", "Distance-only",
                "Traffic-balanced", "Traffic-balanced",
                "RRH-count-balanced", "RRH-count-balanced",
            ],
            "bbu_pool_id": [
                "BBU_01", "BBU_02", "BBU_01", "BBU_02", "BBU_01", "BBU_02",
            ],
            "rrh_count": [2, 8, 6, 4, 3, 7],
        })

        ranked = _ranked_pool_counts(counts, 3)

        self.assertEqual(ranked.index.tolist(), [1, 2])
        self.assertEqual(ranked["Distance-only"].tolist(), [8, 2])
        self.assertEqual(ranked["Traffic-balanced"].tolist(), [6, 4])
        self.assertEqual(ranked["RRH-count-balanced"].tolist(), [7, 3])

    def test_exact_load_and_rrh_conservation_for_all_methods_and_pool_sizes(self):
        rrh_ids = [f"RRH_{index:03d}" for index in range(500)]
        loads = pd.DataFrame({
            "rrh_id": rrh_ids,
            "traffic_load_mbph": [index + 1 for index in range(500)],
        })
        capacities = pd.DataFrame({
            "rrh_id": rrh_ids,
            "phy_capacity_max_mbph": [1000] * 500,
        })
        expected_total = int(loads["traffic_load_mbph"].sum())

        for k in (22, 30, 40):
            pool_ids = [f"BBU_{index + 1:02d}" for index in range(k)]
            for method_offset in range(3):
                assignments = pd.DataFrame({
                    "rrh_id": rrh_ids,
                    "bbu_pool_id": [
                        pool_ids[(index + method_offset) % k]
                        for index in range(500)
                    ],
                    "distance_km": [1.0] * 500,
                })
                metrics = aggregate_pool_metrics(
                    assignments, loads, capacities, pool_ids
                )
                self.assertEqual(len(metrics), k)
                self.assertEqual(int(metrics["rrh_count"].sum()), 500)
                self.assertEqual(int(metrics["load_mbph"].sum()), expected_total)
                self.assertEqual(
                    assert_load_conservation(assignments, loads, rrh_ids),
                    float(expected_total),
                )

    def test_hourly_change_count_and_percentage_are_relative_to_peak(self):
        rrh_ids = [f"RRH_{index:03d}" for index in range(500)]
        peak = pd.DataFrame({
            "rrh_id": rrh_ids,
            "bbu_pool_id": ["BBU_01"] * 500,
            "distance_km": [1.0] * 500,
        })
        hourly = peak.copy()
        hourly.loc[:49, "bbu_pool_id"] = "BBU_02"

        result = hourly_assignment_changes(peak, hourly, "08:00", 30)

        self.assertEqual(result["changed_rrhs"], 50)
        self.assertEqual(result["changed_percent"], 10.0)

    def test_distance_intervals_are_inclusive_and_conserve_all_rrhs(self):
        assignments = pd.DataFrame({
            "rrh_id": [f"RRH_{index}" for index in range(5)],
            "bbu_pool_id": ["BBU_01"] * 5,
            "distance_km": [0.0, 1.0, 1.5, 3.0, 4.0],
        })

        counts = distance_bin_counts(assignments)

        self.assertEqual(counts.to_dict(), {
            "0-1 km": 2,
            "1-2 km": 1,
            "2-3 km": 1,
            "3-4 km": 1,
        })
        self.assertEqual(int(counts.sum()), len(assignments))


if __name__ == "__main__":
    unittest.main()
