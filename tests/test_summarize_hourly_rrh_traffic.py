import unittest

import pandas as pd

from scripts.s4.summarize_hourly_rrh_traffic import (
    _network_daily_metrics,
    build_hourly_traffic,
)


class SummarizeHourlyRrhTrafficTests(unittest.TestCase):
    def test_builds_hourly_volumes_and_category_totals_from_weekday_mean(self):
        assignments = pd.DataFrame([
            {
                "rrh_id": "RRH_001",
                "site_id": "site-1",
                "traffic_category": "Residential",
                "capacity_technology": "4G",
            },
            {
                "rrh_id": "RRH_002",
                "site_id": "site-2",
                "traffic_category": "Office",
                "capacity_technology": "5G",
            },
        ])
        rows = []
        weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        for day_index, day in enumerate(weekdays):
            for rrh_id, base_rate in (("RRH_001", 60), ("RRH_002", 120)):
                for sample_index in range(144):
                    hour = sample_index // 6
                    rate = base_rate + hour
                    if day == "Friday" and hour == 0:
                        rate = base_rate + 60
                    rows.append({
                        "rrh_id": rrh_id,
                        "day_of_week": day,
                        "time": (
                            f"{sample_index // 6:02d}:"
                            f"{(sample_index % 6) * 10:02d}"
                        ),
                        "traffic_load_mbph": rate,
                    })
        loads = pd.DataFrame(rows)

        per_rrh, chart_data = build_hourly_traffic(loads, assignments)

        first = per_rrh.set_index("rrh_id")
        self.assertAlmostEqual(first.loc["RRH_001", "traffic_00_mb"], 72)
        self.assertAlmostEqual(first.loc["RRH_002", "traffic_00_mb"], 132)
        self.assertAlmostEqual(first.loc["RRH_001", "traffic_01_mb"], 61)

        chart = chart_data.set_index("traffic_category")
        self.assertAlmostEqual(chart.loc["Residential", "traffic_00_mb"], 72)
        self.assertAlmostEqual(chart.loc["Office", "traffic_00_mb"], 132)
        self.assertAlmostEqual(chart.loc["Total network", "traffic_00_mb"], 204)
        daily_mean, peak_hour, peak_volume = _network_daily_metrics(chart_data)
        self.assertAlmostEqual(daily_mean, 204)
        self.assertEqual(peak_hour, 23)
        self.assertAlmostEqual(peak_volume, 226)

    def test_rejects_missing_weekday_samples(self):
        assignments = pd.DataFrame([{
            "rrh_id": "RRH_001",
            "site_id": "site-1",
            "traffic_category": "Residential",
            "capacity_technology": "4G",
        }])
        loads = pd.DataFrame([{
            "rrh_id": "RRH_001",
            "day_of_week": "Monday",
            "time": "00:00",
            "traffic_load_mbph": 10,
        }])

        with self.assertRaisesRegex(ValueError, "all 144 ten-minute samples"):
            build_hourly_traffic(loads, assignments)


if __name__ == "__main__":
    unittest.main()
