"""AMD GPU sensors through ADL (`atiadlxx.dll`), with no helper app.

RDNA cards reject the old Overdrive5/6/N sensor calls with ADL_ERR_NOT_SUPPORTED,
which is why NVML-style code shows nothing on a Radeon.  ADL's PMLog interface --
the one GPU-Z and HWiNFO use -- still reports clocks, temperatures, fan speed and
board power on current drivers, and it works without administrator rights.

Everything here is best effort.  If the DLL is missing or the card does not
answer, `AmdGpu.available` stays False and the agent keeps the vendor-neutral PDH
counters it already collects (utilisation, dedicated/shared memory).
"""
import ctypes as C

ADL_MAX_PATH = 256
ADL_PMLOG_MAX_SENSORS = 256
ADL_OK = 0

# ADL_PMLOG_* indices (from the ADL headers; names match LibreHardwareMonitor).
S_GFXCLK = 1
S_MEMCLK = 2
S_TEMP_EDGE = 8
S_TEMP_MEM = 9
S_FAN_RPM = 14
S_FAN_PCT = 15
S_ACTIVITY_GFX = 19
S_ACTIVITY_MEM = 20
S_GFX_VOLTAGE = 21
S_ASIC_POWER = 23
S_TEMP_HOTSPOT = 27
S_GFX_POWER = 30
S_BOARD_POWER = 73


class _AdapterInfo(C.Structure):
    _fields_ = [
        ("iSize", C.c_int), ("iAdapterIndex", C.c_int), ("strUDID", C.c_char * ADL_MAX_PATH),
        ("iBusNumber", C.c_int), ("iDeviceNumber", C.c_int), ("iFunctionNumber", C.c_int),
        ("iVendorID", C.c_int),
        ("strAdapterName", C.c_char * ADL_MAX_PATH), ("strDisplayName", C.c_char * ADL_MAX_PATH),
        ("iPresent", C.c_int), ("iExist", C.c_int),
        ("strDriverPath", C.c_char * ADL_MAX_PATH), ("strDriverPathExt", C.c_char * ADL_MAX_PATH),
        ("strPNPString", C.c_char * ADL_MAX_PATH), ("iOSDisplayIndex", C.c_int),
    ]


class _MemoryInfoX4(C.Structure):
    _fields_ = [
        ("iMemorySize", C.c_longlong),                      # bytes
        ("strMemoryType", C.c_char * ADL_MAX_PATH),         # "GDDR6"
        ("iMemoryBandwidth", C.c_longlong),                 # MB/s
        ("iHyperMemorySize", C.c_longlong),
        ("iInvisibleMemorySize", C.c_longlong),
        ("iVisibleMemorySize", C.c_longlong),
        # ADL writes the whole X4 struct; omitting these three made the driver
        # write 24 bytes past the buffer and corrupt the Python heap.
        ("iVramVendorRevId", C.c_longlong),
        ("iMemoryBandwidthX2", C.c_longlong),
        ("iMemoryBitRateX2", C.c_longlong),
    ]


class _SingleSensor(C.Structure):
    _fields_ = [("supported", C.c_int), ("value", C.c_int)]


class _PMLogData(C.Structure):
    _fields_ = [("size", C.c_int), ("sensors", _SingleSensor * ADL_PMLOG_MAX_SENSORS)]


_MALLOC = C.CFUNCTYPE(C.c_void_p, C.c_int)

# ADL frees everything it allocates through the C runtime, so hand it CRT memory
# rather than Python buffers (mixing the two corrupts the heap at exit).
try:
    _CRT = C.CDLL("ucrtbase.dll")
except OSError:  # pragma: no cover - very old Windows
    _CRT = C.CDLL("msvcrt.dll")
_CRT.malloc.argtypes = [C.c_size_t]
_CRT.malloc.restype = C.c_void_p


class AmdGpu:
    """One AMD adapter's sensors.  Construct once; `available` says whether it worked."""

    def __init__(self):
        self.available = False
        self.name = "GPU"
        self.vram_total_gb = None
        self.vram_type = None
        self._dll = None
        self._ctx = None
        self._idx = 0
        self._alloc_cb = None
        try:
            self._open()
        except Exception:  # noqa: BLE001 - a missing/broken driver must not stop the agent
            self.close()

    # -- setup -----------------------------------------------------------------
    def _open(self):
        dll = C.WinDLL("atiadlxx.dll")
        self._dll = dll

        self._alloc_cb = _MALLOC(lambda size: _CRT.malloc(size))
        ctx = C.c_void_p()
        dll.ADL2_Main_Control_Create.argtypes = [_MALLOC, C.c_int, C.POINTER(C.c_void_p)]
        dll.ADL2_Main_Control_Create.restype = C.c_int
        if dll.ADL2_Main_Control_Create(self._alloc_cb, 1, C.byref(ctx)) != ADL_OK or not ctx:
            return
        self._ctx = ctx

        num = C.c_int()
        dll.ADL2_Adapter_NumberOfAdapters_Get.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
        dll.ADL2_Adapter_NumberOfAdapters_Get.restype = C.c_int
        if dll.ADL2_Adapter_NumberOfAdapters_Get(ctx, C.byref(num)) != ADL_OK or num.value <= 0:
            return
        infos = (_AdapterInfo * num.value)()
        dll.ADL2_Adapter_AdapterInfo_Get.argtypes = [C.c_void_p, C.POINTER(_AdapterInfo), C.c_int]
        dll.ADL2_Adapter_AdapterInfo_Get.restype = C.c_int
        if dll.ADL2_Adapter_AdapterInfo_Get(ctx, infos, C.sizeof(infos)) != ADL_OK:
            return

        # A card shows up once per display output.  Keep one index per physical
        # adapter and choose the one with the most VRAM (discrete over iGPU).
        seen, candidates = set(), []
        dll.ADL2_Adapter_MemoryInfoX4_Get.argtypes = [C.c_void_p, C.c_int, C.POINTER(_MemoryInfoX4)]
        dll.ADL2_Adapter_MemoryInfoX4_Get.restype = C.c_int
        for i in range(num.value):
            info = infos[i]
            if info.iAdapterIndex in seen:
                continue
            seen.add(info.iAdapterIndex)
            mem = _MemoryInfoX4()
            ok = dll.ADL2_Adapter_MemoryInfoX4_Get(ctx, info.iAdapterIndex, C.byref(mem)) == ADL_OK
            candidates.append((mem.iMemorySize if ok else 0, info, mem if ok else None))
        if not candidates:
            return

        _, info, mem = max(candidates, key=lambda c: c[0])
        self._idx = info.iAdapterIndex
        self.name = info.strAdapterName.decode(errors="replace").strip() or "GPU"
        if mem is not None and mem.iMemorySize > 0:
            self.vram_total_gb = round(mem.iMemorySize / 2**30, 1)
            self.vram_type = mem.strMemoryType.decode(errors="replace").strip() or None
        self.available = True

    # -- sampling --------------------------------------------------------------
    def sample(self) -> dict:
        """Clocks, temperatures, fan and power, or {} if the query failed."""
        if not self.available:
            return {}
        try:
            out = _PMLogData()
            out.size = C.sizeof(_PMLogData)
            fn = self._dll.ADL2_New_QueryPMLogData_Get
            fn.argtypes = [C.c_void_p, C.c_int, C.POINTER(_PMLogData)]
            fn.restype = C.c_int
            if fn(self._ctx, self._idx, C.byref(out)) != ADL_OK:
                return {}
        except Exception:  # noqa: BLE001
            return {}

        def get(idx):
            s = out.sensors[idx]
            return s.value if s.supported else None

        power = get(S_BOARD_POWER)
        if power is None:
            power = get(S_ASIC_POWER)
        if power is None:
            power = get(S_GFX_POWER)

        gfx = get(S_GFXCLK)
        mem = get(S_MEMCLK)
        return {
            "name": self.name,
            "vram_total_gb": self.vram_total_gb,
            "vram_type": self.vram_type,
            "temp_c": get(S_TEMP_EDGE),
            "temp_hotspot_c": get(S_TEMP_HOTSPOT),
            "temp_mem_c": get(S_TEMP_MEM),
            "power_w": round(power, 1) if power is not None else None,
            "clock_sm": gfx,
            "clock_mem": mem,
            "fan_pct": get(S_FAN_PCT),
            "fan_rpm": get(S_FAN_RPM),
            "util": get(S_ACTIVITY_GFX),
            "util_mem": get(S_ACTIVITY_MEM),
            "voltage_mv": get(S_GFX_VOLTAGE),
        }

    # -- teardown --------------------------------------------------------------
    def close(self):
        if self._dll is not None and self._ctx:
            try:
                self._dll.ADL2_Main_Control_Destroy.argtypes = [C.c_void_p]
                self._dll.ADL2_Main_Control_Destroy.restype = C.c_int
                self._dll.ADL2_Main_Control_Destroy(self._ctx)
            except Exception:  # noqa: BLE001
                pass
        # Release the DLL handle now, while the interpreter is still healthy.
        # Letting ctypes unload it during shutdown crashes inside ADL.
        self._ctx = None
        self._dll = None
        self._alloc_cb = None
        self.available = False


if __name__ == "__main__":  # tiny self-check: python amd.py
    gpu = AmdGpu()
    print("available:", gpu.available, "| adapter:", gpu.name, "| VRAM:", gpu.vram_total_gb, gpu.vram_type)
    for k, v in (gpu.sample() or {}).items():
        print(f"  {k:16} {v}")
    gpu.close()
