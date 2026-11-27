import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.s4.generate_rrh_traffic_load import generate_traffic_load


class RrhTrafficLoadTests(unittest.TestCase):
    def test_scales_profiles_by_rrh_capacity_for_each_day_and_sample(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scenario_file = root / "scenario.csv"
            schedule_file = root / "schedule.csv"
            output_file = root / "traffic_load.csv"

            scenario_fields = [
                "rrh_id",
                "source_type",
                "technology",
                "is_synthetic",
                "traffic_category",
                "capacity_technology",
                "phy_capacity_max_mbps",
                "phy_capacity_max_mbph",
            ]
            scenario_rows = [
                {
                    "rrh_id": "res",
                    "source_type": "REAL",
                    "technology": "4G",
                    "is_synthetic": "False",
                    "traffic_category": "Residential",
                    "capacity_technology": "4G",
                    "phy_capacity_max_mbps": "1",
                    "phy_capacity_max_mbph": "100",
                },
                {
                    "rrh_id": "office",
                    "source_type": "REAL",
                    "technology": "5G",
                    "is_synthetic": "False",
                    "traffic_category": "Office",
                    "capacity_technology": "5G",
                    "phy_capacity_max_mbps": "2",
                    "phy_capacity_max_mbph": "200",
                },
                {
                    "rrh_id": "mixed",
                    "source_type": "SYNTHETIC",
                    "technology": "ASSUMED_FOR_SIMULATION",
                    "is_synthetic": "True",
                    "traffic_category": "Mixed",
                    "capacity_technology": "4G",
                    "phy_capacity_max_mbps": "3",
                    "phy_capacity_max_mbph": "300",
                },
            ]
            with scenario_file.open(
                "w", encoding="utf-8-sig", newline=""
            ) as output:
                writer = csv.DictWriter(output, fieldnames=scenario_fields)
                writer.writeheader()
                writer.writerows(scenario_rows)

            schedule_fields = ["t_day"]
            days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            categories = ["residential", "office", "mixed"]
            schedule_fields.extend(
                f"thp_{day}_xu17_{category}"
                for day in days
                for category in categories
            )
            with schedule_file.open(
                "w", encoding="utf-8-sig", newline=""
            ) as output:
                writer = csv.DictWriter(output, fieldnames=schedule_fields)
                writer.writeheader()
                for index in range(144):
                    row = {
                        "t_day": "en" if index == 0 else str(index / 144)
                    }
                    for day in days:
                        row[f"thp_{day}_xu17_residential"] = "0.2"
                        row[f"thp_{day}_xu17_office"] = "0.8"
                        row[f"thp_{day}_xu17_mixed"] = "0.5"
                    writer.writerow(row)

            with self.assertRaisesRegex(ValueError, "Invalid t_day"):
                generate_traffic_load(
                    scenario_file, schedule_file, output_file
                )
            count = generate_traffic_load(
                scenario_file,
                schedule_file,
                output_file,
                assume_first_sample_at_midnight=True,
            )
            with output_file.open(
                encoding="utf-8-sig", newline=""
            ) as source:
                loads = list(csv.DictReader(source))

            self.assertEqual(count, 3 * 7 * 144)
            self.assertEqual(len(loads), count)
            self.assertEqual(loads[0]["time"], "00:00")
            self.assertEqual(loads[0]["t_day"], "0")
            self.assertEqual(loads[0]["day_of_week"], "Monday")
            self.assertAlmostEqual(
                float(loads[0]["traffic_load_mbph"]), 20
            )
            office_monday = next(
                row for row in loads
                if row["rrh_id"] == "office"
                and row["day_of_week"] == "Monday"
                and row["time"] == "00:00"
            )
            mixed_sunday = next(
                row for row in loads
                if row["rrh_id"] == "mixed"
                and row["day_of_week"] == "Sunday"
                and row["time"] == "00:00"
            )
            self.assertAlmostEqual(
                float(office_monday["traffic_load_mbph"]), 160
            )
            self.assertAlmostEqual(
                float(mixed_sunday["traffic_load_mbph"]), 150
            )


if __name__ == "__main__":
    unittest.main()
