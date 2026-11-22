import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.s3.calculate_link_capacity import (
    calculate_lte_capacity_mbps,
    calculate_lte_overhead,
    calculate_nr_capacity_mbps,
    update_scenario_capacities,
)


class LinkCapacityTests(unittest.TestCase):
    def test_capacity_formulas_match_the_confirmed_assumptions(self):
        self.assertAlmostEqual(calculate_lte_overhead(), 0.2175714286)
        self.assertAlmostEqual(
            calculate_lte_capacity_mbps(), 146.030841, places=5
        )
        self.assertAlmostEqual(
            calculate_nr_capacity_mbps(), 1752.750090, places=5
        )

    def test_updates_scenario_in_place_for_real_and_synthetic_rrhs(self):
        with TemporaryDirectory() as temp_dir:
            scenario_file = Path(temp_dir) / "scenario.csv"
            with scenario_file.open(
                "w", encoding="utf-8-sig", newline=""
            ) as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "rrh_id",
                        "technology",
                        "is_synthetic",
                        "traffic_category"
                    ]
                )
                writer.writeheader()
                writer.writerows([
                    {
                        "rrh_id": "real-4g",
                        "technology": "4G",
                        "is_synthetic": "False",
                        "traffic_category": "Residential"
                    },
                    {
                        "rrh_id": "real-5g",
                        "technology": "5G",
                        "is_synthetic": "False",
                        "traffic_category": "Office"
                    },
                    {
                        "rrh_id": "synthetic",
                        "technology": "ASSUMED_FOR_SIMULATION",
                        "is_synthetic": "True",
                        "traffic_category": "Mixed"
                    }
                ])

            lte_capacity, nr_capacity = update_scenario_capacities(
                scenario_file
            )

            with scenario_file.open(
                encoding="utf-8-sig", newline=""
            ) as source:
                rows = list(csv.DictReader(source))

            self.assertEqual(len(rows), 3)
            self.assertEqual(
                [row["capacity_technology"] for row in rows],
                ["4G", "5G", "4G"]
            )
            self.assertEqual(
                [row["traffic_category"] for row in rows],
                ["Residential", "Office", "Mixed"]
            )
            self.assertAlmostEqual(
                float(rows[0]["phy_capacity_max_mbps"]),
                lte_capacity,
                places=5
            )
            self.assertAlmostEqual(
                float(rows[1]["phy_capacity_max_mbps"]),
                nr_capacity,
                places=5
            )
            self.assertAlmostEqual(
                float(rows[2]["phy_capacity_max_mbph"]),
                lte_capacity * 450
            )
            self.assertEqual(
                [path.name for path in Path(temp_dir).glob("*.csv")],
                ["scenario.csv"]
            )


if __name__ == "__main__":
    unittest.main()
