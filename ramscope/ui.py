"""RamScope main window: a plain native Windows window — usage bar, Free memory button, app list."""

import subprocess

from PyQt6.QtCore import QFileInfo, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileIconProvider, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import __version__, winapi
from .cleaner import CleanOptions, CleanResult, clean
from .snapshot import Collector, Proc, Snapshot, fmt_bytes
from .widgets import app_icon

REFRESH_MS = 2000
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 2
COL_NAME, COL_MEM, COL_COUNT = range(3)
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class SortItem(QTreeWidgetItem):
    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        return self.data(col, SORT_ROLE) < other.data(col, SORT_ROLE)


class CleanThread(QThread):
    done = pyqtSignal(object)

    def run(self):
        self.done.emit(clean(CleanOptions()))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.collector = Collector()
        self.snapshot: Snapshot | None = None
        self.admin = winapi.is_admin()
        self.icons = QFileIconProvider()
        self.icon_cache: dict[str, QIcon] = {}
        self.items: dict[tuple, SortItem] = {}
        self.clean_thread: CleanThread | None = None

        self.setWindowTitle("RamScope" + (" (Administrator)" if self.admin else ""))
        self.setWindowIcon(app_icon())
        self.resize(560, 620)

        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(11, 11, 11, 6)
        lay.setSpacing(7)

        self.usage = QLabel()
        lay.addWidget(self.usage)
        self.bar = QProgressBar(textVisible=False, maximum=1000)
        self.bar.setFixedHeight(18)
        lay.addWidget(self.bar)
        self.details = QLabel()
        self.details.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.details)

        self.note = QLabel(wordWrap=True)
        self.note.setVisible(False)
        lay.addWidget(self.note)

        buttons = QHBoxLayout()
        self.clean_btn = QPushButton("Free memory")
        self.clean_btn.setDefault(True)
        self.clean_btn.clicked.connect(self.start_clean)
        buttons.addWidget(self.clean_btn)
        if not self.admin:
            admin_btn = QPushButton("Run as administrator")
            admin_btn.setToolTip("Lets Free memory also trim system processes and flush the cache.")
            admin_btn.clicked.connect(self.restart_as_admin)
            buttons.addWidget(admin_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)

        t = self.tree = QTreeWidget()
        t.setHeaderLabels(["Name", "Memory", "Processes"])
        t.headerItem().setTextAlignment(COL_MEM, RIGHT)
        t.headerItem().setTextAlignment(COL_COUNT, RIGHT)
        t.headerItem().setToolTip(COL_MEM, "Private memory, the same figure as Task Manager shows")
        t.setUniformRowHeights(True)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(self._context_menu)
        t.setSortingEnabled(True)
        t.sortByColumn(COL_MEM, Qt.SortOrder.DescendingOrder)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        t.setColumnWidth(COL_MEM, 90)
        t.setColumnWidth(COL_COUNT, 75)
        lay.addWidget(t, 1)

        self.timer = QTimer(self, interval=REFRESH_MS, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    # ---------- refresh ----------

    def refresh(self) -> None:
        snap = self.snapshot = self.collector.collect()
        s = snap.system
        self.usage.setText(f"Memory in use: {fmt_bytes(s.used)} of {fmt_bytes(s.total)} ({s.used_pct:.0f}%)")
        self.bar.setValue(round(s.used_pct * 10))
        cache = f"   Cache: {fmt_bytes(s.standby)}" if s.standby is not None else ""
        self.details.setText(f"Available: {fmt_bytes(s.available)}{cache}   "
                             f"Committed: {fmt_bytes(s.commit_total)} / {fmt_bytes(s.commit_limit)}")

        problems = [i for i in snap.insights if i.level in ("warn", "bad")]
        self.note.setVisible(bool(problems))
        if problems:
            self.note.setText("\n".join(f"⚠ {i.title}" for i in problems[:3]))
            self.note.setToolTip("\n\n".join(f"{i.title}\n{i.text}" for i in problems))
        self._update_tree()

    def _icon(self, path: str) -> QIcon:
        if path not in self.icon_cache:
            self.icon_cache[path] = (self.icons.icon(QFileInfo(path)) if path
                                     else self.icons.icon(QFileIconProvider.IconType.File))
        return self.icon_cache[path]

    def _fill(self, item: SortItem, key: tuple, name: str, tip: str, mem: int, count: int, path: str) -> None:
        item.setData(COL_NAME, KEY_ROLE, key)
        for col, text, sort in ((COL_NAME, name, name.lower()), (COL_MEM, fmt_bytes(mem), mem),
                                (COL_COUNT, str(count), count)):
            if item.text(col) != text:
                item.setText(col, text)
            item.setData(col, SORT_ROLE, sort)
        item.setTextAlignment(COL_MEM, RIGHT)
        item.setTextAlignment(COL_COUNT, RIGHT)
        if item.toolTip(COL_NAME) != tip:
            item.setToolTip(COL_NAME, tip)
            item.setIcon(COL_NAME, self._icon(path))

    def _update_tree(self) -> None:
        t = self.tree
        t.setSortingEnabled(False)
        seen = set()
        for g in self.snapshot.groups:
            gkey = ("g", g.key)
            seen.add(gkey)
            item = self.items.get(gkey) or self.items.setdefault(gkey, SortItem(t))
            label = g.description or g.name
            self._fill(item, gkey, label, g.path or g.name, g.private_ws, len(g.procs), g.path)
            if len(g.procs) < 2:
                continue
            for p in g.procs:
                pkey = ("p", p.pid, p.create_time)
                seen.add(pkey)
                child = self.items.get(pkey) or self.items.setdefault(pkey, SortItem(item))
                self._fill(child, pkey, f"{p.name} (PID {p.pid})", p.path or p.name, p.private_ws, 1, p.path)
        for key in [k for k in self.items if k not in seen]:
            item = self.items.pop(key)
            if item.parent() is not None:
                item.parent().removeChild(item)
            else:
                t.takeTopLevelItem(t.indexOfTopLevelItem(item))
        t.setSortingEnabled(True)

    # ---------- process actions ----------

    def _selected(self) -> list[Proc]:
        by_pid = {(p.pid, p.create_time): p for p in self.snapshot.procs}
        groups = {g.key: g for g in self.snapshot.groups}
        out: dict[int, Proc] = {}
        for item in self.tree.selectedItems():
            key = item.data(COL_NAME, KEY_ROLE)
            if key[0] == "g" and key[1] in groups:
                out.update((p.pid, p) for p in groups[key[1]].procs)
            elif key[0] == "p" and (key[1], key[2]) in by_pid:
                out[key[1]] = by_pid[(key[1], key[2])]
        return list(out.values())

    def _context_menu(self, pos) -> None:
        procs = self._selected()
        if not procs:
            return
        menu = QMenu(self)
        menu.addAction("Free memory", lambda: self.trim(procs))
        end = menu.addAction("End task", lambda: self.end(procs))
        end.setEnabled(not any(p.critical for p in procs))
        menu.addSeparator()
        path = next((p.path for p in procs if p.path), "")
        menu.addAction("Open file location",
                       lambda: subprocess.Popen(["explorer", "/select,", path])).setEnabled(bool(path))
        menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText(path)).setEnabled(bool(path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def trim(self, procs: list[Proc]) -> None:
        pids = {p.pid for p in procs}
        before = sum(p.private_ws for p in procs)
        ok = sum(winapi.trim_process(pid) for pid in pids)

        def report():
            self.refresh()
            after = sum(p.private_ws for p in self.snapshot.procs if p.pid in pids)
            self.statusBar().showMessage(f"Freed {fmt_bytes(max(0, before - after))}" if ok
                                         else "Access denied. Run as administrator.", 10000)
        QTimer.singleShot(700, report)

    def end(self, procs: list[Proc]) -> None:
        names = ", ".join(sorted({p.name for p in procs}))
        if QMessageBox.question(self, "RamScope", f"End {names}?\n\nUnsaved data will be lost.") \
                != QMessageBox.StandardButton.Yes:
            return
        failed = sum(not winapi.terminate_process(p.pid) for p in procs)
        self.statusBar().showMessage("Access denied." if failed else f"Ended {names}", 10000)
        QTimer.singleShot(500, self.refresh)

    # ---------- cleaning ----------

    def start_clean(self) -> None:
        if self.clean_thread and self.clean_thread.isRunning():
            return
        self.clean_btn.setEnabled(False)
        self.statusBar().showMessage("Freeing memory…")
        self.clean_thread = CleanThread(self)
        self.clean_thread.done.connect(self._clean_done)
        self.clean_thread.start()

    def _clean_done(self, r: CleanResult) -> None:
        self.clean_btn.setEnabled(True)
        msg = f"Freed {fmt_bytes(r.freed)}" if r.freed > 1 << 20 else "Nothing to free"
        if r.skipped_admin:
            msg += " (run as administrator to free more)"
        self.statusBar().showMessage(msg, 15000)
        self.refresh()

    def restart_as_admin(self) -> None:
        if winapi.relaunch_as_admin():
            self.close()
