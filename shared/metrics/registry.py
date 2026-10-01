"""A versioned registry of metric definitions.

Section 4: "Each metric definition is versioned and stored." Section 7: "A changed model,
policy, fill assumption or material data fix starts a new version/cohort rather than pooling
incompatible results."

The registry enforces the second rule structurally. A definition is immutable once registered;
changing a formula means registering a NEW version, and results carrying different versions are
never silently comparable. This is what stops the quiet drift where a rate's meaning changes
underneath a dashboard tile that keeps its name.
"""
from __future__ import annotations

from .contract import MetricDefinition

#: The dimensions that decide what a number MEANS. Two results may be compared only if all of
#: these match. `version`, `owner`, `notes` and `code_versions` are deliberately absent: a
#: clarified comment or a rebuilt artifact does not change the quantity being measured.
MEANING_FIELDS = ("metric_id", "kind", "cohort", "grain", "numerator", "denominator", "unit",
                  "decision_authority", "weighting", "outcome", "benchmark")


class MetricRegistry:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], MetricDefinition] = {}

    def register(self, definition: MetricDefinition) -> MetricDefinition:
        key = (definition.metric_id, definition.version)
        existing = self._by_key.get(key)
        if existing is not None:
            # Re-registering the IDENTICAL definition is fine (module reimport, test setup).
            # Re-registering a DIFFERENT one under the same version is the silent-drift bug.
            if existing != definition:
                raise ValueError(
                    f"{definition.metric_id} v{definition.version} is already registered with a "
                    f"different definition. A changed formula needs a NEW version — pooling "
                    f"results across incompatible definitions is the error this prevents.")
            return existing
        self._by_key[key] = definition
        return definition

    def get(self, metric_id: str, version: str) -> MetricDefinition:
        try:
            return self._by_key[(metric_id, version)]
        except KeyError:
            raise KeyError(
                f"no registered definition for {metric_id} v{version}. A number may not be "
                f"published under an unregistered definition.") from None

    def versions(self, metric_id: str) -> list[str]:
        return sorted(v for (m, v) in self._by_key if m == metric_id)

    def latest(self, metric_id: str) -> MetricDefinition:
        versions = self.versions(metric_id)
        if not versions:
            raise KeyError(f"no registered definition for {metric_id}")
        return self._by_key[(metric_id, versions[-1])]

    def comparable(self, a: MetricDefinition, b: MetricDefinition) -> bool:
        """May two results be put side by side?

        Only if every dimension that changes a number's MEANING matches. Version and notes may
        differ — a clarified comment does not change the quantity — but cohort, grain,
        denominator, authority, weighting and outcome may not.
        """
        return all(getattr(a, f) == getattr(b, f) for f in MEANING_FIELDS)

    def __len__(self) -> int:
        return len(self._by_key)


#: Process-wide default. Definitions register themselves against this at import time.
REGISTRY = MetricRegistry()
