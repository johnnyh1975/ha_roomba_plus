"""Pytest configuration for Roomba+ unit tests.

Stubs out roombapy and homeassistant so that the pure-Python modules
(maintenance_store, zone_store, map_renderer, const) can be imported and
tested without a full HA or roombapy installation.

Only modules that have no HA/roombapy dependencies at module level are
imported directly. Everything else uses the stubs defined here.
"""
import sys
import os
import types

# ── 1. Path ───────────────────────────────────────────────────────────────────
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

# ── voluptuous stub ───────────────────────────────────────────────────────────
# __init__.py now imports voluptuous for service schema validation.
# Stub it minimally so that module-level imports succeed in test env.
_vol = types.ModuleType("voluptuous")
_vol.Schema = lambda *a, **kw: (lambda x: x)
_vol.Required = lambda key, **kw: key
_vol.Optional = lambda key, **kw: key
_vol.Any = lambda *a, **kw: a[0] if a else None
_vol.All = lambda *a, **kw: a[0] if a else None
sys.modules["voluptuous"] = _vol

# ── 2. Stub: roombapy ─────────────────────────────────────────────────────────
roombapy = types.ModuleType("roombapy")

class _Roomba:
    pass

class _RoombaConnectionError(Exception):
    pass

class _RoombaFactory:
    pass

roombapy.Roomba = _Roomba
roombapy.RoombaConnectionError = _RoombaConnectionError
roombapy.RoombaFactory = _RoombaFactory
sys.modules["roombapy"] = roombapy

# ── 3. Stub: homeassistant ────────────────────────────────────────────────────
def _make_module(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

# homeassistant.core
import enum as _enum
class _SupportsResponse(_enum.Enum):
    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"
ha_core = _make_module("homeassistant.core", HomeAssistant=object, callback=lambda f: f, CALLBACK_TYPE=object, ServiceCall=object, SupportsResponse=_SupportsResponse)

# homeassistant.const
ha_const = _make_module("homeassistant.const",
    Platform=types.SimpleNamespace(
        VACUUM="vacuum", SENSOR="sensor", BINARY_SENSOR="binary_sensor",
        BUTTON="button", SWITCH="switch", SELECT="select",
        IMAGE="image", CAMERA="camera",
    ),
    EntityCategory=types.SimpleNamespace(CONFIG="config", DIAGNOSTIC="diagnostic"),
    ATTR_CONNECTIONS="connections",
    # Config constants
    CONF_DELAY="delay",
    CONF_HOST="host",
    CONF_NAME="name",
    CONF_PASSWORD="password",
    CONF_IP_ADDRESS="ip_address",
    CONF_PORT="port",
    # Events
    EVENT_HOMEASSISTANT_STOP="homeassistant_stop",
    # Units
    PERCENTAGE="%",
    UnitOfArea=types.SimpleNamespace(SQUARE_METERS="m²", SQUARE_FEET="ft²"),
    UnitOfTime=types.SimpleNamespace(HOURS="h", MINUTES="min", SECONDS="s"),
)

# homeassistant.exceptions
_make_module("homeassistant.exceptions",
    HomeAssistantError=Exception,
    ConfigEntryNotReady=Exception,
    ServiceValidationError=type("ServiceValidationError", (Exception,), {"__init__": lambda self, msg="", **kw: Exception.__init__(self, msg)}),
)

# homeassistant.helpers.storage
class _Store:
    def __init__(self, *a, **kw): pass
    async def async_load(self): return None
    async def async_save(self, data): pass

ha_storage = _make_module("homeassistant.helpers.storage", Store=_Store)

# homeassistant.helpers.entity_registry
ha_er = _make_module("homeassistant.helpers.entity_registry",
    async_get=lambda hass: None,
    async_entries_for_device=lambda reg, dev_id: [],
)

# homeassistant.helpers.device_registry
ha_dr = _make_module("homeassistant.helpers.device_registry",
    async_get=lambda hass: None,
    CONNECTION_NETWORK_MAC="mac",
    DeviceInfo=dict,
)

# homeassistant.helpers (parent)
ha_typing = _make_module("homeassistant.helpers.typing", StateType=type(None))
_make_module("homeassistant.helpers.config_validation",
    ensure_list=list,
    string=str,
    boolean=bool,
    entity_ids=list,
)

ha_helpers = _make_module("homeassistant.helpers",
    storage=ha_storage,
    entity_registry=ha_er,
    device_registry=ha_dr,
    typing=ha_typing,
    config_validation=sys.modules["homeassistant.helpers.config_validation"],
)

# homeassistant.util.dt
import datetime
ha_dt = _make_module("homeassistant.util.dt",
    now=datetime.datetime.now,
    utcnow=datetime.datetime.utcnow,
    utc_from_timestamp=datetime.datetime.utcfromtimestamp,
    as_timestamp=lambda dt: dt.timestamp(),
    dt=datetime,
)

# homeassistant.util
_make_module("homeassistant.util", dt=ha_dt)

# homeassistant.config_entries
_make_module("homeassistant.config_entries", ConfigEntry=object, ConfigEntryNotReady=Exception)

# homeassistant.components.sensor
import dataclasses as _dc

@_dc.dataclass(frozen=True, kw_only=True)
class _SensorEntityDescription:
    key: str = ""
    name: str = ""
    translation_key: str | None = None
    icon: str | None = None
    device_class: object = None
    state_class: object = None
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None
    entity_category: object = None
    entity_registry_enabled_default: bool = True

    def __init_subclass__(cls, **kw):
        pass
_make_module("homeassistant.components.sensor",
    SensorEntity=object,
    SensorEntityDescription=_SensorEntityDescription,
    SensorDeviceClass=types.SimpleNamespace(
        TIMESTAMP="timestamp", DURATION="duration", ENERGY="energy",
        BATTERY="battery", SIGNAL_STRENGTH="signal_strength",
        AREA="area",
    ),
    SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing"),
)

# homeassistant.components.binary_sensor
_make_module("homeassistant.components.binary_sensor",
    BinarySensorEntity=object,
    BinarySensorDeviceClass=types.SimpleNamespace(
        PROBLEM="problem", PRESENCE="presence", CONNECTIVITY="connectivity"
    ),
)

# homeassistant.components.switch
_make_module("homeassistant.components.switch", SwitchEntity=object)

# homeassistant.components.select
_make_module("homeassistant.components.select", SelectEntity=object)

# homeassistant.components.button
_make_module("homeassistant.components.button",
    ButtonEntity=object,
    ButtonDeviceClass=types.SimpleNamespace(RESTART="restart"),
)

# homeassistant.components.vacuum
_make_module("homeassistant.components.vacuum",
    StateVacuumEntity=object,
    VacuumActivity=types.SimpleNamespace(
        CLEANING="cleaning", DOCKED="docked", IDLE="idle",
        PAUSED="paused", RETURNING="returning", ERROR="error",
    ),
    VacuumEntityFeature=types.SimpleNamespace(
        START=1, STOP=2, PAUSE=4, RETURN_HOME=8, BATTERY=16,
        STATUS=32, LOCATE=64, CLEAN_SPOT=128, MAP=256, STATE=512,
        FAN_SPEED=1024, SEND_COMMAND=2048,
    ),
)

# homeassistant.components.image
_make_module("homeassistant.components.image", ImageEntity=object)

# homeassistant.helpers.entity
_make_module("homeassistant.helpers.entity", Entity=object,
    DeviceInfo=dict,
    EntityDescription=object,
)

# homeassistant.helpers.entity_platform
_make_module("homeassistant.helpers.entity_platform",
    AddConfigEntryEntitiesCallback=object,
    async_get_platforms=lambda hass, domain: [],
)


# homeassistant (top-level)
ha = _make_module("homeassistant",
    core=ha_core,
    const=ha_const,
    helpers=ha_helpers,
)
