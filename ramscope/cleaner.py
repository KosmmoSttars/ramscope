"""Frees RAM. Each step is honest about what it does:

- Trimming working sets moves pages that processes are not actively using out to the standby/modified
  lists. "In use" drops immediately; anything a program touches again is faulted back in.
- Flushing the modified list writes dirty pages to disk so they become reusable standby pages.
- Purging the standby list throws away the file cache. It turns "cached" into "free" — Windows already
  counts cache as available, so this mainly helps apps that check the free figure, at the cost of
  reloading files from disk afterwards.
"""

import os
import time
from dataclasses import dataclass, field

from . import winapi


@dataclass
class CleanOptions:
    trim_processes: bool = True
    empty_system_working_sets: bool = True   # admin: also trims services and the kernel's own sets
    flush_modified: bool = True              # admin
    purge_low_priority_standby: bool = True  # admin: drops cache nobody has touched in a while
    purge_standby: bool = False              # admin: drops the whole file cache


@dataclass
class CleanResult:
    used_before: int
    used_after: int
    free_before: int | None
    free_after: int | None
    trimmed: int = 0
    trim_failed: int = 0
    steps: list[str] = field(default_factory=list)
    skipped_admin: list[str] = field(default_factory=list)

    @property
    def freed(self) -> int:
        return self.used_before - self.used_after


def _measure(lists: winapi.PageLists) -> tuple[int, int | None]:
    perf = winapi.performance_info()
    pages = lists.read()
    return perf.physical_total - perf.physical_available, pages["free"] if pages else None


def clean(options: CleanOptions) -> CleanResult:
    lists = winapi.PageLists()
    used_before, free_before = _measure(lists)
    result = CleanResult(used_before, used_before, free_before, free_before)
    admin = winapi.is_admin()

    if options.trim_processes:
        me = os.getpid()
        for p in winapi.list_processes():
            if p.pid in (0, 4, me):
                continue
            if winapi.trim_process(p.pid):
                result.trimmed += 1
            else:
                result.trim_failed += 1
        result.steps.append(f"Trimmed {result.trimmed} processes")

    admin_steps = [
        (options.empty_system_working_sets, winapi.MEMORY_EMPTY_WORKING_SETS, "Emptied system working sets"),
        (options.flush_modified, winapi.MEMORY_FLUSH_MODIFIED_LIST, "Flushed modified pages"),
        (options.purge_standby, winapi.MEMORY_PURGE_STANDBY_LIST, "Purged standby cache"),
        (options.purge_low_priority_standby and not options.purge_standby,
         winapi.MEMORY_PURGE_LOW_PRIORITY_STANDBY_LIST, "Purged low-priority cache"),
    ]
    for enabled, command, label in admin_steps:
        if not enabled:
            continue
        if not admin:
            result.skipped_admin.append(label)
            continue
        if winapi.memory_list_command(command):
            result.steps.append(label)
        else:
            result.steps.append(f"{label} — failed")

    time.sleep(0.8)  # let the memory manager settle before measuring
    result.used_after, result.free_after = _measure(lists)
    return result
