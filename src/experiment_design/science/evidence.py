"""Numeric support checks against supplied source spans."""

import re


def supports_number(value: float | None, span: str | None) -> bool:
    return (
        value is not None
        and span is not None
        and any(
            float(number) == value
            for number in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", span)
        )
    )
