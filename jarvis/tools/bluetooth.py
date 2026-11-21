from __future__ import annotations

import asyncio
import ctypes
import logging
import os
from ctypes import POINTER, Structure, c_int, c_uint, c_ulong, c_void_p, c_wchar_p
from typing import Annotated

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

from jarvis.textutil import best_match, normalize
from jarvis.tools.registry import ToolError, in_worker, tool

log = logging.getLogger(__name__)

KSPROPSETID_BTAUDIO = GUID("{7FA06C40-B8F6-4C7E-8556-E8C33A12E54D}")
KSPROPERTY_ONESHOT_RECONNECT = 0
KSPROPERTY_ONESHOT_DISCONNECT = 1
KSPROPERTY_TYPE_GET = 0x00000001
DEVICE_STATE_ACTIVE = 0x1
DEVICE_STATE_UNPLUGGED = 0x8
E_RENDER, E_CAPTURE = 0, 1

_MAJOR_CLASS = {1: "computadora", 2: "telefono", 4: "audio", 5: "periferico", 6: "imagen", 7: "reloj"}


class IConnector(IUnknown):
    _iid_ = GUID("{9C2C4058-23F5-41DE-877A-DF3AF236A09E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetType", (["out"], POINTER(c_int), "pType")),
        COMMETHOD([], HRESULT, "GetDataFlow", (["out"], POINTER(c_int), "pFlow")),
        COMMETHOD([], HRESULT, "ConnectTo", (["in"], c_void_p, "pConnectTo")),
        COMMETHOD([], HRESULT, "Disconnect"),
        COMMETHOD([], HRESULT, "IsConnected", (["out"], POINTER(c_int), "pbConnected")),
        COMMETHOD([], HRESULT, "GetConnectedTo", (["out"], POINTER(c_void_p), "ppConTo")),
        COMMETHOD([], HRESULT, "GetConnectorIdConnectedTo", (["out"], POINTER(c_wchar_p), "ppwstrConnectorId")),
        COMMETHOD([], HRESULT, "GetDeviceIdConnectedTo", (["out"], POINTER(c_wchar_p), "ppwstrDeviceId")),
    ]


class IDeviceTopology(IUnknown):
    _iid_ = GUID("{2A07407E-6497-4A18-9787-32F79BD0D98F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetConnectorCount", (["out"], POINTER(c_uint), "pCount")),
        COMMETHOD([], HRESULT, "GetConnector", (["in"], c_uint, "nIndex"), (["out"], POINTER(POINTER(IConnector)), "ppConnector")),
        COMMETHOD([], HRESULT, "GetSubunitCount", (["out"], POINTER(c_uint), "pCount")),
        COMMETHOD([], HRESULT, "GetSubunit", (["in"], c_uint, "nIndex"), (["out"], POINTER(c_void_p), "ppSubunit")),
        COMMETHOD([], HRESULT, "GetPartById", (["in"], c_uint, "nId"), (["out"], POINTER(c_void_p), "ppPart")),
        COMMETHOD([], HRESULT, "GetDeviceId", (["out"], POINTER(c_wchar_p), "ppwstrDeviceId")),
    ]


class KSPROPERTY(Structure):
    _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong)]


class IKsControl(IUnknown):
    _iid_ = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "KsProperty",
            (["in"], POINTER(KSPROPERTY), "Property"),
            (["in"], c_ulong, "PropertyLength"),
            (["in"], c_void_p, "PropertyData"),
            (["in"], c_ulong, "DataLength"),
            (["out"], POINTER(c_ulong), "BytesReturned"),
        ),
    ]


def bt_audio_filters() -> list[dict]:
    from pycaw.pycaw import AudioUtilities

    enumerator = AudioUtilities.GetDeviceEnumerator()
    found: list[dict] = []
    for flow in (E_RENDER, E_CAPTURE):
        collection = enumerator.EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE | DEVICE_STATE_UNPLUGGED)
        for i in range(collection.GetCount()):
            dev = collection.Item(i)
            try:
                name = AudioUtilities.CreateDevice(dev).FriendlyName or ""
                topo = dev.Activate(IDeviceTopology._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IDeviceTopology)
                for c in range(topo.GetConnectorCount()):
                    filter_id = topo.GetConnector(c).GetDeviceIdConnectedTo()
                    if filter_id and filter_id.lower().startswith("{2}.\\\\?\\bth"):
                        found.append({"endpoint": name, "filtro": filter_id, "estado": dev.GetState()})
            except comtypes.COMError:
                continue
    return found


def _ks_send(filter_id: str, prop_id: int) -> bool:
    from pycaw.pycaw import AudioUtilities

    enumerator = AudioUtilities.GetDeviceEnumerator()
    device = enumerator.GetDevice(filter_id)
    ks = device.Activate(IKsControl._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IKsControl)
    prop = KSPROPERTY(KSPROPSETID_BTAUDIO, prop_id, KSPROPERTY_TYPE_GET)
    try:
        ks.KsProperty(ctypes.byref(prop), ctypes.sizeof(prop), None, 0)
        return True
    except comtypes.COMError as exc:
        log.info("KsProperty %s fallo en %s: %s", prop_id, filter_id, exc)
        return False


def filters_for(device: dict, filters: list[dict]) -> list[str]:
    mac = device["direccion"].replace(":", "").lower()
    name = normalize(device["nombre"])
    ids: list[str] = []
    for f in filters:
        fid = f["filtro"].lower()
        if (mac and mac in fid) or (name and name in normalize(f["endpoint"])):
            if f["filtro"] not in ids:
                ids.append(f["filtro"])
    return ids


def send_to_device(device: dict, prop_id: int) -> tuple[int, int]:
    ids = filters_for(device, bt_audio_filters())
    ok = sum(1 for fid in ids if _ks_send(fid, prop_id))
    return ok, len(ids)


async def bt_radio():
    from winrt.windows.devices.radios import Radio, RadioKind

    for radio in await Radio.get_radios_async():
        if radio.kind == RadioKind.BLUETOOTH:
            return radio
    return None


async def set_radio(kind_name: str, on: bool) -> str:
    from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState

    kind = RadioKind.BLUETOOTH if kind_name == "bluetooth" else RadioKind.WI_FI
    access = await Radio.request_access_async()
    if access != RadioAccessStatus.ALLOWED:
        raise ToolError("Windows no me deja cambiar la radio. Revise Privacidad > Radios.")
    for radio in await Radio.get_radios_async():
        if radio.kind == kind:
            await radio.set_state_async(RadioState.ON if on else RadioState.OFF)
            return "encendido" if on else "apagado"
    raise ToolError(f"No encontre el adaptador de {kind_name}.")


def _format_mac(value: int) -> str:
    raw = f"{value:012x}"
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


async def paired_devices() -> list[dict]:
    from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    selector = BluetoothDevice.get_device_selector_from_pairing_state(True)
    out: list[dict] = []
    for info in await DeviceInformation.find_all_async_aqs_filter(selector):
        try:
            dev = await BluetoothDevice.from_id_async(info.id)
        except OSError:
            continue
        if dev is None:
            continue
        major = int(dev.class_of_device.major_class)
        out.append(
            {
                "nombre": dev.name or info.name,
                "conectado": dev.connection_status == BluetoothConnectionStatus.CONNECTED,
                "tipo": _MAJOR_CLASS.get(major, "otro"),
                "direccion": _format_mac(dev.bluetooth_address),
                "id": info.id,
            }
        )
    return out


async def _find(nombre: str) -> dict:
    devices = await paired_devices()
    match, _ = best_match(nombre, devices, key=lambda d: d["nombre"], threshold=60)
    if match is None:
        names = ", ".join(d["nombre"] for d in devices) or "ninguno"
        raise ToolError(f"No encontre '{nombre}' entre los emparejados: {names}.")
    return match


async def _wait_status(device: dict, connected: bool, seconds: float = 10) -> bool:
    for _ in range(int(seconds / 0.5)):
        await asyncio.sleep(0.5)
        current = next((d for d in await paired_devices() if d["id"] == device["id"]), None)
        if current and current["conectado"] == connected:
            return True
    return False


def _open_settings() -> None:
    os.startfile("ms-settings:bluetooth")


@tool("Enciende o apaga el Bluetooth de la PC.")
async def bluetooth_encender(encender: Annotated[bool, "true enciende, false apaga"] = True) -> dict:
    return {"bluetooth": await set_radio("bluetooth", encender)}


@tool("Lista los dispositivos Bluetooth emparejados (parlantes, auriculares, celulares) y si estan conectados.")
async def listar_bluetooth() -> dict:
    radio = await bt_radio()
    devices = await paired_devices()
    return {
        "bluetooth_encendido": bool(radio and int(radio.state) == 1),
        "dispositivos": [{k: d[k] for k in ("nombre", "conectado", "tipo")} for d in devices],
    }


@tool(
    "Conecta un parlante o auricular Bluetooth ya emparejado (ej. 'JBL', 'Philips') y opcionalmente "
    "lo deja como salida de audio. El dispositivo debe estar encendido.",
    timeout=40,
)
async def conectar_bluetooth(
    nombre: Annotated[str, "Nombre del dispositivo"],
    usar_como_salida: Annotated[bool, "Dejarlo como salida de audio predeterminada"] = True,
) -> dict:
    radio = await bt_radio()
    if radio is not None and int(radio.state) != 1:
        await set_radio("bluetooth", True)
        await asyncio.sleep(2)
    device = await _find(nombre)
    if device["conectado"]:
        result = {"dispositivo": device["nombre"], "estado": "ya estaba conectado"}
    else:
        ok, total = await in_worker(send_to_device, device, KSPROPERTY_ONESHOT_RECONNECT)
        if total == 0:
            _open_settings()
            raise ToolError(
                f"{device['nombre']} no tiene perfil de audio registrado en Windows; abri la configuracion "
                "de Bluetooth para conectarlo a mano."
            )
        connected = ok > 0 and await _wait_status(device, True)
        if not connected:
            raise ToolError(f"No respondio {device['nombre']}. Verifique que este encendido y cerca.")
        result = {"dispositivo": device["nombre"], "estado": "conectado"}

    if usar_como_salida and device["tipo"] == "audio":
        from jarvis.tools import audio_ctl

        for _ in range(10):
            outputs = await in_worker(audio_ctl.output_devices)
            match, _ = best_match(device["nombre"], outputs, key=lambda d: d.FriendlyName or "", threshold=70)
            if match is not None:
                await in_worker(audio_ctl.set_default_output, match)
                result["salida_audio"] = match.FriendlyName
                break
            await asyncio.sleep(1)
    return result


@tool("Desconecta un parlante o auricular Bluetooth (sigue emparejado).", timeout=30)
async def desconectar_bluetooth(nombre: Annotated[str, "Nombre del dispositivo"]) -> dict:
    device = await _find(nombre)
    if not device["conectado"]:
        return {"dispositivo": device["nombre"], "estado": "ya estaba desconectado"}
    ok, total = await in_worker(send_to_device, device, KSPROPERTY_ONESHOT_DISCONNECT)
    if total == 0 or ok == 0:
        _open_settings()
        raise ToolError(f"No pude desconectar {device['nombre']}; abri la configuracion de Bluetooth.")
    await _wait_status(device, False, seconds=6)
    return {"dispositivo": device["nombre"], "estado": "desconectado"}
