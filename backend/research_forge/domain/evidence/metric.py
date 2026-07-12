"""RFC 6901 JSON metric extraction without any model involvement."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
class MetricComparator(StrEnum):
    EQUALS = "equals"
    GTE = "gte"
    LTE = "lte"


class MetricExtractionError(ValueError):
    """Raised when a metric artifact cannot satisfy its frozen metric contract."""


@dataclass(frozen=True, slots=True)
class MetricExpectation:
    json_pointer: str
    comparator: MetricComparator
    expected_value: float
    tolerance: float
    unit: str


@dataclass(frozen=True, slots=True)
class MetricValidation:
    value: float
    passed: bool
    expectation: MetricExpectation


def extract_and_validate_metric(payload: bytes, expectation: MetricExpectation) -> MetricValidation:
    """Extract one finite JSON number and compare it under the explicit tolerance."""
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetricExtractionError("Metric artifact is not valid UTF-8 JSON.") from exc
    value = _resolve_pointer(document, expectation.json_pointer)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MetricExtractionError("Metric JSON pointer must resolve to a finite number.")
    numeric_value = float(value)
    if expectation.comparator is MetricComparator.EQUALS:
        passed = abs(numeric_value - expectation.expected_value) <= expectation.tolerance
    elif expectation.comparator is MetricComparator.GTE:
        passed = numeric_value >= expectation.expected_value - expectation.tolerance
    else:
        passed = numeric_value <= expectation.expected_value + expectation.tolerance
    return MetricValidation(value=numeric_value, passed=passed, expectation=expectation)


def _resolve_pointer(document: object, pointer: str) -> object:
    if not pointer.startswith("/"):
        raise MetricExtractionError("Metric JSON pointer must start with '/'.")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise MetricExtractionError(f"Metric JSON pointer key is absent: {token}")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (token.startswith("0") and token != "0"):
                raise MetricExtractionError(f"Metric JSON pointer array index is invalid: {token}")
            index = int(token)
            if index >= len(current):
                raise MetricExtractionError(f"Metric JSON pointer array index is absent: {token}")
            current = current[index]
        else:
            raise MetricExtractionError("Metric JSON pointer traverses a scalar value.")
    return current
