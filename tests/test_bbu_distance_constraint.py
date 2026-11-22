import json
import unittest

import numpy as np
import pandas as pd

from scripts.s2.bbu_initial_assignment_kmedian import (
    MAX_DISTANCE_METRES,
    assign_rrhs_to_pools,
    k_medoids_kmedian,
)


class BBUDistanceConstraintTests(unittest.TestCase):
    def test_repairs_unconstrained_solution_to_cover_every_rrh(self):
        coordinates = np.array([
            [0, 0],
            [0, 0],
            [0, 0],
            [7000, 0],
            [7000, 0],
            [7000, 0],
            [3500, 0],
            [12000, 0],
        ])

        selected = k_medoids_kmedian(coordinates, K=2)
        nearest_distances = np.linalg.norm(
            coordinates[:, np.newaxis, :] - coordinates[selected][np.newaxis, :, :],
            axis=2,
        ).min(axis=1)

        self.assertEqual(len(selected), 2)
        self.assertTrue(np.all(nearest_distances <= MAX_DISTANCE_METRES))

    def test_reports_when_k_cannot_cover_all_rrhs(self):
        coordinates = np.array([[0, 0], [9000, 0]])

        with self.assertRaisesRegex(ValueError, "within 4 km"):
            k_medoids_kmedian(coordinates, K=1)

    def test_assignments_include_ordered_json_feasible_options(self):
        coordinates = np.array([[0, 0], [2000, 0], [6000, 0]])
        rrhs = pd.DataFrame({
            "rrh_id": ["RRH_01", "RRH_02", "RRH_03"],
            "latitude": [41.0, 41.0, 41.0],
            "longitude": [2.0, 2.0, 2.0],
        })

        assignments, _ = assign_rrhs_to_pools(
            rrhs,
            coordinates,
            selected_indices=[0, 2],
        )
        middle = assignments.set_index("rrh_id").loc["RRH_02"]

        self.assertEqual(
            json.loads(middle["feasible_bbu_pools"]),
            ["BBU_01", "BBU_02"],
        )
        distances = json.loads(middle["feasible_distances_km"])
        self.assertEqual(len(distances), middle["num_feasible_bbu_pools"])
        self.assertAlmostEqual(distances[0], 2.0)
        self.assertAlmostEqual(distances[1], 4.0)
        self.assertEqual(middle["bbu_pool"], "BBU_01")
        self.assertAlmostEqual(middle["distance_to_bbu_km"], 2.0)


if __name__ == "__main__":
    unittest.main()
