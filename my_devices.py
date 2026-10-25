#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外设电量 —?Windows 10/11 无线外设电量托盘工具

纯标准库 + ctypes + tkinter，零第三方依赖?
用法?    python my_devices.py              # 托盘 + 简洁面板?    python my_devices.py --once       # 读一次并打印
    python my_devices.py --list       # 列出的 HID 接口
    python my_devices.py --panel      # 打开面板（不常驻托盘?    python my_devices.py --install    # 创建开机自启?+ 桌面入口
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import ctypes.wintypes as w
import dataclasses
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import base64
import zlib
import threading
import time
from typing import Optional

UI_SCALE = 1.0


# ===========================================================================
# Win32 基础
# ===========================================================================
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)

INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_RW = 0x00000001 | 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102

kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                         ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.ResetEvent.argtypes = [wt.HANDLE]
user32.SetTimer.argtypes = [wt.HWND, wt.UINT, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wt.HWND, wt.UINT]
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.SetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_size_t]
user32.SetClassLongPtrW.restype = ctypes.c_size_t
dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s: str = ""):
        super().__init__()
        self.parse(s or "{00000000-0000-0000-0000-000000000000}")

    def parse(self, s: str):
        s = s.strip().strip("{}")
        parts = s.split("-", 3)
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        rest = bytes.fromhex(parts[3].replace("-", ""))
        for i in range(8):
            self.Data4[i] = rest[i]

    def as_str(self) -> str:
        return ("{%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}"
                % (self.Data1, self.Data2, self.Data3,
                   self.Data4[0], self.Data4[1], self.Data4[2], self.Data4[3],
                   self.Data4[4], self.Data4[5], self.Data4[6], self.Data4[7]))


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberOutputDataIndices", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


class HIDP_RANGE(ctypes.Structure):
    _fields_ = [("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT),
                ("StringMin", wt.USHORT), ("StringMax", wt.USHORT),
                ("DesignatorMin", wt.USHORT), ("DesignatorMax", wt.USHORT),
                ("DataIndexMin", wt.USHORT), ("DataIndexMax", wt.USHORT)]


class HIDP_NOTRANGE(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("Reserved1", wt.USHORT),
                ("StringIndex", wt.USHORT), ("Reserved2", wt.USHORT),
                ("DesignatorIndex", wt.USHORT), ("Reserved3", wt.USHORT),
                ("DataIndex", wt.USHORT), ("Reserved4", wt.USHORT)]


class HIDP_UNION(ctypes.Union):
    _fields_ = [("Range", HIDP_RANGE), ("NotRange", HIDP_NOTRANGE)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte), ("BitField", wt.USHORT),
        ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
        ("LinkUsagePage", wt.USHORT), ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte), ("BitSize", wt.USHORT),
        ("ReportCount", wt.USHORT), ("Reserved2", wt.USHORT * 5),
        ("UnitsExp", wt.ULONG), ("Units", wt.ULONG),
        ("LogicalMin", wt.LONG), ("LogicalMax", wt.LONG),
        ("PhysicalMin", wt.LONG), ("PhysicalMax", wt.LONG),
        ("u", HIDP_UNION),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                          wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), wt.LPWSTR, wt.DWORD,
    ctypes.POINTER(wt.DWORD)]

hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetProductString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetManufacturerString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetOutputReport.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS),
                                  ctypes.POINTER(wt.USHORT), ctypes.c_void_p]
hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, wt.USHORT,
                                   ctypes.POINTER(wt.ULONG), ctypes.c_void_p,
                                   ctypes.POINTER(HIDP_VALUE_CAPS), ctypes.c_void_p]

GUID_DEVINTERFACE_HID = GUID("{4D1E55B2-F16F-11CF-88CB-001111000030}")
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

HIDP_INPUT = 0
HIDP_OUTPUT = 1
HIDP_FEATURE = 2

# ===========================================================================
# HID 枚举与?# ===========================================================================


@dataclasses.dataclass(slots=True)
class HidIface:
    path: str
    vid: int
    pid: int
    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int
    product: str = ""
    manufacturer: str = ""
    instance: str = ""

    @property
    def label(self) -> str:
        return (f"{self.vid:04X}:{self.pid:04X} "
                f"UP={self.usage_page:04X} U={self.usage:04X} "
                f"in={self.input_len} out={self.output_len} feat={self.feature_len}")


def _open_path(path: str, access: int, overlapped: bool = False) -> int:
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    return kernel32.CreateFileW(path, access, FILE_SHARE_RW, None,
                                OPEN_EXISTING, flags, None)


def enumerate_hid() -> list[HidIface]:
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVINTERFACE_HID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE_VALUE or not hdev:
        return []
    out: list[HidIface] = []
    try:
        idx = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(GUID_DEVINTERFACE_HID), idx,
                    ctypes.byref(did)):
                break
            idx += 1
            need = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), buf, need.value,
                    ctypes.byref(need), None):
                continue
            # DevicePath ?cbSize(DWORD) 之后：偏?4
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            inst = ctypes.create_unicode_buffer(512)
            inst_len = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInstanceIdW(
                hdev, ctypes.byref(did), inst, 512, ctypes.byref(inst_len))

            h = _open_path(path, 0)
            if not h or h == INVALID_HANDLE_VALUE:
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(attrs)
                hid.HidD_GetAttributes(h, ctypes.byref(attrs))
                caps = HIDP_CAPS()
                pp = ctypes.c_void_p()
                if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    hid.HidP_GetCaps(pp, ctypes.byref(caps))
                    hid.HidD_FreePreparsedData(pp)
                prod = ctypes.create_unicode_buffer(256)
                hid.HidD_GetProductString(h, prod, 512)
                manu = ctypes.create_unicode_buffer(256)
                hid.HidD_GetManufacturerString(h, manu, 512)
                out.append(HidIface(
                    path=path, vid=attrs.VendorID, pid=attrs.ProductID,
                    usage_page=caps.UsagePage, usage=caps.Usage,
                    input_len=caps.InputReportByteLength,
                    output_len=caps.OutputReportByteLength,
                    feature_len=caps.FeatureReportByteLength,
                    product=prod.value, manufacturer=manu.value,
                    instance=inst.value))
            finally:
                kernel32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


class HidError(Exception):
    pass


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD),
                ("hEvent", wt.HANDLE)]


class HidDevice:
    """的 HID 接口?
    读句柄终单狔 FILE_FLAG_OVERLAPPED 打开：绝不在同一句柄上并发阻?    ReadFile ?WriteFile/IOCTL（实测会直接进程）?    """

    def __init__(self, path: str, write: bool = False):
        self.path = path
        self.write_mode = write
        self.h = 0
        self.rh = 0
        access = GENERIC_READ | (GENERIC_WRITE if write else 0)
        self.h = _open_path(path, access)
        if not self.h or self.h == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            if write:
                self.h = _open_path(path, 0)
            if not self.h or self.h == INVALID_HANDLE_VALUE:
                raise HidError(f"打开接口失败（错误码 {err}）")
            self.write_mode = False
        self.rh = _open_path(path, GENERIC_READ, overlapped=True)
        if not self.rh or self.rh == INVALID_HANDLE_VALUE:
            self.rh = 0

    def close(self):
        for h in (self.rh, self.h):
            if h and h != INVALID_HANDLE_VALUE:
                kernel32.CloseHandle(h)
        self.rh = 0
        self.h = 0

    def write_output(self, data: bytes) -> int:
        buf = ctypes.create_string_buffer(data, len(data))
        written = wt.DWORD(0)
        ok = kernel32.WriteFile(self.h, buf, len(data),
                                ctypes.byref(written), None)
        if not ok:
            raise HidError(f"写入失败（错误码 {ctypes.get_last_error()}）")
        return written.value

    def read_input(self, length: int, timeout_ms: int = 800) -> Optional[bytes]:
        if not self.rh:
            return None
        ev = kernel32.CreateEventW(None, True, False, None)
        if not ev:
            return None
        try:
            ov = OVERLAPPED()
            ov.hEvent = ev
            buf = ctypes.create_string_buffer(length)
            got = wt.DWORD(0)
            ok = kernel32.ReadFile(self.rh, buf, length,
                                   ctypes.byref(got), ctypes.byref(ov))
            if not ok and ctypes.get_last_error() != ERROR_IO_PENDING:
                return None
            if kernel32.WaitForSingleObject(ev, timeout_ms) == WAIT_TIMEOUT:
                kernel32.CancelIoEx(self.rh, ctypes.byref(ov))
                return None
            kernel32.GetOverlappedResult(self.rh, ctypes.byref(ov),
                                         ctypes.byref(got), False)
            return bytes(buf.raw[:got.value])
        finally:
            kernel32.CloseHandle(ev)

    def get_feature(self, length: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(length)
        if hid.HidD_GetFeature(self.h, buf, length):
            return bytes(buf.raw[:length])
        return None

    def get_feature_report(self, report_id: int, length: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(length)
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))[0] = report_id & 0xFF
        if hid.HidD_GetFeature(self.h, buf, length):
            return bytes(buf.raw[:length])
        return None

    def set_feature(self, data: bytes) -> bool:
        buf = ctypes.create_string_buffer(data, len(data))
        return bool(hid.HidD_SetFeature(self.h, buf, len(data)))

    def set_output_report(self, data: bytes) -> bool:
        buf = ctypes.create_string_buffer(data, len(data))
        return bool(hid.HidD_SetOutputReport(self.h, buf, len(data)))

    def drain(self):
        while self.rh and self.read_input(256, 15):
            pass

    def value_caps(self, kind: int) -> list[HIDP_VALUE_CAPS]:
        pp = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(self.h, ctypes.byref(pp)):
            return []
        try:
            n = wt.USHORT(0)
            hid.HidP_GetValueCaps(kind, None, ctypes.byref(n), pp)
            if not n.value:
                return []
            arr = (HIDP_VALUE_CAPS * n.value)()
            if not hid.HidP_GetValueCaps(kind, arr, ctypes.byref(n), pp):
                return []
            return list(arr[:n.value])
        finally:
            hid.HidD_FreePreparsedData(pp)

    def usage_value(self, kind: int, report: bytes,
                    cap: HIDP_VALUE_CAPS) -> Optional[int]:
        pp = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(self.h, ctypes.byref(pp)):
            return None
        try:
            val = wt.ULONG(0)
            buf = ctypes.create_string_buffer(report, len(report))
            usage = (cap.u.NotRange.Usage if not cap.IsRange
                     else cap.u.Range.UsageMin)
            ok = hid.HidP_GetUsageValue(kind, cap.UsagePage, cap.LinkCollection,
                                        usage, ctypes.byref(val),
                                        pp, ctypes.byref(cap), buf)
            return val.value if ok else None
        finally:
            hid.HidD_FreePreparsedData(pp)


# ===========================================================================
# 协议：CRC / JL_RCSP / ATK
# ===========================================================================
def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_jl_frame(seq: int, payload: bytes, report_id: int = 0x00,
                   include_zero: bool = True, ch: int = 0x10,
                   sub: int = 0x02, total: int = 64) -> bytes:
    """杰理 JL_RCSP 帧?
    include_zero=True  ?帧体?0x00 头（?Report ID 设备?65 字节报文?    include_zero=False ?Report ID 占字节，帧体从 0xFE ?    """
    f = bytearray(total)
    f[0] = report_id
    base = 1
    if include_zero:
        f[base] = 0x00
        base += 1
    start = base
    f[base] = 0xFE
    f[base + 1] = seq & 0xFF
    f[base + 2] = ch
    f[base + 3] = 0x00
    f[base + 4] = sub
    f[base + 5] = 0x00
    f[base + 6] = 0x00
    f[base + 7] = len(payload) & 0xFF
    f[base + 8] = (len(payload) >> 8) & 0xFF
    f[base + 9:base + 9 + len(payload)] = payload
    crc = crc16_ccitt(bytes(f[start:base + 9 + len(payload)]))
    f[base + 9 + len(payload)] = (crc >> 8) & 0xFF
    f[base + 10 + len(payload)] = crc & 0xFF
    return bytes(f)


@dataclasses.dataclass(slots=True)
class JLFrame:
    seq: int
    ch: int
    sub: int
    payload: bytes
    crc_ok: bool

    def tlvs(self):
        out, i, p = [], 0, self.payload
        while i + 2 <= len(p):
            t, ln = p[i], p[i + 1]
            out.append((t, p[i + 2:i + 2 + ln]))
            i += 2 + ln
        return out


def parse_jl_frame(report: bytes) -> Optional[JLFrame]:
    """在报文里找 0xFE 帧头（允许前 1~3 字节的 Report ID / 0x00）。"""
    for start in range(0, 4):
        if start + 12 > len(report):
            break
        if report[start] != 0xFE:
            continue
        ln = report[start + 7] | (report[start + 8] << 8)
        if ln > 60 or start + 9 + ln + 2 > len(report):
            continue
        payload = report[start + 9:start + 9 + ln]
        crc = (report[start + 9 + ln] << 8) | report[start + 10 + ln]
        return JLFrame(seq=report[start + 1], ch=report[start + 2],
                       sub=report[start + 4], payload=payload,
                       crc_ok=(crc == crc16_ccitt(report[start:start + 9 + ln])))
    return None


ATK2_PAYLOAD = bytes([0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01])
ATK1_PAYLOAD = bytes([0x04] + [0x00] * 15)  # 16 字节命令，cmd[15] 由校验?

def atk1_checksum(report_id: int, cmd: bytes) -> int:
    """ATK 16 字节命令：sum(REPORT_ID + cmd[0..14])，校验 = (0x55 - sum) & 0xFF。"""
    total = report_id + sum(cmd[:15])
    return (0x55 - (total & 0xFF)) & 0xFF


def atk_request2(report_id: int = 0x08, total: int = 64) -> bytes:
    body = bytes([report_id]) + ATK2_PAYLOAD
    return body + bytes(total - len(body))


def atk_request1(report_id: int = 0x08, total: int = 17) -> bytes:
    cmd = bytearray(ATK1_PAYLOAD)
    cmd[15] = atk1_checksum(report_id, bytes(cmd))
    body = bytes([report_id]) + bytes(cmd)
    return body + bytes(max(0, total - len(body)))


def atk1_valid(rep: bytes) -> bool:
    if len(rep) < 17 or rep[0] != 0x08 or rep[1] != 0x04:
        return False
    return (sum(rep[:16]) + rep[16]) & 0xFF == 0x55


# ===========================================================================
# 读取结果
# ===========================================================================
@dataclasses.dataclass(slots=True)
class DpiConfig:
    """迈从 A7 Pro 的六档 DPI 配置（无线活动档位）。"""
    stages: tuple[int, ...]
    current_index: int
    usb_index: int
    profile_index: int
    reserved: int
    stage_count: int
    sensor: int
    raw_body: bytes = b""

    @property
    def current_dpi(self) -> int:
        if 0 <= self.current_index < len(self.stages):
            return int(self.stages[self.current_index])
        return 0


@dataclasses.dataclass(slots=True)
class Reading:
    ok: bool = False
    level: Optional[int] = None
    status: str = "unknown"
    source: str = ""
    detail: str = ""
    name: str = ""
    ts: float = dataclasses.field(default_factory=time.time)
    present: bool = True     # 设备接口是否存在（False = 真的没插）
    stale: bool = False      # True = 本次没读到，用的是上次成功值
    vid: int = 0
    pid: int = 0
    usage_page: int = 0
    hid_known: bool = False
    dpi_config: Optional[DpiConfig] = None
    xinput_level: Optional[int] = None
    xinput_slot: Optional[int] = None

    @property
    def text(self) -> str:
        if self.ok and self.level is not None:
            return f"{self.level}%"
        if self.status == "battery_unavailable":
            return "无电量数据"
        if self.status == "asleep":
            return "休眠"
        return "未连接"


STATUS_TEXT = {"charging": "充电中", "discharging": "放电中",
               "full": "已充满", "wired": "有线模式",
               "xinput_full": "四档满", "xinput_medium": "四档中",
               "xinput_low": "低电量", "xinput_empty": "即将耗尽",
               "battery_unavailable": "无电量数据", "unknown": "未知"}


# ===========================================================================
# Windows XInput（Xbox 手柄）
# ===========================================================================
XINPUT_BATTERY_DEVTYPE_GAMEPAD = 0
XINPUT_BATTERY_TYPE_DISCONNECTED = 0
XINPUT_BATTERY_TYPE_WIRED = 1
XINPUT_BATTERY_TYPE_ALKALINE = 2
XINPUT_BATTERY_TYPE_NIMH = 3
XINPUT_BATTERY_TYPE_UNKNOWN = 4
XINPUT_BATTERY_LEVEL_EMPTY = 0
XINPUT_BATTERY_LEVEL_LOW = 1
XINPUT_BATTERY_LEVEL_MEDIUM = 2
XINPUT_BATTERY_LEVEL_FULL = 3
ERROR_DEVICE_NOT_CONNECTED = 1167

XINPUT_LEVEL_PERCENT = {0: 5, 1: 30, 2: 70, 3: 100}
XINPUT_LEVEL_STATUS = {
    0: "xinput_empty", 1: "xinput_low",
    2: "xinput_medium", 3: "xinput_full",
}
XINPUT_LEVEL_TEXT = {0: "空", 1: "低", 2: "中", 3: "满"}
XINPUT_TYPE_TEXT = {
    XINPUT_BATTERY_TYPE_WIRED: "有线",
    XINPUT_BATTERY_TYPE_ALKALINE: "碱性电池",
    XINPUT_BATTERY_TYPE_NIMH: "镍氢电池",
}


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte),
                ("BatteryLevel", ctypes.c_ubyte)]


_XINPUT_DLL = None
_XINPUT_LOAD_TRIED = False


def _xinput_dll():
    """延迟加载 XInput，避免没有 Xbox 手柄时占用额外资源。"""
    global _XINPUT_DLL, _XINPUT_LOAD_TRIED
    if _XINPUT_LOAD_TRIED:
        return _XINPUT_DLL
    _XINPUT_LOAD_TRIED = True
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            lib = ctypes.WinDLL(name)
            state_func = lib.XInputGetState
            state_func.argtypes = [wt.DWORD, ctypes.c_void_p]
            state_func.restype = wt.DWORD
            func = lib.XInputGetBatteryInformation
            func.argtypes = [wt.DWORD, ctypes.c_ubyte,
                             ctypes.POINTER(XINPUT_BATTERY_INFORMATION)]
            func.restype = wt.DWORD
            _XINPUT_DLL = lib
            break
        except (OSError, AttributeError):
            continue
    return _XINPUT_DLL


def xbox_read_xinput(_iface: Optional[HidIface] = None) -> Reading:
    """读取 XInput 四档电量；XInput 不提供精确百分比和充电状态。"""
    lib = _xinput_dll()
    source = "Windows XInput（四档电量）"
    if lib is None:
        return Reading(ok=False, present=True, source=source,
                       detail="当前系统找不到 XInput 接口")
    last_error = 0
    for slot in range(4):
        state = (ctypes.c_ubyte * 16)()
        state_code = int(lib.XInputGetState(slot, ctypes.byref(state)))
        info = XINPUT_BATTERY_INFORMATION()
        code = int(lib.XInputGetBatteryInformation(
            slot, XINPUT_BATTERY_DEVTYPE_GAMEPAD, ctypes.byref(info)))
        if state_code != 0 and code == ERROR_DEVICE_NOT_CONNECTED:
            continue
        if state_code == 0 and code != 0:
            return Reading(
                ok=False, present=True, status="battery_unavailable",
                source=source,
                detail=(f"XInput 槽位 {slot + 1}；手柄已连接，"
                        f"但电池接口返回错误码 {code}"))
        if code != 0:
            last_error = code
            continue
        battery_type = int(info.BatteryType)
        if battery_type == XINPUT_BATTERY_TYPE_DISCONNECTED:
            if state_code == 0:
                return Reading(
                    ok=False, present=True, status="battery_unavailable",
                    source=source,
                    detail=(f"XInput 槽位 {slot + 1}；手柄已连接，"
                            "但当前电池组未上报电量（兼容性限制）"))
            continue
        if battery_type == XINPUT_BATTERY_TYPE_WIRED:
            return Reading(
                ok=True, level=100, status="wired", source=source,
                detail=f"XInput 槽位 {slot + 1}；有线连接，不提供电池百分比")
        if battery_type == XINPUT_BATTERY_TYPE_UNKNOWN:
            return Reading(
                ok=False, present=True, status="battery_unavailable",
                source=source,
                detail=("XInput 未提供可靠电量；蓝牙连接可能不支持，"
                        "建议使用 Xbox 无线适配器"))
        raw_level = int(info.BatteryLevel)
        if raw_level not in XINPUT_LEVEL_PERCENT:
            return Reading(
                ok=False, present=True, source=source,
                detail=f"XInput 返回未知电量档位 {raw_level}")
        level = XINPUT_LEVEL_PERCENT[raw_level]
        battery_text = XINPUT_TYPE_TEXT.get(battery_type, "无线电池")
        return Reading(
            ok=True, level=level, status=XINPUT_LEVEL_STATUS[raw_level],
            source=source,
            detail=(f"XInput 槽位 {slot + 1}；{battery_text}；"
                    f"四档电量={XINPUT_LEVEL_TEXT[raw_level]}"
                    "（非精确百分比）"),
            xinput_level=raw_level, xinput_slot=slot)
    detail = (f"XInput 返回错误码 {last_error}" if last_error else
              "XInput 暂未返回手柄电量，轻按手柄按键后重试")
    return Reading(ok=False, present=True, source=source, detail=detail)


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, int(v)))


# ===========================================================================
# 各策略?# ===========================================================================
def read_standard_battery(iface: HidIface) -> Optional[Reading]:
    if not iface.feature_len:
        return None
    dev = HidDevice(iface.path, write=False)
    try:
        for cap in dev.value_caps(HIDP_FEATURE):
            usage = cap.u.NotRange.Usage if not cap.IsRange else cap.u.Range.UsageMin
            if (cap.UsagePage, usage) not in ((0x0006, 0x0020), (0x000D, 0x003B),
                                              (0x0085, 0x0065)):
                continue
            rep = dev.get_feature(cap.ReportID or iface.feature_len)
            if not rep:
                continue
            val = dev.usage_value(HIDP_FEATURE, rep, cap)
            if val is None:
                continue
            return Reading(ok=True, level=_clamp(val), source="标准 HID 电量字段",
                           detail=f"UsagePage=0x{cap.UsagePage:04X} "
                                  f"Usage=0x{usage:04X} 原={val}")
    except HidError as e:
        return Reading(ok=False, source="标准 HID 电量字段", detail=str(e))
    finally:
        dev.close()
    return None


def atk_read_at(iface: HidIface, kind: str) -> Reading:
    total = iface.output_len or (64 if kind == "atk2" else 17)
    if kind == "atk2":
        if total < 64:
            return Reading(ok=False, source="ATK 协议 2",
                           detail=f"输出报文只有 {total} 字节，不是 64 字节接口")
        req, want = atk_request2(0x08, total), 64
    else:
        if total < 17:
            return Reading(ok=False, source="ATK 协议 1",
                           detail=f"输出报文只有 {total} 字节")
        req, want = atk_request1(0x08, total), 17
    dev = HidDevice(iface.path, write=True)
    try:
        last = b""
        for _ in range(3):
            dev.drain()
            dev.write_output(req)
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                rep = dev.read_input(max(want, iface.input_len), 250)
                if not rep:
                    continue
                last = rep
                if kind == "atk2":
                    if len(rep) > 7 and rep[1] == 0x72 and rep[5] == 0x07:
                        return Reading(ok=True, level=_clamp(rep[7]),
                                       source="ATK 协议 2（0xFF05/64B）",
                                       detail=f"回复 {rep[:12].hex(' ')}")
                else:
                    if atk1_valid(rep):
                        wired = len(rep) > 7 and rep[7] != 0
                        return Reading(ok=True, level=_clamp(rep[6]),
                                       status="wired" if wired else "discharging",
                                       source="ATK 协议（0xFF02/17B，0x55 校验）",
                                       detail=f"电量字节=0x{rep[6]:02X} "
                                              f"校验=0x{rep[16]:02X}")
        return Reading(ok=False, source=f"ATK {kind}",
                       detail=("设备没有给出有效回复（鼠标可能在休眠，动一下鼠标）"
                               if not last else f"回复无效：{last[:12].hex(' ')}"))
    except HidError as e:
        return Reading(ok=False, source=f"ATK {kind}", detail=str(e))
    finally:
        dev.close()


def mchose_read(iface: HidIface, timeout: float = 1.0) -> Reading:
    """MCHOSE V9 Turbo（C-Media dongle）电量协议?
    实测：向 Report ID 0x55 ?55 65 01 ...，回 55 65 <电量> <状态? ...
    电量 = resp[2]?x3C = 60%）?    """
    dev = HidDevice(iface.path, write=True)
    try:
        req = bytes([0x55, 0x65, 0x01]) + bytes(max(0, iface.output_len - 3))
        dev.drain()
        dev.write_output(req)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rep = dev.read_input(max(64, iface.input_len), 250)
            if not rep or len(rep) < 3:
                continue
            if rep[0] == 0x55 and rep[1] == 0x65 and 0 <= rep[2] <= 100:
                return Reading(ok=True, level=rep[2],
                               source="MCHOSE 协议（0x55/0x65）",
                               detail=f"回复 {rep[:8].hex(' ')}")
        return Reading(ok=False, source="MCHOSE 协议",
                       detail="没有收到 0x55/0x65 应答（耳机可能没开机）")
    except HidError as e:
        return Reading(ok=False, source="MCHOSE 协议", detail=str(e))
    finally:
        dev.close()


def mchose_a7_pro_read(iface: HidIface) -> Reading:
    """迈从 A7 Pro（VID 5253 / PID 1021，RealTek Feature 0x11）。

    官方 WebHID 在收到请求回显时会继续取 Report，而不是把第一包当成结果。
    本机实测 A7 Pro 固件返回的是明文状态体 ``11 06 53 52 10 00 ...``，
    其中 ``53 52`` 正是 VID 0x5253；部分固件则返回逐字节 XOR 0xFF 的版本。
    因此两种格式都尝试解析，并用回包内的 VID 识别真正的状态包。
    """
    source = "MCHOSE A7 Pro 内置协议（Feature 0x11/65B）"
    if iface.usage_page != 0xFF01 or iface.feature_len != 65:
        return Reading(ok=False, source=source,
                       detail=(f"接口不匹配：UsagePage=0x{iface.usage_page:04X} "
                               f"Feature={iface.feature_len}B"))
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source=source, detail=str(e))

    req = bytes([0x11, 0xF9, 0xFF]) + bytes([0xFF] * 62)
    last = b""
    last_error = ""
    try:
        for send_attempt in range(2):
            if not dev.set_feature(req):
                if send_attempt == 0:
                    time.sleep(0.12)
                    continue
                return Reading(
                    ok=False, source=source,
                    detail=(f"Feature 请求被拒绝（错误码 "
                            f"{ctypes.get_last_error()}），请关闭官方驱动后重试"))

            # 鼠标收到查询后往往先回显请求，再在下一包给出真实数据。
            # 参考官方驱动最多读取 6 次，期间不重发，以免配置通道被打乱。
            time.sleep(0.12 + send_attempt * 0.04)
            for receive_index in range(6):
                try:
                    rep = dev.get_feature_report(0x11, 65)
                except HidError as exc:
                    last_error = str(exc)
                    rep = None
                if rep:
                    last = rep

                # 官方 0x11/0x06 状态体：uint16 VID、uint16 PID、uint32 固件版本。
                # body[9] 为保留/状态字节，body[10] 为 chargeStatus，body[11]
                # 才是电量。实测 Windows HID 返回明文；兼容旧固件的 XOR 0xFF 编码。
                if rep and len(rep) >= 14 and rep[0] == 0x11 and rep[1] == 0x06:
                    payload = rep[2:]
                    variants = (
                        (payload, "明文"),
                        (bytes(b ^ 0xFF for b in payload), "XOR"),
                    )
                    for body, encoding in variants:
                        if len(body) < 12:
                            continue
                        body_vid = struct.unpack_from("<H", body, 0)[0]
                        if body_vid != iface.vid:
                            continue
                        # 参考 MCHOSE 协议实现，电量位于 body[11]；仅对该字段
                        # 异常时回退到 body[9]，兼容极少数旧固件布局。
                        level_index = 11
                        level = body[level_index]
                        if level > 100:
                            level_index = 9
                            level = body[level_index]
                        flags = body[10]
                        charge_code = flags & 0x0F
                        full_flag = (flags >> 4) & 0x0F
                        if charge_code == 2 or full_flag:
                            status = "full"
                        elif charge_code == 1:
                            status = "charging"
                        else:
                            status = "discharging"
                        if level <= 100:
                            return Reading(
                                ok=True, level=level, status=status,
                                source=source,
                                detail=(f"{encoding}状态体 {body[:12].hex(' ')}；"
                                        f"电量=0x{level:02X} (body[{level_index}]) "
                                        f"状态=0x{flags:02X}"))
                if receive_index + 1 < 6:
                    time.sleep(0.035)
            if send_attempt == 0:
                time.sleep(0.12)

        if last:
            detail = f"回复校验失败：{last[:16].hex(' ')}"
        elif last_error:
            detail = last_error
        else:
            detail = "没有收到电量回复（鼠标可能休眠）"
        return Reading(ok=False, source=source, detail=detail)
    except HidError as e:
        return Reading(ok=False, source=source, detail=str(e))
    finally:
        dev.close()


def vgn_v98_pro_read(iface: HidIface) -> Reading:
    """VGN V98 Pro V2 键盘（VID 320F / PID 5088）。

    私有控制集合 UsagePage=0xFF1C、Usage=0x0092，走 64 字节中断 OUT。
    查询帧 ``04 00 00 1A 06 00 00 00``，回包 ``rep[8]`` 为电量、
    ``rep[9] == 1`` 表示正在充电。
    """
    source = "VGN V98 Pro 内置协议（FF1C/64B）"
    if (iface.usage_page != 0xFF1C or iface.usage != 0x0092
            or iface.output_len < 60 or iface.input_len < 60):
        return Reading(
            ok=False, source=source,
            detail=(f"接口不匹配：UsagePage=0x{iface.usage_page:04X} "
                    f"Usage=0x{iface.usage:04X} "
                    f"IN={iface.input_len}B OUT={iface.output_len}B"))
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source=source, detail=str(e))
    req = (bytes.fromhex("04 00 00 1A 06 00 00 00")
           + bytes(max(0, iface.output_len - 8)))
    last = b""
    try:
        for attempt in range(3):
            dev.drain()
            try:
                dev.write_output(req)
            except HidError as e:
                if attempt == 2:
                    return Reading(ok=False, source=source, detail=str(e))
                time.sleep(0.12)
                continue
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                rep = dev.read_input(max(64, iface.input_len), 250)
                if not rep:
                    continue
                last = rep
                if (len(rep) >= 10 and rep[:5] == bytes.fromhex("04 00 00 1A 06")
                        and 0 <= rep[8] <= 100):
                    level = int(rep[8])
                    status = ("full" if level >= 100
                              else ("charging" if rep[9] == 1
                                    else "discharging"))
                    return Reading(
                        ok=True, level=level, status=status, source=source,
                        detail=(f"回包 {rep[:12].hex(' ')}；"
                                f"电量={level}% 充电={rep[9]}"))
            time.sleep(0.08)
        return Reading(ok=False, source=source,
                       detail=(f"回包校验失败：{last[:16].hex(' ')}"
                               if last else "没有收到键盘电量回报（键盘可能休眠）"))
    except HidError as e:
        return Reading(ok=False, source=source, detail=str(e))
    finally:
        dev.close()


MCHOSE_DPI_MIN = 100
MCHOSE_DPI_MAX = 26000
MCHOSE_DPI_STEP = 100
_MCHOSE_IO_LOCK = threading.RLock()
_MCHOSE_LAST_IO = 0.0


class MchoseDpiError(RuntimeError):
    pass


def _mchose_feature_frame(report_id: int, command: int,
                          args: bytes = b"", length: int = 65) -> bytes:
    """构造 RealTek 私有 Feature Report：载荷逐字节 XOR 0xFF。"""
    payload_len = length - 1
    payload = bytes([command]) + args
    if len(payload) > payload_len:
        raise MchoseDpiError("DPI 请求超过 Feature Report 长度")
    payload += b"\x00" * (payload_len - len(payload))
    return bytes([report_id]) + bytes(v ^ 0xFF for v in payload)


def _mchose_send(dev: HidDevice, frame: bytes, action: str) -> None:
    """串行发送命令，并遵守鼠标配置通道的 25 ms 最小间隔。"""
    global _MCHOSE_LAST_IO
    delay = 0.025 - (time.monotonic() - _MCHOSE_LAST_IO)
    if delay > 0:
        time.sleep(delay)
    ctypes.set_last_error(0)
    if not dev.set_feature(frame):
        err = ctypes.get_last_error()
        raise MchoseDpiError(
            f"{action}失败（错误码 {err or '未知'}），请关闭官方驱动后重试")
    _MCHOSE_LAST_IO = time.monotonic()
    time.sleep(0.035)


def _mchose_read_dpi_with_device(dev: HidDevice,
                                 attempts: int = 6) -> DpiConfig:
    request = _mchose_feature_frame(0x12, 0x67)
    last = ""
    for attempt in range(max(1, attempts)):
        try:
            with _MCHOSE_IO_LOCK:
                _mchose_send(dev, request, "读取 DPI 配置")
                rep = dev.get_feature_report(0x12, 65)
        except (HidError, MchoseDpiError) as exc:
            last = str(exc)
            rep = None
        if rep and len(rep) == 65 and rep[0] == 0x12:
            decoded = bytes(b ^ 0xFF for b in rep[1:])
            if decoded and decoded[0] == 0x67 and len(decoded) == 64:
                body = decoded[1:]
                if not any(body):
                    # 长报告通道偶尔会卡死并回一整块 0。此时绝不能把它
                    # 当成 0 DPI；重新选择配置档位可以把通道唤醒。
                    last = "配置通道返回全零"
                    if attempt + 1 < attempts:
                        profile = _mchose_feature_frame(0x11, 0x58, b"\x00")
                        try:
                            with _MCHOSE_IO_LOCK:
                                _mchose_send(dev, profile, "重新选择配置档位")
                        except (HidError, MchoseDpiError) as exc:
                            last = f"{last}；恢复失败：{exc}"
                        time.sleep(0.15 * (attempt + 1))
                    continue
                stages = tuple(struct.unpack_from("<6H", body, 4))
                current_index = body[2] & 0x0F
                if current_index >= len(stages):
                    last = f"活动档位越界：{current_index}"
                else:
                    return DpiConfig(
                        stages=stages,
                        current_index=current_index,
                        usb_index=body[1] & 0x0F,
                        profile_index=body[0],
                        reserved=body[3],
                        stage_count=body[16],
                        sensor=body[17],
                        raw_body=body,
                    )
            else:
                last = "回复命令不匹配"
        elif rep:
            last = f"回复长度/Report ID 异常（{len(rep)}B）"
        if attempt + 1 < attempts:
            time.sleep(0.06 * (attempt + 1))
    raise MchoseDpiError(
        "没有读到 DPI 配置" + (f"：{last}" if last else "（鼠标可能休眠）")
        + "；请轻动鼠标或拔插接收器后重试")


def mchose_a7_pro_read_dpi(iface: HidIface) -> DpiConfig:
    """读取迈从 A7 Pro 的完整 DPI 配置。"""
    if iface.usage_page != 0xFF01 or iface.feature_len != 65:
        raise MchoseDpiError(
            f"接口不匹配：UsagePage=0x{iface.usage_page:04X} "
            f"Feature={iface.feature_len}B")
    dev = HidDevice(iface.path, write=True)
    try:
        with _MCHOSE_IO_LOCK:
            return _mchose_read_dpi_with_device(dev)
    finally:
        dev.close()


def _mchose_config_matches(wanted: bytes, current: bytes) -> bool:
    """比较写入与回读，忽略鼠标自己维护的配置字节。"""
    if len(wanted) < 63 or len(current) < 63:
        return False
    for index, (a, b) in enumerate(zip(wanted[:63], current[:63])):
        if index == 3:          # 鼠标会在活动档位变化时自行翻转
            continue
        if index == 17:         # bit 0 由传感器保留，无法清零
            if (a & 0xFE) != (b & 0xFE):
                return False
            continue
        if a != b:
            return False
    return True


def mchose_a7_pro_write_dpi(iface: HidIface, dpi: int) -> DpiConfig:
    """修改无线活动档位 DPI，并通过回读确认硬件已接受。"""
    if iface.usage_page != 0xFF01 or iface.feature_len != 65:
        raise MchoseDpiError(
            f"接口不匹配：UsagePage=0x{iface.usage_page:04X} "
            f"Feature={iface.feature_len}B")
    target = int(round(int(dpi) / MCHOSE_DPI_STEP) * MCHOSE_DPI_STEP)
    target = max(MCHOSE_DPI_MIN, min(MCHOSE_DPI_MAX, target))
    dev = HidDevice(iface.path, write=True)
    try:
        with _MCHOSE_IO_LOCK:
            config = _mchose_read_dpi_with_device(dev)
            if len(config.raw_body) < 63:
                raise MchoseDpiError("DPI 配置长度异常，请重新拔插接收器")

            # 修改完整配置块中的 X/Y 两轴，再原样写回。这样不会丢掉
            # 回报率、按键、传感器等本工具没有建模的设置。
            body = bytearray(config.raw_body)
            stage = config.current_index
            struct.pack_into("<H", body, 4 + stage * 2, target)
            struct.pack_into("<H", body, 51 + stage * 2, target)
            request = _mchose_feature_frame(0x12, 0x57, bytes(body))
            _mchose_send(dev, request, "写入 DPI")

            # 鼠标写入配置后需要短暂提交；轮询回读确认整块配置已接受，
            # 不只依赖 SetFeature 的返回值。
            wanted = bytes(body)
            last = config
            for attempt in range(5):
                time.sleep(0.08 * (attempt + 1))
                try:
                    current = _mchose_read_dpi_with_device(dev, attempts=2)
                except MchoseDpiError:
                    continue
                last = current
                if _mchose_config_matches(wanted, current.raw_body):
                    return current
            raise MchoseDpiError(
                f"配置已发送，但回读未确认（当前 {last.current_dpi} DPI）；"
                "请关闭官方驱动后重试")
    finally:
        dev.close()


def read_builtin_dpi_config(ifaces: list[HidIface], vid: int,
                            pid: int) -> Optional[DpiConfig]:
    profile = BUILTIN_PROTOCOLS.get((vid, pid))
    if not profile or not profile.get("dpi_reader"):
        return None
    cands = [d for d in ifaces
             if d.vid == vid and d.pid == pid
             and d.usage_page == int(profile["usage_page"])]
    last = None
    for iface in cands:
        try:
            return profile["dpi_reader"](iface)
        except Exception as exc:
            last = exc
    if last is not None:
        raise last
    return None


def write_builtin_dpi(vid: int, pid: int, dpi: int) -> DpiConfig:
    profile = BUILTIN_PROTOCOLS.get((vid, pid))
    if not profile or not profile.get("dpi_writer"):
        raise MchoseDpiError("该设备不支持 DPI 修改")
    cands = [d for d in enumerate_hid()
             if d.vid == vid and d.pid == pid
             and d.usage_page == int(profile["usage_page"])]
    if not cands:
        raise MchoseDpiError("设备未连接或接收器已拔出")
    return profile["dpi_writer"](cands[0], dpi)


def jl_read(iface: HidIface, seconds: float = 1.2) -> Reading:
    """在 64 字节厂商接口上尝试杰理 TLV 协议，自动探测 Report ID。"""
    dev = HidDevice(iface.path, write=True)
    try:
        ids = [0x55, 0x00]
        for rid in range(0, 0x100):
            if rid in ids:
                continue
            try:
                if dev.set_output_report(bytes([rid]) + bytes(63)):
                    ids.append(rid)
                    break
            except HidError:
                continue
        for rid in ids:
            for include_zero in (True, False):
                frame = build_jl_frame(0, b"\x01\x00", report_id=rid,
                                       include_zero=include_zero, total=64)
                try:
                    dev.drain()
                    dev.write_output(frame)
                except HidError:
                    continue
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    rep = dev.read_input(64, 200)
                    if not rep:
                        continue
                    fr = parse_jl_frame(rep)
                    if not fr:
                        continue
                    for t, v in fr.tlvs():
                        if t == 0x01 and v:
                            return Reading(ok=True, level=_clamp(v[0]),
                                           source="杰理 JL_RCSP（TLV 0x01）",
                                           detail=f"ReportID=0x{rid:02X} "
                                                  f"回复 {rep[:16].hex(' ')}")
        return Reading(ok=False, source="杰理 JL_RCSP",
                       detail="耳机没有上报电量（确认耳机已开机并与 dongle 连上）")
    except HidError as e:
        return Reading(ok=False, source="杰理 JL_RCSP", detail=str(e))
    finally:
        dev.close()


def endgame_read(iface: HidIface) -> Optional[Reading]:
    """Endgame Gear 无线鼠标（feature 0xA1，64 字节）。"""
    if iface.vid != 0x3367 or iface.feature_len < 64:
        return None
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="Endgame 协议", detail=str(e))
    try:
        for _ in range(3):
            dev.set_feature(bytes([0xA1, 0xB4]) + bytes(62))
            time.sleep(0.3)
            rep = dev.get_feature_report(0xA1, 64)
            if (rep and len(rep) > 17 and rep[1] == 0x01
                    and 0 <= rep[16] <= 100):
                return Reading(ok=True, level=rep[16],
                               source="Endgame 协议（Feature 0xA1）",
                               detail=f"原始 {rep[:20].hex(' ')}")
        return Reading(ok=False, source="Endgame 协议",
                       detail="没有有效回复（鼠标可能休眠）")
    except HidError as e:
        return Reading(ok=False, source="Endgame 协议", detail=str(e))
    finally:
        dev.close()


def vaxee_read(iface: HidIface) -> Optional[Reading]:
    """VAXEE / ZYGEN（feature 0x0E，电量以 5% 为长）?"""
    if iface.vid != 0x3057 or iface.feature_len < 64:
        return None
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="VAXEE 协议", detail=str(e))
    try:
        for _ in range(2):
            dev.set_feature(bytes([0x0E, 0xA5, 0x0B, 0x01, 0x01]) + bytes(59))
            time.sleep(0.15)
            rep = dev.get_feature_report(0x0E, 64)
            if rep and len(rep) > 5 and 0 <= rep[5] <= 20:
                return Reading(ok=True, level=rep[5] * 5,
                               source="VAXEE 协议（Feature 0x0E）",
                               detail=f"原始 {rep[:10].hex(' ')}")
        return Reading(ok=False, source="VAXEE 协议", detail="没有有效回复")
    except HidError as e:
        return Reading(ok=False, source="VAXEE 协议", detail=str(e))
    finally:
        dev.close()


def nordic_read(iface: HidIface) -> Optional[Reading]:
    """Nordic nRF 方案接收器（feature 0x05，32 字节）。"""
    if iface.vid != 0x1915 or iface.feature_len < 32:
        return None
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="Nordic 协议", detail=str(e))
    try:
        for _ in range(2):
            dev.set_feature(bytes([0x05, 0x15, 0x00, 0x00, 0x01]) + bytes(27))
            time.sleep(0.1)
            rep = dev.get_feature_report(0x05, 32)
            if rep and len(rep) > 9 and 0 <= rep[9] <= 100:
                return Reading(ok=True, level=rep[9],
                               source="Nordic 协议（Feature 0x05）",
                               detail=f"原始 {rep[:12].hex(' ')}")
        return Reading(ok=False, source="Nordic 协议", detail="没有有效回复")
    except HidError as e:
        return Reading(ok=False, source="Nordic 协议", detail=str(e))
    finally:
        dev.close()


def _hidpp_call(dev: HidDevice, rid: int, devidx: int, featidx: int,
                func: int, params: bytes, length: int,
                timeout: float = 0.3) -> Optional[bytes]:
    body = bytes([rid, devidx, featidx, ((func & 0x0F) << 4) | 0x01]) + params
    body = (body + bytes(length))[:length]
    dev.drain()
    dev.write_output(body)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rep = dev.read_input(length, 100)
        if not rep or len(rep) < 5:
            continue
        if rep[0] != rid or rep[1] != devidx or rep[2] != featidx:
            continue
        if ((rep[3] >> 4) & 0x0F) != func:
            continue
        return rep
    return None


def logitech_read(iface: HidIface) -> Optional[Reading]:
    """罗技 HID++ 2.0?x1000 / 0x1004 / 0x1001）?"""
    if iface.vid != 0x046D or iface.output_len < 7:
        return None
    rid = 0x11 if iface.output_len >= 20 else 0x10
    length = 20 if rid == 0x11 else 7
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="罗技 HID++", detail=str(e))
    try:
        for devidx in (0xFF, 1, 2, 3, 4, 5, 6):
            for fid, kind in ((0x1004, "unified"), (0x1000, "status"),
                              (0x1001, "voltage")):
                rep = _hidpp_call(dev, rid, devidx, 0x00, 0x00,
                                  bytes([fid >> 8, fid & 0xFF]), length)
                if not rep or (len(rep) > 3 and rep[3] & 0x80):
                    continue
                featidx = rep[4]
                if not featidx:
                    continue
                rep = _hidpp_call(dev, rid, devidx, featidx, 0x00, b"", length)
                if not rep or len(rep) < 6:
                    continue
                if kind == "unified":
                    level, status = rep[4], rep[5]
                    return Reading(ok=True, level=_clamp(level),
                                   status="charging" if status in (1, 2, 4) else
                                   ("full" if status == 3 else "discharging"),
                                   source="罗技 HID++（0x1004 统一电池）",
                                   detail=f"索引 {devidx} 状态={status}")
                if kind == "status" and len(rep) >= 7:
                    level, status = rep[4], rep[6]
                    return Reading(ok=True, level=_clamp(level),
                                   status="charging" if status in (1, 2, 4) else
                                   ("full" if status == 3 else "discharging"),
                                   source="罗技 HID++（0x1000 电池状态）",
                                   detail=f"索引 {devidx} 状态={status}")
                if kind == "voltage":
                    mv = rep[4] | (rep[5] << 8)
                    if 2000 <= mv <= 5000:
                        return Reading(ok=False, source="罗技 HID++（0x1001 电压）",
                                       detail=f"{mv} mV（该机型不直接给百分比）")
        return Reading(ok=False, source="罗技 HID++", detail="没有找到电池特性")
    except HidError as e:
        return Reading(ok=False, source="罗技 HID++", detail=str(e))
    finally:
        dev.close()


def listen_battery(iface: HidIface, seconds: float = 2.0) -> Optional[Reading]:
    if not iface.input_len:
        return None
    try:
        dev = HidDevice(iface.path, write=False)
    except HidError:
        return None
    try:
        caps = []
        for cap in dev.value_caps(HIDP_INPUT):
            usage = cap.u.NotRange.Usage if not cap.IsRange else cap.u.Range.UsageMin
            if (cap.UsagePage, usage) == (0x0006, 0x0020):
                caps.append(cap)
        if not caps:
            return None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rep = dev.read_input(iface.input_len, 250)
            if not rep:
                continue
            for cap in caps:
                val = dev.usage_value(HIDP_INPUT, rep, cap)
                if val is not None:
                    return Reading(ok=True, level=_clamp(val),
                                   source="输入报告电量字段",
                                   detail=f"原值={val}")
        return Reading(ok=False, source="输入报告电量字段",
                       detail="监听期间没有收到电量字段")
    finally:
        dev.close()


# ===========================================================================
# 设备服务
# ===========================================================================
@dataclasses.dataclass(slots=True)
class DeviceSpec:
    key: str
    name: str
    kind: str
    ids: tuple


DEVICES = [
    DeviceSpec("mouse", "ATK Dragonfly A9 MINI", "atk",
               ((0x373B, 0x1278), (0x373B, 0x128E),
                (0x373B, 0x1216), (0x3554, 0x1216))),
    DeviceSpec("headset", "MCHOSE V9 Turbo", "headset",
               ((0x3837, 0x600D), (0x1837, 0x600D), (0x3654, 0x6901))),
]


BUILTIN_PROTOCOLS = {
    (0x5253, 0x1021): {
        "name": "MCHOSE A7 Pro",
        "usage_page": 0xFF01,
        "reader": mchose_a7_pro_read,
        "dpi_reader": mchose_a7_pro_read_dpi,
        "dpi_writer": mchose_a7_pro_write_dpi,
    },
    (0x320F, 0x5088): {
        "name": "VGN V98 Pro",
        "usage_page": 0xFF1C,
        "reader": vgn_v98_pro_read,
    },
}

# Xbox 控制器通过系统 XInput 接口读取；PID 列表覆盖常见 Xbox One/Series
# 控制器及使用微软协议兼容模式的第三方手柄。未知 PID 仍可通过 HID 常规方式添加，
# 但只有 XInput 能提供电量的型号需要在此登记。
XBOX_XINPUT_PIDS = (
    0x02D1, 0x02DD, 0x02E3, 0x02EA, 0x02FD,
    0x0B00, 0x0B05, 0x0B0A, 0x0B12, 0x0B13, 0x0B20,
)
for _xbox_pid in XBOX_XINPUT_PIDS:
    BUILTIN_PROTOCOLS.setdefault((0x045E, _xbox_pid), {
        "name": "Xbox 控制器",
        "usage_page": 0x0001,
        "reader": xbox_read_xinput,
        "type": "gamepad",
    })


DEVICE_TYPES = (
    ("mouse", "鼠标"),
    ("keyboard", "键盘"),
    ("headset", "耳机"),
    ("speaker", "音箱"),
    ("gamepad", "手柄"),
    ("receiver", "接收器"),
    ("other", "其他"),
)
DEVICE_TYPE_LABELS = dict(DEVICE_TYPES)


def infer_device_type(vid: int, pid: int, name: str = "") -> str:
    """为旧配置和新设备推断一个可修改的默认类型。"""
    pair = (int(vid), int(pid))
    if pair == (0x5253, 0x1021) or pair in DEVICES[0].ids:
        return "mouse"
    if pair == (0x320F, 0x5088):
        return "keyboard"
    if pair in DEVICES[1].ids:
        return "headset"
    profile = BUILTIN_PROTOCOLS.get(pair)
    if profile and profile.get("type"):
        return str(profile["type"])
    if pair[0] == 0x045E:
        return "gamepad"
    text = (name or "").lower()
    aliases = (
        ("gamepad", ("手柄", "gamepad", "controller", "xbox")),
        ("headset", ("耳机", "耳麦", "headset", "earphone", "buds")),
        ("speaker", ("音箱", "音响", "speaker")),
        ("keyboard", ("键盘", "keyboard")),
        ("mouse", ("鼠标", "mouse")),
    )
    for type_key, words in aliases:
        if any(word in text for word in words):
            return type_key
    return "receiver"


def has_builtin_protocol(vid: int, pid: int) -> bool:
    return (vid, pid) in BUILTIN_PROTOCOLS


def read_builtin_protocol(ifaces: list[HidIface], vid: int,
                          pid: int) -> Optional[Reading]:
    """用内置型号协议读取电量；没有对应型号时返回 None。"""
    profile = BUILTIN_PROTOCOLS.get((vid, pid))
    if profile is None:
        return None
    usage_page = int(profile["usage_page"])
    cands = [d for d in ifaces
             if d.vid == vid and d.pid == pid and d.usage_page == usage_page]
    if not cands:
        return Reading(
            ok=False, present=any(d.vid == vid and d.pid == pid for d in ifaces),
            source=f"{profile['name']} 内置协议",
            detail=f"没有找到 UsagePage=0x{usage_page:04X} 的私有接口")
    last = None
    for d in cands:
        try:
            r = profile["reader"](d)
        except Exception as e:
            r = Reading(ok=False, source=f"{profile['name']} 内置协议",
                        detail=str(e))
        if r is not None and r.ok and r.level is not None:
            return r
        if r is not None:
            last = r
    return last


def discover_builtin_devices(ifaces: list[HidIface]) -> dict[str, Reading]:
    """发现带内置协议的设备，避免依赖首次抓包或标准 HID 电量字段。"""
    out: dict[str, Reading] = {}
    for (vid, pid), profile in BUILTIN_PROTOCOLS.items():
        group = [d for d in ifaces if d.vid == vid and d.pid == pid]
        if not group:
            continue
        r = read_builtin_protocol(group, vid, pid)
        if r is None:
            continue
        key = f"x{vid:04X}{pid:04X}"
        prod = next((d.product for d in group if d.product), "")
        meta = next((d for d in group
                     if d.usage_page == profile["usage_page"]), group[0])
        r.name = custom_names().get(key) or prod or profile["name"]
        r.present = True
        r.vid, r.pid, r.usage_page = vid, pid, meta.usage_page
        r.hid_known = True
        if profile.get("dpi_reader"):
            try:
                r.dpi_config = read_builtin_dpi_config(group, vid, pid)
            except Exception:
                r.dpi_config = None
        out[key] = r
    return out


def read_mouse(ifaces: list[HidIface]) -> Reading:
    # 测试：临时忘记鼠标（VID/PID 与协讼
    return Reading(ok=False, name="", source="", present=False,
                   detail="已临时忘记该设备")
    spec = DEVICES[0]
    cands = [d for d in ifaces if (d.vid, d.pid) in spec.ids]
    if not cands:
        return Reading(ok=False, name=spec.name, source="", present=False,
                       detail="没有找到 ATK Dragonfly A9 MINI（dongle 未插入？）")
    for d in cands:
        if d.usage_page == 0xFF05 and d.output_len >= 64:
            r = atk_read_at(d, "atk2")
            if r.ok:
                return r
    for d in cands:
        if d.usage_page == 0xFF02 and d.output_len >= 17:
            r = atk_read_at(d, "atk1")
            if r.ok and r.level is not None:
                return r
    for d in cands:
        if d.output_len >= 64:
            r = atk_read_at(d, "atk2")
            if r.ok:
                return r
    for d in cands:
        r = read_standard_battery(d)
        if r and r.ok:
            return r
    for d in cands:
        r = listen_battery(d, 1.5)
        if r and r.ok:
            return r
    return Reading(ok=False, name=spec.name, source="ATK 协议",
                   detail="没有读到电量（鼠标可能休眠，动一下鼠标再试）")


def read_headset(ifaces: list[HidIface]) -> Reading:
    # 测试：临时忘记机（VID/PID 与协讼
    return Reading(ok=False, name="", source="", present=False,
                   detail="已临时忘记该设备")
    spec = DEVICES[1]
    cands = [d for d in ifaces if (d.vid, d.pid) in spec.ids]
    if not cands:
        return Reading(ok=False, name=spec.name, source="", present=False,
                       detail="没有找到耳机 dongle（未插入？）")
    vendor = [d for d in cands if d.output_len >= 64 and d.usage_page >= 0xFF00]
    for d in vendor:
        r = mchose_read(d)
        if r.ok:
            r.name = spec.name
            return r
    for d in vendor:
        r = jl_read(d)
        if r.ok:
            r.name = spec.name
            return r
    for d in cands:
        r = read_standard_battery(d)
        if r and r.ok:
            r.name = spec.name
            return r
    for d in cands:
        r = listen_battery(d, 1.2)
        if r and r.ok:
            r.name = spec.name
            return r
    return Reading(ok=False, name=spec.name, source="杰理 JL_RCSP",
                   detail="耳机没有上报电量（确认耳机已开机并与 dongle 连上）")


def read_all(ifaces: Optional[list[HidIface]] = None) -> dict[str, Reading]:
    ifaces = ifaces if ifaces is not None else enumerate_hid()
    mouse = read_mouse(ifaces)
    headset = read_headset(ifaces)
    # 设备名统用接收器（HID Product String）上报的名字
    for key, r in (("mouse", mouse), ("headset", headset)):
        spec = DEVICES[0] if key == "mouse" else DEVICES[1]
        if r.present:
            prod = next((d.product for d in ifaces
                         if (d.vid, d.pid) in spec.ids and d.product), "")
            r.name = custom_names().get(key) or prod or spec.name
        else:
            r.name = ""
        cand = [d for d in ifaces if (d.vid, d.pid) in spec.ids]
        if cand:
            meta = next((d for d in cand if d.usage_page >= 0xFF00), cand[0])
            r.vid, r.pid, r.usage_page = (meta.vid, meta.pid,
                                          meta.usage_page)
            r.hid_known = True
    out = {"mouse": mouse, "headset": headset}
    try:
        out.update(discover_builtin_devices(ifaces))
    except Exception:
        pass
    try:
        out.update(discover_extras(ifaces))
    except Exception:
        pass
    return out


def known_ids() -> set:
    """内置协议由专用读取器处理，通用发现不再重复创建卡片。"""
    return set(BUILTIN_PROTOCOLS)


def device_key(vid: int, pid: int) -> str:
    return f"x{vid:04X}{pid:04X}"


def has_device_protocol(vid: int, pid: int) -> bool:
    """内置协议、标准 HID 或用户抓包协议都算已有读取方案。"""
    return (has_builtin_protocol(vid, pid)
            or f"{vid:04X}:{pid:04X}" in user_protocols())


def save_standard_protocol(vid: int, pid: int) -> None:
    key = f"{vid:04X}:{pid:04X}"
    cfg = load_config()
    protos = cfg.get("protocols")
    if not isinstance(protos, dict):
        protos = {}
    if key not in protos:
        protos[key] = {"kind": "standard"}
        cfg["protocols"] = protos
        save_config(cfg)


def load_saved_devices() -> list:
    raw = load_config().get("devices")
    if not isinstance(raw, list):
        return []
    out = []
    for d in raw:
        try:
            vid = int(d["vid"])
            pid = int(d["pid"])
        except Exception:
            continue
        name = str(d.get("name", ""))
        stored_type = str(d.get("type", ""))
        type_key = (stored_type if stored_type in DEVICE_TYPE_LABELS
                    else infer_device_type(vid, pid, name))
        out.append({"vid": vid, "pid": pid, "name": name,
                    "type": type_key})
    return out


def save_saved_devices(devs: list) -> None:
    cfg = load_config()
    cfg["devices"] = devs
    save_config(cfg)


def read_saved_device(dev: dict, ifaces=None) -> Reading:
    vid = int(dev["vid"])
    pid = int(dev["pid"])
    name = dev.get("name") or ""
    ifaces = ifaces if ifaces is not None else enumerate_hid()
    cand = [d for d in ifaces if d.vid == vid and d.pid == pid]
    if not cand:
        return Reading(ok=False, present=False,
                       name=name or f"{vid:04X}:{pid:04X}",
                       status=("unplugged" if has_device_protocol(vid, pid)
                               else "unknown_proto"),
                       vid=vid, pid=pid, detail="未连接")
    prod = next((d.product for d in cand if d.product), "")
    if not name:
        name = prod or f"{vid:04X}:{pid:04X}"
    # 读取顺序：标准 HID -> 内置型号协议 -> 用户抓包协议。
    r = None
    for d in cand:
        try:
            rr = read_standard_battery(d)
        except Exception:
            rr = None
        if rr and rr.ok and rr.level is not None:
            r = rr
            break
    key = f"{vid:04X}:{pid:04X}"
    proto = user_protocols().get(key)
    if not r or not r.ok:
        if has_builtin_protocol(vid, pid):
            try:
                rr = read_builtin_protocol(cand, vid, pid)
                if rr and (rr.ok or rr.status == "battery_unavailable"):
                    r = rr
            except Exception:
                pass
        if ((not r or not r.ok) and proto is not None
                and proto.get("kind", "captured") != "standard"):
            try:
                rr = _read_user_protocol(cand, proto)
                if rr and rr.ok:
                    r = rr
            except Exception:
                pass
    elif proto is None:
        # 标准 HID 能直接读：保存成协议，之后休眠也算“有协议”
        save_standard_protocol(vid, pid)
    supported = has_device_protocol(vid, pid)
    if r is None:
        r = Reading(
            ok=False, present=True, name=name,
            status="asleep" if supported else "unknown_proto",
            detail=("设备休眠" if supported
                    else "协议未知，无法读取电量"))
    elif not r.ok and supported and r.status != "battery_unavailable":
        r.status = "asleep"
        if has_builtin_protocol(vid, pid):
            r.source = f"{BUILTIN_PROTOCOLS[(vid, pid)]['name']} 内置协议"
        elif proto is not None and proto.get("kind") == "standard":
            r.source = "标准 HID 电量"
        else:
            r.source = "抓包协议"
        r.detail = "设备休眠"
    r.name = name
    r.present = True
    meta = next((d for d in cand if d.usage_page >= 0xFF00), cand[0])
    r.vid, r.pid = vid, pid
    r.usage_page = meta.usage_page
    r.hid_known = True
    profile = BUILTIN_PROTOCOLS.get((vid, pid))
    if profile and profile.get("dpi_reader"):
        try:
            r.dpi_config = read_builtin_dpi_config(cand, vid, pid)
        except Exception:
            r.dpi_config = None
    return r


def _unknown_reading(name: str, ifaces: list[HidIface],
                     skip_compat: bool = False) -> Reading:
    """对未记录设备做只读试掼失败则标记协讜知?"""
    for d in ifaces:
        try:
            r = read_standard_battery(d)
        except Exception:
            r = None
        if r and r.ok and r.level is not None:
            r.name = name
            r.source = "标准 HID 电量"
            return r
    for d in ifaces:
        if not has_builtin_protocol(d.vid, d.pid):
            continue
        try:
            r = read_builtin_protocol(ifaces, d.vid, d.pid)
        except Exception:
            r = None
        if r and r.ok and r.level is not None:
            r.name = name
            return r
        break
    if not skip_compat:
        for d in ifaces:
            try:
                if d.usage_page == 0xFF02 and d.output_len >= 17:
                    r = atk_read_at(d, "atk1")
                elif d.output_len >= 64 and d.usage_page >= 0xFF00:
                    r = mchose_read(d, timeout=0.6)
                else:
                    r = None
            except Exception:
                r = None
            if r and r.ok and r.level is not None:
                r.name = name
                r.source = "兼容厂商协议"
                return r
        for fn in (endgame_read, vaxee_read, nordic_read, logitech_read):
            for d in ifaces:
                try:
                    r = fn(d)
                except Exception:
                    r = None
                if r is not None and r.ok and r.level is not None:
                    r.name = name
                    r.source = "厂商协议"
                    return r
    for d in ifaces[:2]:
        if not d.input_len:
            continue
        try:
            r = listen_battery(d, 0.6)
        except Exception:
            r = None
        if r and r.ok and r.level is not None:
            r.name = name
            r.source = "Input 监听"
            return r
    return Reading(ok=False, present=True, name=name,
                   status="unknown_proto", source="HID 通用",
                   detail="协议未知，无法读取电量")


def discover_extras(ifaces: list[HidIface]) -> dict[str, Reading]:
    """把未记录的厂商 HID 设备（2.4G 接收器）聚合成额外卡片。"""
    groups: dict[str, dict] = {}
    known = known_ids()
    for d in ifaces:
        if (d.vid, d.pid) in known:
            continue
        if d.usage_page < 0xFF00:
            continue
        # 2.4G 接收器一定有多字节输入/输出报告；只有 Feature 的（触控板等）跳过
        if not (d.output_len >= 2 or d.input_len >= 2):
            continue
        inst = (d.instance or "").upper()
        prod = (d.product or "").upper()
        if "HIDI2C" in inst or "ACPI" in inst or "HIDI2C" in prod:
            continue
        key = f"x{d.vid:04X}{d.pid:04X}"
        g = groups.get(key)
        if g is None:
            g = {"name": d.product or f"未知设备 {d.vid:04X}:{d.pid:04X}",
                 "ifaces": [], "vid": d.vid, "pid": d.pid}
            groups[key] = g
        g["ifaces"].append(d)
    out: dict[str, Reading] = {}
    for key, g in groups.items():
        name = custom_names().get(key) or g["name"]
        meta = next((d for d in g["ifaces"] if d.usage_page >= 0xFF00),
                    g["ifaces"][0])
        proto = user_protocols().get(f"{g['vid']:04X}:{g['pid']:04X}")
        if proto:
            r = _read_user_protocol(g["ifaces"], proto)
            if r is not None:
                r.name = name
                r.vid, r.pid, r.usage_page = (meta.vid, meta.pid,
                                              meta.usage_page)
                r.hid_known = True
                out[key] = r
                continue
        r = _unknown_reading(name, g["ifaces"], skip_compat=True)
        r.name = name
        r.vid, r.pid, r.usage_page = meta.vid, meta.pid, meta.usage_page
        out[key] = r
    return out


# ---------------------------------------------------------------------------
# 上成功读数缓存：备休?关机读不到时沿用，只有真正没插才显示朿?# ---------------------------------------------------------------------------
def _last_store() -> dict:
    cfg = load_config()
    last = cfg.get("last")
    return last if isinstance(last, dict) else {}


def _save_last(last: dict) -> None:
    cfg = load_config()
    cfg["last"] = last
    save_config(cfg)


def apply_last_known(res: dict[str, Reading]) -> dict[str, Reading]:
    last = _last_store()
    changed = False
    out: dict[str, Reading] = {}
    for key, r in res.items():
        if r.ok and r.level is not None:
            last[key] = {"level": r.level, "ts": r.ts, "name": r.name,
                         "source": r.source, "status": r.status,
                         "vid": r.vid, "pid": r.pid,
                         "usage_page": r.usage_page,
                         "hid_known": r.hid_known,
                         "xinput_level": r.xinput_level,
                         "xinput_slot": r.xinput_slot}
            changed = True
            out[key] = r
            continue
        if ((not r.ok) and key in last
                and r.status not in ("unknown_proto", "unplugged",
                                     "battery_unavailable")):
            item = last[key]
            try:
                level = int(item.get("level"))
            except Exception:
                level = None
            if level is not None:
                ts = float(item.get("ts", time.time()))
                stamp = time.strftime("%H:%M", time.localtime(ts))
                out[key] = Reading(
                    ok=True, level=level,
                    status=item.get("status", "unknown"),
                    source=item.get("source", ""),
                    name=r.name or item.get("name", ""),
                    ts=ts, stale=True,
                    detail=f"上次更新 {stamp} · {r.detail}")
                out[key].vid = int(item.get("vid", 0) or 0)
                out[key].pid = int(item.get("pid", 0) or 0)
                out[key].usage_page = int(item.get("usage_page", 0) or 0)
                out[key].hid_known = bool(item.get("hid_known", False))
                xinput_level = item.get("xinput_level")
                if xinput_level is not None:
                    try:
                        out[key].xinput_level = int(xinput_level)
                    except (TypeError, ValueError):
                        pass
                xinput_slot = item.get("xinput_slot")
                if xinput_slot is not None:
                    try:
                        out[key].xinput_slot = int(xinput_slot)
                    except (TypeError, ValueError):
                        pass
                out[key].dpi_config = r.dpi_config
                continue
        out[key] = r
    if changed:
        _save_last(last)
    return out


# ===========================================================================
# 主 / 注册?# ===========================================================================
def reg_dword(path: str, name: str, default: int = 0) -> int:
    advapi32.RegGetValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, wt.LPCWSTR,
                                      wt.DWORD, ctypes.POINTER(wt.DWORD),
                                      ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
    advapi32.RegGetValueW.restype = wt.LONG
    val = wt.DWORD(0)
    size = wt.DWORD(4)
    try:
        rc = advapi32.RegGetValueW(
            ctypes.c_void_p(0x80000001),  # HKEY_CURRENT_USER
            path, name, 0x00000010, None,
            ctypes.byref(val), ctypes.byref(size))
        if rc == 0:
            return val.value
    except Exception:
        pass
    return default


def is_light_theme() -> bool:
    return reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                     "AppsUseLightTheme", 1) != 0


# ===========================================================================
# 图标（GDI 画电池，无需 Pillow?# ===========================================================================
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD),
                ("yHotspot", wt.DWORD), ("hbmMask", wt.HBITMAP),
                ("hbmColor", wt.HBITMAP)]


gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE,
                                   wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT,
                               ctypes.c_void_p]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wt.HGDIOBJ
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
gdi32.CreatePen.restype = wt.HPEN
gdi32.RoundRect.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int]
gdi32.Arc.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int]
gdi32.Rectangle.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int]
gdi32.Polygon.argtypes = [wt.HDC, ctypes.POINTER(wt.POINT), ctypes.c_int]
gdi32.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wt.COLORREF
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]

user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRoundRectRgn.restype = wt.HRGN
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
gdi32.GdiFlush.argtypes = []
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP

COLORREF = lambda r, g, b: (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)


def _level_color(level: Optional[int]) -> tuple[int, int, int]:
    if level is None:
        return (120, 128, 140)
    if level >= 60:
        return (34, 197, 94)
    if level >= 30:
        return (245, 158, 11)
    return (239, 68, 68)


def make_battery_icon(level: Optional[int], light: bool,
                      charging: bool = False, size: int = 32) -> int:
    """size×size 的电量圆环图标，4 倍超采样后缩小，边缘更圆。"""
    SS = 4
    big = size * SS
    hdc_screen = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc_screen)
    hbm_small = 0

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = big
    bmi.bmiHeader.biHeight = -big
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbm_big = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(memdc, hbm_big)
    try:
        key = COLORREF(255, 0, 255)
        bg = gdi32.CreateSolidBrush(key)
        rect = wt.RECT(0, 0, big, big)
        user32.FillRect(memdc, ctypes.byref(rect), bg)
        gdi32.DeleteObject(bg)

        # 电量：暗色轨道圆 + 顺时针进度弧
        s = big / 32.0
        cx = cy = big / 2.0
        radius = 12.0 * s
        thick = max(2, int(round(4 * s)))
        track = (95, 98, 105) if not light else (175, 180, 188)
        pen_track = gdi32.CreatePen(0, thick, COLORREF(*track))
        gdi32.SelectObject(memdc, pen_track)
        null_brush = gdi32.GetStockObject(5)  # NULL_BRUSH
        gdi32.SelectObject(memdc, null_brush)
        gdi32.Ellipse(memdc, int(cx - radius), int(cy - radius),
                      int(cx + radius), int(cy + radius))
        if level is not None:
            if charging:
                r, g, b = 34, 197, 94          # 充电：弧线绿色
            else:
                r, g, b = _level_color(level)
            pen_arc = gdi32.CreatePen(0, thick, COLORREF(r, g, b))
            gdi32.SelectObject(memdc, pen_arc)
            if level >= 100:
                gdi32.Ellipse(memdc, int(cx - radius), int(cy - radius),
                              int(cx + radius), int(cy + radius))
            else:
                theta = 2.0 * math.pi * max(2, min(100, level)) / 100.0
                ex = cx + radius * math.cos(-math.pi / 2 + theta)
                ey = cy + radius * math.sin(-math.pi / 2 + theta)
                # Arc 逆时针画：从终点绕回顶点，等价于从顶点顺时针的进度弧
                gdi32.Arc(memdc, int(cx - radius), int(cy - radius),
                          int(cx + radius), int(cy + radius),
                          int(round(ex)), int(round(ey)), int(cx), int(cy - radius))
            gdi32.DeleteObject(pen_arc)

        if charging:
            # 圆环中央的白色闪电，保证任务栏缩小到 16px 时也能辨认。
            bolt = [(18.0, 7.0), (10.0, 18.0), (15.0, 18.0),
                    (13.0, 25.0), (23.0, 13.0), (17.0, 13.0)]
            pts = (wt.POINT * len(bolt))()
            for i, (px, py) in enumerate(bolt):
                pts[i].x = int(round(px * s))
                pts[i].y = int(round(py * s))
            bolt_brush = gdi32.CreateSolidBrush(COLORREF(247, 250, 252))
            null_pen = gdi32.GetStockObject(8)  # NULL_PEN
            old_pen = gdi32.SelectObject(memdc, null_pen)
            old_brush = gdi32.SelectObject(memdc, bolt_brush)
            gdi32.Polygon(memdc, pts, len(bolt))
            gdi32.SelectObject(memdc, old_brush)
            gdi32.SelectObject(memdc, old_pen)
            gdi32.DeleteObject(bolt_brush)

        gdi32.SelectObject(memdc, old)
        gdi32.GdiFlush()
        raw = ctypes.string_at(bits, big * big * 4)

        # 4×4 覆盖率降采样：每个目标像素统计非洋红比例作为 alpha
        small = bytearray(size * size * 4)
        for y in range(size):
            for x in range(size):
                cnt = 0
                rs = gs = bs = 0
                for yy in range(y * SS, (y + 1) * SS):
                    base = yy * big * 4 + x * SS * 4
                    for xx in range(SS):
                        i = base + xx * 4
                        b_, g_, r_ = raw[i], raw[i + 1], raw[i + 2]
                        if (r_, g_, b_) != (255, 0, 255):
                            cnt += 1
                            rs += r_
                            gs += g_
                            bs += b_
                o = (y * size + x) * 4
                if cnt:
                    small[o] = bs // cnt
                    small[o + 1] = gs // cnt
                    small[o + 2] = rs // cnt
                    small[o + 3] = cnt * 255 // (SS * SS)

        bmi2 = BITMAPINFO()
        bmi2.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi2.bmiHeader.biWidth = size
        bmi2.bmiHeader.biHeight = -size
        bmi2.bmiHeader.biPlanes = 1
        bmi2.bmiHeader.biBitCount = 32
        bmi2.bmiHeader.biCompression = 0
        bits2 = ctypes.c_void_p()
        hbm_small = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi2), 0,
                                           ctypes.byref(bits2), None, 0)
        gdi32.SelectObject(memdc, hbm_small)
        ctypes.memmove(bits2, bytes(small), len(small))

        mask = gdi32.CreateBitmap(size, size, 1, 1, None)
        ii = ICONINFO()
        ii.fIcon = 1
        ii.hbmMask = mask
        ii.hbmColor = hbm_small
        icon = user32.CreateIconIndirect(ctypes.byref(ii))
        gdi32.DeleteObject(mask)
        return icon
    finally:
        if hbm_small:
            gdi32.DeleteObject(hbm_small)
        gdi32.DeleteObject(hbm_big)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, hdc_screen)

# ===========================================================================
# 开机自启?/ 桌面链接 / 忍方式（纯 ctypes COM?# ===========================================================================
def _shell_folder(csidl: int) -> str:
    buf = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return buf.value


def desktop_dir() -> str:
    return _shell_folder(0x0010)  # CSIDL_DESKTOPDIRECTORY


def startup_dir() -> str:
    return _shell_folder(0x0007)  # CSIDL_STARTUP


def startup_lnk() -> str:
    return os.path.join(startup_dir(), "2.4G.lnk")


def legacy_startup_lnk() -> str:
    return os.path.join(startup_dir(), "电量托盘.lnk")


def startup_enabled() -> bool:
    return (os.path.isfile(startup_lnk())
            or os.path.isfile(legacy_startup_lnk()))


def project_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def script_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(__file__)


def pythonw_path() -> str:
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(cand):
            return cand
    if exe.lower().endswith("pythonw.exe"):
        return exe
    return exe


def _com_method(obj, index, restype, *argtypes):
    vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vt[index])


def create_shortcut(lnk_path: str, target: str, args: str = "",
                    workdir: str = "", desc: str = "",
                    icon: str = "") -> None:
    ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    CLSID_ShellLink = GUID("{00021401-0000-0000-C000-000000000046}")
    IID_IShellLinkW = GUID("{000214F9-0000-0000-C000-000000000046}")
    IID_IPersistFile = GUID("{0000010B-0000-0000-C000-000000000046}")

    psl = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1,
                                ctypes.byref(IID_IShellLinkW), ctypes.byref(psl))
    if hr != 0 or not psl:
        raise RuntimeError(f"CoCreateInstance(CLSID_ShellLink) 失败 hr=0x{hr & 0xFFFFFFFF:08X}")

    # IShellLinkW: SetPath=20, SetArguments=11, SetWorkingDirectory=9,
    #              SetDescription=7, SetIconLocation=17
    _com_method(psl, 20, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(target))
    if args:
        _com_method(psl, 11, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(args))
    if workdir:
        _com_method(psl, 9, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(workdir))
    if desc:
        _com_method(psl, 7, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(desc))
    if icon:
        _com_method(psl, 17, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)(
            psl, ctypes.c_wchar_p(icon), 0)

    ppf = ctypes.c_void_p()
    _com_method(psl, 0, ctypes.c_long, ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p))(
        psl, ctypes.byref(IID_IPersistFile), ctypes.byref(ppf))
    if not ppf:
        _com_method(psl, 2, ctypes.c_ulong)(psl)
        raise RuntimeError("QueryInterface(IPersistFile) 失败")
    # IPersistFile: Save=6, SaveCompleted=7
    _com_method(ppf, 6, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)(
        ppf, ctypes.c_wchar_p(lnk_path), 1)
    _com_method(ppf, 2, ctypes.c_ulong)(ppf)
    _com_method(psl, 2, ctypes.c_ulong)(psl)
    ole32.CoUninitialize()


def install_startup() -> bool:
    path = startup_lnk()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if getattr(sys, "frozen", False):
        create_shortcut(path, script_path(), "", project_dir(),
                        "2.4G", script_path())
    else:
        create_shortcut(path, pythonw_path(), f'"{script_path()}"',
                        project_dir(), "2.4G", pythonw_path())
    if os.path.isfile(path):
        try:
            os.remove(legacy_startup_lnk())
        except FileNotFoundError:
            pass
    return os.path.isfile(path)


def remove_startup() -> None:
    for p in (startup_lnk(), legacy_startup_lnk()):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def migrate_startup_link() -> None:
    """把旧的“电量托盘.lnk”迁移成“2.4G.lnk”，指向当前程序。"""
    if os.path.isfile(legacy_startup_lnk()):
        try:
            install_startup()
        except Exception:
            pass


kernel32.CreateSymbolicLinkW.restype = wt.BOOL
kernel32.CreateSymbolicLinkW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]


def _create_dir_link(link: str, target: str) -> bool:
    try:
        if kernel32.CreateSymbolicLinkW(link, target, 0x1 | 0x2):
            if os.path.isdir(link):
                return True
    except Exception:
        pass
    # 降级：junction
    try:
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True, shell=False)
        return os.path.isdir(link)
    except Exception:
        return False


def _create_file_link(link: str, target: str) -> bool:
    try:
        if kernel32.CreateSymbolicLinkW(link, target, 0x2):
            if os.path.isfile(link):
                return True
    except Exception:
        pass
    try:
        os.link(target, link)
        return os.path.isfile(link)
    except Exception:
        pass
    # 跨卷 / 无号链接权限：后化成 .lnk（仍昌向脚朚链接，不昉朼
    try:
        create_shortcut(link + ".lnk", target, "", project_dir(),
                        "外设电量启动脚本", pythonw_path())
        return os.path.isfile(link + ".lnk")
    except Exception:
        return False


def install_desktop_links() -> list[str]:
    created = []
    desk = desktop_dir()
    if getattr(sys, "frozen", False):
        lnk = os.path.join(desk, "外设电量.lnk")
        if not os.path.exists(lnk):
            try:
                create_shortcut(lnk, script_path(), "", project_dir(),
                                "外设电量", script_path())
                created.append(lnk)
            except Exception:
                pass
        return created
    dlink = os.path.join(desk, "外设电量")
    flink = os.path.join(desk, "my_devices.py")
    if not os.path.exists(dlink):
        if _create_dir_link(dlink, project_dir()):
            created.append(dlink)
    if not os.path.exists(flink):
        if _create_file_link(flink, script_path()):
            created.append(flink if os.path.exists(flink) else flink + ".lnk")
    return created


# ===========================================================================
# 配置（只存刷新间隔；不是日志?# ===========================================================================
def config_dir() -> str:
    base = os.environ.get("APPDATA") or project_dir()
    return os.path.join(base, "BatteryTray")


def config_file() -> str:
    return os.path.join(config_dir(), "config.json")


def clamp_interval(sec) -> int:
    try:
        sec = int(sec)
    except Exception:
        return 60
    return max(30, min(1800, sec))


def load_config() -> dict:
    try:
        with open(config_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(data: dict) -> None:
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(config_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def custom_names() -> dict:
    names = load_config().get("names")
    return names if isinstance(names, dict) else {}


def usbpcap_path() -> str:
    p = load_config().get("usbpcap")
    if isinstance(p, str) and os.path.isfile(p):
        return p
    return ""


def set_usbpcap_path(path: str) -> None:
    cfg = load_config()
    cfg["usbpcap"] = path
    save_config(cfg)


def user_protocols() -> dict:
    v = load_config().get("protocols")
    return v if isinstance(v, dict) else {}


def save_user_protocol(vid: int, pid: int, proto: dict) -> None:
    cfg = load_config()
    protos = cfg.get("protocols")
    if not isinstance(protos, dict):
        protos = {}
    protos[f"{vid:04X}:{pid:04X}"] = {
        "kind": "captured",
        "requests": list(proto.get("requests", [])),
        "offset": int(proto.get("offset", 2)),
    }
    cfg["protocols"] = protos
    save_config(cfg)


def infer_generic(samples: list) -> dict:
    """通用推断：两次不同电量抓包，找差值≈电量巚字节?
    samples: [(电量, frames), ...]，frames ?[(ep, transfer, bytes)]?    """
    if len(samples) < 2:
        return None
    down = []
    up = []
    for _lv, frames in samples[:2]:
        rs = [d for ep, _tr, d in frames
              if (ep & 0x80) and ep != 0x80 and len(d) >= 4]
        os_ = [d for ep, _tr, d in frames
               if not (ep & 0x80) and ep != 0 and len(d) >= 3]
        down.append(rs)
        for d in os_:
            if d not in up:
                up.append(d)
    resp = down
    reqs = up
    if not requests_ok(reqs) or not resp[0] or not resp[1]:
        return None
    lv_a, lv_b = samples[0][0], samples[1][0]
    delta = lv_b - lv_a
    best = None
    best_n = 0
    for a in resp[0]:
        for b in resp[1]:
            if len(a) != len(b):
                continue
            for off in range(1, len(a) - 1):
                if a[off] == lv_a and b[off] == lv_b:
                    if abs((b[off] - a[off]) - delta) <= 2:
                        n = 0
                        for x in resp[0]:
                            for y in resp[1]:
                                if (len(x) == len(y) and len(x) > off
                                        and x[off] == lv_a
                                        and y[off] == lv_b):
                                    n += 1
                        if n > best_n:
                            best_n = n
                            best = off
    if best is None:
        return None
    return {
        "requests": [" ".join(f"{b:02X}" for b in d[:32])
                     for d in reqs[:6]],
        "offset": best,
    }


_DESCRIPTOR_PREFIXES = (
    b"\x12\x01",  # 设备描述符
    b"\x09\x02",  # 配置描述符
    b"\x09\x04",  # 接口描述符
    b"\x07\x05",  # 端点描述符
    b"\x09\x21",  # HID 描述符
    b"\x05\x01",  # 报告描述符：通用桌面
    b"\x05\x0c",  # 报告描述符：消费控制
    b"\x06\x00",  # 报告描述符：厂商页
    b"\x0a\x06",  # 设备限定符
    b"\x07\x07",  # Other Speed 配置
    b"\x0f\x05",  # BOS 描述符
)


def _looks_descriptor(d: bytes) -> bool:
    return len(d) >= 2 and d[:2] in _DESCRIPTOR_PREFIXES


def _looks_control_setup(d: bytes, ep: int) -> bool:
    """EP0 上不承载电量的标准控制请求。"""
    if ep not in (0x00, 0x80) or len(d) != 8:
        return False
    # GET_DESCRIPTOR / SET_CONFIGURATION / SET_INTERFACE / CLEAR_FEATURE
    return ((d[0] == 0x80 and d[1] == 0x06)
            or (d[0] == 0x00 and d[1] == 0x09)
            or (d[0] == 0x01 and d[1] == 0x0B)
            or (d[0] == 0x02 and d[1] == 0x01))


def _looks_string_descriptor(d: bytes, ep: int) -> bool:
    """EP0 上的字符串描述符帧：设备身份文本（厂商/产品/序列号），
    只是枚举期从设备读回来的 UTF-16 文本，绝不承载电量。
    不过滤会拿文本里的字母撞值，例如 'd' = 0x64 = 100%。"""
    if ep != 0x80:
        return False
    # 字符串描述符本体：bLength 必须等于实际长度，bDescriptorType = 0x03
    if len(d) >= 2 and d[1] == 0x03 and d[0] == len(d):
        return True
    # GET_DESCRIPTOR 的 setup 阶段（80 06 ...，固定 8 字节）：只是请求包，不是数据
    return len(d) == 8 and d[0] == 0x80 and d[1] == 0x06


def _split_frames(frames: list):
    ins = []
    outs = []
    for idx, (ep, _tr, d) in enumerate(frames):
        if not d:
            continue
        if ep & 0x80:
            if len(d) < 4:
                continue
            if ep == 0x80 and (_looks_descriptor(d)
                               or _looks_control_setup(d, ep)
                               or _looks_string_descriptor(d, ep)):
                continue
            if not any(d):
                continue
            ins.append((idx, d))
        elif ep != 0 and len(d) >= 3:
            outs.append((idx, d))
    return ins, outs


def _match_offsets(ins: list, level: int) -> set:
    out = set()
    for _idx, d in ins:
        for off in range(1, min(len(d), 64)):
            if d[off] == level:
                out.add(off)
    return out


def _requests_at(frames: list, ins: list, outs: list,
                 off: int, level: int) -> list:
    reqs = []
    oi = 0
    last_idx = -1
    last = None
    for idx, d in ins:
        while oi < len(outs) and outs[oi][0] < idx:
            last_idx, last = outs[oi][0], outs[oi][1]
            oi += 1
        if len(d) <= off or d[off] != level:
            continue
        if last is not None and idx - last_idx <= 80 and last not in reqs:
            reqs.append(last)
    if not reqs:
        prefixes = {d[:2] for _i, d in ins
                    if len(d) > off and d[off] == level}
        reqs = [d for _i, d in outs if d[:2] in prefixes]
    if not reqs:
        reqs = [d for _i, d in outs]
    return reqs[:6]


def _proto_at(reqs: list, off: int) -> dict:
    return {
        "requests": [" ".join(f"{b:02X}" for b in d[:32])
                     for d in reqs[:6]],
        "offset": off,
    }


def infer_single_ex(level: int, frames: list):
    """单次抓取：唯一字节位置直接采用，多个位置返回 ambiguous。"""
    level = int(level)
    if level <= 0:
        return None, "none"
    ins, outs = _split_frames(frames)
    if not ins or not outs:
        return None, "none"
    offs = _match_offsets(ins, level)
    if not offs:
        return None, "none"
    if len(offs) > 1:
        return None, "ambiguous"
    off = next(iter(offs))
    reqs = _requests_at(frames, ins, outs, off, level)
    if not reqs:
        return None, "none"
    return _proto_at(reqs, off), "ok"


def infer_two(level_a: int, frames_a: list,
              level_b: int, frames_b: list):
    """两次不同电量：某字节的差值等于两次电量差值，唯一则采用。"""
    level_a = int(level_a)
    level_b = int(level_b)
    if level_a <= 0 or level_b <= 0 or level_a == level_b:
        return None, "none"
    ins_a, outs_a = _split_frames(frames_a)
    ins_b, outs_b = _split_frames(frames_b)
    if not ins_a or not ins_b:
        return None, "none"
    delta = level_b - level_a
    candidates = []
    for off in range(1, 64):
        va = {d[off] for _i, d in ins_a if len(d) > off}
        vb = {d[off] for _i, d in ins_b if len(d) > off}
        if any((y - x) == delta for x in va for y in vb):
            candidates.append(off)
    if not candidates:
        return None, "none"
    if len(candidates) > 1:
        return None, "ambiguous"
    off = candidates[0]
    reqs = _requests_at(frames_a, ins_a, outs_a, off, level_a)
    if not reqs:
        reqs = _requests_at(frames_b, ins_b, outs_b, off, level_b)
    if not reqs:
        return None, "none"
    return _proto_at(reqs, off), "ok"


def infer_single(level: int, frames: list) -> dict:
    return infer_single_ex(level, frames)[0]


def requests_ok(reqs: list) -> bool:
    return bool(reqs)


def _read_user_protocol(ifaces: list, proto: dict):
    """用抓包得到的通用协议读一次电量（逐个尝试候求帧）?"""
    off = int(proto.get("offset", 2))
    if off < 1:
        return None
    raw = proto.get("requests")
    if not isinstance(raw, list):
        raw = [proto.get("request", "")]
    reqs = []
    for s in raw:
        try:
            b = bytes.fromhex(str(s).replace(" ", ""))
        except Exception:
            continue
        if len(b) >= 2:
            reqs.append(b)
    if not reqs:
        return None
    for req in reqs:
        for d in ifaces:
            if d.output_len < len(req) or d.input_len <= off:
                continue
            dev = None
            try:
                dev = HidDevice(d.path, write=True)
                buf = bytes(req) + bytes(max(0, d.output_len - len(req)))
                dev.drain()
                dev.write_output(buf)
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline:
                    rep = dev.read_input(max(64, d.input_len), 250)
                    if not rep or len(rep) <= off:
                        continue
                    if 0 <= rep[off] <= 100:
                        return Reading(ok=True, level=rep[off],
                                       source="抓包协议",
                                       detail=f"回复 {rep[:8].hex(' ')}")
            except Exception:
                pass
            finally:
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass
    return None


def _listen_user_protocol(ifaces: list, proto: dict,
                          seconds: float = 1.5):
    """被动监听输入报告：不发请求，只看设备自己上报的字节。"""
    off = int(proto.get("offset", 2))
    if off < 1:
        return None
    raw = proto.get("requests")
    if not isinstance(raw, list):
        raw = [proto.get("request", "")]
    reqs = []
    for s in raw:
        try:
            b = bytes.fromhex(str(s).replace(" ", ""))
        except Exception:
            continue
        if b:
            reqs.append(b)
    req_ids = {b[0] for b in reqs}
    for d in ifaces:
        if d.input_len <= off:
            continue
        dev = None
        try:
            dev = HidDevice(d.path, write=False)
        except Exception:
            continue
        try:
            deadline = time.monotonic() + seconds
            seen = {}
            first = None
            while time.monotonic() < deadline:
                rep = dev.read_input(max(64, d.input_len), 250)
                if not rep or len(rep) <= off:
                    continue
                val = rep[off]
                if not (0 <= val <= 100):
                    continue
                if first is None:
                    first = (val, rep)
                if req_ids and rep[0] in req_ids:
                    return Reading(ok=True, level=val,
                                   source="被动监听输入报告",
                                   detail=f"报告 {rep[:8].hex(' ')}")
                seen[val] = seen.get(val, 0) + 1
                if seen[val] >= 2:
                    return Reading(ok=True, level=val,
                                   source="被动监听输入报告",
                                   detail=f"连续报告 {rep[:8].hex(' ')}")
            if first is not None:
                return Reading(ok=True, level=first[0],
                               source="被动监听输入报告",
                               detail=f"单次报告 {first[1][:8].hex(' ')}")
        except Exception:
            pass
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
    return None


def _verify_saved_protocol(vid: int, pid: int, proto: dict):
    """保存协议后立即验证：先主动回读，失败再被动监听。"""
    try:
        ifaces = [d for d in enumerate_hid()
                  if d.vid == vid and d.pid == pid]
    except Exception:
        ifaces = []
    if not ifaces:
        return None
    r = _read_user_protocol(ifaces, proto)
    if r and r.ok:
        return r
    return _listen_user_protocol(ifaces, proto)


def _iter_pcap_packets(path: str):
    """流式读取 USBPcap pcap，逐包产出 (bus, dev, ep, transfer, payload)。"""
    try:
        f = open(path, "rb")
    except Exception:
        return
    with f:
        head = f.read(24)
        if len(head) < 24:
            return
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                return
            try:
                _ts, _tu, incl, _orig = struct.unpack("<IIII", ph)
            except Exception:
                return
            if incl <= 0 or incl > 64 * 1024 * 1024:
                return
            pkt = f.read(incl)
            if len(pkt) < incl:
                return
            if len(pkt) < 27:
                continue
            try:
                hlen = struct.unpack_from("<H", pkt, 0)[0]
            except Exception:
                continue
            if not (27 <= hlen <= 600) or len(pkt) < hlen:
                continue
            dlen = struct.unpack_from("<I", pkt, 23)[0]
            payload = pkt[hlen:hlen + dlen]
            bus = struct.unpack_from("<H", pkt, 17)[0]
            dev = struct.unpack_from("<H", pkt, 19)[0]
            yield bus, dev, pkt[21], pkt[22], payload


def scan_pcap_map(path: str) -> dict:
    """返回 {(bus, device): (vid, pid)}，来自 pcap 里的设备描述符。"""
    out = {}
    for bus, dev, _ep, _tr, payload in _iter_pcap_packets(path):
        for i in range(0, max(0, len(payload) - 11)):
            if payload[i] == 18 and payload[i + 1] == 1:
                vid = payload[i + 8] | (payload[i + 9] << 8)
                pid = payload[i + 10] | (payload[i + 11] << 8)
                if vid and pid:
                    out[(bus, dev)] = (vid, pid)
                break
    return out


def scan_pcap_devices(path: str) -> list:
    """返回 pcap 里出现过的 [(vid, pid)]。"""
    seen = []
    for v in scan_pcap_map(path).values():
        if v not in seen:
            seen.append(v)
    return seen


def extract_pcap_reports(path: str, target: set = None,
                         limit: int = None) -> list:
    """流式提取 HID 帧；target 为 {(bus, device)} 时只收目标设备。"""
    out = []
    for bus, dev, ep, transfer, payload in _iter_pcap_packets(path):
        if target is not None and (bus, dev) not in target:
            continue
        if len(payload) < 4:
            continue
        # HID SET_REPORT 控制传输：把报告本体当作 OUT 帧
        if (ep == 0 and len(payload) >= 10
                and payload[0] == 0x21 and payload[1] == 0x09):
            wlen = payload[6] | (payload[7] << 8)
            report = payload[8:8 + wlen]
            if len(report) >= 3 and (not out or out[-1][2] != report):
                out.append((0x02, 1, report))
            if limit and len(out) >= limit:
                break
            continue
        if not out or out[-1][2] != payload:
            out.append((ep, transfer, payload))
            if limit and len(out) >= limit:
                break
    return out


def extract_pcap_for(path: str, vid: int, pid: int):
    """只提取目标 VID:PID 的帧；找不到映射时退回全部帧。"""
    mapping = scan_pcap_map(path)
    targets = {k for k, v in mapping.items() if v == (vid, pid)} if vid else set()
    frames = extract_pcap_reports(path, targets or None)
    return frames, mapping


def validate_protocol(proto: dict, vid: int, pid: int, level: int):
    """用推断出的协议回读一次，必须与填入电量一致才允许保存。"""
    try:
        ifaces = [d for d in enumerate_hid()
                  if d.vid == vid and d.pid == pid]
    except Exception as e:
        return False, f"枚举失败：{e}"
    if not ifaces:
        return False, "没有找到设备接口"
    try:
        r = _read_user_protocol(ifaces, proto)
    except Exception as e:
        return False, f"回读异常：{e}"
    if not r or not r.ok or r.level is None:
        return False, "回读失败"
    if abs(int(r.level) - int(level)) <= 2:
        return True, f"回读 {r.level}%"
    return False, f"回读 {r.level}%，与填入 {level}% 不符"


def format_reports(reports: list) -> str:
    lines = ["# endpoint dir transfer length data"]
    for ep, transfer, data in reports:
        direction = "IN" if ep & 0x80 else "OUT"
        hexs = " ".join(f"{b:02X}" for b in data)
        lines.append(f"{ep:04X} {direction} t={transfer} "
                     f"len={len(data)} {hexs}")
    return "\n".join(lines)


def set_custom_name(key: str, name: str) -> None:
    cfg = load_config()
    names = cfg.get("names")
    if not isinstance(names, dict):
        names = {}
    name = (name or "").strip()
    if name:
        names[key] = name
    else:
        names.pop(key, None)
    cfg["names"] = names
    save_config(cfg)


# ===========================================================================
# CLI
# ===========================================================================
def print_once():
    res = apply_last_known(read_all())
    for key, r in res.items():
        if not r.name or (not r.ok and r.status == "unknown_proto"):
            continue
        if r.ok:
            state = STATUS_TEXT.get(r.status, "")
            state_text = (f" · {state}"
                          if state and r.status != "unknown" else "")
            suffix = f"；{r.detail}" if r.stale else f"（{r.source}）"
            print(f"{r.name}：{r.level}%{state_text}{suffix}")
        else:
            print(f"{r.name}：{r.text}（{r.detail}）")
        if r.dpi_config:
            cfg = r.dpi_config
            stages = " / ".join(str(v) for v in cfg.stages)
            print(f"  DPI：{cfg.current_dpi}（第 {cfg.current_index + 1} 档："
                  f"{stages}）")


def list_devices():
    for d in enumerate_hid():
        print(d.label, "|", d.manufacturer, "|", d.product, "|", d.instance)


def install_all():
    ok = install_startup()
    links = install_desktop_links()
    print("开机自启", startup_lnk(), "（已创建）" if ok else "（失败）")
    for l in links:
        print("桌面链接", l)
    if not links:
        print("桌面链接：已存在，未重创建")


CREATE_NO_WINDOW = 0x08000000

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = w.HANDLE
k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                            w.DWORD, w.DWORD, w.HANDLE]


def _helper_scan_devices(path):
    out = []
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return out
    if len(data) < 24:
        return out
    off = 24
    n = len(data)
    while off + 16 <= n:
        try:
            _ts, _tu, incl, _orig = struct.unpack_from("<IIII", data, off)
        except Exception:
            break
        off += 16
        if incl <= 0 or off + incl > n:
            break
        pkt = data[off:off + incl]
        off += incl
        if len(pkt) < 31:
            continue
        hlen = struct.unpack_from("<H", pkt, 0)[0]
        if not (27 <= hlen <= 600) or len(pkt) < hlen:
            continue
        dlen = struct.unpack_from("<I", pkt, 23)[0]
        payload = pkt[hlen:hlen + dlen]
        for i in range(0, len(payload) - 17):
            if payload[i] == 18 and payload[i + 1] == 1:
                vid = payload[i + 8] | (payload[i + 9] << 8)
                pid = payload[i + 10] | (payload[i + 11] << 8)
                if vid and pid and (vid, pid) not in out:
                    out.append((vid, pid))
    return out


def _helper_hub_exists(n):
    h = k32.CreateFileW("\\\\.\\USBPcap%d" % n, 0, 3, None, 3, 0, None)
    err = ctypes.get_last_error()
    if h and h != w.HANDLE(-1).value:
        k32.CloseHandle(h)
        return True
    return err == 5


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation",
                 _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


_JOB = None


def _setup_job():
    """Job：helper 退出时连带收掉 USBPcapCMD。"""
    global _JOB
    try:
        k32.CreateJobObjectW.restype = w.HANDLE
        k32.GetCurrentProcess.restype = w.HANDLE
        k32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                    ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
        _JOB = job
    except Exception:
        _JOB = None


def _helper_main():
    _setup_job()
    (cp, vid_s, pid_s, outdir, stop_flag, status_file) = sys.argv[1:7]
    vid = int(vid_s, 16)
    pid = int(pid_s, 16)

    def status(state, hub=0, file=""):
        tmp = status_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"state": state, "hub": hub, "file": file}, f)
            os.replace(tmp, status_file)
        except Exception:
            pass

    hubs = [n for n in range(1, 7) if _helper_hub_exists(n)]
    status("probing")
    any_bytes = False
    found = 0
    if vid:
        for hub in hubs:
            probe = os.path.join(outdir, "probe%d.pcap" % hub)
            try:
                os.remove(probe)
            except Exception:
                pass
            try:
                p = subprocess.Popen(
                    [cp, "-d", "\\\\.\\USBPcap%d" % hub, "-A",
                     "--inject-descriptors", "-o", probe],
                    creationflags=CREATE_NO_WINDOW,
                    stderr=subprocess.DEVNULL)
            except Exception:
                continue
            time.sleep(4.0)
            try:
                p.terminate()
                p.wait(3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            try:
                if os.path.getsize(probe) > 0:
                    any_bytes = True
            except Exception:
                pass
            devs = _helper_scan_devices(probe)
            if (vid, pid) in devs:
                found = hub
                break
        if not found:
            status("notraffic" if any_bytes else "busy")
            return
    else:
        found = hubs[0] if hubs else 1

    status("capturing", found)
    final = os.path.join(outdir, "capture.pcap")
    try:
        p = subprocess.Popen(
            [cp, "-d", "\\\\.\\USBPcap%d" % found, "-A",
             "--inject-descriptors", "-o", final],
            creationflags=CREATE_NO_WINDOW,
            stderr=subprocess.DEVNULL)
    except Exception:
        status("error")
        return
    started = time.time()
    while not os.path.exists(stop_flag):
        if p.poll() is not None:
            break
        if time.time() - started > 180:
            break
        try:
            if os.path.getsize(final) > 256 * 1024 * 1024:
                break
        except Exception:
            pass
        time.sleep(0.2)
    try:
        p.terminate()
        p.wait(3)
    except Exception:
        try:
            p.kill()
            p.wait(2)
        except Exception:
            pass
    status("done", found, final)


def _helper_run(args: list):
    old = sys.argv
    sys.argv = ["capture_helper.py"] + list(args)
    try:
        _helper_main()
    finally:
        sys.argv = old


def main(argv):
    if argv and argv[0] == "--capture-helper":
        _helper_run(argv[1:])
        return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--once" in argv:
        print_once()
        return 0
    if "--list" in argv:
        list_devices()
        return 0
    if "--install" in argv:
        install_all()
        return 0
    return run_gui(panel_only="--panel" in argv)


# ===========================================================================
# 配色
# ===========================================================================
def theme_colors(light: bool) -> dict:
    if light:
        return {
            "bg1": (238, 243, 249), "bg2": (219, 229, 241),
            "card": (251, 253, 255), "card2": (243, 248, 255),
            "line": (208, 220, 235), "text": (18, 27, 38),
            "sub": (91, 107, 124), "dim": (140, 155, 170),
            "accent": (22, 119, 255), "green": (22, 163, 74),
            "yellow": (217, 119, 6), "red": (220, 38, 38),
            "track": (226, 235, 245), "hover": (232, 241, 252),
            "glass": (255, 255, 255),
        }
    return {
        "bg1": (13, 18, 25), "bg2": (27, 36, 48),
        "card": (31, 41, 54), "card2": (38, 50, 65),
        "line": (55, 70, 88), "text": (232, 240, 250),
        "sub": (150, 165, 182), "dim": (110, 126, 144),
        "accent": (80, 160, 255), "green": (52, 211, 120),
        "yellow": (250, 190, 60), "red": (248, 113, 113),
        "track": (48, 62, 79), "hover": (44, 58, 75),
        "glass": (255, 255, 255),
    }


def level_color(level: Optional[int], light: bool) -> tuple:
    c = theme_colors(light)
    if level is None:
        return c["dim"]
    if level >= 60:
        return c["green"]
    if level >= 30:
        return c["yellow"]
    return c["red"]


def _rgb(c):
    return "#%02x%02x%02x" % c


def _make_png_data(width: int, height: int, rgba: bytes) -> str:
    """把 RGBA 像素编码成 Tk 可直接读取的 PNG base64 数据。"""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride:(y + 1) * stride])
    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                      8, 6, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
           chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


def round_rect_points(x1, y1, x2, y2, r, steps=10):
    pts = []
    for cx, cy, a0 in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0),
                       (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for i in range(steps + 1):
            a = (a0 + 90 * i / steps) * 3.141592653589793 / 180
            pts.append((cx + r * __import__("math").cos(a),
                        cy + r * __import__("math").sin(a)))
    return pts


def draw_round_rect(cv: "tk.Canvas", x1, y1, x2, y2, r, **kw):
    pts = round_rect_points(x1, y1, x2, y2, r)
    flat = [v for p in pts for v in p]
    return cv.create_polygon(flat, smooth=False, **kw)


# ===========================================================================
# 窗口玻璃 / 圆
# ===========================================================================
class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", wt.DWORD), ("AnimationId", ctypes.c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


def apply_acrylic(hwnd: int, light: bool) -> bool:
    try:
        user32.SetWindowCompositionAttribute.argtypes = [
            wt.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        policy = ACCENTPOLICY()
        policy.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        policy.AccentFlags = 2
        # AABBGGRR
        policy.GradientColor = 0xE6F2F2F2 if light else 0xE61A1A1A
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)
        return bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    except Exception:
        return False


def set_round_region(hwnd: int, w: int, h: int, radius: int = 24) -> None:
    try:
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
        user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


# ===========================================================================
# 简洁面板?# ===========================================================================
import tkinter as tk
import tkinter.font as tkfont

FONT = "Microsoft YaHei UI"


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", wt.BYTE), ("BlendFlags", wt.BYTE),
                ("SourceConstantAlpha", wt.BYTE), ("AlphaFormat", wt.BYTE)]


user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]


class ShadowWindow:
    """分层窗口：在面板背后画一圈向外渐隐的灰色光晕?"""

    def __init__(self, panel):
        self.panel = panel
        self.hwnd = 0
        self._bitmap = None
        self._create()

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        # TOPMOST：边框层要能压在另一个同款窗口之上
        ex = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080 | 0x00000008
        self.hwnd = user32.CreateWindowExW(
            ex, "STATIC", "", 0x80000000, 0, 0, 10, 10,
            None, None, hinst, None)

    def _build(self):
        W, H, S = self.panel.W, self.panel.H, self.panel.S
        margin = max(24, int(28 * S))
        w, h = W + 2 * margin, H + 2 * margin
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        memdc = gdi32.CreateCompatibleDC(None)
        hbm = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        gdi32.SelectObject(memdc, hbm)
        buf = bytearray(w * h * 4)
        cx, cy = margin + W / 2.0, margin + H / 2.0
        r = 16.0 * S
        hx, hy = W / 2.0 - r, H / 2.0 - r
        sigma = 6.0 * S
        kw = 1.0 / (2.0 * sigma * sigma)
        limit = 3.0 * sigma
        for yy in range(h):
            py = yy + 0.5
            qy = abs(py - cy) - hy
            if qy > limit + r:
                continue
            oy = qy if qy > 0 else 0.0
            for xx in range(w):
                px = xx + 0.5
                qx = abs(px - cx) - hx
                if qx > limit + r:
                    continue
                ox = qx if qx > 0 else 0.0
                d = math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r
                i = (yy * w + xx) * 4
                if -3.0 < d <= 3.0:
                    cov = 1.0 if d <= 1.8 else max(0.0, (3.0 - d) / 1.2)
                    a = int(255 * cov)
                    buf[i] = 60 * a // 255
                    buf[i + 1] = 60 * a // 255
                    buf[i + 2] = 60 * a // 255
                    buf[i + 3] = a
                elif 3.0 < d < limit:
                    a = int(40.0 * math.exp(-(d * d) * kw))
                    if a > 0:
                        buf[i + 3] = a
        ctypes.memmove(bits, bytes(buf), len(buf))
        self._bitmap = (w, h, memdc, hbm, margin)

    def _top(self):
        return user32.GetAncestor(self.panel.hwnd, 2) or self.panel.hwnd

    def show(self, x, y):
        if not self.hwnd:
            return
        if not getattr(self.panel, "_visible", True):
            return
        if self._bitmap is None:
            self._build()
        w, h, memdc, _hbm, margin = self._bitmap
        pt_dst = wt.POINT(int(x) - margin, int(y) - margin)
        size = wt.SIZE(w, h)
        pt_src = wt.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pt_dst),
                                   ctypes.byref(size), memdc,
                                   ctypes.byref(pt_src), 0,
                                   ctypes.byref(blend), 2)
        user32.SetWindowPos(self.hwnd, self._top(),
                            int(x) - margin, int(y) - margin, w, h,
                            0x0010 | 0x0040)

    def move(self, x, y):
        if not self.hwnd or self._bitmap is None:
            return
        w, h, _memdc, _hbm, margin = self._bitmap
        user32.SetWindowPos(self.hwnd, self._top(),
                            int(x) - margin, int(y) - margin, w, h, 0x0010)

    def hide(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)

    def release_bitmap(self):
        if self._bitmap:
            _w, _h, memdc, hbm, _m = self._bitmap
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(memdc)
            self._bitmap = None

    def destroy(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = 0
        self.release_bitmap()


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("fMask", ctypes.c_ulong),
        ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
        ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR),
        ("lpDirectory", wt.LPCWSTR), ("nShow", ctypes.c_int),
        ("hInstApp", wt.HINSTANCE), ("lpIDList", ctypes.c_void_p),
        ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY),
        ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE),
        ("hProcess", wt.HANDLE),
    ]


class HelpWindow:
    """与主面板同的使用明窗口。"""

    LW, LH = 520, 520
    HEADER_H = 74

    def __init__(self, parent, key=None):
        self.parent = parent
        self.key = key
        r = parent.app.snapshot().get(key) if key else None
        self.dev_name = (r.name if r and r.name else "")
        self.target_vid = r.vid if r else 0
        self.target_pid = r.pid if r else 0
        self.S = parent.S
        self.W = int(self.LW * self.S)
        self.H = int(self.LH * self.S)
        self.root = tk.Toplevel(parent.root)
        self.root.overrideredirect(True)
        self.root.configure(bg="#0B1322")
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0, bg="#0B1322")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        set_round_region(self.hwnd, self.W, self.H, int(32 * self.S))
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - self.W) // 2)
        y = max(0, (sh - self.H) // 2 - 20)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self.shadow = None
        try:
            self.shadow = ShadowWindow(self)
        except Exception:
            self.shadow = None
        self._drag = None
        self._capturing = False
        self._cap_file = None
        self._cap_proc = None
        self._cap_dir = ""
        self._cap_status_file = ""
        self._cap_stop = ""
        self._cap_status = "点击「开始抓包」，抓包时请打开厂家软件显示电量"
        self._cap_txt = ""
        self._ambiguous = False
        self._cap_a = None
        self._purge_capture_files()
        self.render()
        try:
            self.root.update_idletasks()
            if self.shadow:
                self.shadow.show(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            pass

    def _f(self, size, bold=False):
        px = max(10, int(round(size * self.S * 1.06)))
        return (FONT, -px, "bold" if bold else "normal")

    def render(self):
        cv = self.cv
        cv.delete("all")
        W, H = self.LW, self.LH
        cv.create_rectangle(0, 0, W, H, fill=_rgb((27, 27, 28)), outline="")
        cv.create_text(28, 34, text="使用说明", anchor="w",
                       fill=_rgb((245, 245, 247)), font=self._f(16, True))
        sub = "协议未知 · 如何让软件读到这款设备"
        if self.dev_name:
            sub = f"{self.dev_name} · 如何让软件读到这款设备"
        cv.create_text(28, 56, text=sub[:44], anchor="w",
                       fill=_rgb((160, 163, 168)),
                       font=self._f(9))
        # 关闭按钮（圆形，悬停变红）
        cv.create_oval(W - 44, 16, W - 16, 44, fill=_rgb((27, 27, 28)),
                       outline=_rgb((27, 27, 28)), tags=("help_close",))
        cv.create_text(W - 30, 30, text="✕", fill=_rgb((160, 163, 168)),
                       font=self._f(11), tags=("help_close",))
        cv.tag_bind("help_close", "<Button-1>", lambda e: self.close())
        cv.tag_bind("help_close", "<Enter>", self._close_hover)
        cv.tag_bind("help_close", "<Leave>", self._close_leave)

        lines = [
            ("h", "为什么读不到"),
            ("b", "接收器在，但厂商协议未收录；软件只做只读试探。"),
            ("h", "你可以这样做"),
            ("b", "1. 安装 USBPcap（装完重启电脑）。"),
            ("b", "2. 关闭厂家驱动，点「开始抓包」并允许管理员权限。"),
            ("b", "3. 打开厂家驱动，让它显示一次电量。"),
            ("b", "4. 点「停止」，填入刚才看到的电量。"),
            ("b", "5. 只有一个字节符合，就直接保存这个字节作为电量。"),
            ("b", "6. 多个字节符合时，换一个电量再抓一次，对比差值。"),
            ("b", "7. 保存后，以后插上就能直接读电量。"),
            ("h", "如果多次都没有电量数据"),
            ("b", "可能厂家的协议做了加密，这方面需要用户自行解决。"),
        ]
        y = 96
        for kind, text in lines:
            if kind == "h":
                cv.create_text(28, y, text=text, anchor="w",
                               fill=_rgb((245, 245, 247)),
                               font=self._f(10, True))
                y += 28
            else:
                cv.create_text(28, y, text=text, anchor="w",
                               fill=_rgb((175, 178, 184)),
                               font=self._f(9))
                y += 22
        cv.create_text(28, H - 12, text="点击右上角 ✕ 关闭",
                       anchor="w", fill=_rgb((120, 123, 128)),
                       font=self._f(8))
        self._cap_button(28, 400, 110, 30, "开始抓包", "cap_start",
                         enabled=not self._capturing)
        self._cap_button(150, 400, 80, 30, "停止", "cap_stop",
                         enabled=self._capturing)
        if self._ambiguous:
            self._cap_button(242, 400, 100, 30, "清理抓包", "cap_clear",
                             enabled=not self._capturing)
        cv.create_text(28, 450, text=self._cap_status, anchor="nw",
                       fill=_rgb((150, 153, 158)), font=self._f(8),
                       width=(W - 56), justify="left")
        cv.scale("all", 0, 0, self.S, self.S)

    def _close_hover(self, e=None):
        for item in self.cv.find_withtag("help_close"):
            if self.cv.type(item) == "oval":
                self.cv.itemconfigure(item, fill=_rgb((196, 43, 28)),
                                      outline=_rgb((196, 43, 28)))
        txt = [i for i in self.cv.find_withtag("help_close")
               if self.cv.type(i) == "text"]
        for i in txt:
            self.cv.itemconfigure(i, fill=_rgb((255, 255, 255)))
        self.cv.configure(cursor="hand2")

    def _close_leave(self, e=None):
        for item in self.cv.find_withtag("help_close"):
            if self.cv.type(item) == "oval":
                self.cv.itemconfigure(item, fill=_rgb((27, 27, 28)),
                                      outline=_rgb((27, 27, 28)))
        for i in [i for i in self.cv.find_withtag("help_close")
                  if self.cv.type(i) == "text"]:
            self.cv.itemconfigure(i, fill=_rgb((160, 163, 168)))
        self.cv.configure(cursor="")

    def _on_press(self, e):
        try:
            if getattr(self, "shadow", None):
                self.shadow.show(self.root.winfo_x(), self.root.winfo_y())
                self.root.after(
                    60, lambda: self.shadow.show(
                        self.root.winfo_x(), self.root.winfo_y())
                    if (self.shadow and self.root.winfo_viewable()) else None)
        except Exception:
            pass
        self._drag = None
        if e.y >= self.HEADER_H * self.S:
            return
        if "help_close" in self.cv.gettags("current"):
            return
        self._drag = (e.x_root, e.y_root,
                      self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        dx = e.x_root - x0
        dy = e.y_root - y0
        if abs(dx) < 3 and abs(dy) < 3:
            return
        nx = wx + dx
        ny = wy + dy
        self.root.geometry(f"+{nx}+{ny}")
        if self.shadow:
            try:
                self.shadow.move(nx, ny)
            except Exception:
                pass

    def _on_release(self, e):
        self._drag = None

    def _cap_button(self, x, y, w, h, label, tag, enabled=True):
        cv = self.cv
        fill = (42, 43, 46) if enabled else (30, 31, 34)
        border = (58, 59, 64) if enabled else (40, 41, 45)
        col = (245, 245, 247) if enabled else (110, 112, 116)
        draw_round_rect(cv, x, y, x + w, y + h, 8, fill=_rgb(fill),
                        outline=_rgb(border), width=1, tags=(tag,))
        cv.create_text(x + w / 2, y + h / 2, text=label, fill=_rgb(col),
                       font=self._f(9, True), tags=(tag,))
        cv.tag_bind(tag, "<Button-1>", lambda e, t=tag: self._cap_click(t))
        cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))

    def _cap_click(self, tag):
        if tag == "cap_start":
            self.start_capture()
        elif tag == "cap_stop":
            self.stop_capture()
        elif tag == "cap_path":
            self._choose_usbpcap()
        elif tag == "cap_clear":
            self._clear_capture()

    def _clear_capture(self):
        self._capturing = False
        try:
            if self._cap_stop:
                with open(self._cap_stop, "w", encoding="utf-8") as f:
                    f.write("1")
        except Exception:
            pass
        if self._cap_proc:
            try:
                kernel32.TerminateProcess(self._cap_proc, 0)
                kernel32.CloseHandle(self._cap_proc)
            except Exception:
                pass
            self._cap_proc = None
        for p in (self._cap_file, getattr(self, "_cap_txt", ""),
                  self._cap_status_file, self._cap_stop):
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        outdir = self._cap_dir or os.path.join(
            tempfile.gettempdir(), "BatteryTrayCapture")
        for name in ("capture.pcap", "status.json", "stop.flag"):
            try:
                os.remove(os.path.join(outdir, name))
            except Exception:
                pass
        self._cap_file = None
        self._cap_txt = ""
        self._ambiguous = False
        self._cap_a = None
        self._cap_status = "已清理抓包，可以重新开始"
        self.render()

    def _find_usbpcap(self):
        p = usbpcap_path()
        if p and os.path.isfile(p):
            return p
        for c in (r"C:\Program Files\USBPcap\USBPcapCMD.exe",
                  r"C:\Program Files (x86)\USBPcap\USBPcapCMD.exe"):
            if os.path.isfile(c):
                set_usbpcap_path(c)
                return c
        return ""

    def _path_status(self):
        p = usbpcap_path()
        if not p:
            return "未选择 USBPcap 路径"
        shown = p if len(p) <= 56 else "…" + p[-55:]
        return f"USBPcap：{shown}"

    def _choose_usbpcap(self):
        try:
            from tkinter import filedialog
            p = filedialog.askopenfilename(
                parent=self.root, title="选择 USBPcapCMD.exe",
                filetypes=[("USBPcapCMD.exe", "USBPcapCMD.exe"),
                           ("可执行文件", "*.exe")])
        except Exception:
            p = ""
        if p:
            set_usbpcap_path(p)
            self._cap_status = self._path_status()
            self.render()

    def _open_site(self):
        try:
            os.startfile("https://desowin.org/usbpcap/")
        except Exception:
            pass

    def start_capture(self):
        if self._capturing:
            return
        cmd = self._find_usbpcap()
        if not cmd:
            self._cap_status = "未找到 USBPcapCMD.exe，请先安装 USBPcap"
            self.render()
            return
        outdir = os.path.join(tempfile.gettempdir(), "BatteryTrayCapture")
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception:
            pass
        for name in ("capture.pcap", "status.json", "stop.flag"):
            try:
                os.remove(os.path.join(outdir, name))
            except Exception:
                pass
        status_file = os.path.join(outdir, "status.json")
        stop_file = os.path.join(outdir, "stop.flag")
        if getattr(sys, "frozen", False):
            params = (f'--capture-helper "{cmd}" {self.target_vid:04X} '
                      f'{self.target_pid:04X} "{outdir}" "{stop_file}" '
                      f'"{status_file}"')
        else:
            script = os.path.abspath(__file__)
            params = (f'"{script}" --capture-helper "{cmd}" '
                      f'{self.target_vid:04X} {self.target_pid:04X} '
                      f'"{outdir}" "{stop_file}" "{status_file}"')
        sei = _SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = sys.executable
        sei.lpParameters = params
        sei.lpDirectory = outdir
        sei.nShow = 0
        try:
            ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        except Exception:
            ok = 0
        if not ok:
            self._cap_status = "启动失败（可能取消了管理员授权）"
            self.render()
            return
        self._cap_proc = sei.hProcess
        self._cap_dir = outdir
        self._cap_status_file = status_file
        self._cap_stop = stop_file
        self._cap_file = os.path.join(outdir, "capture.pcap")
        self._capturing = True
        self._cap_status = ("正在查找设备所在的 USBPcap 接口…"
                            if self.target_vid else "正在抓包…")
        self.render()
        self.root.after(400, self._poll_capture)

    def _poll_capture(self):
        if not self._capturing:
            return
        state = ""
        hub = 0
        path = ""
        try:
            with open(self._cap_status_file, "r", encoding="utf-8") as f:
                info = json.load(f)
            state = info.get("state", "")
            hub = int(info.get("hub", 0) or 0)
            path = info.get("file", "")
        except Exception:
            pass
        if state == "capturing":
            self._cap_status = f"正在抓包：USBPcap{hub}"
            self.render()
        elif state == "busy":
            self._capturing = False
            self._cap_status = "抓包设备打不开（可能被其它抓包占用，或需要管理员权限）"
            self.render()
            return
        elif state == "notraffic":
            self._capturing = False
            self._cap_status = "抓到了流量但没发现目标设备，请在抓包时拔插一次接收器"
            self.render()
            return
        elif state == "notfound":
            self._capturing = False
            self._cap_status = "没有在任何 USBPcap 接口上找到这台设备"
            self.render()
            return
        elif state == "error":
            self._capturing = False
            self._cap_status = "USBPcap 启动失败"
            self.render()
            return
        elif state == "done":
            self._capturing = False
            if path and os.path.isfile(path):
                self._cap_file = path
                self._finish_capture()
            else:
                self._cap_status = "抓包结束，但没有生成文件"
                self.render()
            return
        self.root.after(400, self._poll_capture)

    def stop_capture(self):
        if not self._capturing:
            return
        try:
            with open(self._cap_stop, "w", encoding="utf-8") as f:
                f.write("1")
        except Exception:
            pass
        self._cap_status = "正在停止…"
        self.render()

    def _finish_capture(self):
        if not self._cap_file:
            self._cap_status = "抓包结束，但没有生成文件"
            self.render()
            return
        try:
            size = os.path.getsize(self._cap_file)
            detail = f"（{max(1, size // 1024)} KB）"
        except Exception:
            detail = "（没有生成文件）"
        if self.target_vid:
            frames, _mapping = extract_pcap_for(
                self._cap_file, self.target_vid, self.target_pid)
        else:
            frames = extract_pcap_reports(self._cap_file)
        self._cap_txt = ""
        if frames:
            txt = os.path.splitext(self._cap_file)[0] + ".txt"
            try:
                with open(txt, "w", encoding="utf-8") as f:
                    f.write(format_reports(frames))
                self._cap_txt = txt
            except Exception:
                self._cap_txt = ""
        devs = scan_pcap_devices(self._cap_file)
        if self.target_vid:
            devs = [d for d in devs
                    if d == (self.target_vid, self.target_pid)]
        if not devs and self.target_vid:
            self._ambiguous = False
            self._cap_status = "抓包里没有这台设备，请确认接收器已插好"
            self.render()
            return
        if frames and len(devs) == 1:
            try:
                from tkinter import simpledialog
                lv = simpledialog.askinteger(
                    "抓包电量",
                    "这次抓包时，厂商软件显示的电量是多少？",
                    parent=self.root, minvalue=0, maxvalue=100)
            except Exception:
                lv = None
            if lv is None:
                self._cap_status = "已停止，但没有填写电量，未记录本次抓包"
                self.render()
                return
            second = self._cap_a is not None
            if second:
                lv_a, frames_a = self._cap_a
                if int(lv) == int(lv_a):
                    self._ambiguous = True
                    self._cap_status = "两次电量相同，请换一个相差至少 10% 的电量再抓一次"
                    self.render()
                    return
                proto, why = infer_two(lv_a, frames_a, int(lv), frames)
            else:
                proto, why = infer_single_ex(int(lv), frames)

            if proto:
                vid, pid = devs[0]
                save_user_protocol(vid, pid, proto)
                verified = _verify_saved_protocol(vid, pid, proto)
                self._ambiguous = False
                self._cap_a = None
                if verified is not None and verified.ok:
                    self._cap_status = (
                        f"已保存协议 {vid:04X}:{pid:04X}"
                        f"（回读 {verified.level}%）")
                else:
                    cfg = load_config()
                    protos = cfg.get("protocols")
                    if isinstance(protos, dict):
                        protos.pop(f"{vid:04X}:{pid:04X}", None)
                        save_config(cfg)
                    self._cap_status = (
                        "抓包中不含电量数据，可能是没按要求让厂商软件发电量请求")
                try:
                    self.parent.app.refresh()
                except Exception:
                    pass
                self.render()
                return

            if why == "ambiguous":
                self._cap_a = (int(lv), frames)
                self._ambiguous = True
                if second:
                    self._cap_status = "两次仍匹配到多个字节，请再换一个电量抓取"
                else:
                    self._cap_status = "检测到多个电量字节，请换一个相差至少 10% 的电量再抓一次"
                self.render()
                return

            if second:
                self._cap_a = None
                self._ambiguous = False
                self._cap_status = "两次抓包没有找到随电量变化的字节，请重新抓取"
            else:
                self._ambiguous = False
                self._cap_status = "没有匹配的电量数据，可能是操作不规范，请重复操作一遍"
            self.render()
            return

        self._ambiguous = False
        parts = [f"已停止：{os.path.basename(self._cap_file)}{detail}"]
        if devs:
            parts.append("、".join(f"{v:04X}:{p:04X}" for v, p in devs[:2]))
        if frames:
            parts.append(f"帧 {len(frames)}")
        elif not devs:
            parts.append("未识别到设备")
        self._cap_status = " · ".join(parts)
        self.render()

    def _purge_capture_files(self):
        outdir = os.path.join(tempfile.gettempdir(), "BatteryTrayCapture")
        for name in ("capture.pcap", "status.json", "stop.flag"):
            try:
                os.remove(os.path.join(outdir, name))
            except Exception:
                pass

    def close(self):
        try:
            if self._capturing:
                self._capturing = False
                if self._cap_stop:
                    with open(self._cap_stop, "w", encoding="utf-8") as f:
                        f.write("1")
            if self._cap_proc:
                try:
                    kernel32.TerminateProcess(self._cap_proc, 0)
                    kernel32.CloseHandle(self._cap_proc)
                except Exception:
                    pass
                self._cap_proc = None
            self._purge_capture_files()
        except Exception:
            pass
        try:
            if self.shadow:
                self.shadow.destroy()
        except Exception:
            pass
        self.shadow = None
        try:
            self.root.destroy()
        except Exception:
            pass
        self.parent._help_win = None


class AddDeviceWindow:
    """发现新设备时的 DSH 风格确认窗口。"""

    LW, LH = 380, 240

    def __init__(self, app, vid: int, pid: int, name: str,
                 level=None):
        self.app = app
        self.vid = vid
        self.pid = pid
        self.name = name
        self.level = level
        self.parent = app.panel
        self.S = max(1.0, UI_SCALE)
        self.W = int(self.LW * self.S)
        self.H = int(self.LH * self.S)
        self.root = tk.Toplevel(self.parent.root)
        try:
            self.root.transient(self.parent.root)
        except Exception:
            pass
        self.root.overrideredirect(True)
        self.root.configure(bg="#1B1B1C")
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0, bg="#1B1B1C")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-1>", self._on_click)
        self.root.update_idletasks()
        set_round_region(self.root.winfo_id(), self.W, self.H,
                         int(24 * self.S))
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - self.W) // 2
        y = (sh - self.H) // 2
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self.hwnd = self.root.winfo_id()
        self.shadow = None
        self.render()
        try:
            self.root.update_idletasks()
            if self.shadow:
                self.shadow.show(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            pass

    def _f(self, size, bold=False):
        px = max(10, int(round(size * self.S * 1.06)))
        return (FONT, -px, "bold" if bold else "normal")

    def render(self):
        cv = self.cv
        cv.delete("all")
        cv.create_rectangle(0, 0, self.LW, self.LH,
                            fill="#1B1B1C", outline="")
        draw_round_rect(cv, 1, 1, self.LW - 1, self.LH - 1, 12,
                        fill="", outline="#3C3C3C", width=2)
        cv.create_text(24, 28, text="发现新设备", anchor="w",
                       fill="#F5F5F7", font=self._f(16, True))
        cv.create_text(24, 70, text=self.name or "未知设备", anchor="w",
                       fill="#F5F5F7", font=self._f(14, True))
        cv.create_text(24, 98, anchor="w",
                       text=f"{self.vid:04X}:{self.pid:04X}",
                       fill="#A0A3A8", font=self._f(9))
        if self.level is not None:
            cv.create_text(24, 122, text=f"当前电量 {self.level}%",
                           anchor="w", fill="#22C55E",
                           font=self._f(10, True))
        cv.create_text(24, 152, text="双击名称改名 · 双击设备类型切换",
                       anchor="w", fill="#787B80", font=self._f(8))
        draw_round_rect(cv, 24, 188, 164, 222, 8,
                        fill="#22C55E", outline="#22C55E",
                        tags=("add",))
        cv.create_text(94, 205, text="添加", fill="#121416",
                       font=self._f(10, True), tags=("add",))
        draw_round_rect(cv, 180, 188, 320, 222, 8,
                        fill="#1B1B1C", outline="#303136", width=1,
                        tags=("cancel",))
        cv.create_text(250, 205, text="暂不", fill="#A0A3A8",
                       font=self._f(10, True), tags=("cancel",))
        cv.tag_bind("add", "<Button-1>", lambda e: self._done(True))
        cv.tag_bind("cancel", "<Button-1>", lambda e: self._done(False))
        for t in ("add", "cancel"):
            cv.tag_bind(t, "<Enter>",
                        lambda e: cv.configure(cursor="hand2"))
            cv.tag_bind(t, "<Leave>", lambda e: cv.configure(cursor=""))
        cv.scale("all", 0, 0, self.S, self.S)

    def _on_click(self, e):
        x = e.x / self.S
        y = e.y / self.S
        if 24 <= x <= 164 and 188 <= y <= 222:
            self._done(True)
        elif 180 <= x <= 320 and 188 <= y <= 222:
            self._done(False)

    def _done(self, ok: bool):
        if ok:
            try:
                self.app.add_device(self.vid, self.pid, self.name)
            except Exception:
                pass
        try:
            if self.shadow:
                self.shadow.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self.app._add_win = None


class CardMenu:
    """卡片右键菜单：DSH 风格的小按钮。"""

    LW, LH = 140, 44

    def __init__(self, panel, key, x_root, y_root):
        self.panel = panel
        self.key = key
        self.S = panel.S
        self.W = int(self.LW * self.S)
        self.H = int(self.LH * self.S)
        self.root = tk.Toplevel(panel.root)
        try:
            self.root.transient(panel.root)
        except Exception:
            pass
        self.root.overrideredirect(True)
        self.root.configure(bg="#1B1B1C")
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0, bg="#1B1B1C")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-1>", lambda e: self._delete())
        self.root.update_idletasks()
        set_round_region(self.root.winfo_id(), self.W, self.H,
                         int(16 * self.S))
        self.root.geometry(f"{self.W}x{self.H}+{int(x_root)}+{int(y_root)}")
        self.hwnd = self.root.winfo_id()
        self.shadow = None
        try:
            self.shadow = ShadowWindow(self)
        except Exception:
            self.shadow = None
        self.render()
        try:
            self.root.update_idletasks()
            if self.shadow:
                self.shadow.show(self.root.winfo_x(), self.root.winfo_y())
            self.root.lift()
            self.root.attributes("-topmost", True)
            _top = user32.GetAncestor(self.hwnd, 2) or self.hwnd
            user32.SetWindowPos(_top, -1, 0, 0, 0, 0, 0x0003)
        except Exception:
            pass

    def render(self):
        cv = self.cv
        cv.create_rectangle(0, 0, self.LW, self.LH,
                            fill="#1B1B1C", outline="")
        draw_round_rect(cv, 1, 1, self.LW - 1, self.LH - 1, 8,
                        fill="#1B1B1C", outline="#3C3C3C", width=2,
                        tags=("del",))
        cv.create_text(self.LW / 2, self.LH / 2, text="删除设备",
                       fill="#F5F5F7", font=(FONT, -int(round(10 * self.S)),
                                             "normal"), tags=("del",))
        cv.tag_bind("del", "<Button-1>", lambda e: self._delete())
        cv.tag_bind("del", "<Enter>",
                    lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind("del", "<Leave>", lambda e: cv.configure(cursor=""))
        cv.scale("all", 0, 0, self.S, self.S)

    def _delete(self):
        try:
            self.panel.delete_device(self.key)
        except Exception:
            pass
        self.close()

    def close(self):
        try:
            if self.shadow:
                self.shadow.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        if getattr(self.panel, "_menu", None) is self:
            self.panel._menu = None


class Panel:
    LW, LH = 640, 590

    def _f(self, size, bold=False):
        # 原实现中 8-9px 的负字号在高 DPI 下会被重度提示，显得发虚。
        # 统一给正文保留至少 10px，再做轻微比例放大。
        px = max(10, int(round(size * self.S * 1.06)))
        return (FONT, -px, "bold" if bold else "normal")

    def _apply_theme(self):
        dark = {
            "DSH_BG": (24, 25, 28), "DSH_BG_BOTTOM": (29, 30, 33),
            "DSH_CARD": (34, 35, 39), "DSH_CARD_HI": (40, 42, 47),
            "DSH_BORDER": (55, 57, 64), "DSH_TEXT": (246, 247, 249),
            "DSH_SUB": (166, 170, 178), "DSH_MUTED": (116, 121, 130),
            "DSH_BTN": (66, 69, 76), "DSH_BTN_HOVER": (82, 86, 95),
            "DSH_TRACK": (53, 56, 63), "DSH_ACCENT": (82, 156, 255),
            "DSH_ACCENT_SOFT": (43, 72, 111), "DSH_OK": (66, 211, 146),
            "DSH_WARN": (248, 190, 78), "DSH_ERROR": (244, 112, 112),
            "DSH_DANGER_BG": (24, 25, 28),
            "DSH_DANGER_BORDER": (91, 51, 55),
            "DSH_DANGER_TEXT": (244, 146, 146),
        }
        light = {
            "DSH_BG": (245, 246, 248), "DSH_BG_BOTTOM": (235, 237, 241),
            "DSH_CARD": (255, 255, 255), "DSH_CARD_HI": (242, 244, 248),
            "DSH_BORDER": (218, 222, 229), "DSH_TEXT": (29, 34, 43),
            "DSH_SUB": (83, 91, 104), "DSH_MUTED": (126, 134, 147),
            "DSH_BTN": (229, 233, 240), "DSH_BTN_HOVER": (216, 222, 232),
            "DSH_TRACK": (224, 228, 235), "DSH_ACCENT": (43, 111, 226),
            "DSH_ACCENT_SOFT": (215, 227, 250), "DSH_OK": (13, 145, 96),
            "DSH_WARN": (184, 116, 9), "DSH_ERROR": (205, 58, 58),
            "DSH_DANGER_BG": (255, 244, 244),
            "DSH_DANGER_BORDER": (238, 196, 196),
            "DSH_DANGER_TEXT": (190, 47, 47),
        }
        for name, value in (light if self.light else dark).items():
            setattr(self, name, value)

    def set_theme(self, light: bool):
        self.light = bool(light)
        self._apply_theme()
        try:
            self.root.configure(bg=_rgb(self.DSH_BG))
            self.cv.configure(bg=_rgb(self.DSH_BG))
        except Exception:
            pass
        self._icon_photos.clear()
        if getattr(self, "_visible", False):
            self.render()

    def _toggle_theme(self):
        self.app.toggle_panel_theme()

    def __init__(self, app: "TrayApp"):
        self.app = app
        self.S = max(1.0, UI_SCALE)
        self.W = int(self.LW * self.S)
        self.H = int(self.LH * self.S)
        self.light = bool(getattr(app, "panel_light", False))
        self._apply_theme()
        self.root = tk.Tk()
        self.root.title("2.4G")
        self.root.overrideredirect(True)
        self.root.configure(bg=_rgb(self.DSH_BG))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0, bg=_rgb(self.DSH_BG))
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind_all("<Button-1>", self._maybe_commit, add="+")
        self.root.bind("<Escape>", lambda e: self.hide())
        self._drag = None
        self._entry = None
        self._entry_key = None
        self._entry_win = None
        self._entry_item = None
        self._entry_guard = 0.0
        self._help_win = None
        self._name_font = None
        self._name_limit_px = 0
        self._name_max_chars = 0
        self._scroll = 0.0
        self._max_scroll = 0.0
        self._card_rects = {}
        self._dpi_slider_rects = {}
        self._dpi_drag = None
        self._dpi_preview = {}
        self._dpi_expanded = set()
        self._icon_photos = {}
        self._font_metrics = {}
        self._menu = None
        self._menu_children = []
        self.cv.bind("<Button-3>", self._on_right_click)
        self._track_h = 0.0
        self._thumb_h = 0.0
        self._bar_drag = None
        self.cv.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<MouseWheel>", self._on_wheel)
        self._built = False
        self._startup_var = startup_enabled()
        self.root.update_idletasks()
        hwnd = self.root.winfo_id()
        self.hwnd = hwnd
        # 圆半径 16px（Region 的椭圆直径是半径的两倍）
        set_round_region(hwnd, self.W, self.H, int(32 * self.S))
        try:
            GCL_STYLE = -26
            style = user32.GetClassLongPtrW(hwnd, GCL_STYLE)
            user32.SetClassLongPtrW(hwnd, GCL_STYLE, style | 0x00020000)
        except Exception:
            pass
        # 面板没有输入框：摘掉 IME，避免输入法候条弹在窗口上方
        try:
            imm32.ImmAssociateContext.argtypes = [wt.HWND, wt.HANDLE]
            imm32.ImmAssociateContext(hwnd, None)
        except Exception:
            pass
        self._visible = False
        self._shadow = None
        try:
            self._shadow = ShadowWindow(self)
        except Exception:
            self._shadow = None
        self._center()
        self.root.withdraw()

    def _center(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - self.W) // 2)
        y = max(0, (sh - self.H) // 2 - 40)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    # -- 交互 --------------------------------------------------------------
    def _on_press(self, e):
        self._clear_menu()
        try:
            if getattr(self, "_shadow", None):
                self._shadow.show(self.root.winfo_x(), self.root.winfo_y())
                self.root.after(
                    60, lambda: self._shadow.show(
                        self.root.winfo_x(), self.root.winfo_y())
                    if (self._shadow and self.root.winfo_viewable()) else None)
        except Exception:
            pass
        self._drag = None
        if e.y >= 64 * self.S:
            return
        tags = self.cv.gettags("current")
        if any(t.startswith(("btn_", "chk_", "fix_", "rename_",
                             "dpi_toggle_", "type_")) for t in tags):
            return
        self._drag = (e.x_root, e.y_root,
                      self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, e):
        if self._dpi_drag:
            self._dpi_update_from_event(e, self._dpi_drag)
            return
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        dx = e.x_root - x0
        dy = e.y_root - y0
        if abs(dx) < 3 and abs(dy) < 3:
            return
        nx = wx + dx
        ny = wy + dy
        self.root.geometry(f"+{nx}+{ny}")
        if self._shadow:
            try:
                self._shadow.move(nx, ny)
            except Exception:
                pass

    def _on_release(self, e):
        if self._dpi_drag:
            self._dpi_finish_drag(e)
            self._drag = None
            return
        self._drag = None

    def show(self):
        self._visible = True
        self.set_theme(getattr(self.app, "panel_light", False))
        self.root.deiconify()
        try:
            # 叽顶不抢焦点：避免输入法条弹在面板上方
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.root.lift()
        if self._shadow:
            try:
                self.root.update_idletasks()
                self._shadow.show(self.root.winfo_x(), self.root.winfo_y())
            except Exception:
                pass

    def _close_help(self):
        hw = getattr(self, "_help_win", None)
        if hw:
            try:
                hw.close()
            except Exception:
                pass
        self._help_win = None

    def hide(self):
        if getattr(self, "_entry", None):
            self._commit_name(True)
        self._visible = False
        self._close_help()
        self._clear_menu()
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass
        if getattr(self.app, "panel_only", False):
            if not getattr(self.app, "_stop", False):
                self.app.quit()
            return
        if self._shadow:
            try:
                self._shadow.hide()
                self._shadow.release_bitmap()
            except Exception:
                pass
        self.root.withdraw()
        # 隐藏后释放 Canvas、字体、位图和交互控件的临时缓存。
        try:
            self.cv.delete("all")
            self._btns.clear()
            self._card_rects.clear()
            self._dpi_slider_rects.clear()
            self._dpi_preview.clear()
            self._dpi_expanded.clear()
            self._dpi_drag = None
            self._icon_photos.clear()
            self._font_metrics.clear()
            self._name_font = None
            self._scroll = 0.0
            self._max_scroll = 0.0
        except Exception:
            pass
        import gc
        gc.collect()

    def close(self):
        self._close_help()
        try:
            self.cv.delete("all")
            self._icon_photos.clear()
        except Exception:
            pass
        if self._shadow:
            try:
                self._shadow.destroy()
            except Exception:
                pass
            self._shadow = None
        try:
            self.root.destroy()
        except Exception:
            pass

    # -- 夜航玻璃绘制（Nocturne Glassmorphism?-----------------------------
    LW_ID = (11, 19, 34)      # #0B1322
    LW_NEAR = (6, 10, 19)     # #060A13
    LW_MOON = (124, 156, 196)
    LW_STEEL = (51, 81, 122)
    LW_CHAMP = (228, 184, 99)  # #E4B863
    LW_CHAMP_L = (243, 220, 168)
    LW_WHITE = (245, 248, 255)

    def _mix(self, a, b, t):
        return (int(a[0] + (b[0] - a[0]) * t),
                int(a[1] + (b[1] - a[1]) * t),
                int(a[2] + (b[2] - a[2]) * t))

    def _level_rgb(self, lv):
        if lv is None:
            return (109, 127, 150)
        if lv >= 60:
            return self.DSH_OK
        if lv >= 30:
            return self.DSH_WARN
        return self.DSH_ERROR

    def _updated_text(self) -> str:
        rs = self.app.snapshot()
        times = [r.ts for r in rs.values() if r.ts]
        if not times:
            return "尚未读取"
        return "上次更新 " + time.strftime("%H:%M:%S", time.localtime(max(times)))

    def _on_right_click(self, e):
        self._clear_menu()
        ly = e.y / self.S
        lx = e.x / self.S
        for key, (x1, y1, x2, y2) in self._card_rects.items():
            if x1 <= lx <= x2 and y1 <= ly <= y2:
                menu_bg = _rgb(self.DSH_CARD)
                menu_fg = _rgb(self.DSH_TEXT)
                menu_active = _rgb(self.DSH_BTN_HOVER)
                menu = tk.Menu(
                    self.root, tearoff=0, bg=menu_bg, fg=menu_fg,
                    activebackground=menu_active,
                    activeforeground=menu_fg, bd=0, relief="flat")
                menu.add_command(
                    label="重命名设备",
                    command=lambda k=key: self._begin_rename(k))
                type_menu = tk.Menu(
                    menu, tearoff=0, bg=menu_bg, fg=menu_fg,
                    activebackground=menu_active, activeforeground=menu_fg,
                    bd=0, relief="flat")
                selected_type = self.app.device_type(key)
                for type_key, label in DEVICE_TYPES:
                    marker = "● " if type_key == selected_type else "   "
                    type_menu.add_command(
                        label=marker + label,
                        command=lambda k=key, v=type_key:
                        self.app.set_device_type(k, v))
                menu.add_cascade(label="设备类型", menu=type_menu)
                menu.add_separator()
                menu.add_command(
                    label="删除设备",
                    command=lambda k=key: self.delete_device(k))
                self._menu = menu
                self._menu_children = [type_menu]
                menu.bind("<Unmap>", lambda ev: self._clear_menu())
                menu.tk_popup(e.x_root, e.y_root)
                return

    def _begin_rename(self, key: str):
        self._clear_menu()
        rect = self._card_rects.get(key)
        if rect:
            x1, y1, _x2, _y2 = rect
            x, y = (x1 + 34) * self.S, (y1 + 24) * self.S
        else:
            x, y = 58 * self.S, self.HEADER_H * self.S
        self._edit_name(key, x, y)

    def _open_type_menu(self, key: str, x_root: int, y_root: int):
        self._clear_menu()
        menu_bg = _rgb(self.DSH_CARD)
        menu_fg = _rgb(self.DSH_TEXT)
        menu_active = _rgb(self.DSH_BTN_HOVER)
        menu = tk.Menu(
            self.root, tearoff=0, bg=menu_bg, fg=menu_fg,
            activebackground=menu_active, activeforeground=menu_fg,
            bd=0, relief="flat")
        selected = self.app.device_type(key)
        for type_key, label in DEVICE_TYPES:
            marker = "● " if type_key == selected else "   "
            menu.add_command(
                label=marker + label,
                command=lambda k=key, v=type_key:
                self.app.set_device_type(k, v))
        self._menu = menu
        self._menu_children = []
        menu.bind("<Unmap>", lambda ev: self._clear_menu())
        menu.tk_popup(int(x_root), int(y_root))

    def _clear_menu(self):
        menu = self._menu
        children = self._menu_children
        self._menu = None
        self._menu_children = []
        for child in children:
            try:
                child.destroy()
            except Exception:
                pass
        if menu is not None:
            try:
                menu.unpost()
            except Exception:
                pass
            try:
                menu.destroy()
            except Exception:
                pass

    def delete_device(self, key: str):
        self._clear_menu()
        try:
            self.app.remove_device(key)
        except Exception:
            pass

    # -- DSH 同扁平风格（盖上面的夜航玻璃实现?---------------------------
    DSH_BG = (24, 25, 28)        # #18191C
    DSH_BG_BOTTOM = (29, 30, 33) # #1D1E21
    DSH_CARD = (34, 35, 39)      # #222327
    DSH_CARD_HI = (40, 42, 47)   # #282A2F
    DSH_BORDER = (55, 57, 64)    # #373940
    DSH_TEXT = (246, 247, 249)   # #F6F7F9
    DSH_SUB = (166, 170, 178)    # #A6AAB2
    DSH_MUTED = (116, 121, 130)  # #747982
    DSH_BTN = (66, 69, 76)       # #42454C
    DSH_BTN_HOVER = (82, 86, 95)
    DSH_TRACK = (53, 56, 63)
    DSH_ACCENT = (82, 156, 255)  # #529CFF
    DSH_ACCENT_SOFT = (43, 72, 111)
    DSH_OK = (66, 211, 146)
    DSH_WARN = (248, 190, 78)
    DSH_ERROR = (244, 112, 112)

    HEADER_H = 94
    FOOTER_H = 70
    CARD_GAP = 14
    CARD_H = 148
    DPI_COLLAPSED_H = 176
    DPI_CARD_H = 248

    def _is_a7_pro(self, reading) -> bool:
        return bool(reading and reading.vid == 0x5253 and reading.pid == 0x1021)

    def _card_height(self, reading, key=None) -> int:
        if not self._is_a7_pro(reading):
            return self.CARD_H
        return (self.DPI_CARD_H if key in self._dpi_expanded
                else self.DPI_COLLAPSED_H)

    def _paint_background(self):
        cv = self.cv
        W, H = self.LW, self.LH
        for y in range(0, H, 8):
            t = y / max(1, H - 1)
            col = self._mix(self.DSH_BG, self.DSH_BG_BOTTOM, t)
            cv.create_rectangle(0, y, W, min(H, y + 8),
                                fill=_rgb(col), outline="")
        cv.create_line(24, 1, W - 24, 1,
                       fill=_rgb(self._mix(self.DSH_BG, self.DSH_TEXT, 0.12)))

    def render(self):
        if getattr(self, "_entry", None):
            return
        cv = self.cv
        cv.delete("all")
        self._btns = {}
        self._dpi_slider_rects = {}
        W, H = self.LW, self.LH
        self._paint_background()

        snap = self.app.snapshot()
        # 写入成功或失败后恢复硬件实际值；拖动期间保留本地预览。
        for preview_key in list(self._dpi_preview):
            phase, _ = self.app.dpi_status(preview_key)
            if phase != "busy" and preview_key != self._dpi_drag:
                self._dpi_preview.pop(preview_key, None)
        keys = list(snap.keys())
        self._card_rects = {}
        heights = [self._card_height(snap.get(k), k) for k in keys]
        content = (sum(heights) + self.CARD_GAP * max(0, len(keys) - 1)
                   if keys else self.CARD_H)
        viewport_h = self.LH - self.HEADER_H - self.FOOTER_H
        self._max_scroll = max(0.0, content - viewport_h)
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))
        y = self.HEADER_H - self._scroll
        if keys:
            for key, height in zip(keys, heights):
                self._card(24, y, W - 24, y + height, key)
                y += height + self.CARD_GAP
        else:
            self._empty_card(24, self.HEADER_H, W - 24,
                             self.HEADER_H + self.CARD_H)

        # 裁剪视口，避免滚动卡片压住标题栏和底栏。
        cv.create_rectangle(0, 0, W, self.HEADER_H,
                            fill=_rgb(self.DSH_BG), outline="")
        cv.create_rectangle(0, H - self.FOOTER_H, W, H,
                            fill=_rgb(self.DSH_BG_BOTTOM), outline="")
        cv.create_line(0, H - self.FOOTER_H, W, H - self.FOOTER_H,
                       fill=_rgb(self._mix(self.DSH_BG_BOTTOM,
                                          self.DSH_BORDER, 0.50)))

        # 品牌标识与标题
        cv.create_image(24, 20, image=self._brand_photo(34), anchor="nw")
        cv.create_text(72, 31, text="外设中心", anchor="w",
                       fill=_rgb(self.DSH_TEXT), font=self._f(18, True))
        iv = self.app.interval
        iv_text = (f"{iv // 60} 分钟" if iv % 60 == 0 else f"{iv} 秒")
        online = sum(1 for r in snap.values() if r and r.ok)
        subtitle = f"2.4G WIRELESS · {online}/{len(keys)} 台在线 · {iv_text}自动刷新"
        cv.create_text(72, 55, text=subtitle, anchor="w",
                       fill=_rgb(self.DSH_SUB), font=self._f(10))
        self._icon_button(W - 86, 18, 36, 36,
                          self._theme_photo(self.light),
                          self._toggle_theme, tooltip="切换日夜主题")
        self._icon_button(W - 42, 22, 24, 24,
                          self._close_photo(), self.hide, kind="close",
                          tooltip="隐藏到托盘")
        self._scrollbar()

        upd = self._ctext(24, H - 35, self._updated_text(),
                          _rgb(self.DSH_MUTED), self._f(10), anchor="w",
                          tags=("upd_btn",))
        cv.tag_bind("upd_btn", "<Button-1>", lambda e: self.app.refresh())
        cv.tag_bind("upd_btn", "<Enter>",
                    lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind("upd_btn", "<Leave>",
                    lambda e: cv.configure(cursor=""))
        self._check(W - 270, H - 35, self._startup_var, "开机自启",
                    lambda: self._toggle_startup())
        self._button(W - 188, H - 49, 74, 28, "最小化", self.hide,
                     kind="ghost")
        self._button(W - 96, H - 49, 72, 28, "退出", self.app.quit,
                     kind="danger")
        cv.scale("all", 0, 0, self.S, self.S)

    def _draw_border(self):
        """整圈 2px 边框：一条闭合折线 + 圆角连接，避免直线/圆弧接头。"""
        cv = self.cv
        W, H = self.W, self.H
        R = 16.0 * self.S
        col = _rgb((68, 70, 77))
        pts = round_rect_points(2, 2, W - 2, H - 2, R - 2.0)
        flat = [v for p in pts for v in p]
        flat += flat[:2]
        cv.create_line(*flat, fill=col, width=2,
                       joinstyle="round", capstyle="round", smooth=False)

    def open_help(self, key=None):
        old = getattr(self, "_help_win", None)
        if old is not None:
            if getattr(old, "key", None) == key:
                return
            try:
                old.close()
            except Exception:
                pass
            self._help_win = None
        self._help_win = HelpWindow(self, key)

    def _on_wheel(self, e):
        if self._entry:
            self._commit_name(True)
        if self._dpi_drag or self._max_scroll <= 0:
            return
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll + (-40 if e.delta > 0 else 40)))
        self.render()

    def _sbar_press(self, e):
        self._bar_drag = (e.y, self._scroll)

    def _sbar_move(self, e):
        if self._bar_drag is None or self._thumb_h <= 0:
            return
        y0, s0 = self._bar_drag
        travel = max(1.0, self._track_h - self._thumb_h)
        dy = (e.y - y0) / self.S
        self._scroll = max(0.0, min(self._max_scroll,
                                    s0 + dy * (self._max_scroll / travel)))
        self.render()

    def _sbar_release(self, e):
        self._bar_drag = None

    def _scrollbar(self):
        if self._max_scroll <= 0:
            self._track_h = self._thumb_h = 0.0
            return
        cv = self.cv
        W = self.LW
        x1, x2 = W - 14, W - 8
        y1 = self.HEADER_H + 10
        y2 = self.LH - self.FOOTER_H - 10
        track_h = y2 - y1
        viewport_h = self.LH - self.HEADER_H - self.FOOTER_H
        content = self._max_scroll + viewport_h
        thumb_h = max(24.0, track_h * viewport_h / content)
        ty = y1 + (self._scroll / self._max_scroll) * (track_h - thumb_h)
        self._track_h, self._thumb_h = track_h, thumb_h
        draw_round_rect(cv, x1, y1, x2, y2, 3,
                        fill=_rgb(self._mix(self.DSH_BG, self.DSH_TRACK, 0.55)),
                        outline="")
        bar = draw_round_rect(cv, x1, ty, x2, ty + thumb_h, 3,
                              fill=_rgb(self._mix(self.DSH_BORDER,
                                                 self.DSH_SUB, 0.32)),
                              outline="")
        cv.addtag_withtag("sbar_thumb", bar)
        cv.tag_bind("sbar_thumb", "<Button-1>", self._sbar_press)
        cv.tag_bind("sbar_thumb", "<B1-Motion>", self._sbar_move)
        cv.tag_bind("sbar_thumb", "<ButtonRelease-1>", self._sbar_release)

    @staticmethod
    def _in_round_rect(u, v, left, top, right, bottom, radius):
        if u < left or u > right or v < top or v > bottom:
            return False
        qx = max(left + radius - u, 0.0, u - (right - radius))
        qy = max(top + radius - v, 0.0, v - (bottom - radius))
        return qx * qx + qy * qy <= radius * radius

    @staticmethod
    def _segment_distance(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) /
                         (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def _aa_photo(self, key, width, height, sampler):
        """4x4 超采样生成带透明边缘的小 PNG，替代 Tk 的锯齿 Canvas 图元。"""
        cached = self._icon_photos.get(key)
        if cached is not None:
            return cached
        w = max(2, int(round(width)))
        h = max(2, int(round(height)))
        ss = 4
        total_samples = ss * ss
        rgba = bytearray(w * h * 4)
        for py in range(h):
            for px in range(w):
                count = sr = sg = sb = 0
                for sy in range(ss):
                    for sx in range(ss):
                        u = (px + (sx + 0.5) / ss) / w
                        v = (py + (sy + 0.5) / ss) / h
                        color = sampler(u, v)
                        if color is None:
                            continue
                        count += 1
                        sr += color[0]
                        sg += color[1]
                        sb += color[2]
                if not count:
                    continue
                i = (py * w + px) * 4
                rgba[i:i + 4] = bytes((
                    (sr + count // 2) // count,
                    (sg + count // 2) // count,
                    (sb + count // 2) // count,
                    (255 * count + total_samples // 2) // total_samples,
                ))
        photo = tk.PhotoImage(master=self.root, format="png",
                              data=_make_png_data(w, h, bytes(rgba)))
        self._icon_photos[key] = photo
        return photo

    def _circle_photo(self, color, logical_size):
        px = max(4, int(round(logical_size * self.S)))
        key = ("circle", color, px)

        def sample(u, v):
            return color if math.hypot(u - 0.5, v - 0.5) <= 0.5 else None

        return self._aa_photo(key, px, px, sample)

    def _brand_photo(self, logical_size=34):
        px = max(12, int(round(logical_size * self.S)))
        key = ("brand", px)

        def sample(u, v):
            def inside(inset, radius):
                return self._in_round_rect(
                    u, v, inset, inset, 1.0 - inset, 1.0 - inset, radius)

            if not inside(0.0, 10 / 34):
                return None
            if not inside(1 / 34, 9 / 34):
                return (69, 122, 190)
            base = (43, 72, 111)
            accent = (82, 156, 255)
            cx, cy = 0.5, 0.76
            d = math.hypot(u - cx, v - cy)
            if d <= 0.052:
                return accent
            angle = math.degrees(math.atan2(cy - v, u - cx))
            if 38 <= angle <= 142:
                for radius, thickness in ((0.13, 0.042),
                                          (0.235, 0.050),
                                          (0.34, 0.056)):
                    if abs(d - radius) <= thickness / 2:
                        return accent
            return base

        return self._aa_photo(key, px, px, sample)

    def _dpi_thumb_photo(self, enabled):
        px = max(12, int(round(22 * self.S)))
        key = ("dpi-thumb", enabled, px)
        accent = self.DSH_ACCENT if enabled else self.DSH_MUTED

        def sample(u, v):
            d = math.hypot(u - 0.5, v - 0.5)
            if d > 0.5:
                return None
            if d >= 0.5 - 2 / 22:
                return accent
            if d <= 4 / 22:
                return accent
            return self.DSH_CARD

        return self._aa_photo(key, px, px, sample)

    def _check_photo(self, checked):
        px = max(8, int(round(12 * self.S)))
        key = ("check", checked, px)

        def sample(u, v):
            if not self._in_round_rect(u, v, 0, 0, 1, 1, 3.5 / 12):
                return None
            if not self._in_round_rect(
                    u, v, 1 / 12, 1 / 12, 11 / 12, 11 / 12, 2.5 / 12):
                return self.DSH_BORDER
            if checked:
                d1 = self._segment_distance(u, v, 0.27, 0.52, 0.44, 0.69)
                d2 = self._segment_distance(u, v, 0.44, 0.69, 0.76, 0.32)
                if min(d1, d2) <= 0.075:
                    return self.DSH_TEXT
            return self.DSH_BTN if checked else self.DSH_BG

        return self._aa_photo(key, px, px, sample)

    def _theme_photo(self, light_mode: bool):
        px = max(18, int(round(20 * self.S)))
        key = ("theme", light_mode, px)
        accent = self.DSH_ACCENT if light_mode else self.DSH_WARN

        def sample(u, v):
            if light_mode:
                # 当前为浅色模式，按钮显示月亮（点击切回夜间）。
                outer = math.hypot(u - 0.50, v - 0.50) <= 0.36
                cut = math.hypot(u - 0.64, v - 0.36) <= 0.32
                return accent if outer and not cut else None
            # 当前为深色模式，按钮显示太阳。
            d = math.hypot(u - 0.5, v - 0.5)
            if d <= 0.19:
                return accent
            angle = math.atan2(v - 0.5, u - 0.5)
            ray = abs(math.cos(4 * angle))
            if 0.29 <= d <= 0.32 + 0.08 * ray:
                return accent
            return None

        return self._aa_photo(key, px, px, sample)

    def _close_photo(self):
        px = max(14, int(round(14 * self.S)))
        key = ("close", px)
        col = self.DSH_SUB

        def sample(u, v):
            d1 = self._segment_distance(u, v, 0.27, 0.27, 0.73, 0.73)
            d2 = self._segment_distance(u, v, 0.73, 0.27, 0.27, 0.73)
            return col if min(d1, d2) <= 0.075 else None

        return self._aa_photo(key, px, px, sample)

    def _chevron_photo(self, expanded):
        w = max(10, int(round(12 * self.S)))
        h = max(7, int(round(8 * self.S)))
        key = ("chevron", expanded, w, h)
        col = self.DSH_SUB

        def sample(u, v):
            if expanded:
                d1 = self._segment_distance(u, v, 0.15, 0.68, 0.50, 0.34)
                d2 = self._segment_distance(u, v, 0.50, 0.34, 0.85, 0.68)
            else:
                d1 = self._segment_distance(u, v, 0.15, 0.34, 0.50, 0.68)
                d2 = self._segment_distance(u, v, 0.50, 0.68, 0.85, 0.34)
            return col if min(d1, d2) <= 0.09 else None

        return self._aa_photo(key, w, h, sample)

    def _toggle_dpi(self, key: str):
        if key in self._dpi_expanded:
            self._dpi_expanded.discard(key)
            self._dpi_preview.pop(key, None)
            if self._dpi_drag == key:
                self._dpi_drag = None
        else:
            self._dpi_expanded.add(key)
        self.render()

    def _dpi_row(self, x1, x2, y, key, r, expanded):
        tag = f"dpi_toggle_{key}"
        cv = self.cv
        draw_round_rect(cv, x1 + 10, y - 13, x2 - 10, y + 13, 8,
                        fill=_rgb(self.DSH_CARD_HI), outline="",
                        tags=(tag,))
        self._ctext(x1 + 20, y, "DPI 设置", _rgb(self.DSH_SUB),
                    self._f(10, True), tags=(tag,), anchor="w")
        cfg = r.dpi_config
        preview = self._dpi_preview.get(key)
        value = (preview if preview is not None else
                 (cfg.current_dpi if cfg else None))
        value_text = f"{value} DPI" if value is not None else "--"
        self._ctext(x2 - 40, y, value_text, _rgb(self.DSH_ACCENT),
                    self._f(10, True), tags=(tag,), anchor="e")
        cv.create_image(x2 - 20, y, image=self._chevron_photo(expanded),
                        anchor="e", tags=(tag,))
        cv.tag_bind(tag, "<Button-1>",
                    lambda e, k=key: self._toggle_dpi(k))
        cv.tag_bind(tag, "<Enter>",
                    lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))

    def _dpi_value_at(self, x: float, key: str) -> int:
        rect = self._dpi_slider_rects.get(key)
        if not rect:
            return MCHOSE_DPI_MIN
        sx1, sx2 = rect
        ratio = max(0.0, min(1.0, (x - sx1) / max(1.0, sx2 - sx1)))
        value = MCHOSE_DPI_MIN + ratio * (MCHOSE_DPI_MAX - MCHOSE_DPI_MIN)
        return max(MCHOSE_DPI_MIN, min(
            MCHOSE_DPI_MAX,
            int(round(value / MCHOSE_DPI_STEP) * MCHOSE_DPI_STEP)))

    def _dpi_press(self, e, key: str):
        r = self.app.snapshot().get(key)
        phase, _ = self.app.dpi_status(key)
        if not r or not r.dpi_config or phase == "busy":
            return "break"
        self._dpi_drag = key
        self._dpi_update_from_event(e, key)
        return "break"

    def _dpi_update_from_event(self, e, key: str):
        value = self._dpi_value_at(e.x / self.S, key)
        self._dpi_preview[key] = value
        self.render()

    def _dpi_finish_drag(self, e):
        key = self._dpi_drag
        self._dpi_drag = None
        if not key:
            return
        value = self._dpi_value_at(e.x / self.S, key)
        self._dpi_preview[key] = value
        r = self.app.snapshot().get(key)
        current = r.dpi_config.current_dpi if (r and r.dpi_config) else None
        if current is not None and value == current:
            self._dpi_preview.pop(key, None)
        else:
            self.app.set_dpi(key, value)
        self.render()

    def _text_width(self, text, size=10, bold=False):
        try:
            key = (size, bold)
            font = self._font_metrics.get(key)
            if font is None:
                font = tkfont.Font(master=self.root,
                                   font=self._f(size, bold))
                self._font_metrics[key] = font
            return font.measure(text) / max(1.0, self.S)
        except Exception:
            return len(text) * size

    def _fit_text(self, text, max_width, size=10, bold=False):
        text = str(text or "")
        if self._text_width(text, size, bold) <= max_width:
            return text
        while text and self._text_width(text + "…", size, bold) > max_width:
            text = text[:-1]
        return (text + "…") if text else "…"

    def _status_chip(self, x, y, status, stale=False):
        if stale:
            label, color = "缓存数据", self.DSH_MUTED
        else:
            label, color = {
                "charging": ("充电中", self.DSH_OK),
                "full": ("已充满", self.DSH_OK),
                "wired": ("有线模式", self.DSH_ACCENT),
                "discharging": ("电池供电", self.DSH_SUB),
                "asleep": ("设备休眠", self.DSH_WARN),
                "xinput_full": ("四档满", self.DSH_OK),
                "xinput_medium": ("四档中", self.DSH_ACCENT),
                "xinput_low": ("四档低", self.DSH_WARN),
                "xinput_empty": ("即将耗尽", self.DSH_ERROR),
                "battery_unavailable": ("无电量数据", self.DSH_WARN),
            }.get(status, ("", self.DSH_MUTED))
        if not label:
            return
        width = self._text_width(label, 9, True) + 22
        height = 22
        fill = self._mix(self.DSH_CARD, color, 0.16)
        border = self._mix(self.DSH_CARD, color, 0.52)
        draw_round_rect(self.cv, x, y - height / 2, x + width, y + height / 2,
                        11, fill=_rgb(fill), outline=_rgb(border), width=1)
        self._ctext(x + width / 2, y, label, _rgb(color), self._f(9, True))

    def _draw_dpi_slider(self, x1, x2, cy, key, r):
        cv = self.cv
        cfg = r.dpi_config
        preview = self._dpi_preview.get(key)
        value = preview if preview is not None else (cfg.current_dpi if cfg else 0)
        phase, message = self.app.dpi_status(key)
        enabled = bool(cfg) and r.ok and r.present and phase != "busy"
        sx1, sx2 = x1 + 18, x2 - 18
        sv = max(MCHOSE_DPI_MIN, min(MCHOSE_DPI_MAX, value))
        ratio = (sv - MCHOSE_DPI_MIN) / (MCHOSE_DPI_MAX - MCHOSE_DPI_MIN)
        px = sx1 + ratio * (sx2 - sx1)
        self._dpi_slider_rects[key] = (sx1, sx2)

        tag = f"dpi_{key}"
        # 宽命中区让轨道任意位置都能按下并开始拖动。
        cv.create_rectangle(sx1 - 10, cy - 13, sx2 + 10, cy + 15,
                            fill=_rgb(self.DSH_CARD), outline="", tags=(tag,))
        draw_round_rect(cv, sx1, cy - 3, sx2, cy + 3, 3,
                        fill=_rgb(self.DSH_TRACK), outline="", tags=(tag,))
        if px > sx1:
            draw_round_rect(cv, sx1, cy - 3, max(sx1 + 6, px), cy + 3, 3,
                            fill=_rgb(self.DSH_ACCENT if enabled
                                      else self.DSH_MUTED), outline="",
                            tags=(tag,))
        for i in range(6):
            tx = sx1 + (sx2 - sx1) * i / 5
            cv.create_line(tx, cy + 8, tx, cy + 12,
                           fill=_rgb((76, 80, 89)), width=1, tags=(tag,))
        cv.create_image(px, cy, image=self._dpi_thumb_photo(enabled),
                        anchor="center", tags=(tag,))
        if enabled:
            cv.tag_bind(tag, "<Button-1>",
                        lambda e, k=key: self._dpi_press(e, k))
            cv.tag_bind(tag, "<Enter>",
                        lambda e: cv.configure(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))
        cv.create_text(sx1, cy + 24, text=str(MCHOSE_DPI_MIN), anchor="w",
                       fill=_rgb(self.DSH_MUTED), font=self._f(9))
        cv.create_text(sx2, cy + 24, text=str(MCHOSE_DPI_MAX), anchor="e",
                       fill=_rgb(self.DSH_MUTED), font=self._f(9))

        if not cfg:
            hint, color = "DPI 暂不可用 · 重新拔插接收器后刷新", self.DSH_WARN
        elif not r.ok or not r.present:
            hint, color = "设备休眠或未连接，暂时不能修改 DPI", self.DSH_MUTED
        elif phase == "busy":
            hint, color = message or "正在应用…", self.DSH_ACCENT
        elif phase == "error":
            hint, color = message or "应用失败", self.DSH_ERROR
        elif phase == "ok":
            hint, color = message or "已应用", self.DSH_OK
        else:
            hint, color = "拖动后松开应用 · 自动写入当前无线档位", self.DSH_MUTED
        cv.create_text(x1 + 18, cy + 43, text=hint[:72], anchor="w",
                       fill=_rgb(color), font=self._f(9))

    def _card(self, x1, y1, x2, y2, key):
        cv = self.cv
        self._card_rects[key] = (x1, y1, x2, y2)
        r = (self.app.snapshot().get(key)
             or Reading(ok=False, name="设备"))
        dpi_capable = self._is_a7_pro(r)
        dpi_open = dpi_capable and key in self._dpi_expanded
        card_fill = (self.DSH_CARD if r.present
                     else self._mix(self.DSH_CARD, self.DSH_BG, 0.34))
        draw_round_rect(cv, x1, y1, x2, y2, 14,
                        fill=_rgb(card_fill), outline=_rgb(self.DSH_BORDER),
                        width=1)
        cv.create_line(x1 + 16, y1 + 1, x2 - 16, y1 + 1,
                       fill=_rgb(self._mix(card_fill, self.DSH_TEXT, 0.10)))
        if dpi_capable:
            draw_round_rect(cv, x1 + 1, y1 + 18, x1 + 3, y2 - 18, 1,
                            fill=_rgb(self.DSH_ACCENT), outline="")

        status_col = (self.DSH_OK if r.ok else
                      (self.DSH_WARN if r.present else self.DSH_MUTED))
        cv.create_image(x1 + 22.5, y1 + 23.5,
                        image=self._circle_photo(status_col, 9),
                        anchor="center")

        display_name = r.name or "设备"
        max_name_w = max(90, (x2 - 194) - (x1 + 34))
        display_name = self._fit_text(display_name, max_name_w, 12, True)
        nm_tag = f"nm_{key}"
        cv.create_text(x1 + 34, y1 + 24, text=display_name, anchor="w",
                       fill=_rgb(self.DSH_TEXT), font=self._f(12, True),
                       tags=(nm_tag,))
        cv.tag_bind(nm_tag, "<Double-Button-1>",
                    lambda e, k=key: self._begin_rename(k))
        cv.tag_bind(nm_tag, "<Enter>",
                    lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(nm_tag, "<Leave>",
                    lambda e: cv.configure(cursor=""))

        unknown = (r.status == "unknown_proto")
        type_key = self.app.device_type(key)
        type_label = DEVICE_TYPE_LABELS.get(type_key, "其他")
        type_tag = f"type_{key}"
        prefix = "设备类型"
        type_x, type_y = x1 + 34, y1 + 49
        self._ctext(type_x, type_y, prefix, _rgb(self.DSH_MUTED),
                    self._f(9), tags=(type_tag,), anchor="w")
        label_x = type_x + self._text_width(prefix, 9) + 7
        type_item = self._ctext(
            label_x, type_y, type_label, _rgb(self.DSH_TEXT),
            self._f(10, True), tags=(type_tag,), anchor="w")
        type_right = label_x + self._text_width(type_label, 10, True)
        cv.tag_bind(
            type_tag, "<Double-Button-1>",
            lambda e, k=key: self._open_type_menu(k, e.x_root, e.y_root))
        cv.tag_bind(
            type_tag, "<Enter>",
            lambda e, item=type_item: (
                cv.itemconfigure(item, fill=_rgb(self.DSH_ACCENT)),
                cv.configure(cursor="hand2")))
        cv.tag_bind(
            type_tag, "<Leave>",
            lambda e, item=type_item: (
                cv.itemconfigure(item, fill=_rgb(self.DSH_TEXT)),
                cv.configure(cursor="")))

        hint_text, hint_col = "", self.DSH_MUTED
        if r.stale:
            hint_text, hint_col = r.detail or "上次更新", self.DSH_WARN
        elif r.status == "asleep":
            hint_text, hint_col = r.detail or "设备休眠", self.DSH_MUTED
        elif r.status == "battery_unavailable":
            hint_text, hint_col = r.detail or "电池组未上报电量", self.DSH_WARN
        elif unknown:
            hint_text, hint_col = "协议未知 · 点我解决", self.DSH_WARN
        max_hint_w = max(10, x2 - 22 - (type_right + 10))
        if hint_text:
            hint_text = self._fit_text(hint_text, max_hint_w, 9)
            hint_tag = f"fix_{key}" if unknown else ""
            tags = (hint_tag,) if unknown else ()
            cv.create_text(type_right + 10, y1 + 50, text=hint_text, anchor="w",
                           fill=_rgb(hint_col), font=self._f(9, bool(unknown)),
                           tags=tags)
            if unknown:
                cv.tag_bind(hint_tag, "<Button-1>",
                            lambda e, k=key: self.open_help(k))
                cv.tag_bind(hint_tag, "<Enter>",
                            lambda e: cv.configure(cursor="hand2"))
                cv.tag_bind(hint_tag, "<Leave>",
                            lambda e: cv.configure(cursor=""))

        lv = r.level if r.ok else None
        charge_like = r.ok and not r.stale and r.status in (
            "charging", "full", "wired")
        if r.ok and lv is not None:
            pct_col = self.DSH_OK if charge_like else self.DSH_TEXT
            cv.create_text(x2 - 43, y1 + 34, text=str(lv), anchor="e",
                           fill=_rgb(pct_col), font=self._f(30, True))
            cv.create_text(x2 - 22, y1 + 35, text="%", anchor="e",
                           fill=_rgb(self.DSH_SUB), font=self._f(11))
        elif r.status == "asleep":
            cv.create_text(x2 - 23, y1 + 34, text="休眠", anchor="e",
                           fill=_rgb(self.DSH_MUTED), font=self._f(14, True))
        elif r.status == "battery_unavailable":
            cv.create_text(x2 - 23, y1 + 34, text="无数据", anchor="e",
                           fill=_rgb(self.DSH_WARN), font=self._f(14, True))
        elif not unknown:
            cv.create_text(x2 - 23, y1 + 34, text="未连接", anchor="e",
                           fill=_rgb(self.DSH_MUTED), font=self._f(14, True))

        self._status_chip(x1 + 18, y1 + 73, r.status, r.stale)
        bx1, bx2 = x1 + 18, x2 - 18
        by = y1 + (93 if dpi_open else 111)
        draw_round_rect(cv, bx1, by, bx2, by + 7, 3.5,
                        fill=_rgb(self.DSH_TRACK), outline="")
        if lv is not None and lv > 0:
            w = int((bx2 - bx1) * lv / 100)
            bar_col = (self.DSH_OK if r.status in ("charging", "full")
                       else self.DSH_ACCENT if r.status == "wired"
                       else self._level_rgb(lv))
            draw_round_rect(cv, bx1, by, bx1 + max(7, w), by + 7, 3.5,
                            fill=_rgb(bar_col), outline="")

        if not dpi_capable:
            return
        if not dpi_open:
            cv.create_line(x1 + 18, y1 + 123, x2 - 18, y1 + 123,
                           fill=_rgb(self.DSH_BORDER))
            self._dpi_row(x1, x2, y1 + 150, key, r, False)
            return
        cv.create_line(x1 + 18, y1 + 117, x2 - 18, y1 + 117,
                       fill=_rgb(self.DSH_BORDER))
        self._dpi_row(x1, x2, y1 + 139, key, r, True)
        cfg = r.dpi_config
        detail = (f"第 {cfg.current_index + 1} / {len(cfg.stages)} 档  ·  "
                  f"无线 2.4G" if cfg else "等待读取鼠标配置")
        cv.create_text(x1 + 18, y1 + 161, text=detail, anchor="w",
                       fill=_rgb(self.DSH_MUTED), font=self._f(9))
        self._draw_dpi_slider(x1, x2, y1 + 186, key, r)

    def _empty_card(self, x1, y1, x2, y2):
        """没有任何 2.4G 接收器时的占位卡。"""
        cv = self.cv
        draw_round_rect(cv, x1, y1, x2, y2, 13,
                        fill=_rgb(self.DSH_CARD), outline=_rgb(self.DSH_BORDER),
                        width=1)
        cv.create_image(x1 + 18, y1 + 22,
                        image=self._brand_photo(40), anchor="nw")
        cv.create_text(x1 + 74, y1 + 34, text="请插入设备", anchor="w",
                       fill=_rgb(self.DSH_TEXT), font=self._f(12, True))
        cv.create_text(x1 + 74, y1 + 54,
                       text="拔插一次接收器即可添加，无需抓包", anchor="w",
                       fill=_rgb(self.DSH_SUB), font=self._f(9))
        draw_round_rect(cv, x1 + 18, y2 - 28, x2 - 18, y2 - 20, 4,
                        fill=_rgb(self.DSH_TRACK), outline="")

    def _edit_name(self, key, x, y):
        if self._entry:
            self._commit_name(True)
        self._entry_guard = time.time()
        r = self.app.snapshot().get(key)
        current = (r.name if r and r.name else "")
        S = self.S
        bar_len = self.W - 84 * S
        limit_px = max(80, int(bar_len * 0.6))
        font = tkfont.Font(family=FONT, size=-int(round(12 * S)),
                           weight="bold")
        cjk = max(1, font.measure("汉"))
        eng = max(1.0, font.measure("abcdefghij") / 10.0)
        avg = cjk * 0.4 + eng * 0.6
        self._name_font = font
        self._name_limit_px = limit_px
        self._name_max_chars = max(4, int(limit_px / max(1.0, avg)))
        e = tk.Entry(self.root, bg=_rgb(self.DSH_CARD_HI),
                     fg=_rgb(self.DSH_TEXT),
                     insertbackground=_rgb(self.DSH_TEXT), relief="flat", bd=0,
                     highlightthickness=1,
                     highlightbackground=_rgb(self.DSH_BORDER),
                     highlightcolor=_rgb(self.DSH_ACCENT),
                     font=(FONT, -int(round(12 * S)), "bold"),
                     justify="left", selectbackground=_rgb(self.DSH_ACCENT),
                     selectforeground="#FFFFFF")
        e.insert(0, current)
        e.select_range(0, "end")
        e.configure(validate="key",
                    validatecommand=(self.root.register(self._validate_name),
                                    "%P"))
        self._entry = e
        self._entry_key = key
        self._entry_win = self.cv.create_window(
            x, y, anchor="w", window=e, width=limit_px,
            height=max(24, int(22 * S)))
        self._entry_item = None
        ids = self.cv.find_withtag(f"nm_{key}")
        if ids:
            self._entry_item = ids[0]
            self.cv.itemconfigure(self._entry_item, state="hidden")
        e.focus_set()
        e.select_range(0, "end")
        e.icursor("end")
        e.bind("<Return>", lambda ev: self._commit_name(True))
        e.bind("<Escape>", self._cancel_name)
        e.bind("<FocusOut>", lambda ev: self._commit_name(True))

    def _cancel_name(self, ev=None):
        self._commit_name(False)
        return "break"

    def _maybe_commit(self, e):
        if self._entry is not None and e.widget is not self._entry:
            if time.time() - self._entry_guard < 0.25:
                return
            self._commit_name(True)

    def _validate_name(self, proposed):
        if len(proposed) > self._name_max_chars:
            return False
        try:
            if self._name_font.measure(proposed) > self._name_limit_px:
                return False
        except Exception:
            pass
        return True

    def _commit_name(self, save):
        e = self._entry
        if not e:
            return
        key = self._entry_key
        text = e.get().strip()
        self._entry = None
        self._entry_key = None
        try:
            e.destroy()
        except Exception:
            pass
        if self._entry_win:
            try:
                self.cv.delete(self._entry_win)
            except Exception:
                pass
            self._entry_win = None
        if self._entry_item:
            try:
                self.cv.itemconfigure(self._entry_item, state="normal")
            except Exception:
                pass
            self._entry_item = None
        if save:
            try:
                self.app.rename_device(key, text)
            except Exception:
                pass
            set_custom_name(key, text)
            if text:
                try:
                    with self.app._lock:
                        rr = self.app._readings.get(key)
                        if rr is not None:
                            rr.name = text
                except Exception:
                    pass
            self.render()
            try:
                self.app.refresh()
            except Exception:
                pass
        else:
            self.render()

    def _ctext(self, x, y, text, fill, font, tags=(), anchor="center"):
        """按文字实际包围盒做垂直光学居?"""
        item = self.cv.create_text(x, y, text=text, fill=fill, font=font,
                                   tags=tags, anchor=anchor)
        try:
            _x1, y1, _x2, y2 = self.cv.bbox(item)
            dy = (y1 + y2) / 2.0 - y
            if abs(dy) >= 0.5:
                self.cv.move(item, 0, -dy)
        except Exception:
            pass
        return item

    def _button(self, x, y, w, h, text, cmd, kind="ghost"):
        tag = f"btn_{text}_{x}"
        if kind == "solid":
            fill, border, col = self.DSH_BTN, self.DSH_BTN, self.DSH_TEXT
            hover, hover_col = self.DSH_BTN_HOVER, self.DSH_TEXT
        elif kind == "close":
            # Windows 风格：正圆、透明底，悬停变红、图标变白
            fill, border, col = self.DSH_BG, self.DSH_BG, self.DSH_SUB
            hover, hover_col = (196, 43, 28), (255, 255, 255)
        elif kind == "danger":
            fill, border, col = (self.DSH_DANGER_BG, self.DSH_DANGER_BORDER,
                                 self.DSH_DANGER_TEXT)
            hover = self._mix(self.DSH_DANGER_BG, self.DSH_ERROR, 0.18)
            hover_col = self.DSH_ERROR
        else:
            fill, border, col = self.DSH_BG, self.DSH_BORDER, self.DSH_SUB
            hover, hover_col = self.DSH_BTN_HOVER, self.DSH_TEXT
        radius = h / 2 if kind == "close" else 8
        poly = draw_round_rect(self.cv, x, y, x + w, y + h, radius,
                               fill=_rgb(fill), outline=_rgb(border),
                               width=1, tags=(tag,))
        txt = self._ctext(x + w / 2, y + h / 2, text, _rgb(col),
                          self._f(11 if kind == "close" else 9), tags=(tag,))
        self._btns[tag] = (poly, fill, hover, txt, col, hover_col, False)
        self.cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
        self.cv.tag_bind(tag, "<Enter>", lambda e: self._hover_dsh(tag, True))
        self.cv.tag_bind(tag, "<Leave>", lambda e: self._hover_dsh(tag, False))

    def _icon_button(self, x, y, w, h, photo, cmd, kind="ghost",
                     tooltip=""):
        tag = f"btn_{tooltip}_{x}"
        fill = self.DSH_BG
        hover = ((196, 43, 28) if kind == "close"
                 else self.DSH_BTN_HOVER)
        poly = draw_round_rect(self.cv, x, y, x + w, y + h,
                               h / 2 if kind == "close" else 9,
                               fill=_rgb(fill), outline=_rgb(fill),
                               width=1, tags=(tag,))
        image = self.cv.create_image(x + w / 2, y + h / 2, image=photo,
                                     anchor="center", tags=(tag,))
        self.cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
        self.cv.tag_bind(
            tag, "<Enter>",
            lambda e: (self.cv.itemconfigure(poly, fill=_rgb(hover),
                                             outline=_rgb(hover)),
                       self.cv.configure(cursor="hand2")))
        self.cv.tag_bind(
            tag, "<Leave>",
            lambda e: (self.cv.itemconfigure(poly, fill=_rgb(fill),
                                             outline=_rgb(fill)),
                       self.cv.configure(cursor="")))
        return image

    def _hover_dsh(self, tag, on):
        info = self._btns.get(tag)
        if not info:
            return
        poly, base, hover, txt, col, hover_col, state = info
        if state == on:
            return
        self._btns[tag] = (poly, base, hover, txt, col, hover_col, on)
        self.cv.itemconfigure(poly, fill=_rgb(hover if on else base),
                              outline=_rgb(hover if on else base))
        self.cv.itemconfigure(txt, fill=_rgb(hover_col if on else col))

    def _check(self, x, y, checked, label, cmd):
        tag = f"chk_{label}"
        if checked:
            fill, border = self.DSH_BTN, self.DSH_BTN
        else:
            fill, border = self.DSH_BG, self.DSH_BORDER
        # y 昕行的垂直丿；方框与文字同高?2px），不再向下突出
        self.cv.create_image(x, y, image=self._check_photo(checked),
                             anchor="w", tags=(tag,))
        self._ctext(x + 20, y, label, _rgb(self.DSH_SUB), self._f(9),
                    tags=(tag,), anchor="w")
        self.cv.tag_bind(tag, "<Button-1>", lambda e: cmd())

    def _toggle_startup(self):
        try:
            if self._startup_var:
                remove_startup()
                self._startup_var = False
            else:
                self._startup_var = install_startup()
            self.render()
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("开机自启失败", f"设置失败：{e}")

    # 主线程轮诼把后台结果刷新到界面
    def poll(self):
        if getattr(self, "_dirty", False):
            self._dirty = False
            if self.root.winfo_viewable() and not self._entry:
                self.render()
        self.root.after(350, self.poll)


# ===========================================================================
# 托盘
# ===========================================================================
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_APP_REFRESH = WM_APP + 2
WM_APP_SHOW = WM_APP + 3
WM_APP_INTERVAL = WM_APP + 4
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_SETTINGCHANGE = 0x001A
WM_DEVICECHANGE = 0x0219
DBT_DEVNODES_CHANGED = 0x0007
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
TRAY_MENU_OPEN = 1001
TRAY_MENU_EXIT = 1002

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
NIF_INFO = 0x10
NIIF_INFO = 0x1
NIIF_USER = 0x4
NIIF_LARGE_ICON = 0x20

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                             wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE,
                                   ctypes.c_void_p]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
user32.SetProcessDPIAware.argtypes = []
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wt.HWND,
                                  ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_uint
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL


class Tray:
    CLASS_NAME = "BatteryTrayWndDSH"

    def __init__(self, app: "TrayApp"):
        self.app = app
        self.hwnd = 0
        self.icons: dict[int, int] = {}
        self.icon_keys: dict[int, tuple] = {}
        self.thread: Optional[threading.Thread] = None
        self.nids: dict[int, NOTIFYICONDATAW] = {}
        self.enabled = True
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self._proc)
        self._notice_icons: dict[tuple, int] = {}

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _make_nid(self, uid: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        return nid

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = self.CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.CLASS_NAME, "2.4G", 0,
                                           0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            return
        self._add_icons()
        user32.SetTimer(self.hwnd, 1, max(30, self.app.interval) * 1000, None)

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 消息循环结束（WM_CLOSE 毁窗、WM_DESTROY 或 PostQuitMessage）
        # 通知主线程结束 Tk 主循环，绝不留下“窗口没了进程还活着”的僵尸
        self.app._stop = True

    def _add_icons(self):
        for old in self.icons.values():
            if old:
                user32.DestroyIcon(old)
        self.icons.clear()
        self.nids.clear()
        self.icon_keys.clear()
        self.sync_icons()

    def sync_icons(self):
        """所有已连接设备共用同一个聚合托盘图标。"""
        desired = {1: "__all__"}
        for uid in list(self.nids):
            if uid not in desired:
                shell32.Shell_NotifyIconW(NIM_DELETE,
                                          ctypes.byref(self.nids[uid]))
                del self.nids[uid]
                old = self.icons.pop(uid, 0)
                self.icon_keys.pop(uid, None)
                if old:
                    user32.DestroyIcon(old)
        for uid, key in desired.items():
            if uid in self.nids:
                nid = self.nids[uid]
                self._fill_icon(nid, key)
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            else:
                nid = self._make_nid(uid)
                self._fill_icon(nid, key)
                shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                self.nids[uid] = nid

    def _fill_icon(self, nid: NOTIFYICONDATAW, key: str):
        snap = self.app.snapshot()
        readings = [r for r in snap.values() if r and r.present]
        levels = [r.level for r in readings
                  if r.ok and r.level is not None]
        lvl = min(levels) if levels else None
        charging = any(r.ok and r.status == "charging" for r in readings)
        full = any(r.ok and r.status == "full" for r in readings)
        k = (lvl is None, None if lvl is None else (lvl // 10), charging,
             full, self.app.theme_light)
        old = self.icons.get(nid.uID)
        if self.icon_keys.get(nid.uID) != k or not old:
            icon = make_battery_icon(lvl, self.app.theme_light, charging)
            self.icons[nid.uID] = icon
            self.icon_keys[nid.uID] = k
            nid.hIcon = icon
            if old:
                user32.DestroyIcon(old)
        else:
            nid.hIcon = old

        if not readings:
            tip = "暂无设备"
        else:
            parts = []
            for r in readings:
                name = (r.name or f"{r.vid:04X}:{r.pid:04X}")[:40]
                if r.ok and r.level is not None:
                    state = STATUS_TEXT.get(r.status, "")
                    suffix = f" {state}" if state and r.status not in (
                        "unknown", "discharging") else ""
                    parts.append(f"{name} {r.level}%{suffix}")
                elif r.status == "asleep":
                    parts.append(f"{name} 休眠")
                elif r.status == "battery_unavailable":
                    parts.append(f"{name} 已连接·无电量数据")
                else:
                    parts.append(f"{name} 未连接")
            tip = "\n".join(parts)
        nid.szTip = tip[:127]

    def refresh_icons(self):
        self.sync_icons()

    def _show_context_menu(self):
        """右键原生菜单；菜单在当前托盘线程弹出，避免跨线程调用 Tk。"""
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, MF_STRING, TRAY_MENU_OPEN,
                               "打开外设中心")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, TRAY_MENU_EXIT, "退出")
            point = wt.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                point.x, point.y, 0, self.hwnd, None)
            if cmd == TRAY_MENU_OPEN:
                self.app.request_show()
            elif cmd == TRAY_MENU_EXIT:
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            else:
                # 让菜单关闭后托盘窗口及时处理下一次点击。
                user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)

    def notify(self, text: str, title: str = "2.4G",
               level: Optional[int] = 100, charging: bool = False) -> bool:
        """显示托盘气泡；图标颜色跟随当前电量。"""
        nid = self.nids.get(1)
        if nid is None:
            return False
        icon_key = (None if level is None else max(0, min(100, int(level))) // 10,
                    self.app.theme_light, bool(charging))
        notice_icon = self._notice_icons.get(icon_key)
        if not notice_icon:
            notice_icon = make_battery_icon(
                level, self.app.theme_light, charging, size=64)
            if notice_icon:
                self._notice_icons[icon_key] = notice_icon
        old_icon = nid.hIcon
        if notice_icon:
            nid.hIcon = notice_icon
        nid.uFlags = NIF_INFO | NIF_ICON
        nid.uVersion = 10000
        nid.szInfo = text[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        nid.hBalloonIcon = notice_icon
        ok = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.hIcon = old_icon
        nid.hBalloonIcon = 0
        return bool(ok)

    def destroy(self):
        for uid, nid in list(self.nids.items()):
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.nids.clear()
        for h in list(self.icons.values()):
            user32.DestroyIcon(h)
        self.icons.clear()
        for icon in self._notice_icons.values():
            if icon:
                user32.DestroyIcon(icon)
        self._notice_icons.clear()

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            cmd = lparam & 0xFFFF
            if cmd == WM_RBUTTONUP:
                self._show_context_menu()
            elif cmd == WM_LBUTTONUP:
                self.app.request_show()
            return 0
        if msg == WM_TIMER:
            self.app.refresh()
            return 0
        if msg == self.taskbar_created:
            self._add_icons()
            return 0
        if msg == WM_SETTINGCHANGE:
            if lparam:
                try:
                    s = ctypes.wstring_at(lparam)
                    if s == "ImmersiveColorSet":
                        self.app.on_theme_changed()
                except Exception:
                    pass
            return 0
        if msg == WM_DEVICECHANGE:
            if wparam == DBT_DEVNODES_CHANGED:
                self.app._hotplug_requested = True
            return 0
        if msg == WM_APP_REFRESH:
            self.refresh_icons()
            return 0
        if msg == WM_APP_SHOW:
            self.app.request_show()
            return 0
        if msg == WM_APP_INTERVAL:
            user32.KillTimer(hwnd, 1)
            user32.SetTimer(hwnd, 1, max(30, self.app.interval) * 1000, None)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.destroy()
            user32.KillTimer(hwnd, 1)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ===========================================================================
# 应用编排
# ===========================================================================
class TrayApp:
    def __init__(self, panel_only: bool = False):
        self.panel_only = panel_only
        self.theme_light = is_light_theme()
        cfg = load_config()
        self.panel_light = cfg.get("panel_theme", "dark") == "light"
        self.interval = clamp_interval(cfg.get("interval", 60))
        self._readings: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self._dpi_lock = threading.Lock()
        self._dpi_states: dict[str, tuple[str, str]] = {}
        self._dpi_busy: set[str] = set()
        self._refreshing = False
        self._refresh_event = threading.Event()
        self._worker_thread = None
        self._worker_start_lock = threading.Lock()
        self._stop = False
        self.tray: Optional[Tray] = None
        self.panel: Optional[Panel] = None
        self._dirty = False
        self._show_requested = False
        self._full: dict[str, bool] = {}
        self._low: dict[str, bool] = {}
        self.devices = load_saved_devices()
        self._seen = None
        self._asked = set()
        self._pending_add = None
        self._add_win = None
        self._last_poll = 0.0
        self._hotplug_requested = False

    # -- 数据 --------------------------------------------------------------
    def snapshot(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._readings)

    def device_type(self, key: str) -> str:
        for dev in self.devices:
            if device_key(dev["vid"], dev["pid"]) == key:
                type_key = str(dev.get("type", ""))
                if type_key in DEVICE_TYPE_LABELS:
                    return type_key
                return infer_device_type(dev["vid"], dev["pid"],
                                         str(dev.get("name", "")))
        r = self.snapshot().get(key)
        if r:
            return infer_device_type(r.vid, r.pid, r.name)
        return "receiver"

    def set_device_type(self, key: str, type_key: str):
        if type_key not in DEVICE_TYPE_LABELS:
            return
        for dev in self.devices:
            if device_key(dev["vid"], dev["pid"]) == key:
                dev["type"] = type_key
                save_saved_devices(self.devices)
                self._dirty = True
                if self.panel:
                    self.panel.render()
                return

    def refresh(self):
        if self._refreshing:
            return
        with self._dpi_lock:
            if self._dpi_busy:
                return
        self._refreshing = True
        self._start_worker()
        self._refresh_event.set()

    def _start_worker(self):
        with self._worker_start_lock:
            thread = self._worker_thread
            if thread is not None and thread.is_alive():
                return
            self._worker_thread = threading.Thread(
                target=self._worker_loop, name="battery-refresh", daemon=True)
            self._worker_thread.start()

    def _worker_loop(self):
        while not self._stop:
            self._refresh_event.wait()
            self._refresh_event.clear()
            if not self._stop:
                self._worker_once()

    def dpi_status(self, key: str) -> tuple[str, str]:
        with self._dpi_lock:
            return self._dpi_states.get(key, ("idle", ""))

    def set_dpi(self, key: str, value: int) -> bool:
        """后台写入 DPI，避免 Tk 主线程在 HID 通信时卡住。"""
        r = self.snapshot().get(key)
        if not r or not r.dpi_config or not r.vid or not r.pid:
            return False
        with self._dpi_lock:
            if key in self._dpi_busy:
                return False
            self._dpi_busy.add(key)
            self._dpi_states[key] = ("busy", f"正在应用 {int(value)} DPI…")

        vid, pid = r.vid, r.pid

        def work():
            try:
                cfg = write_builtin_dpi(vid, pid, int(value))
                with self._lock:
                    current = self._readings.get(key)
                    if current is not None:
                        current.dpi_config = cfg
                with self._dpi_lock:
                    self._dpi_states[key] = (
                        "ok", f"已应用 {cfg.current_dpi} DPI")
            except Exception as exc:
                with self._dpi_lock:
                    self._dpi_states[key] = (
                        "error", f"应用失败：{exc}")
            finally:
                with self._dpi_lock:
                    self._dpi_busy.discard(key)
                self._dirty = True

        self._dirty = True
        threading.Thread(target=work, name="dpi-writer", daemon=True).start()
        return True

    def _worker_once(self):
        try:
            ifaces = enumerate_hid()
            res = {}
            for dev in self.devices:
                key = device_key(dev["vid"], dev["pid"])
                res[key] = read_saved_device(dev, ifaces)
            res = apply_last_known(res)
        except Exception:
            res = {}
        try:
            with self._lock:
                self._readings = res
            self._check_full(res)
            self._check_low_battery(res)
            if self.tray and self.tray.hwnd:
                user32.PostMessageW(self.tray.hwnd, WM_APP_REFRESH, 0, 0)
        finally:
            self._refreshing = False
            self._dirty = True

    def _check_full(self, res):
        """电量首?100% 时弹?Windows 通知；掉?97% 以下重新布防?"""
        for key, r in res.items():
            if not r.ok or r.level is None or r.stale:
                continue
            if r.level >= 100:
                if not self._full.get(key):
                    self._full[key] = True
                    if self.tray:
                        try:
                            self.tray.notify(f"{r.name} 已经充满电啦",
                                             level=100)
                        except Exception:
                            pass
            elif r.level < 97:
                self._full[key] = False

    def _check_low_battery(self, res):
        """低于 20% 时提醒一次；充电或恢复到 25% 后重新布防。"""
        warnings = []
        lowest = None
        for key, r in res.items():
            if not r.ok or r.level is None or r.stale or not r.present:
                continue
            if r.status in ("charging", "full", "wired"):
                self._low[key] = False
                continue
            level = max(0, min(100, int(r.level)))
            if level < 20:
                if self._low.get(key):
                    continue
                self._low[key] = True
                name = r.name or "设备"
                if r.status == "xinput_empty":
                    message = f"{name} 电量即将耗尽（XInput 档位约 5%）"
                elif r.status in ("xinput_low", "xinput_medium"):
                    message = f"{name} 电量偏低（XInput 档位约 {level}%）"
                else:
                    message = f"{name} 电量仅剩 {level}%，建议尽快充电"
                warnings.append(message)
                lowest = level if lowest is None else min(lowest, level)
            elif level >= 25:
                self._low[key] = False
        if warnings and self.tray:
            text = (warnings[0] if len(warnings) == 1
                    else "以下设备电量较低：\n" + "\n".join(warnings))
            try:
                self.tray.notify(text[:255], "低电量提醒", level=lowest)
            except Exception:
                pass

    # -- 交互 --------------------------------------------------------------
    def request_show(self):
        self._show_requested = True

    def set_interval(self, sec) -> int:
        sec = clamp_interval(sec)
        if sec == self.interval:
            return sec
        self.interval = sec
        cfg = load_config()
        cfg["interval"] = sec
        save_config(cfg)
        self._dirty = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_APP_INTERVAL, 0, 0)
        return sec

    def toggle_panel_theme(self):
        self.panel_light = not self.panel_light
        cfg = load_config()
        cfg["panel_theme"] = "light" if self.panel_light else "dark"
        save_config(cfg)
        if self.panel:
            self.panel.set_theme(self.panel_light)
        self._dirty = True

    def on_theme_changed(self):
        # 系统主题只影响托盘图标；面板主题由顶栏按钮独立控制并持久化。
        self.theme_light = is_light_theme()
        self._dirty = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_APP_REFRESH, 0, 0)

    def _poll_hotplug(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_poll < 15.0:
            return
        self._last_poll = now
        try:
            present = {(d.vid, d.pid) for d in enumerate_hid()}
        except Exception:
            return
        if self._seen is None:
            self._seen = present
            self._asked = set(present)
            return
        new = present - self._seen
        self._seen = present
        self._asked &= present
        for vid, pid in sorted(new):
            if (vid, pid) in self._asked:
                continue
            if any(d["vid"] == vid and d["pid"] == pid
                   for d in self.devices):
                continue
            self._asked.add((vid, pid))
            self._pending_add = (vid, pid)
            break

    def _show_add(self, vid: int, pid: int):
        try:
            ifaces = enumerate_hid()
            r = read_saved_device({"vid": vid, "pid": pid, "name": ""},
                                  ifaces)
            name = r.name or f"{vid:04X}:{pid:04X}"
            level = r.level if r.ok else None
            msg = f"设备：{name}\nVID:PID：{vid:04X}:{pid:04X}"
            if level is not None:
                msg += f"\n当前电量：{level}%"
            msg += "\n\n添加设备后，双击设备名可以改名\n\n是否添加？"
            from tkinter import messagebox
            if messagebox.askyesno("发现新设备", msg,
                                   parent=self.panel.root):
                self.add_device(vid, pid, name)
        except Exception:
            pass
        self._add_win = None

    def rename_device(self, key: str, name: str):
        for d in self.devices:
            if device_key(d["vid"], d["pid"]) == key:
                d["name"] = name
                save_saved_devices(self.devices)
                break

    def add_device(self, vid: int, pid: int, name: str):
        if any(d["vid"] == vid and d["pid"] == pid for d in self.devices):
            return
        self.devices.append({
            "vid": vid, "pid": pid, "name": name,
            "type": infer_device_type(vid, pid, name),
        })
        save_saved_devices(self.devices)
        self.refresh()

    def remove_device(self, key: str):
        removed = next((d for d in self.devices
                        if device_key(d["vid"], d["pid"]) == key), None)
        self.devices = [d for d in self.devices
                        if device_key(d["vid"], d["pid"]) != key]
        save_saved_devices(self.devices)
        if removed is not None:
            cfg = load_config()
            protocols = cfg.get("protocols")
            if isinstance(protocols, dict):
                protocols.pop(f"{removed['vid']:04X}:{removed['pid']:04X}",
                              None)
            last = cfg.get("last")
            if isinstance(last, dict):
                last.pop(key, None)
            save_config(cfg)
        with self._lock:
            self._readings.pop(key, None)
        self.refresh()

    def quit(self):
        self._stop = True
        self._refresh_event.set()
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_CLOSE, 0, 0)
        if self.panel:
            try:
                self.panel.hide()
            except Exception:
                pass
        try:
            self.root.quit()
        except Exception:
            pass

    # -- 主循?------------------------------------------------------------
    def run(self):
        migrate_startup_link()
        self.panel = Panel(self)
        self.root = self.panel.root
        if not self.panel_only:
            self.tray = Tray(self)
            self.tray.start()
        self.refresh()

        def tick():
            if self._hotplug_requested:
                self._hotplug_requested = False
                self._poll_hotplug(force=True)
            else:
                self._poll_hotplug()
            if self._pending_add and self._add_win is None:
                vid, pid = self._pending_add
                self._pending_add = None
                self._show_add(vid, pid)
            if self._show_requested:
                self._show_requested = False
                self.panel.show()
            if self._dirty:
                self._dirty = False
                if self.root.winfo_viewable():
                    self.panel.render()
            if self._stop:
                self.root.quit()
                return
            self.root.after(500, tick)

        self.root.after(500, tick)
        if self.panel_only:
            self.panel.show()
        self.root.mainloop()
        try:
            if self.tray:
                self.tray.destroy()
        except Exception:
            pass
        os._exit(0)


APP_ID = "2.4GDevice.BatteryPanel"
APP_DISPLAY_NAME = "2.4G"


def register_app_identity():
    """?Windows 通知显示?2.4G Device，不?Python?"""
    try:
        import winreg
        path = r"Software\Classes\AppUserModelId\%s" % APP_ID
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ,
                              APP_DISPLAY_NAME)
    except Exception:
        pass
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wt.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def run_gui(panel_only: bool = False) -> int:
    global UI_SCALE
    register_app_identity()
    user32.SetProcessDPIAware()
    try:
        user32.GetDpiForSystem.restype = wt.UINT
        UI_SCALE = max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        UI_SCALE = 1.0
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, "Local\\BatteryTrayDSH_Mutex")
    if ctypes.get_last_error() == 183 and mutex:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(Tray.CLASS_NAME, None)
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_SHOW, 0, 0)
        return 0
    app = TrayApp(panel_only=panel_only)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

