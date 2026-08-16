from __future__ import annotations

import unittest

from matplotlib.figure import Figure

from pubfig.annotation_geometry import annotation_display_geometry, rotated_endpoint
from pubfig.plot_config import AnnotationConfig


class AnnotationGeometryTests(unittest.TestCase):
    def test_display_geometry_uses_shorter_axes_dimension_as_unit(self) -> None:
        figure = Figure(figsize=(4, 2), dpi=100)
        axes = figure.add_axes((0.1, 0.1, 0.8, 0.8))
        annotation = AnnotationConfig(x=0.25, y=0.5, width=0.2, height=-0.1)

        start_x, start_y, width_px, height_px = annotation_display_geometry(
            axes, annotation
        )

        self.assertAlmostEqual(start_x, 120.0)
        self.assertAlmostEqual(start_y, 100.0)
        self.assertAlmostEqual(width_px, 32.0)
        self.assertAlmostEqual(height_px, -16.0)

    def test_rotated_endpoint_matches_interaction_and_renderer(self) -> None:
        self.assertEqual(rotated_endpoint(10, 20, 3, 4, 0), (13, 24))
        x, y = rotated_endpoint(10, 20, 4, 0, 90)
        self.assertAlmostEqual(x, 10.0)
        self.assertAlmostEqual(y, 24.0)


if __name__ == "__main__":
    unittest.main()
