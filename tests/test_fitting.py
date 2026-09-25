from __future__ import annotations

import math
import unittest

from pubfig.fitting import LinearFitError, calculate_linear_fit, linear_fit


class LinearFitTests(unittest.TestCase):
    def test_exact_unsorted_line_reports_equation_r_squared_and_extent(self) -> None:
        result = linear_fit([3, 0, 2, 1], [7, 1, 5, 3])

        self.assertAlmostEqual(result.slope, 2.0)
        self.assertAlmostEqual(result.intercept, 1.0)
        self.assertEqual(result.r_squared, 1.0)
        self.assertEqual((result.point_count, result.n, result.sample_count), (4, 4, 4))
        self.assertEqual((result.x_min, result.x_max), (0.0, 3.0))
        self.assertIn("y = 2x + 1", result.equation_text())
        self.assertIn("R² = 1", result.equation_text())

    def test_finite_filter_and_inclusive_range_use_only_selected_pairs(self) -> None:
        result = calculate_linear_fit(
            [0, 1, 2, 3, "bad", 4, math.inf],
            [99, 3, 5, 7, 9, math.nan, 11],
            x_min=1,
            x_max=3,
        )

        self.assertAlmostEqual(result.slope, 2.0)
        self.assertAlmostEqual(result.intercept, 1.0)
        self.assertEqual(result.point_count, 3)
        self.assertEqual((result.x_min, result.x_max), (1.0, 3.0))

    def test_constant_y_has_undefined_r_squared(self) -> None:
        result = linear_fit([0, 1, 2], [4, 4, 4])

        self.assertEqual(result.slope, 0.0)
        self.assertEqual(result.intercept, 4.0)
        self.assertIsNone(result.r_squared)
        self.assertIn("R² = undefined", result.equation_text())

    def test_noisy_data_reports_nonperfect_r_squared(self) -> None:
        result = linear_fit([0, 1, 2], [1, 2, 2])

        self.assertAlmostEqual(result.slope, 0.5)
        self.assertAlmostEqual(result.intercept, 7 / 6)
        self.assertAlmostEqual(result.r_squared, 0.75)

    def test_extreme_magnitudes_and_small_positive_r_squared_are_preserved(self) -> None:
        tiny_y = linear_fit([0, 1, 2], [0, 1e-200, 2e-200])
        tiny_x = linear_fit([0, 1e-200], [0, 1])
        huge_x = linear_fit([1e200, 2e200], [1, 2])
        weak = linear_fit([0, 1, 2], [1 - 1e-7, -2, 1 + 1e-7])

        self.assertEqual(tiny_y.r_squared, 1.0)
        self.assertAlmostEqual(tiny_y.slope, 1e-200)
        self.assertEqual(tiny_x.r_squared, 1.0)
        self.assertAlmostEqual(tiny_x.slope, 1e200)
        self.assertEqual(huge_x.r_squared, 1.0)
        self.assertAlmostEqual(huge_x.slope, 1e-200)
        self.assertGreater(weak.r_squared, 0.0)
        self.assertAlmostEqual(weak.r_squared, 1 / 3 * 1e-14, delta=1e-20)

    def test_small_variation_on_large_offsets_retains_float_precision(self) -> None:
        baseline = 1e16
        step = math.ulp(baseline)

        large_x = linear_fit(
            [baseline, baseline + step, baseline + 2 * step],
            [1, 2, 3],
        )
        large_y = linear_fit(
            [0, 1, 2],
            [baseline, baseline + step, baseline + 2 * step],
        )

        self.assertEqual(large_x.slope, 1 / step)
        self.assertEqual(large_x.intercept, 1 - baseline / step)
        self.assertEqual(large_x.r_squared, 1.0)
        self.assertEqual(large_y.slope, step)
        self.assertEqual(large_y.intercept, baseline)
        self.assertEqual(large_y.r_squared, 1.0)

        near_limit = linear_fit(
            [1e308, 1e308 + math.ulp(1e308)],
            [1e308, 1e308 + 2 * math.ulp(1e308)],
        )
        self.assertEqual(near_limit.slope, 2.0)
        self.assertEqual(near_limit.intercept, -1e308)
        self.assertEqual(near_limit.predict(near_limit.x_min), 1e308)

    def test_invalid_range_insufficient_points_and_constant_x_are_clear(self) -> None:
        with self.assertRaisesRegex(LinearFitError, "minimum must be"):
            linear_fit([0, 1], [0, 1], x_min=2, x_max=1)
        with self.assertRaisesRegex(LinearFitError, "at least 2 finite points"):
            linear_fit([0, 1], [0, math.nan])
        with self.assertRaisesRegex(LinearFitError, "distinct X"):
            linear_fit([2, 2, 2], [1, 2, 3])
        with self.assertRaisesRegex(LinearFitError, "matching X and Y"):
            linear_fit([0, 1], [0])
        extreme = linear_fit([-1e308, 1e308], [-1e308, 1e308])
        self.assertEqual(extreme.slope, 1.0)
        self.assertEqual(extreme.intercept, 0.0)
        self.assertEqual(extreme.r_squared, 1.0)


if __name__ == "__main__":
    unittest.main()
