import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import folium
import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box

from scripts.generate_random_traffic import (
    assign_random_categories,
    calculate_category_counts,
)
from scripts.generate_traffic_map import (
    add_markers,
    classify_scenario,
    classify_traffic,
    validate_classification,
)


class TrafficAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.scenario = pd.DataFrame({
            "rrh_id": [f"rrh-{index}" for index in range(10)],
            "latitude": [41.0] * 10,
            "longitude": [2.0] * 10
        })

    def test_random_assignment_observes_percentages_for_variable_size(self):
        result = assign_random_categories(
            self.scenario,
            residential_percent=20,
            office_percent=30,
            mixed_percent=50,
            seed=7
        )

        self.assertEqual(
            result["traffic_category"].value_counts().to_dict(),
            {"Mixed": 5, "Office": 3, "Residential": 2}
        )
        validate_classification(result)

    def test_random_assignment_is_reproducible_with_seed(self):
        first = assign_random_categories(
            self.scenario, 20, 30, 50, seed=21
        )
        second = assign_random_categories(
            self.scenario, 20, 30, 50, seed=21
        )

        self.assertEqual(
            first["traffic_category"].tolist(),
            second["traffic_category"].tolist()
        )

    def test_percentages_must_total_100(self):
        with self.assertRaisesRegex(ValueError, "add up to 100"):
            calculate_category_counts(
                10,
                {"Residential": 20, "Office": 20, "Mixed": 20}
            )

    def test_realistic_rules_assign_categories_from_activity_evidence(self):
        cases = [
            ("Low", False, True, "Office"),
            ("High", True, False, "Residential"),
            ("High", True, True, "Mixed"),
            ("No AMB data", False, True, "Residential"),
            ("Low", False, False, "Residential")
        ]
        for density, residential, office, expected in cases:
            with self.subTest(expected=expected):
                result = classify_traffic(pd.Series({
                    "residential_density_group": density,
                    "residential_evidence": residential,
                    "office_evidence": office
                }))
                self.assertEqual(result["traffic_category"], expected)

    def test_realistic_assignment_accepts_variable_scenario_size(self):
        metric_points = [
            (430000, 4580000),
            (432000, 4580000),
            (434000, 4580000)
        ]
        transformer = Transformer.from_crs(
            "EPSG:25831", "EPSG:4326", always_xy=True
        )
        coordinates = [
            transformer.transform(x, y)
            for x, y in metric_points
        ]
        scenario = pd.DataFrame({
            "rrh_id": ["rrh-1", "rrh-2", "rrh-3"],
            "latitude": [latitude for _, latitude in coordinates],
            "longitude": [longitude for longitude, _ in coordinates],
            "densresi21": [-1, -1, -1],
            "office_count_500m": [99, 99, 99],
            "traffic_category": ["Old"] * 3
        })
        residential = gpd.GeoDataFrame(
            {"densresi21": [10, 50, 100]},
            geometry=[
                box(x - 800, 4580000 - 800, x + 800, 4580000 + 800)
                for x, _ in metric_points
            ],
            crs="EPSG:25831"
        )
        offices = pd.DataFrame({
            "category": ["office"] * 5,
            "x_25831": [430010, 430020, 430030, 430040, 430050],
            "y_25831": [4580000] * 5
        })

        with TemporaryDirectory() as temp_dir:
            scenario_file = Path(temp_dir) / "scenario.csv"
            office_file = Path(temp_dir) / "offices.csv"
            scenario.to_csv(scenario_file, index=False)
            offices.to_csv(office_file, index=False)
            with patch(
                "scripts.generate_traffic_map.load_residential_areas",
                return_value=residential
            ):
                result = classify_scenario(
                    scenario_file=scenario_file,
                    osm_file=office_file
                )

        self.assertEqual(len(result), 3)
        self.assertEqual(
            result["traffic_category"].tolist(),
            ["Office", "Residential", "Residential"]
        )
        self.assertEqual(result["office_count_500m"].tolist(), [5, 0, 0])

    def test_map_uses_shapes_for_all_categories(self):
        classification = self.scenario.iloc[:3].copy()
        classification["traffic_category"] = [
            "Residential", "Mixed", "Office"
        ]
        map_object = folium.Map()
        add_markers(map_object, classification)

        rendered = map_object.get_root().render()
        self.assertIn("border-radius:50%", rendered)
        self.assertIn("clip-path:polygon", rendered)
        self.assertIn("width:14px;height:14px;border:2px solid #222;background:#fff", rendered)


if __name__ == "__main__":
    unittest.main()
