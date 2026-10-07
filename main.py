"""RamScope entry point.

    python main.py               open the window
    python main.py --self-check  write ramscope_check.txt next to the app and exit (used by tools/build.py)
"""

import sys
from pathlib import Path


def self_check() -> int:
    from ramscope.snapshot import Collector, fmt_bytes

    base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    lines = []
    ok = True
    try:
        snap = Collector().collect()
        s = snap.system
        ok = len(snap.procs) > 10 and 0 < s.used < s.total
        lines.append(f"processes  {len(snap.procs)} ({len(snap.groups)} apps)")
        lines.append(f"memory     {fmt_bytes(s.used)} of {fmt_bytes(s.total)} ({s.used_pct:.0f}%)")
        lines.append(f"page lists {'ok' if s.standby is not None else 'unavailable'}")
        top = ", ".join(f"{g.name} {fmt_bytes(g.private_ws)}" for g in snap.groups[:3])
        lines.append(f"top        {top}")
        lines.append(f"insights   {len(snap.insights)}")
    except Exception as e:  # report anything; the build must see it
        ok = False
        lines.append(f"error      {e!r}")
    lines.insert(0, f"self-check {'passed' if ok else 'FAILED'}")
    (base / "ramscope_check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()

    from PyQt6.QtWidgets import QApplication

    from ramscope.ui import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("RamScope")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
