"""Starter implementation with an intentional min-max normalization bug."""

from __future__ import annotations


def normalize(values: list[float]) -> list[float]:
    if not values:
        return []

    maximum = max(values)
    if maximum == 0:
        return [0.0 for _ in values]

    # Intentional bug: this scales by max instead of using min-max normalization.
    return [value / maximum for value in values]
