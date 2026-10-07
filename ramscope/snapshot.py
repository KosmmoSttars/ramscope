"""Collects one moment of memory state and explains it: who uses RAM, and why it is so full."""

import time
from collections import deque
from dataclasses import dataclass, field

from . import winapi

GB = 1 << 30
MB = 1 << 20

# Pseudo-processes and kernel pieces that have no executable to read a description from.
SYSTEM_DESCRIPTIONS = {
    "system idle process": "Idle time placeholder",
    "system": "Windows kernel and drivers",
    "registry": "Windows registry hives",
    "memory compression": "Compressed memory store",
    "secure system": "Virtualization-based security",
    "vmmem": "WSL 2 / Docker virtual machine",
    "vmmemwsl": "WSL 2 virtual machine",
    # Protected processes: their executables cannot be opened to read a description.
    "msmpeng.exe": "Microsoft Defender Antivirus",
    "nissrv.exe": "Microsoft Defender network inspection",
    "dwm.exe": "Desktop Window Manager",
    "searchindexer.exe": "Windows Search indexer",
    "csrss.exe": "Client Server Runtime Process",
    "lsass.exe": "Local Security Authority",
    "services.exe": "Services and Controller app",
    "wininit.exe": "Windows Start-Up Application",
    "winlogon.exe": "Windows Logon Application",
    "smss.exe": "Windows Session Manager",
    "fontdrvhost.exe": "Usermode Font Driver Host",
    "sgrmbroker.exe": "System Guard Runtime Monitor",
}

# Never offered for termination: killing these crashes or logs off Windows.
CRITICAL = {
    "system idle process", "system", "registry", "memory compression", "secure system", "smss.exe",
    "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "dwm.exe",
    "fontdrvhost.exe", "svchost.exe",
}

# Practical advice for the apps that most often eat RAM.
APP_HINTS = {
    "chrome.exe": "Every tab and extension is a separate process. Close unused tabs, remove extensions "
                  "you do not need, and turn on Memory Saver (Settings → Performance).",
    "msedge.exe": "Every tab and extension is a separate process. Turn on Sleeping tabs / Efficiency mode "
                  "(Settings → System and performance) and close unused tabs.",
    "firefox.exe": "Close unused tabs, check about:memory and about:processes for heavy pages, "
                   "and disable extensions you do not need.",
    "browser.exe": "Yandex Browser keeps every tab in its own process. Close unused tabs and enable "
                   "tab hibernation in settings.",
    "opera.exe": "Every tab is a separate process. Close unused tabs and enable tab snoozing.",
    "discord.exe": "Discord is an Electron app (a full Chromium inside). Disable hardware acceleration "
                   "and close it to the tray when not in a call — or use the web version.",
    "telegram.exe": "Large media caches grow over time: Settings → Advanced → Manage local storage.",
    "steamwebhelper.exe": "Steam's built-in browser. Steam → Settings → Interface: enable "
                          "low-performance mode, or start Steam with -no-browser.",
    "code.exe": "VS Code: heavy extensions and language servers add up. Check Help → Process Explorer.",
    "java.exe": "Java reserves its whole heap up front (-Xmx). If this is Minecraft, lower the RAM "
                "allocated in the launcher to what the modpack actually needs.",
    "javaw.exe": "Java reserves its whole heap up front (-Xmx). If this is Minecraft, lower the RAM "
                 "allocated in the launcher to what the modpack actually needs.",
    "msmpeng.exe": "Microsoft Defender's scanner. Usage spikes during scans and usually settles down.",
    "searchindexer.exe": "Windows Search indexing. It calms down once indexing finishes; you can limit "
                         "indexed folders in Indexing Options.",
    "vmmem": "The WSL 2 / Docker VM keeps memory it once used. Run `wsl --shutdown`, or cap it with "
             "memory= in %UserProfile%\\.wslconfig.",
    "vmmemwsl": "The WSL 2 VM keeps memory it once used. Run `wsl --shutdown`, or cap it with "
                "memory= in %UserProfile%\\.wslconfig.",
    "explorer.exe": "Explorer grows with open windows and shell extensions. Restarting it is safe.",
    "dwm.exe": "The desktop compositor. High usage usually means a GPU driver leak — update the "
               "graphics driver.",
    "photoshop.exe": "Photoshop uses as much RAM as it is allowed: Preferences → Performance → "
                     "Memory Usage.",
    "obs64.exe": "OBS: replay buffer and high-resolution sources take memory.",
}


@dataclass
class Proc:
    pid: int
    ppid: int
    name: str
    private_ws: int
    working_set: int
    commit: int
    peak_ws: int
    handles: int
    threads: int
    create_time: int
    path: str = ""
    description: str = ""

    @property
    def key(self) -> str:
        return self.name.lower()

    @property
    def critical(self) -> bool:
        return self.key in CRITICAL or self.pid in (0, 4)


@dataclass
class Group:
    key: str
    name: str
    description: str
    procs: list[Proc]

    @property
    def private_ws(self) -> int:
        return sum(p.private_ws for p in self.procs)

    @property
    def working_set(self) -> int:
        return sum(p.working_set for p in self.procs)

    @property
    def commit(self) -> int:
        return sum(p.commit for p in self.procs)

    @property
    def path(self) -> str:
        return next((p.path for p in self.procs if p.path), "")


@dataclass
class SystemMemory:
    total: int                 # RAM usable by Windows
    available: int             # free + standby: what programs can get right now
    installed: int | None      # RAM sticks, including hardware reserved
    commit_total: int
    commit_limit: int
    commit_peak: int
    kernel_paged: int
    kernel_nonpaged: int
    system_cache: int
    free: int | None           # page lists (from performance counters)
    standby: int | None
    modified: int | None
    compressed: int            # working set of the "Memory Compression" process
    apps: int                  # sum of private working sets of ordinary processes
    handles: int
    processes: int
    threads: int

    @property
    def used(self) -> int:
        return self.total - self.available

    @property
    def used_pct(self) -> float:
        return 100 * self.used / self.total if self.total else 0

    @property
    def commit_pct(self) -> float:
        return 100 * self.commit_total / self.commit_limit if self.commit_limit else 0

    @property
    def hardware_reserved(self) -> int:
        return max(0, self.installed - self.total) if self.installed else 0

    @property
    def kernel(self) -> int:
        """Kernel pools: memory of Windows itself and its drivers."""
        rest = max(0, self.used - (self.modified or 0) - self.apps - self.compressed)
        return min(rest, self.kernel_paged + self.kernel_nonpaged)

    @property
    def shared(self) -> int:
        """The rest of in-use RAM: shared DLLs and mapped files, page tables, driver-locked and GPU memory."""
        return max(0, self.used - (self.modified or 0) - self.apps - self.compressed - self.kernel)


@dataclass
class Insight:
    level: str   # "ok", "info", "warn", "bad"
    title: str
    text: str

    def as_tuple(self):
        return (self.level, self.title, self.text)


@dataclass
class Snapshot:
    time: float
    system: SystemMemory
    procs: list[Proc]
    groups: list[Group]
    insights: list[Insight] = field(default_factory=list)


def fmt_bytes(n: float, signed: bool = False) -> str:
    sign = ""
    if signed:
        sign = "+" if n > 0 else "−" if n < 0 else "±"
        n = abs(n)
    if n >= GB:
        return f"{sign}{n / GB:.2f} GB"
    if n >= 100 * MB:
        return f"{sign}{n / MB:.0f} MB"
    if n >= MB:
        return f"{sign}{n / MB:.1f} MB"
    return f"{sign}{n / 1024:.0f} KB"


class Collector:
    """Builds snapshots. Keeps caches (exe paths, descriptions) and a short per-app history for trends."""

    TREND_WINDOW = 60.0       # seconds compared by the "1 min" change column
    HISTORY_KEEP = 600.0      # seconds of per-app history, for leak detection

    def __init__(self):
        self.page_lists = winapi.PageLists()
        self.installed = winapi.installed_memory()
        self.paths: dict[tuple[int, int], str] = {}
        self.descriptions: dict[str, str] = {}
        self.history: dict[str, deque[tuple[float, int]]] = {}

    def _path(self, pid: int, create_time: int) -> str:
        key = (pid, create_time)
        if key not in self.paths:
            self.paths[key] = winapi.process_path(pid) if pid > 4 else ""
        return self.paths[key]

    def _description(self, name: str, path: str) -> str:
        if name.lower() in SYSTEM_DESCRIPTIONS:
            return SYSTEM_DESCRIPTIONS[name.lower()]
        if not path:
            return ""
        if path not in self.descriptions:
            try:
                self.descriptions[path] = winapi.file_description(path)
            except OSError:
                self.descriptions[path] = ""
        return self.descriptions[path]

    def collect(self) -> Snapshot:
        now = time.monotonic()
        raw = winapi.list_processes()
        perf = winapi.performance_info()
        lists = self.page_lists.read()

        alive = set()
        procs = []
        for r in raw:
            if r.pid == 0:
                continue
            path = self._path(r.pid, r.create_time)
            alive.add((r.pid, r.create_time))
            procs.append(Proc(
                pid=r.pid, ppid=r.ppid, name=r.name, private_ws=r.private_ws, working_set=r.working_set,
                commit=r.commit, peak_ws=r.peak_ws, handles=r.handles, threads=r.threads,
                create_time=r.create_time, path=path, description=self._description(r.name, path),
            ))
        for stale in set(self.paths) - alive:
            del self.paths[stale]

        groups: dict[str, Group] = {}
        for p in procs:
            g = groups.get(p.key)
            if g is None:
                groups[p.key] = g = Group(p.key, p.name, p.description, [])
            g.procs.append(p)
            if not g.description and p.description:
                g.description = p.description
        group_list = sorted(groups.values(), key=lambda g: g.private_ws, reverse=True)

        compressed = sum(p.working_set for p in procs if p.key == "memory compression")
        apps = sum(p.private_ws for p in procs if p.key != "memory compression" and p.pid != 4)

        system = SystemMemory(
            total=perf.physical_total, available=perf.physical_available, installed=self.installed,
            commit_total=perf.commit_total, commit_limit=perf.commit_limit, commit_peak=perf.commit_peak,
            kernel_paged=perf.kernel_paged, kernel_nonpaged=perf.kernel_nonpaged,
            system_cache=perf.system_cache,
            free=lists["free"] if lists else None,
            standby=lists["standby"] if lists else None,
            modified=lists["modified"] if lists else None,
            compressed=compressed, apps=apps,
            handles=perf.handles, processes=perf.processes, threads=perf.threads,
        )

        for g in group_list:
            h = self.history.setdefault(g.key, deque())
            h.append((now, g.private_ws))
            while h and now - h[0][0] > self.HISTORY_KEEP:
                h.popleft()
        for key in set(self.history) - set(groups):
            del self.history[key]

        snap = Snapshot(now, system, procs, group_list)
        snap.insights = self.explain(snap)
        return snap

    def change(self, key: str, window: float | None = None) -> int | None:
        """How much an app's memory changed over the window (default: one minute)."""
        h = self.history.get(key)
        if not h or len(h) < 2:
            return None
        window = window or self.TREND_WINDOW
        now, current = h[-1]
        if now - h[0][0] < min(window, 10):
            return None
        past = next((v for t, v in h if now - t <= window), h[0][1])
        return current - past

    def explain(self, snap: Snapshot) -> list[Insight]:
        s = snap.system
        out: list[Insight] = []
        total = s.total

        if s.used_pct >= 90:
            out.append(Insight("bad", f"Memory is almost full — {s.used_pct:.0f}%",
                               f"Only {fmt_bytes(s.available)} left. Windows is compressing pages and "
                               "using the page file, which makes everything slow."))
        elif s.used_pct >= 75:
            out.append(Insight("warn", f"Memory is under pressure — {s.used_pct:.0f}%",
                               f"{fmt_bytes(s.available)} available. Close a heavy app from the list below "
                               "if things feel slow."))

        # Who is the biggest consumer?
        for g in snap.groups[:3]:
            if g.key == "memory compression":
                continue
            share = g.private_ws / total
            if share < 0.10:
                break
            n = len(g.procs)
            label = g.description or g.name
            procs_note = f" across {n} processes" if n > 1 else ""
            hint = APP_HINTS.get(g.key, "")
            level = "bad" if share >= 0.35 else "warn" if share >= 0.20 else "info"
            out.append(Insight(level, f"{label}: {fmt_bytes(g.private_ws)} ({share * 100:.0f}% of RAM)",
                               f"{g.name}{procs_note}. {hint}".strip()))

        # Apps that keep growing: a likely leak.
        for g in snap.groups[:25]:
            grown = self.change(g.key, 300)
            h = self.history.get(g.key)
            if grown is None or not h or snap.time - h[0][0] < 120:
                continue
            if grown >= max(400 * MB, total * 0.03):
                out.append(Insight("warn", f"{g.description or g.name} keeps growing",
                                   f"{fmt_bytes(grown, signed=True)} in the last 5 minutes. A steady climb "
                                   "without you doing more in the app is a memory leak — restart it."))

        many = [g for g in snap.groups if len(g.procs) >= 25 and g.key not in ("svchost.exe",)]
        for g in many[:2]:
            out.append(Insight("info", f"{len(g.procs)} × {g.name}",
                               f"Together {fmt_bytes(g.private_ws)}. Each process carries its own overhead."))

        if s.kernel_nonpaged >= max(768 * MB, total * 0.05):
            out.append(Insight("warn", f"Kernel non-paged pool: {fmt_bytes(s.kernel_nonpaged)}",
                               "Normal is 200–500 MB. This memory belongs to drivers and never leaves RAM; "
                               "a large or growing value means a driver leak. Usual suspects: network "
                               "(Killer, Realtek), VPN, antivirus, RGB/monitoring tools. Update or remove "
                               "them; poolmon.exe from the WDK shows the exact tag."))
        if s.kernel_paged >= max(2 * GB, total * 0.12):
            out.append(Insight("warn", f"Kernel paged pool: {fmt_bytes(s.kernel_paged)}",
                               "Unusually large. Often caused by a leaking driver or a huge registry / "
                               "file-system metadata cache."))

        if s.commit_pct >= 90:
            out.append(Insight("bad", f"Commit charge at {s.commit_pct:.0f}% of the limit",
                               f"{fmt_bytes(s.commit_total)} of {fmt_bytes(s.commit_limit)}. When it hits "
                               "100%, programs crash with \"out of memory\" even if RAM looks free. "
                               "Enlarge the page file or let Windows manage it."))
        elif s.commit_total > total * 1.3:
            out.append(Insight("info", f"Committed {fmt_bytes(s.commit_total)} — more than your RAM",
                               "Programs reserved more memory than is physically installed; the excess is "
                               "backed by the page file. Fine while the limit is not reached."))

        if s.compressed >= max(GB, total * 0.06):
            out.append(Insight("info", f"Compressed memory: {fmt_bytes(s.compressed)}",
                               "Windows squeezed rarely used pages instead of writing them to disk. "
                               "A big value is a sign memory was short recently."))

        if s.shared >= total * 0.15:
            out.append(Insight("info", f"{fmt_bytes(s.shared)} shared and unattributed",
                               "DLLs and files mapped by many apps at once, page tables, driver-locked and GPU "
                               "memory. It is not counted in any app's Memory column. If it keeps growing, "
                               "suspect a driver or the graphics driver."))

        if s.hardware_reserved >= 400 * MB:
            out.append(Insight("info", f"{fmt_bytes(s.hardware_reserved)} reserved by hardware",
                               "Taken by firmware and an integrated GPU before Windows starts. It never "
                               "shows up as usable RAM."))

        if s.standby is not None and s.standby >= total * 0.15:
            out.append(Insight("ok", f"{fmt_bytes(s.standby)} is file cache (standby)",
                               "This is not a problem: cached data is handed back instantly when an app "
                               "needs memory, and it already counts as available."))

        if not any(i.level in ("warn", "bad") for i in out):
            out.insert(0, Insight("ok", "Memory is healthy",
                                  f"{s.used_pct:.0f}% in use, {fmt_bytes(s.available)} available."))
        return out

