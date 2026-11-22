import unittest

import pandas as pd

from scripts.s3.load_balancing_algorithms import (
    assign_by_rrh_count,
    assign_by_traffic,
    build_connection_matrix,
    initialize_unique_assignments,
    order_remaining_rrhs,
    weekday_mean_loads,
)


def make_two_pool_fixture():
    connections = pd.DataFrame([
        {
            "rrh_id": "RRH_1",
            "feasible_bbu_pools": ["BBU_A"],
            "feasible_distances_km": [1.0],
        },
        {
            "rrh_id": "RRH_2",
            "feasible_bbu_pools": ["BBU_B"],
            "feasible_distances_km": [1.0],
        },
        {
            "rrh_id": "RRH_3",
            "feasible_bbu_pools": ["BBU_A", "BBU_B"],
            "feasible_distances_km": [1.0, 2.0],
        },
        {
            "rrh_id": "RRH_4",
            "feasible_bbu_pools": ["BBU_A", "BBU_B"],
            "feasible_distances_km": [1.0, 2.0],
        },
        {
            "rrh_id": "RRH_5",
            "feasible_bbu_pools": ["BBU_A", "BBU_B"],
            "feasible_distances_km": [2.0, 1.0],
        },
    ])
    loads = pd.DataFrame({
        "rrh_id": ["RRH_1", "RRH_2", "RRH_3", "RRH_4", "RRH_5"],
        "traffic_load_mbph": [10, 60, 80, 10, 5],
    })
    return connections, loads


class LoadBalancingTests(unittest.TestCase):
    def test_unique_rrhs_seed_base_loads_and_pool_counts(self):
        connections, loads = make_two_pool_fixture()

        unique, base_loads, base_counts, remaining = (
            initialize_unique_assignments(connections, loads)
        )

        self.assertEqual(
            unique[["rrh_id", "bbu_pool_id"]].values.tolist(),
            [["RRH_1", "BBU_A"], ["RRH_2", "BBU_B"]],
        )
        self.assertEqual(base_loads, {"BBU_A": 10.0, "BBU_B": 60.0})
        self.assertEqual(base_counts, {"BBU_A": 1, "BBU_B": 1})
        self.assertEqual(set(remaining["rrh_id"]), {"RRH_3", "RRH_4", "RRH_5"})

    def test_remaining_rrhs_are_sorted_by_option_count(self):
        connections = pd.DataFrame([
            {
                "rrh_id": "RRH_1",
                "feasible_bbu_pools": ["A", "B", "C"],
                "feasible_distances_km": [1.0, 2.0, 3.0],
            },
            {
                "rrh_id": "RRH_2",
                "feasible_bbu_pools": ["A", "B"],
                "feasible_distances_km": [3.8, 4.0],
            },
            {
                "rrh_id": "RRH_3",
                "feasible_bbu_pools": ["A", "B"],
                "feasible_distances_km": [2.0, 3.0],
            },
            {
                "rrh_id": "RRH_4",
                "feasible_bbu_pools": ["A"],
                "feasible_distances_km": [1.0],
            },
            {
                "rrh_id": "RRH_5",
                "feasible_bbu_pools": ["A", "B", "C"],
                "feasible_distances_km": [2.0, 1.0, 3.0],
            },
        ])

        self.assertEqual(
            order_remaining_rrhs(connections, method="traffic"),
            ["RRH_3", "RRH_2", "RRH_1", "RRH_5"],
        )
        self.assertEqual(
            order_remaining_rrhs(connections, method="count"),
            ["RRH_2", "RRH_3", "RRH_1", "RRH_5"],
        )

    def test_traffic_method_updates_pool_load_before_next_assignment(self):
        connections, loads = make_two_pool_fixture()

        result = assign_by_traffic(connections, loads).set_index("rrh_id")

        self.assertEqual(result.loc["RRH_3", "bbu_pool_id"], "BBU_A")
        self.assertEqual(result.loc["RRH_4", "bbu_pool_id"], "BBU_B")
        self.assertEqual(result.loc["RRH_5", "bbu_pool_id"], "BBU_B")

    def test_count_method_uses_current_counts_then_distance(self):
        connections, loads = make_two_pool_fixture()

        result = assign_by_rrh_count(connections, loads).set_index("rrh_id")

        self.assertEqual(result.loc["RRH_3", "bbu_pool_id"], "BBU_A")
        self.assertEqual(result.loc["RRH_4", "bbu_pool_id"], "BBU_B")
        self.assertEqual(result.loc["RRH_5", "bbu_pool_id"], "BBU_B")

    def test_each_method_assigns_every_rrh_once(self):
        connections, loads = make_two_pool_fixture()

        for assign in (assign_by_traffic, assign_by_rrh_count):
            result = assign(connections, loads)
            self.assertEqual(len(result), len(connections))
            self.assertEqual(result.columns.tolist(), [
                "rrh_id",
                "bbu_pool_id",
                "distance_km",
            ])
            self.assertEqual(result["rrh_id"].nunique(), len(connections))
            self.assertEqual(set(result["rrh_id"]), set(connections["rrh_id"]))

    def test_connection_matrix_maps_json_source_ids_to_phase1_candidates(self):
        scenario = pd.DataFrame({
            "rrh_id": ['["source-1","alias"]', "SYN_RRH_001"],
        })
        phase1_assignment = pd.DataFrame({
            "site_id": ["source-1", "SYN_RRH_001"],
            "feasible_bbu_pools": ['["BBU_A"]', '["BBU_A","BBU_B"]'],
            "feasible_distances_km": ["[1.0]", "[2.0,3.0]"],
        })

        result = build_connection_matrix(scenario, phase1_assignment)

        self.assertEqual(
            list(result["rrh_id"]),
            ['["source-1","alias"]', "SYN_RRH_001"],
        )
        self.assertEqual(
            result.loc[0, "feasible_bbu_pools"],
            '["BBU_A"]',
        )
        self.assertEqual(
            result.loc[1, "feasible_distances_km"],
            "[2.0,3.0]",
        )

    def test_weekday_loads_are_averaged_not_summed(self):
        load_data = pd.DataFrame({
            "rrh_id": ["RRH_1"] * 7,
            "day_of_week": [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ],
            "time": ["13:50"] * 7,
            "traffic_load_mbph": [10, 20, 30, 40, 50, 1000, 2000],
        })

        result = weekday_mean_loads(load_data, "13:50")

        self.assertEqual(result.loc[0, "traffic_load_mbph"], 30)


if __name__ == "__main__":
    unittest.main()
