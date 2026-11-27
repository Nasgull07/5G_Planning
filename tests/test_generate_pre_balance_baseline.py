import unittest

import pandas as pd

from scripts.s4.generate_pre_balance_baseline import (
    build_prebalance_reports,
)


class PreBalanceBaselineTests(unittest.TestCase):
    def test_builds_weekday_rates_pool_loads_and_peak_sensitivity(self):
        weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        assignments = pd.DataFrame([
            {
                "rrh_id": "RRH_001",
                "bbu_pool": "BBU_01",
                "traffic_category": "Residential",
                "phy_capacity_max_mbph": 100,
            },
            {
                "rrh_id": "RRH_002",
                "bbu_pool": "BBU_02",
                "traffic_category": "Office",
                "phy_capacity_max_mbph": 200,
            },
        ])
        loads = []
        for day in weekdays:
            for sample_index in range(144):
                time = (
                    f"{sample_index // 6:02d}:"
                    f"{(sample_index % 6) * 10:02d}"
                )
                loads.extend([
                    {
                        "rrh_id": "RRH_001",
                        "day_of_week": day,
                        "time": time,
                        "traffic_category": "Residential",
                        "traffic_load_mbph": 10 + sample_index,
                    },
                    {
                        "rrh_id": "RRH_002",
                        "day_of_week": day,
                        "time": time,
                        "traffic_category": "Office",
                        "traffic_load_mbph": 20 + sample_index,
                    },
                ])
        schedule = pd.DataFrame({
            "t_day": [index / 144 for index in range(144)]
        })
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            schedule[f"thp_{day}_xu17_residential"] = 0.1
            schedule[f"thp_{day}_xu17_office"] = 0.2
            schedule[f"thp_{day}_xu17_mixed"] = 0.3

        profile, pool_loads, sensitivity, peak_time = build_prebalance_reports(
            pd.DataFrame(loads), assignments, schedule
        )

        self.assertEqual(peak_time, "23:50")
        peak = profile.loc[profile["time"] == peak_time].iloc[0]
        self.assertAlmostEqual(peak["Residential"], 153)
        self.assertAlmostEqual(peak["Office"], 163)
        self.assertAlmostEqual(peak["Total network"], 316)
        pool_8am = pool_loads.set_index("bbu_pool_id")
        self.assertAlmostEqual(pool_8am.loc["BBU_01", "load_0800_mbph"], 58)
        self.assertAlmostEqual(pool_8am.loc["BBU_02", "load_0800_mbph"], 68)
        sensitivity_by_pool = sensitivity.set_index("bbu_pool_id")
        self.assertAlmostEqual(
            sensitivity_by_pool.loc["BBU_01", "all_residential_mbph"], 10
        )
        self.assertAlmostEqual(
            sensitivity_by_pool.loc["BBU_02", "all_office_mbph"], 40
        )
        self.assertAlmostEqual(
            sensitivity_by_pool.loc["BBU_01", "real_case_mbph"], 153
        )


if __name__ == "__main__":
    unittest.main()
