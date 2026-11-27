import unittest

import pandas as pd

from scripts.s4.test.baseline_delay_analysis import (
    assert_pool_load_conservation,
    build_assignment_map,
    build_weekday_profiles,
    detect_weekday_peak,
    merge_loads_with_assignment,
    _weekday_pool_means,
)


class BaselineDelayAnalysisTests(unittest.TestCase):
    def test_merge_preserves_500_rrhs_and_pool_totals_at_each_sample(self):
        rrh_count = 500
        scenario = pd.DataFrame({
            "rrh_id": [f"RRH_{index:03d}" for index in range(rrh_count)],
        })
        assignment = pd.DataFrame({
            "site_id": scenario["rrh_id"],
            "bbu_pool": [f"BBU_{index % 10:02d}" for index in range(rrh_count)],
        })
        assignment_map = build_assignment_map(scenario, assignment)

        rows = []
        for day_index, day in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        ):
            for sample_index, time in enumerate(("08:00", "15:00", "21:00")):
                for rrh_index, rrh_id in enumerate(scenario["rrh_id"]):
                    rows.append({
                        "rrh_id": rrh_id,
                        "day_of_week": day,
                        "time": time,
                        "traffic_load_mbph": rrh_index + day_index + sample_index,
                    })
        loads = pd.DataFrame(rows)

        merged = merge_loads_with_assignment(loads, assignment_map)
        self.assertEqual(len(merged), rrh_count * 5 * 3)
        self.assertEqual(merged["rrh_id"].nunique(), rrh_count)
        assert_pool_load_conservation(merged)

        total_by_sample = loads.groupby(["day_of_week", "time"])[
            "traffic_load_mbph"
        ].sum()
        pool_by_sample = (
            merged.groupby(["day_of_week", "time", "bbu_pool_id"])[
                "traffic_load_mbph"
            ]
            .sum()
            .groupby(level=["day_of_week", "time"])
            .sum()
        )
        pd.testing.assert_series_equal(total_by_sample, pool_by_sample)

    def test_assignment_map_uses_first_source_id_from_json_rrh_ids(self):
        scenario = pd.DataFrame({
            "rrh_id": ['["site-1","alias-1"]', "SYN_RRH_001"],
        })
        assignment = pd.DataFrame({
            "site_id": ["site-1", "SYN_RRH_001"],
            "bbu_pool": ["BBU_01", "BBU_02"],
        })

        mapping = build_assignment_map(scenario, assignment)

        self.assertEqual(
            list(mapping.itertuples(index=False, name=None)),
            [
                ('["site-1","alias-1"]', "BBU_01"),
                ("SYN_RRH_001", "BBU_02"),
            ],
        )

    def test_merge_fails_if_any_load_row_has_no_assignment(self):
        loads = pd.DataFrame({
            "rrh_id": ["RRH_001", "RRH_002"],
            "day_of_week": ["Monday", "Monday"],
            "time": ["08:00", "08:00"],
            "traffic_load_mbph": [10, 20],
        })
        assignment_map = pd.DataFrame({
            "rrh_id": ["RRH_001"],
            "bbu_pool_id": ["BBU_01"],
        })

        with self.assertRaisesRegex(ValueError, "no assignment"):
            merge_loads_with_assignment(loads, assignment_map)

    def test_weekday_peak_ignores_weekends_and_uses_sample_index(self):
        schedule = pd.DataFrame({"t_day": [index / 144 for index in range(144)]})
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            for profile in ("residential", "office", "mixed"):
                schedule[f"thp_{day}_xu17_{profile}"] = 0.1
        for day in ("mon", "tue", "wed", "thu", "fri"):
            for profile, value in (
                ("residential", 0.7),
                ("office", 0.8),
                ("mixed", 0.6),
            ):
                schedule.loc[83, f"thp_{day}_xu17_{profile}"] = value
        for profile in ("residential", "office", "mixed"):
            schedule.loc[20, f"thp_sat_xu17_{profile}"] = 1.0
            schedule.loc[20, f"thp_sun_xu17_{profile}"] = 1.0

        profiles = build_weekday_profiles(schedule)
        peak_index = detect_weekday_peak(profiles)

        self.assertEqual(peak_index, 83)
        self.assertEqual(profiles.loc[peak_index, "time"], "13:50")
        self.assertAlmostEqual(
            profiles.loc[peak_index, "combined_normalized_sum"], 2.1
        )

    def test_pool_load_mean_excludes_weekend_days(self):
        merged = pd.DataFrame({
            "day_of_week": [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ],
            "time": ["08:00"] * 7,
            "bbu_pool_id": ["BBU_01"] * 7,
            "traffic_load_mbph": [10, 20, 30, 40, 50, 1000, 2000],
        })

        pool_means, network_means = _weekday_pool_means(merged)

        self.assertEqual(pool_means.loc[0, "traffic_load_mbph"], 30)
        self.assertEqual(network_means.loc[0, "traffic_load_mbph"], 30)


if __name__ == "__main__":
    unittest.main()
