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

# ── aiohttp stub ─────────────────────────────────────────────────────────────
# cloud_api.py imports aiohttp at module level for type annotations.
_aiohttp = types.ModuleType("aiohttp")
_aiohttp.ClientSession = object
_aiohttp.ClientError = Exception
# Stub aiohttp.web for api_views.py
_aiohttp_web = types.ModuleType("aiohttp.web")
class _FakeRequest:
    def __init__(self): self.app = {}; self.query = {}
_aiohttp_web.Request = _FakeRequest
_aiohttp_web.Response = object
_aiohttp.web = _aiohttp_web
sys.modules["aiohttp"] = _aiohttp
sys.modules["aiohttp.web"] = _aiohttp_web

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
class _Event:
    """Stub for homeassistant.core.Event."""
    def __init__(self, event_type="", data=None):
        self.event_type = event_type
        self.data = data or {}

ha_core = _make_module("homeassistant.core",
    HomeAssistant=object,
    callback=lambda f: f,
    CALLBACK_TYPE=object,
    ServiceCall=object,
    SupportsResponse=_SupportsResponse,
    Event=_Event,
)

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
    UnitOfTime=types.SimpleNamespace(HOURS="h", MINUTES="min", SECONDS="s", DAYS="d"),
)

# homeassistant.exceptions
_make_module("homeassistant.exceptions",
    HomeAssistantError=Exception,
    ConfigEntryNotReady=Exception,
    ConfigEntryAuthFailed=Exception,
    ServiceValidationError=type("ServiceValidationError", (Exception,), {"__init__": lambda self, msg="", **kw: Exception.__init__(self, msg)}),
)

# homeassistant.helpers.storage
class _Store:
    def __init__(self, *a, **kw): pass
    async def async_load(self): return None
    async def async_save(self, data): pass

ha_storage = _make_module("homeassistant.helpers.storage", Store=_Store)

# homeassistant.helpers.issue_registry
_make_module("homeassistant.helpers.issue_registry",
    async_create_issue=lambda *a, **kw: None,
    async_delete_issue=lambda *a, **kw: None,
    IssueSeverity=types.SimpleNamespace(ERROR="error", WARNING="warning", INFO="info"),
)

# homeassistant.helpers.aiohttp_client
_make_module("homeassistant.helpers.aiohttp_client",
    async_get_clientsession=lambda hass: None,
)

# homeassistant.helpers.update_coordinator
class _DataUpdateCoordinator:
    def __init__(self, hass=None, logger=None, *, name="", config_entry=None, update_interval=None, **kw):
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self.hass = hass
        self.name = name
    async def async_config_entry_first_refresh(self): pass
    async def _async_setup(self): pass
    async def _async_update_data(self): return {}
    def __class_getitem__(cls, item): return cls

class _ConfigEntryAuthFailed(Exception): pass
class _UpdateFailed(Exception): pass

_make_module("homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_DataUpdateCoordinator,
    ConfigEntryAuthFailed=_ConfigEntryAuthFailed,
    UpdateFailed=_UpdateFailed,
)

# homeassistant.helpers.entity_registry
class _FakeEntityRegistry:
    def async_get(self, entity_id): return None
    def async_get_entity_id(self, domain, platform, unique_id): return None
    def async_entries_for_device(self, reg, dev_id): return []

ha_er = _make_module("homeassistant.helpers.entity_registry",
    async_get=lambda hass: _FakeEntityRegistry(),
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
def _tz_aware_now(tz=None):
    """Return timezone-aware datetime like dt_util.now() does in production."""
    return datetime.datetime.now(tz=tz or datetime.timezone.utc)

def _tz_aware_utcnow():
    return datetime.datetime.now(tz=datetime.timezone.utc)

def _as_local(dt_val):
    """Return datetime in local time (stub: just return tz-aware UTC)."""
    if dt_val.tzinfo is None:
        dt_val = dt_val.replace(tzinfo=datetime.timezone.utc)
    return dt_val

ha_dt = _make_module("homeassistant.util.dt",
    now=_tz_aware_now,
    utcnow=_tz_aware_utcnow,
    utc_from_timestamp=lambda ts: datetime.datetime.fromtimestamp(ts, datetime.timezone.utc),
    as_timestamp=lambda dt: dt.timestamp(),
    as_local=_as_local,
    parse_datetime=datetime.datetime.fromisoformat,
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
    SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing", TOTAL="total"),
)

# homeassistant.components.binary_sensor
_make_module("homeassistant.components.binary_sensor",
    BinarySensorEntity=object,
    BinarySensorDeviceClass=types.SimpleNamespace(
        PROBLEM="problem", PRESENCE="presence", CONNECTIVITY="connectivity",
        OPENING="opening", UPDATE="update", RUNNING="running", BATTERY_CHARGING="battery_charging",
    ),
)

# homeassistant.components.switch
_make_module("homeassistant.components.switch", SwitchEntity=object)

# homeassistant.components.select
_make_module("homeassistant.components.select", SelectEntity=object)

# homeassistant.components.button
import dataclasses as _dc

@_dc.dataclass(frozen=True, kw_only=True)
class _ButtonEntityDescription:
    key: str = ""
    translation_key: str | None = None
    icon: str | None = None
    entity_category: object = None

_make_module("homeassistant.components.button",
    ButtonEntity=object,
    ButtonEntityDescription=_ButtonEntityDescription,
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


# homeassistant.components.http
class _HomeAssistantView:
    """Stub for HomeAssistantView."""
    url = ""
    name = ""
    requires_auth = True
    def json(self, result, status_code=200): return result
    def json_message(self, msg, status_code=200): return msg

_make_module("homeassistant.components.http",
    HomeAssistantView=_HomeAssistantView,
)

# homeassistant (top-level)
ha = _make_module("homeassistant",
    core=ha_core,
    const=ha_const,
    helpers=ha_helpers,
)
