#!/usr/bin/env python3

import logging

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service

from ble import (
    Advertisement,
    Characteristic,
    Service,
    Application,
    find_adapter,
    Descriptor,
    Agent,
)

import struct
import requests
import array
from enum import Enum

import sys

MainLoop = None
try:
    from gi.repository import GLib

    MainLoop = GLib.MainLoop
except ImportError:
    import gobject as GObject

    MainLoop = GObject.MainLoop

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logHandler = logging.StreamHandler()
filelogHandler = logging.FileHandler("logs.log")
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logHandler.setFormatter(formatter)
filelogHandler.setFormatter(formatter)
logger.addHandler(filelogHandler)
logger.addHandler(logHandler)


Gr0GBaseUrl = "com.ag.gr0g"

mainloop = None
gr0g_bus = None

BLUEZ_SERVICE_NAME = "org.bluez"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"


class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class NotSupportedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotSupported"


class NotPermittedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotPermitted"


class InvalidValueLengthException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.InvalidValueLength"


class FailedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.Failed"


def register_app_cb():
    logger.info("GATT application registered")


def register_app_error_cb(error):
    logger.critical("Failed to register application: " + str(error))
    mainloop.quit()


class Gr0GS1Service(Service):

    GR0G_SVC_STATUS_UUID = "00001802-0000-1000-8000-00805f9b38fb"

    def __init__(self, bus, index):
        Service.__init__(self, bus, index, self.GR0G_SVC_STATUS_UUID, True)
        #self.add_characteristic(FanControlCharacteristic(bus, 0, self))
        self.add_characteristic(LightCharacteristic(bus, 1, self))
        self.add_characteristic(LightControlCharacteristic(bus, 2, self))
        self.add_characteristic(TemperatureCharacteristic(bus, 3, self))
        self.add_characteristic(TemperatureSetpointCharacteristic(bus, 4, self))
        self.add_characteristic(HumidityCharacteristic(bus, 5, self))
        self.add_characteristic(HumiditySetpointCharacteristic(bus, 6, self))

class FanControlCharacteristic(Characteristic):
    uuid = "304cf226-411e-11eb-b378-0242ac130002"
    description = b"Get/set machine fan state {'ON', 'OFF', 'UNKNOWN'}"

    class State(Enum):
        on = "ON"
        off = "OFF"
        unknown = "UNKNOWN"

        @classmethod
        def has_value(cls, value):
            return value in cls._value2member_map_

    fan_options = {"ON", "OFF", "UNKNOWN"}

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read", "write"], service,
        )

        self.value = [0xFF]
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.debug("FAN Read: " + repr(self.value))
        res = None
        try:
            res = requests.get(Gr0GBaseUrl + ".status")
            self.value = bytearray(res.json()["fan"], encoding="utf8")
        except Exception as e:
            logger.error(f"Error getting status {e}")
            self.value = bytearray(self.State.unknown, encoding="utf8")

        return self.value

    def WriteValue(self, value, options):
        logger.debug("FAN Write: " + repr(value))
        cmd = bytes(value).decode("utf-8")
        if self.State.has_value(cmd):
            # write it to machine
            logger.info("writing {cmd} to machine")
            data = {"cmd": cmd.lower()}
            try:
                res = requests.post(Gr0GBaseUrl + "/status/cmds", json=data)
            except Exceptions as e:
                logger.error(f"Error updating fan state: {e}")
        else:
            logger.info(f"invalid state written {cmd}")
            raise NotPermittedException

        self.value = value

class LightCharacteristic(Characteristic):
    uuid = "00002a06-0000-1000-8000-00805f9b34fe"
    description = b"Get light level"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read"], service,
        )

        self.value = []
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        global gr0g_bus
        logger.info("Light read: " + repr(self.value))

        try:
            remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
            status = remote_object.status()
            print(status)

            self.value = bytearray(struct.pack("d", float(status["light"])))
        except Exception as e:
            logger.error(f"Error getting status {e}")

        return self.value

class LightControlCharacteristic(Characteristic):
    uuid = "00002a06-0000-1000-8000-00805f9b35fe"
    description = b"Set light light state can be `on` or `off`"
    lastValue = 0

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read", "write"], service,
        )

        self.value = [ 0 ]
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.info("Light control read: " + repr(self.value) +","+ repr(self.lastValue))
        res = None
        try:
            self.value = [ self.lastValue ]
        except Exception as e:
            logger.error(f"Error getting status {e}")

        return self.value

    def WriteValue(self, value, options):
        logger.info("Light state Write: " + repr(value))

        svalue = ""
        for byteValue in value:
            svalue += (chr(byteValue))
        logger.info("svalue: " + svalue)

        ivalue = int(svalue)
        if (ivalue == 0) or (ivalue == 1) or (ivalue == 2) :
            # write it to machine
            logger.info("writing {svalue} to light")
            data = {"cmd": "setlight", "state": svalue.lower()}
            try:
                remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
                status = remote_object.cmd(data)

                logger.info(status)
            except Exceptions as e:
                logger.error(f"Error updating machine state: {e}")
                raise
        else:
            logger.info(f"invalid state written {cmd}")
            raise NotPermittedException

        self.lastValue = ivalue

class TemperatureCharacteristic(Characteristic):
    uuid = "00002a06-0000-1000-8000-00805f9b34fc"
    description = b"Get temperature"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read"], service,
        )

        self.value = []
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.info("Temperature read: " + repr(self.value))

        try:
            remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
            status = remote_object.status()
            print(status)

            self.value = bytearray(struct.pack("d", float(status["temperature"])))
        except Exception as e:
            logger.error(f"Error getting status {e}")

        return self.value

class TemperatureSetpointCharacteristic(Characteristic):
    uuid = "00002a06-0000-1000-8000-00805f9b36fc"
    description = b"Get/set temperature setpoint"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read", "write"], service,
        )

        self.value = []
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.info("Temperature setpoint read: " + repr(self.value))

        try:
            remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
            status = remote_object.status()
            print(status)

            self.value = bytearray(struct.pack("d", float(status["temperature_setpoint"])))
        except Exception as e:
            logger.error(f"Error getting status {e}")

        return self.value

    def WriteValue(self, value, options):
        logger.info("Temperature write: " + repr(value))
        cmd = bytes(value)

        # write it to machine
        logger.info("writing {fvalue} to temperature_setpoint")
        data = {"cmd": "temperature_setpoint", "value": struct.unpack("d", cmd)[0]}
        try:
            remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
            status = remote_object.cmd(data)

            logger.info(status)
        except Exceptions as e:
            logger.error(f"Error updating machine state: {e}")
            raise

class HumidityCharacteristic(Characteristic):
    uuid = "00002a06-0000-1000-8000-00805f9b34fd"
    description = b"Get humidity"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read"], service,
        )

        self.value = []
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.info("Humidity read: " + repr(self.value))

        try:
            remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
            status = remote_object.status()
            print(status)

            self.value = bytearray(struct.pack("d", float(status["humidity"])))
        except Exception as e:
            logger.error(f"Error getting status {e}")

        return self.value

class HumiditySetpointCharacteristic(Characteristic):
    uuid = "00002a06-0000-1000-8000-00805f9b35fd"
    description = b"Get/set humidity setpoint"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read", "write"], service,
        )

        self.value = []
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.info("Humidity setpoint read: " + repr(self.value))

        try:
            remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
            status = remote_object.status()

            self.value = bytearray(struct.pack("d", float(status["humidity_setpoint"])))
        except Exception as e:
            logger.error(f"Error getting status {e}")

        return self.value

    def WriteValue(self, value, options):
        logger.info("Temperature write: " + repr(value))
        cmd = bytes(value)

        # write it to machine
        logger.info("writing {cmd} to humidity_setpoint")
        data = {"cmd": "humidity_setpoint", "value": struct.unpack("i", cmd)[0]}
        try:
            remote_object = gr0g_bus.get_object("com.ag.gr0g", "/gr0g")
            status = remote_object.cmd(data)

            logger.info(status)
        except Exceptions as e:
            logger.error(f"Error updating machine state: {e}")
            raise



class CharacteristicUserDescriptionDescriptor(Descriptor):
    """
    Writable CUD descriptor.
    """

    CUD_UUID = "2901"

    def __init__(
        self, bus, index, characteristic,
    ):

        self.value = array.array("B", characteristic.description)
        self.value = self.value.tolist()
        Descriptor.__init__(self, bus, index, self.CUD_UUID, ["read"], characteristic)

    def ReadValue(self, options):
        return self.value

    def WriteValue(self, value, options):
        if not self.writable:
            raise NotPermittedException()
        self.value = value


class Gr0GAdvertisement(Advertisement):
    def __init__(self, bus, index):
        Advertisement.__init__(self, bus, index, "peripheral")
        self.add_manufacturer_data(
            0xFFFF, [0x70, 0x74],
        )
        self.add_service_uuid(Gr0GS1Service.GR0G_SVC_STATUS_UUID)

        self.add_local_name("Gr0G")
        self.include_tx_power = True


def register_ad_cb():
    logger.info("Advertisement registered")


def register_ad_error_cb(error):
    logger.critical("Failed to register advertisement: " + str(error))
    mainloop.quit()


AGENT_PATH = "/gr0g"


def main():
    global mainloop
    global gr0g_bus 

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    # get the system bus
    bus = dbus.SystemBus()
    # get the ble controller
    adapter = find_adapter(bus)

    if not adapter:
        logger.critical("GattManager1 interface not found")
        return

    adapter_obj = bus.get_object(BLUEZ_SERVICE_NAME, adapter)

    adapter_props = dbus.Interface(adapter_obj, "org.freedesktop.DBus.Properties")

    # powered property on the controller to on
    adapter_props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(1))

    # Get manager objs
    service_manager = dbus.Interface(adapter_obj, GATT_MANAGER_IFACE)
    ad_manager = dbus.Interface(adapter_obj, LE_ADVERTISING_MANAGER_IFACE)

    advertisement = Gr0GAdvertisement(bus, 0)
    obj = bus.get_object(BLUEZ_SERVICE_NAME, "/org/bluez")

    agent = Agent(bus, AGENT_PATH)

    gr0g_bus = dbus.SessionBus()

    app = Application(bus)
    app.add_service(Gr0GS1Service(bus, 2))

    mainloop = MainLoop()

    agent_manager = dbus.Interface(obj, "org.bluez.AgentManager1")
    agent_manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")

    ad_manager.RegisterAdvertisement(
        advertisement.get_path(),
        {},
        reply_handler=register_ad_cb,
        error_handler=register_ad_error_cb,
    )

    logger.info("Registering GATT application...")

    service_manager.RegisterApplication(
        app.get_path(),
        {},
        reply_handler=register_app_cb,
        error_handler=[register_app_error_cb],
    )

    agent_manager.RequestDefaultAgent(AGENT_PATH)

    mainloop.run()
    # ad_manager.UnregisterAdvertisement(advertisement)
    # dbus.service.Object.remove_from_connection(advertisement)


if __name__ == "__main__":
    main()
