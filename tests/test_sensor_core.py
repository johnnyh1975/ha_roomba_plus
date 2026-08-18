

class TestAreaSensorsCanBeConverted:
    """@chairstacker (#69): his Home Assistant is on imperial and his
    iRobot app shows square feet, but the area sensors read square
    metres regardless.

    The unit was declared and the value converted correctly — HA just
    had nothing telling it what kind of quantity this is. Without
    `device_class`, it displays the native unit as-is and the user's
    unit system is ignored.
    """

    def test_every_area_sensor_declares_its_device_class(self):
        from homeassistant.components.sensor import SensorDeviceClass
        from homeassistant.const import UnitOfArea

        from custom_components.roomba_plus.sensor_core import SENSORS

        offenders = [
            d.key for d in SENSORS
            if d.native_unit_of_measurement in (
                UnitOfArea.SQUARE_METERS, UnitOfArea.SQUARE_FEET,
            )
            and d.device_class is not SensorDeviceClass.AREA
        ]

        assert not offenders, (
            f"{offenders} report an area without device_class=AREA, so "
            f"Home Assistant cannot convert them to the user's unit system"
        )

    def test_there_are_area_sensors_to_check(self):
        from homeassistant.const import UnitOfArea

        from custom_components.roomba_plus.sensor_core import SENSORS

        found = [
            d.key for d in SENSORS
            if d.native_unit_of_measurement in (
                UnitOfArea.SQUARE_METERS, UnitOfArea.SQUARE_FEET,
            )
        ]

        assert len(found) >= 2
