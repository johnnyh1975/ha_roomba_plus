"""Pytest configuration for Roomba+ tests.

Applies compatibility shims for HA API differences between test environments
and ensures the integration is importable regardless of installed HA version.
"""


def pytest_configure(config):
    """Apply HA version compatibility shims before any test module is imported.

    Shim: AddConfigEntryEntitiesCallback was introduced in HA 2024.4.
    Older test environments (pytest-homeassistant-custom-component < 0.13.x)
    only export AddEntitiesCallback.  We alias it here so the integration
    imports cleanly regardless of which version is installed.

    When to remove: once the minimum HA version in hacs.json is bumped past
    2024.4 and the CI requirement updated accordingly, this shim can be
    deleted along with conftest.py if no other shims are needed.
    """
    import importlib
    try:
        ep = importlib.import_module("homeassistant.helpers.entity_platform")
        if not hasattr(ep, "AddConfigEntryEntitiesCallback"):
            ep.AddConfigEntryEntitiesCallback = getattr(
                ep, "AddEntitiesCallback", None
            )
    except ImportError:
        pass
