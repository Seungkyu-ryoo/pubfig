from __future__ import annotations

import unittest

from pubfig.legend_layout import legend_row_ids, new_row_flags, normalize_row_lengths


class LegendLayoutTests(unittest.TestCase):
    def test_rows_have_one_shared_mapping_policy(self) -> None:
        self.assertEqual(normalize_row_lengths([2, -1, 1]), [2, 0, 1])
        self.assertEqual(legend_row_ids(5, [2, 0, 1]), [0, 0, 2, 3, 4])
        self.assertEqual(new_row_flags(5, [2, 0, 1]), [True, False, True, True, True])

    def test_empty_layout_means_one_entry_per_row(self) -> None:
        self.assertEqual(legend_row_ids(3, []), [0, 1, 2])
        self.assertEqual(new_row_flags(3, []), [True, True, True])


if __name__ == "__main__":
    unittest.main()
