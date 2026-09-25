from __future__ import annotations

import unittest

from data_parser import parse_clipboard_grid, parse_table_text


class DataParserTests(unittest.TestCase):
    def test_common_delimiters_and_header_detection(self) -> None:
        cases = {
            "tab": "x\ty\n1\t2\n3\t4",
            "comma": "x,y\n1,2\n3,4",
            "semicolon": "x;y\n1;2\n3;4",
            "whitespace": "x y\n1 2\n3 4",
        }

        for expected_delimiter, text in cases.items():
            with self.subTest(expected_delimiter):
                parsed = parse_table_text(text)
                self.assertEqual(parsed.delimiter, expected_delimiter)
                self.assertTrue(parsed.has_header)
                self.assertEqual(parsed.dataframe.shape, (2, 2))

    def test_clipboard_grid_preserves_spaces_and_uses_only_tabs_for_columns(self) -> None:
        self.assertEqual(parse_clipboard_grid("1st cycle"), [["1st cycle"]])
        self.assertEqual(parse_clipboard_grid("Smith, John"), [["Smith, John"]])
        self.assertEqual(
            parse_clipboard_grid("1st cycle\n2nd cycle"),
            [["1st cycle"], ["2nd cycle"]],
        )
        self.assertEqual(
            parse_clipboard_grid(
                "1st cycle\t2nd cycle\n3rd cycle\t4th cycle"
            ),
            [
                ["1st cycle", "2nd cycle"],
                ["3rd cycle", "4th cycle"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
