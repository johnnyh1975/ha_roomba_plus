"""Integration test conftest — loads real custom_components before any stubs.

Also stubs out HA component modules that have optional OS-level dependencies
(aiodhcpwatcher, zeroconf) not present in the CI environment.
"""
import sys
import os
import types

# Must happen at module load time (before pytest fixtures run)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Stub DHCP and Zeroconf service_info — these modules require OS packages
# (aiodhcpwatcher, zeroconf) not available in the test environment.
# The stubs expose only the classes used by config_flow.py.

def _stub_dhcp():
    from dataclasses import dataclass
    m = types.ModuleType("homeassistant.helpers.service_info.dhcp")
    @dataclass
    class DhcpServiceInfo:
        ip: str = ""
        hostname: str = ""
        macaddress: str = ""
    m.DhcpServiceInfo = DhcpServiceInfo
    sys.modules["homeassistant.helpers.service_info.dhcp"] = m

def _stub_zeroconf():
    from dataclasses import dataclass
    m = types.ModuleType("homeassistant.helpers.service_info.zeroconf")
    @dataclass
    class ZeroconfServiceInfo:
        host: str = ""
        hostname: str = ""
        port: int = 0
        type: str = ""
        name: str = ""
        properties: dict = None
        def __post_init__(self):
            if self.properties is None:
                self.properties = {}
    m.ZeroconfServiceInfo = ZeroconfServiceInfo
    sys.modules["homeassistant.helpers.service_info.zeroconf"] = m

_stub_dhcp()
_stub_zeroconf()

# Pre-load the real custom_components.roomba_plus
import custom_components.roomba_plus  # noqa: E402, F401

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(hass, enable_custom_integrations):
    """Enable roomba_plus custom integration for all integration tests."""
    yield
