"""Pytest configuration for Roomba+ tests.

Applies compatibility shims for HA API differences between test environments
and ensures the integration is importable regardless of installed HA version.
"""


def pytest_configure(config):
    """Apply HA version compatibility shims before any test module is imported.

    Shim 1 (existing): AddConfigEntryEntitiesCallback — HA 2024.4.
    Shim 2 (v2.4.0): Segment dataclass — HA 2026.3 vacuum.clean_area.
    Shim 3 (v2.4.0): VacuumEntityFeature.CLEAN_AREA — HA 2026.3.

    When to remove shims 2 & 3: once CI is pinned to pytest-homeassistant-
    custom-component built against HA 2026.3+.
    """
    import dataclasses
    import enum
    import importlib

    # Shim 1: AddConfigEntryEntitiesCallback
    try:
        ep = importlib.import_module("homeassistant.helpers.entity_platform")
        if not hasattr(ep, "AddConfigEntryEntitiesCallback"):
            ep.AddConfigEntryEntitiesCallback = getattr(
                ep, "AddEntitiesCallback", None
            )
    except ImportError:
        pass

    # Shim 2 + 3: vacuum.clean_area symbols (HA 2026.3)
    try:
        vacuum_mod = importlib.import_module("homeassistant.components.vacuum")

        if not hasattr(vacuum_mod, "Segment"):
            @dataclasses.dataclass(slots=True)
            class Segment:
                id: str
                name: str
                group: str | None = None
            vacuum_mod.Segment = Segment

        feature_cls = getattr(vacuum_mod, "VacuumEntityFeature", None)
        if feature_cls is not None and not hasattr(feature_cls, "CLEAN_AREA"):
            existing_values = [m.value for m in feature_cls]
            next_bit = max(existing_values) << 1 if existing_values else 1
            new_cls = enum.IntFlag(
                "VacuumEntityFeature",
                [(m.name, m.value) for m in feature_cls] + [("CLEAN_AREA", next_bit)],
            )
            vacuum_mod.VacuumEntityFeature = new_cls
    except ImportError:
        pass
