from __future__ import annotations

import unittest

from data_parser import parse_table_text


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


if __name__ == "__main__":
    unittest.main()
