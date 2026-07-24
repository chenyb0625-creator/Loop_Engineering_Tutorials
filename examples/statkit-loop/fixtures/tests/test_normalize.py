from __future__ import annotations

import unittest

from statkit import normalize


class NormalizeTest(unittest.TestCase):
    def test_empty_sequence(self) -> None:
        self.assertEqual(normalize([]), [])

    def test_mixed_sign_values_use_min_max_scaling(self) -> None:
        self.assertEqual(normalize([-2.0, 0.0, 2.0]), [0.0, 0.5, 1.0])

    def test_positive_values_do_not_assume_zero_minimum(self) -> None:
        self.assertEqual(normalize([10.0, 20.0, 30.0]), [0.0, 0.5, 1.0])

    def test_constant_sequence_becomes_zeroes(self) -> None:
        self.assertEqual(normalize([7.0, 7.0, 7.0]), [0.0, 0.0, 0.0])

    def test_input_is_not_modified(self) -> None:
        values = [3.0, 1.0, 2.0]
        original = values.copy()
        normalize(values)
        self.assertEqual(values, original)


if __name__ == "__main__":
    unittest.main()
