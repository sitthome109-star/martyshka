"""
ЗАРЯД КЛАВИАТУРЫ AULA F75 Max (через приёмник 2.4G).

Тот же способ, что в программе брата «F75 Studio» (C:\\Users\\offeballls\\F75 Studio\\src\\Program.cs, QueryBattery):
служебный канал приёмника (HID usage page 0xFF60), пакеты по 32 байта, последний байт — контрольная сумма.
Отправляем «привет» (0x02), затем вопрос «заряд?» (0x20 0x01); в ответе байт [3] — проценты.
Только эти два вопроса — никаких настроек клавиатуре не шлём.
"""
import ctypes
import ctypes.wintypes as wt
import threading

VID, PID, PAGE = 0x05AC, 0x024F, 0xFF60     # приёмник 2.4G (как в F75 Studio: dvid/dpid)
_lock = threading.Lock()

_setupapi = ctypes.windll.setupapi
_hid = ctypes.windll.hid
_k32 = ctypes.windll.kernel32


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _IFACE(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", _GUID), ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class _CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT), ("InputReportByteLength", wt.USHORT),
                ("OutputReportByteLength", wt.USHORT), ("FeatureReportByteLength", wt.USHORT),
                ("Reserved", wt.USHORT * 17), ("rest", wt.USHORT * 10)]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wt.DWORD),
                ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]


_k32.CreateFileW.restype = wt.HANDLE
_k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
_k32.CreateEventW.restype = wt.HANDLE
_k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
_k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
_k32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(wt.DWORD), wt.BOOL]
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.CancelIo.argtypes = [wt.HANDLE]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_INVALID = wt.HANDLE(-1).value


def _find():
    """Путь к служебному каналу приёмника и размеры отчётов (или None, если приёмника нет)."""
    g = _GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(g))
    _setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    dev = _setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)
    want = f"vid_{VID:04x}&pid_{PID:04x}"
    try:
        i = 0
        while True:
            d = _IFACE(); d.cbSize = ctypes.sizeof(d)
            if not _setupapi.SetupDiEnumDeviceInterfaces(ctypes.c_void_p(dev), None, ctypes.byref(g), i, ctypes.byref(d)):
                return None
            i += 1
            need = wt.DWORD()
            _setupapi.SetupDiGetDeviceInterfaceDetailW(ctypes.c_void_p(dev), ctypes.byref(d), None, 0,
                                                       ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(ctypes.c_void_p(dev), ctypes.byref(d), buf, need,
                                                              None, None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if want not in path.lower():
                continue
            h = _k32.CreateFileW(path, 0, 3, None, 3, 0, None)     # без доступа — только узнать описание
            if h in (None, 0, _INVALID):
                continue
            try:
                pp = ctypes.c_void_p(); caps = _CAPS()
                if _hid.HidD_GetPreparsedData(wt.HANDLE(h), ctypes.byref(pp)):
                    _hid.HidP_GetCaps(pp, ctypes.byref(caps))
                    _hid.HidD_FreePreparsedData(pp)
                    if caps.UsagePage == PAGE:
                        return path, max(33, caps.InputReportByteLength), max(33, caps.OutputReportByteLength)
            finally:
                _k32.CloseHandle(h)
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(dev))


def _exchange(h, body: bytes, n_in: int, n_out: int, timeout_ms: int) -> bytes | None:
    pkt = bytearray(32)
    pkt[:len(body)] = body
    pkt[31] = sum(pkt[:31]) & 0xFF                        # контрольная сумма
    out = bytes([0]) + bytes(pkt) + bytes(max(0, n_out - 33))
    ev = _k32.CreateEventW(None, True, False, None)
    try:
        ov = _OVERLAPPED(); ov.hEvent = ev; done = wt.DWORD()
        if not _k32.WriteFile(h, out, len(out), None, ctypes.byref(ov)):
            if _k32.WaitForSingleObject(ev, 1000) != 0:
                _k32.CancelIo(h)
                return None
        import time
        until = time.time() + timeout_ms / 1000
        while time.time() < until:
            _k32.ResetEvent(ev)
            ov = _OVERLAPPED(); ov.hEvent = ev
            inb = ctypes.create_string_buffer(n_in)
            _k32.ReadFile(h, inb, n_in, None, ctypes.byref(ov))
            left = max(1, int((until - time.time()) * 1000))
            if _k32.WaitForSingleObject(ev, left) != 0:
                _k32.CancelIo(h)
                return None
            _k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(done), False)
            r = inb.raw
            if r[1] == pkt[0] and r[2] == pkt[1]:          # ответ именно на наш вопрос
                return r[1:33]
        return None
    finally:
        _k32.CloseHandle(ev)


def battery() -> int | None:
    """Заряд клавиатуры в процентах или None (приёмника нет, клавиатура спит или подключена кабелем)."""
    with _lock:
        try:
            found = _find()
            if not found:
                return None
            path, n_in, n_out = found
            h = _k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0x40000000, None)   # чтение+запись, асинхронно
            if h in (None, 0, _INVALID):
                return None
            try:
                _exchange(h, b"\x02", n_in, n_out, 600)            # «привет» (как в F75 Studio)
                r = _exchange(h, b"\x20\x01", n_in, n_out, 600)    # «заряд?»
                if r is None:
                    return None
                v = r[3]
                return v if v <= 100 else None
            finally:
                _k32.CloseHandle(h)
        except Exception:
            return None


if __name__ == "__main__":
    print("заряд клавиатуры:", battery())
