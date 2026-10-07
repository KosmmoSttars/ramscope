"""Thin ctypes layer over the Windows APIs RamScope needs.

Everything here is read through the same sources Task Manager and RAMMap use:
- NtQuerySystemInformation(SystemProcessInformation) gives every process, including protected ones,
  with its private working set (Task Manager's "Memory" column) without opening a single handle.
- GetPerformanceInfo gives commit charge, kernel pools and the system cache.
- PDH counters give the free / standby / modified page lists without admin rights.
- NtSetSystemInformation(SystemMemoryListInformation) empties working sets and purges page lists (admin).
"""

import ctypes
import struct
import sys
from ctypes import wintypes as wt
from dataclasses import dataclass

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)
pdh = ctypes.WinDLL("pdh")

STATUS_INFO_LENGTH_MISMATCH = -1073741820  # 0xC0000004 as a signed NTSTATUS
SYSTEM_PROCESS_INFORMATION_CLASS = 5
SYSTEM_MEMORY_LIST_INFORMATION_CLASS = 80

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x0002
ERROR_NOT_ALL_ASSIGNED = 1300

# SYSTEM_MEMORY_LIST_COMMAND
MEMORY_EMPTY_WORKING_SETS = 2
MEMORY_FLUSH_MODIFIED_LIST = 3
MEMORY_PURGE_STANDBY_LIST = 4
MEMORY_PURGE_LOW_PRIORITY_STANDBY_LIST = 5


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", ctypes.c_void_p)]


class SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    # Only the leading part is declared; the I/O counters and thread array that follow are not needed.
    _fields_ = [
        ("NextEntryOffset", wt.ULONG),
        ("NumberOfThreads", wt.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wt.ULONG),
        ("NumberOfThreadsHighWatermark", wt.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wt.ULONG),
        ("SessionId", wt.ULONG),
        ("UniqueProcessKey", ctypes.c_size_t),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wt.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
    ]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wt.DWORD),
        ("ProcessCount", wt.DWORD),
        ("ThreadCount", wt.DWORD),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


class _PdhValue(ctypes.Union):
    _fields_ = [("longValue", wt.LONG), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong)]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", wt.DWORD), ("value", _PdhValue)]


ntdll.NtQuerySystemInformation.argtypes = [wt.ULONG, ctypes.c_void_p, wt.ULONG, ctypes.POINTER(wt.ULONG)]
ntdll.NtQuerySystemInformation.restype = ctypes.c_long
ntdll.NtSetSystemInformation.argtypes = [wt.ULONG, ctypes.c_void_p, wt.ULONG]
ntdll.NtSetSystemInformation.restype = ctypes.c_long

kernel32.K32GetPerformanceInfo.argtypes = [ctypes.POINTER(PERFORMANCE_INFORMATION), wt.DWORD]
kernel32.K32GetPerformanceInfo.restype = wt.BOOL
kernel32.GetPhysicallyInstalledSystemMemory.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
kernel32.GetPhysicallyInstalledSystemMemory.restype = wt.BOOL
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.K32EmptyWorkingSet.argtypes = [wt.HANDLE]
kernel32.K32EmptyWorkingSet.restype = wt.BOOL
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateProcess.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL

advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(LUID)]
advapi32.AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
                                           wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]

shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_void_p

version.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
version.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p]
version.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wt.UINT)]

pdh.PdhOpenQueryW.argtypes = [wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wt.HANDLE)]
pdh.PdhOpenQueryW.restype = wt.LONG
pdh.PdhAddEnglishCounterW.argtypes = [wt.HANDLE, wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wt.HANDLE)]
pdh.PdhAddEnglishCounterW.restype = wt.LONG
pdh.PdhCollectQueryData.argtypes = [wt.HANDLE]
pdh.PdhCollectQueryData.restype = wt.LONG
pdh.PdhGetFormattedCounterValue.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                            ctypes.POINTER(PDH_FMT_COUNTERVALUE)]
pdh.PdhGetFormattedCounterValue.restype = wt.LONG
PDH_FMT_LARGE = 0x00000400


@dataclass
class RawProcess:
    pid: int
    ppid: int
    name: str
    private_ws: int      # private working set: what Task Manager shows as "Memory"
    working_set: int     # includes shared pages (DLLs), so it overlaps between processes
    commit: int          # private bytes, backed by RAM or the page file
    peak_ws: int
    handles: int
    threads: int
    session: int
    create_time: int


def list_processes() -> list[RawProcess]:
    size = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(size)
        needed = wt.ULONG()
        status = ntdll.NtQuerySystemInformation(SYSTEM_PROCESS_INFORMATION_CLASS, buf, size,
                                                ctypes.byref(needed))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, needed.value + 65536)
            continue
        if status < 0:
            raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08X}")
        break

    result = []
    offset = 0
    while True:
        info = SYSTEM_PROCESS_INFORMATION.from_buffer(buf, offset)
        pid = info.UniqueProcessId or 0
        if info.ImageName.Buffer:
            name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2)
        else:
            name = "System Idle Process" if pid == 0 else "?"
        result.append(RawProcess(
            pid=pid,
            ppid=info.InheritedFromUniqueProcessId or 0,
            name=name,
            private_ws=max(0, info.WorkingSetPrivateSize),
            working_set=info.WorkingSetSize,
            commit=info.PagefileUsage,
            peak_ws=info.PeakWorkingSetSize,
            handles=info.HandleCount,
            threads=info.NumberOfThreads,
            session=info.SessionId,
            create_time=info.CreateTime,
        ))
        if not info.NextEntryOffset:
            break
        offset += info.NextEntryOffset
    return result


@dataclass
class PerfInfo:
    commit_total: int
    commit_limit: int
    commit_peak: int
    physical_total: int
    physical_available: int
    system_cache: int
    kernel_paged: int
    kernel_nonpaged: int
    handles: int
    processes: int
    threads: int


def performance_info() -> PerfInfo:
    pi = PERFORMANCE_INFORMATION()
    pi.cb = ctypes.sizeof(pi)
    if not kernel32.K32GetPerformanceInfo(ctypes.byref(pi), pi.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    page = pi.PageSize
    return PerfInfo(
        commit_total=pi.CommitTotal * page,
        commit_limit=pi.CommitLimit * page,
        commit_peak=pi.CommitPeak * page,
        physical_total=pi.PhysicalTotal * page,
        physical_available=pi.PhysicalAvailable * page,
        system_cache=pi.SystemCache * page,
        kernel_paged=pi.KernelPaged * page,
        kernel_nonpaged=pi.KernelNonpaged * page,
        handles=pi.HandleCount,
        processes=pi.ProcessCount,
        threads=pi.ThreadCount,
    )


def installed_memory() -> int | None:
    """Physically installed RAM in bytes, including what firmware and the integrated GPU reserve."""
    kb = ctypes.c_ulonglong()
    if kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)):
        return kb.value * 1024
    return None


class PageLists:
    """Free, standby and modified page list sizes from performance counters (no admin needed)."""

    COUNTERS = {
        "free": [r"\Memory\Free & Zero Page List Bytes"],
        "modified": [r"\Memory\Modified Page List Bytes"],
        "standby": [r"\Memory\Standby Cache Reserve Bytes",
                    r"\Memory\Standby Cache Normal Priority Bytes",
                    r"\Memory\Standby Cache Core Bytes"],
    }

    def __init__(self):
        self.query = wt.HANDLE()
        self.handles: dict[str, list[wt.HANDLE]] = {}
        self.ok = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) == 0
        if not self.ok:
            return
        for key, paths in self.COUNTERS.items():
            for path in paths:
                counter = wt.HANDLE()
                if pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter)) == 0:
                    self.handles.setdefault(key, []).append(counter)
        self.ok = all(key in self.handles for key in self.COUNTERS)

    def read(self) -> dict[str, int] | None:
        if not self.ok or pdh.PdhCollectQueryData(self.query) != 0:
            return None
        out = {}
        value = PDH_FMT_COUNTERVALUE()
        for key, counters in self.handles.items():
            total = 0
            for counter in counters:
                if pdh.PdhGetFormattedCounterValue(counter, PDH_FMT_LARGE, None, ctypes.byref(value)) != 0:
                    return None
                total += value.value.largeValue
            out[key] = total
        return out


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    """Starts a new elevated copy of the app. Returns False if the user declined the UAC prompt."""
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, sys.argv[1:]
    else:
        exe, args = sys.executable, sys.argv
    params = " ".join(f'"{a}"' for a in args)
    return (shell32.ShellExecuteW(None, "runas", exe, params, None, 1) or 0) > 32


def enable_privilege(name: str) -> bool:
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                     ctypes.byref(token)):
        return False
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES * 1)(LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED)))
        ctypes.set_last_error(0)
        if not advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            return False
        return ctypes.get_last_error() != ERROR_NOT_ALL_ASSIGNED
    finally:
        kernel32.CloseHandle(token)


def memory_list_command(command: int) -> bool:
    """Runs a system-wide memory list command. Needs admin rights and SeProfileSingleProcessPrivilege."""
    enable_privilege("SeProfileSingleProcessPrivilege")
    enable_privilege("SeIncreaseQuotaPrivilege")
    cmd = ctypes.c_int(command)
    return ntdll.NtSetSystemInformation(SYSTEM_MEMORY_LIST_INFORMATION_CLASS, ctypes.byref(cmd),
                                        ctypes.sizeof(cmd)) >= 0


def trim_process(pid: int) -> bool:
    """Moves a process's pages out of its working set. They stay in RAM as standby/modified pages."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.K32EmptyWorkingSet(handle))
    finally:
        kernel32.CloseHandle(handle)


def terminate_process(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def file_description(path: str) -> str:
    """The "File description" from an executable's version resource, e.g. "Google Chrome"."""
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return ""
    ptr = ctypes.c_void_p()
    length = wt.UINT()
    tables = []
    if version.VerQueryValueW(data, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(length)):
        raw = ctypes.string_at(ptr.value, length.value)
        for i in range(0, len(raw) - 3, 4):
            lang, codepage = struct.unpack_from("<HH", raw, i)
            tables.append(f"{lang:04x}{codepage:04x}")
    tables += ["040904b0", "040904e4", "000004b0"]
    for table in tables:
        if version.VerQueryValueW(data, rf"\StringFileInfo\{table}\FileDescription",
                                  ctypes.byref(ptr), ctypes.byref(length)) and length.value > 1:
            text = ctypes.wstring_at(ptr.value).strip()
            if text:
                return text
    return ""
