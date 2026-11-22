import unittest

import pandas as pd

from scripts.s3.visualize_load_balancing import (
    pool_loads_by_pool,
    relative_pool_load_percent,
)


class VisualizeLoadBalancingTests(unittest.TestCase):
    def test_pool_loads_are_aggregated_without_losing_rrhs(self):
        assignments = pd.DataFrame({
            "rrh_id": ["RRH_1", "RRH_2", "RRH_3"],
            "bbu_pool_id": ["BBU_A", "BBU_A", "BBU_B"],
        })
        loads = pd.DataFrame({
            "rrh_id": ["RRH_1", "RRH_2", "RRH_3"],
            "traffic_load_mbph": [10, 20, 30],
        })

        result = pool_loads_by_pool(assignments, loads)

        self.assertEqual(result.to_dict(), {"BBU_A": 30, "BBU_B": 30})
        self.assertEqual(result.sum(), loads["traffic_load_mbph"].sum())

    def test_relative_pool_loads_use_mean_pool_load_as_100_percent(self):
        result = relative_pool_load_percent(
            pd.Series({"BBU_A": 50.0, "BBU_B": 150.0})
        )

        self.assertEqual(result["BBU_A"], 50.0)
        self.assertEqual(result["BBU_B"], 150.0)


if __name__ == "__main__":
    unittest.main()
