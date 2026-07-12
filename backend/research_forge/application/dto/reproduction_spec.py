"""Validation and canonicalization for the frozen ReproductionSpec v1 contract."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator


class ReproductionSpecValidationError(ValueError):
    """Raised when a spec is structurally or semantically invalid."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("ReproductionSpec validation failed: " + "; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class ReproductionSpec:
    """Canonical, immutable representation of an accepted mission input."""

    payload: Mapping[str, Any]
    normalized_json: str
    sha256: str


class JsonSchemaReproductionSpecValidator:
    """Validate frozen v1 reproduce/repair inputs before any worker or decision adapter runs."""

    def __init__(self, schema: Mapping[str, Any]) -> None:
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema)

    def validate(self, raw_spec: Mapping[str, Any]) -> ReproductionSpec:
        if not isinstance(raw_spec, Mapping):
            raise ReproductionSpecValidationError(["spec must be a JSON object"])

        schema_errors = sorted(
            self._format_schema_error(error)
            for error in self._validator.iter_errors(raw_spec)
        )
        if schema_errors:
            raise ReproductionSpecValidationError(schema_errors)

        try:
            normalized_json = json.dumps(
                raw_spec,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            payload = json.loads(normalized_json)
        except (TypeError, ValueError) as exc:
            raise ReproductionSpecValidationError([f"spec is not JSON canonicalizable: {exc}"]) from exc

        semantic_errors = self._semantic_errors(payload)
        if semantic_errors:
            raise ReproductionSpecValidationError(semantic_errors)

        return ReproductionSpec(
            payload=payload,
            normalized_json=normalized_json,
            sha256=hashlib.sha256(normalized_json.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _format_schema_error(error: object) -> str:
        path = getattr(error, "absolute_path")
        parts = [str(part) for part in path]
        location = "$" if not parts else "$." + ".".join(parts)
        return f"{location}: {getattr(error, 'message')}"

    @staticmethod
    def _semantic_errors(spec: Mapping[str, Any]) -> list[str]:
        execution = spec["execution"]
        change_budget = spec["change_budget"]
        budget = spec["budget"]
        errors: list[str] = []

        mode = spec["mode"]
        if mode == "ablation":
            errors.append("ablation is disabled until its variable contract is frozen")
        if execution["setup_mode"] != "prebuilt":
            errors.append("VS-001 accepts only execution.setup_mode='prebuilt'")
        if execution["network_policy"] != "offline":
            errors.append("VS-001 accepts only execution.network_policy='offline'")
        if execution["allowed_domains"]:
            errors.append("execution.allowed_domains must be empty when network_policy='offline'")
        if mode == "reproduce":
            if change_budget["allowed_paths"]:
                errors.append("reproduce requires change_budget.allowed_paths to be empty")
            for field in (
                "max_files",
                "max_changed_lines",
                "max_candidate_commits",
                "max_candidate_runs",
            ):
                if change_budget[field] != 0:
                    errors.append(f"reproduce requires change_budget.{field}=0")
        elif mode == "repair":
            if not change_budget["allowed_paths"]:
                errors.append("repair requires at least one change_budget.allowed_paths entry")
            if not 1 <= change_budget["max_files"] <= 3:
                errors.append("repair requires change_budget.max_files in [1, 3]")
            if not 1 <= change_budget["max_changed_lines"] <= 200:
                errors.append("repair requires change_budget.max_changed_lines in [1, 200]")
            if change_budget["max_candidate_commits"] != 1:
                errors.append("repair requires change_budget.max_candidate_commits=1")
            if change_budget["max_candidate_runs"] != 1:
                errors.append("repair requires change_budget.max_candidate_runs=1")

        if budget["max_wall_time_seconds"] < execution["timeout_seconds"]:
            errors.append("budget.max_wall_time_seconds must be >= execution.timeout_seconds")

        for path, value in _walk_values(spec):
            if isinstance(value, float) and not math.isfinite(value):
                errors.append(f"{path} must be a finite number")

        return errors


def _walk_values(value: object, path: str = "$") -> list[tuple[str, object]]:
    if isinstance(value, Mapping):
        return [
            item
            for key, nested in value.items()
            for item in _walk_values(nested, f"{path}.{key}")
        ]
    if isinstance(value, list):
        return [
            item
            for index, nested in enumerate(value)
            for item in _walk_values(nested, f"{path}[{index}]")
        ]
    return [(path, value)]
