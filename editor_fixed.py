"""
editor.py — the workflow editor screen: palette, canvas, JSON preview,
run/save controls. Settings-panel rendering lives in editor_settings.py
(mixed in here) since that part changes most often.
"""
import json
import os

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTextEdit, QScrollArea, QApplication,
    QSplitter, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QObject, QSize
from PyQt6.QtGui import QKeySequence, QShortcut, QColor, QImage

from theme import ACCENT, API
from api_client import api_get, api_post, api_post_stream
from storage import list_projects, load_project, save_project, load_ui_state, save_ui_state
from canvas import Canvas, node_pixmap
from editor_widgets import (GripSplitter, DragJsonTree,
                            DropLineEdit, DropTextEdit)
from panel_base import PanelBox, EdgeArrow, DotSplitter
from editor_workers import SimWorker, RunWorker, EventListener
from editor_settings import SettingsPanelMixin
from node_popup import NodePopupMixin

from PyQt6.QtGui import QPainter, QColor as _QColor










class _NullWidget:
    """Stand-in for a module widget that is not on screen.

    Panels start empty, so the editor may try to write to a run log or a code
    view that the user has not added yet. Rather than guarding every call site
    with a hasattr check, those attributes point here: every method quietly
    does nothing, and any attribute lookup returns another no-op.
    """
    def __getattr__(self, _name):
        return self._noop
    def _noop(self, *a, **k):
        return None


class _NullList(_NullWidget):
    """Same idea for list widgets, where a few calls expect a number back."""
    def count(self):
        return 0
    def item(self, _i):
        return None


class Editor(QWidget, SettingsPanelMixin, NodePopupMixin):
    def __init__(self, app):
        super().__init__()
        self.app = app; self.current_project = None; self.meta_by_type = {}
        self._last_results = {}        # node name -> ports (kept after a run)
        self._last_inputs = {}         # node name -> items that entered it
        # The window is one vertical stack: a full-width TOP BAR across the
        # top, and everything else below it. Panels therefore start under the
        # bar instead of beside it, so a panel can run the full height of the
        # work area without the logo pushing it down.
        root = QVBoxLayout(self); root.setContentsMargins(8, 8, 8, 8); root.setSpacing(6)

        # ===================== TOP BAR (spans the whole width)
        topbar = QHBoxLayout()
        topbar.setContentsMargins(0, 0, 0, 0)
        topbar.setSpacing(8)

        home = QLabel("DuGS")
        home.setStyleSheet(f"color:{ACCENT}; font-family:monospace; font-size:20px;")
        home.setCursor(Qt.CursorShape.PointingHandCursor)
        home.mousePressEvent = lambda _e: self.app.go_home()
        self.logo = home
        topbar.addWidget(home)

        self.proj_label = QLabel("-")
        self.proj_label.setStyleSheet(
            f"color:{ACCENT}; font-family:monospace; font-size:14px;")
        topbar.addWidget(self.proj_label)
        topbar.addStretch()

        self.run_btn = QPushButton("Run"); self.run_btn.clicked.connect(self.run)
        self.sim_btn = QPushButton("\u25b6 Simulate")
        self.sim_btn.clicked.connect(self.simulate)
        self.sim_btn.setVisible(False)      # servo projects only
        save_btn = QPushButton("Save"); save_btn.clicked.connect(self.save)
        self.download_btn = QPushButton("Download")
        self.download_btn.setToolTip(
            "Save this workflow as a .json file to move it to another machine")
        self.download_btn.clicked.connect(self.download_project)
        self.deploy_btn = QPushButton("Deploy")
        self.deploy_btn.setToolTip(
            "Copy this workflow into the runner's folder so it keeps working "
            "when the app is closed")
        self.deploy_btn.clicked.connect(self.deploy)
        for b in (save_btn, self.download_btn, self.deploy_btn, self.sim_btn, self.run_btn):
            topbar.addWidget(b)
        root.addLayout(topbar)

        # ---- PANELS -----------------------------------------------------
        # Panels are containers down each edge of the WORK AREA (below the top
        # bar). They start EMPTY and collapsed: pull one open with the arrow on
        # its edge, press [+], and tick which modules it should show.
        self._load_modules()

        self.panel_left = PanelBox("left", self)
        self.panel_right = PanelBox("right", self)
        self.panel_bottom = PanelBox("bottom", self)
        self.panels_by_side = {
            "left": self.panel_left,
            "right": self.panel_right,
            "bottom": self.panel_bottom,
        }

        self.arrow_left = EdgeArrow("left", self.toggle_panel)
        self.arrow_right = EdgeArrow("right", self.toggle_panel)
        self.arrow_bottom = EdgeArrow("bottom", self.toggle_panel)
        self.arrows_by_side = {
            "left": self.arrow_left,
            "right": self.arrow_right,
            "bottom": self.arrow_bottom,
        }

        # ===================== WORK AREA (arrows on the true edges)
        work = QHBoxLayout()
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(0)

        # the arrows sit hard against the real left/right edges of the screen,
        # pinned to the top of the work area so they are always findable
        left_edge = QVBoxLayout(); left_edge.setContentsMargins(0, 0, 0, 0)
        left_edge.addWidget(self.arrow_left); left_edge.addStretch()
        work.addLayout(left_edge)

        self._main_split = DotSplitter(Qt.Orientation.Horizontal)
        self._main_split.setChildrenCollapsible(True)
        self._main_split.setHandleWidth(8)

        # center: canvas with the bottom panel (and its arrow) beneath it
        center_col = QWidget()
        cc = QVBoxLayout(center_col)
        cc.setContentsMargins(6, 0, 6, 0); cc.setSpacing(4)
        self.canvas = Canvas(self)
        cc.addWidget(self.canvas, 1)
        bottom_edge = QHBoxLayout(); bottom_edge.setContentsMargins(0, 0, 0, 0)
        bottom_edge.addWidget(self.arrow_bottom); bottom_edge.addStretch()
        cc.addLayout(bottom_edge)
        cc.addWidget(self.panel_bottom)

        self._main_split.addWidget(self.panel_left)
        self._main_split.addWidget(center_col)
        self._main_split.addWidget(self.panel_right)
        self._main_split.setStretchFactor(0, 0)
        self._main_split.setStretchFactor(1, 1)
        self._main_split.setStretchFactor(2, 0)
        self._main_split.setSizes([250, 800, 250])
        work.addWidget(self._main_split, 1)

        right_edge = QVBoxLayout(); right_edge.setContentsMargins(0, 0, 0, 0)
        right_edge.addWidget(self.arrow_right); right_edge.addStretch()
        work.addLayout(right_edge)

        root.addLayout(work, 1)

        # Nothing is saved until construction finishes: hiding the panels here
        # would otherwise overwrite the stored layout with "all closed" before
        # _restore_layout gets a chance to read it.
        self._loading_layout = True
        for side in ("left", "right", "bottom"):
            self.toggle_panel(side, False)
        self._restore_layout()
        self._loading_layout = False
        self._main_split.splitterMoved.connect(lambda _p, _i: self._save_layout())

        # --- undo/redo + autosave state ---
        self._undo_stack = []      # list of workflow-JSON snapshots (strings)
        self._redo_stack = []
        self._undo_limit = 30
        self._suppress_snapshot = False   # set while applying undo/redo
        self._autosave_timer = QTimer(self); self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._do_autosave)

        QShortcut(QKeySequence("Delete"), self, lambda: self.canvas.delete_selected())
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
        QShortcut(QKeySequence("Ctrl+C"), self, lambda: self.canvas.copy_selected())
        QShortcut(QKeySequence("Ctrl+V"), self, lambda: self.canvas.paste())

        # listen for webhook-triggered runs so the canvas lights up live
        self._evt_listener = EventListener()
        self._evt_listener.event.connect(self._on_webhook_event)
        self._evt_listener.start()

        # follow the settings popup: apply the workflow appearance now, and
        # register so future saves update this screen too
        self.apply_theme()
        try:
            from home_screen import register_themed_screen
            register_themed_screen(self)
        except Exception:
            pass

    def filter_palette(self, text):
        text = text.lower().strip()
        for i in range(self.palette.count()):
            it = self.palette.item(i)
            nd = it.data(Qt.ItemDataRole.UserRole) or {}
            if nd.get("__header__"):
                it.setHidden(False)   # keep section headers visible
                continue
            hay = (nd.get("title", "") + " " + nd.get("type", "")).lower()
            it.setHidden(bool(text) and text not in hay)

    def _add_palette_header(self, label, color=None):
        it = QListWidgetItem(label)
        it.setData(Qt.ItemDataRole.UserRole, {"__header__": True})
        it.setFlags(Qt.ItemFlag.NoItemFlags)   # not selectable/clickable
        f = it.font(); f.setBold(True); f.setPointSize(f.pointSize() - 1); it.setFont(f)
        it.setForeground(QColor(color or ACCENT))
        it.setSizeHint(QSize(180, 30))
        self.palette.addItem(it)

    def _add_palette_node(self, nd, color="#ffffff"):
        """One palette row: node name on the left, its icon on the FAR right
        (just past the name slot, not overlapping the text)."""
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, nd)
        self.palette.addItem(it)

        ROW_H = 34   # comfortable height; text was getting vertically clipped
        row = QWidget()
        row.setFixedHeight(ROW_H)
        lay = QHBoxLayout(row); lay.setContentsMargins(10, 0, 10, 0); lay.setSpacing(6)
        name = QLabel(nd["title"])
        name.setStyleSheet(f"color: {color}; background: transparent;")
        name.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(name)
        lay.addStretch(1)                      # pushes the icon to the right edge
        pm = node_pixmap(nd["type"], 20)
        if pm is not None:
            ic = QLabel(); ic.setPixmap(pm)
            ic.setFixedSize(22, 22)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic.setStyleSheet("background: transparent;")
            lay.addWidget(ic)
        # padding is overridden to 0 on the palette, so the hint == row height
        it.setSizeHint(QSize(180, ROW_H))
        self.palette.setItemWidget(it, row)

    # robotics sub-groups: which node types live under each sub-header
    ROBOTICS_GROUPS = [
        ("FLOW",    ["device.on_start", "device.on_repeat", "device.repeat", "device.comment"]),
        ("SERVO",   ["device.servo", "device.servo_array", "device.servo_move"]),
        ("SCREEN",  ["device.screen"]),
        ("INPUT",   ["device.button", "device.encoder"]),
        ("LOGIC",   ["device.if", "device.state", "device.goto_state", "device.random"]),
        ("TIMING",  ["device.wait", "device.timer"]),
        ("ROUTING", ["device.pins", "device.pin", "device.array", "device.array_set",
                     "device.var", "device.map"]),
    ]

    # normal-node sub-groups for the NODES section (non-servo projects)
    NODE_GROUPS = [
        ("LOGIC",   ["logic.if", "logic.switch", "logic.filter", "core.merge"]),
        ("DATA",    ["core.set", "core.edit_fields", "core.text", "core.code",
                     "core.split_out", "core.aggregate", "core.sort", "core.limit",
                     "core.dedupe", "core.datetime", "core.hash"]),
        ("FLOW",    ["core.loop", "core.wait", "core.wait_webhook"]),
        ("ACTION",  ["web.http", "action.telegram", "action.discord",
                     "webhook.respond"]),
        ("DATA STORE", ["data.tabel"]),
        ("DEBUG",   ["core.log"]),
    ]

    def load_palette(self):
        self.palette.clear(); self.meta_by_type.clear()
        try:
            data = api_get("/nodes")
        except Exception as e:
            self.palette.addItem(QListWidgetItem("[API offline]"))
            self.results.setText(f"Can't reach API at {API}\nStart it: python3 api.py\n\n{e}"); return

        nodes = data["nodes"]
        for nd in nodes:
            self.meta_by_type[nd["type"]] = nd

        WHITE = "#ffffff"
        RED = "#ff6b6b"

        robotics = [n for n in nodes if n.get("category") == "robotics" or n.get("device")]
        is_servo = (getattr(self, "project_kind", "normal") == "servo")

        # ---- SERVO project: ONLY robotics nodes, grouped into sub-sections ----
        if is_servo:
            by_type = {n["type"]: n for n in robotics}
            placed = set()
            for group_name, types in self.ROBOTICS_GROUPS:
                group_nodes = [by_type[t] for t in types if t in by_type]
                if not group_nodes:
                    continue
                self._add_palette_header(group_name, RED)
                for nd in group_nodes:
                    self._add_palette_node(nd, WHITE)
                    placed.add(nd["type"])
            leftover = [n for n in robotics if n["type"] not in placed]
            if leftover:
                self._add_palette_header("OTHER", RED)
                for nd in leftover:
                    self._add_palette_node(nd, WHITE)
            return

        # ---- NORMAL project: triggers, then grouped nodes, NO robotics ----
        triggers = [n for n in nodes
                    if n.get("category") == "trigger" and n not in robotics]
        others   = [n for n in nodes
                    if n.get("category") not in ("trigger", "robotics")
                    and not n.get("device")]

        if triggers:
            self._add_palette_header("TRIGGERS", WHITE)
            for nd in triggers:
                self._add_palette_node(nd, WHITE)

        by_type = {n["type"]: n for n in others}
        placed = set()
        for group_name, types in self.NODE_GROUPS:
            group_nodes = [by_type[t] for t in types if t in by_type]
            if not group_nodes:
                continue
            self._add_palette_header(group_name, ACCENT)
            for nd in group_nodes:
                self._add_palette_node(nd, WHITE)
                placed.add(nd["type"])
        leftover = [n for n in others if n["type"] not in placed]
        if leftover:
            self._add_palette_header("MORE", ACCENT)
            for nd in leftover:
                self._add_palette_node(nd, WHITE)

    def paintEvent(self, event):
        """Paint the editor's own background.

        The app window is translucent (ui.py sets WA_TranslucentBackground), so
        a screen that paints nothing lets whatever was behind it — the home
        screen — stay visible underneath the workflow. Filling here stops that.

        With "no background" on, the editor goes see-through with a dark fog
        instead, so the desktop shows through but everything stays readable.
        """
        s = getattr(self, "ui_settings", {}) or {}
        no_bg = s.get("canvas_no_background", False)
        p = QPainter(self)
        if no_bg:
            fog = max(0, min(255, int(s.get("fog_opacity", 150))))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self.rect(), _QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.fillRect(self.rect(), _QColor(0, 0, 0, fog))
        else:
            try:
                from home_screen import GREY_BG as _grey
            except Exception:
                _grey = "#3a3a3a"
            # the user picks the panel colour in the settings popup
            p.fillRect(self.rect(), _QColor(s.get("panel_color") or _grey))
        p.end()
        super().paintEvent(event)

    def _load_modules(self):
        """Discover every module in panels/. They are NOT placed anywhere yet —
        panels start empty and the user picks what goes in them."""
        # Panels start empty, so the widgets modules normally provide may not
        # exist. These stand-ins keep the editor working either way: writing to
        # a log that isn't on screen simply goes nowhere instead of crashing.
        self.results = _NullWidget()
        self.json_view = _NullWidget()
        self.json_label = _NullWidget()
        self.palette = _NullList()
        self.palette_search = _NullWidget()
        self.other_projects = _NullList()
        self.settings_area = None
        self.settings_host = None
        self.settings_layout = None

        self.all_modules = []
        try:
            from panel_base import discover_modules
            here = os.path.dirname(os.path.abspath(__file__))
            for cls in discover_modules(os.path.join(here, "panels")):
                try:
                    self.all_modules.append(cls(self))
                except Exception as e:
                    print(f"  [module warn] {cls.__name__} failed to start: {e}")
        except Exception as e:
            print(f"  [module warn] module system unavailable: {e}")

    @property
    def panels(self):
        """Every module currently placed in a panel (what the hooks fire on)."""
        out = []
        for box in getattr(self, "panels_by_side", {}).values():
            out.extend(box.modules)
        return out

    def toggle_panel(self, side, is_open):
        """Show or hide one edge panel."""
        box = self.panels_by_side.get(side)
        if box is None:
            return
        box.setVisible(is_open)
        arrow = self.arrows_by_side.get(side)
        if arrow is not None and arrow.open != is_open:
            arrow.set_open(is_open)
        self._save_layout()

    def _default_layout(self):
        """The layout a brand-new user starts with.

        Rather than opening to a blank editor with no idea what the panels do,
        the first launch lays out the familiar arrangement: tools on the left,
        the node palette on the right, code/JSON along the bottom. Everything
        can then be moved or removed.
        """
        return {
            "left": {"open": True, "modules": ["settings", "other_projects", "run_log"]},
            "right": {"open": True, "modules": ["nodes"]},
            "bottom": {"open": False, "modules": ["json"]},
        }

    def toggle_module(self, module, panel, wanted):
        """Tick/untick a module in a panel's [+] menu."""
        if wanted:
            # a module lives in exactly one panel — take it out of its old one
            if module.host is not None and module.host is not panel:
                module.host.remove_module(module)
            panel.add_module(module)
            if not panel.isVisible():
                self.toggle_panel(panel.side, True)
            self._populate_module(module)
        else:
            if module.host is not None:
                module.host.remove_module(module)
        self.apply_theme()
        self._save_layout()

    def _populate_module(self, module):
        """Fill a module with data the moment it is added.

        A module builds an empty widget; the content normally arrives on the
        next project-open or workflow change. Without this, adding one
        mid-session shows an empty box until the user reopens the workflow.
        """
        try:
            module.on_project_opened(self.current_project)
        except Exception:
            pass
        # the palette and the JSON/code view are rebuilt by the editor, so
        # trigger the same refreshes opening a project would have done
        try:
            if module.ID == "nodes":
                self.load_palette()
            elif module.ID == "json":
                self.refresh_json()
            elif module.ID == "other_projects":
                self.refresh_other_projects()
            elif module.ID == "settings":
                self.show_node_settings(self.canvas.selected)
            elif module.ID == "run_log":
                last = getattr(self, "_last_log_text", "")
                if last:
                    module.widget.setText(last)
        except Exception as e:
            print(f"  [module warn] populating {module.ID}: {e}")
        try:
            module.refresh()
        except Exception:
            pass

    def move_module(self, module, target_panel):
        """Middle-mouse drag dropped a module onto another panel."""
        if module.host is target_panel:
            return
        if module.host is not None:
            module.host.remove_module(module)
        target_panel.add_module(module)
        if not target_panel.isVisible():
            self.toggle_panel(target_panel.side, True)
        self.apply_theme()
        self._save_layout()

    def panel_at_global(self, gpos):
        """Which panel is under this screen position (for middle-mouse drops).

        A closed panel is still a valid target: its edge arrow counts as the
        drop zone, so you can drag a module into a panel you have not opened
        yet and it opens itself.
        """
        for side, box in self.panels_by_side.items():
            if box.isVisible():
                rect = box.rect().translated(box.mapToGlobal(box.rect().topLeft()))
                if rect.contains(gpos):
                    return box
        for side, arrow in getattr(self, "arrows_by_side", {}).items():
            if arrow.isVisible():
                rect = arrow.rect().translated(arrow.mapToGlobal(arrow.rect().topLeft()))
                # a little slack so you don't have to hit 12 pixels exactly
                rect.adjust(-14, -14, 14, 14)
                if rect.contains(gpos):
                    return self.panels_by_side.get(side)
        return None

    def _panels_notify(self, hook, *args):
        """Call an optional hook on every panel, ignoring ones that don't have
        it and never letting a broken panel take the editor down."""
        for p in getattr(self, "panels", []):
            fn = getattr(p, hook, None)
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception as e:
                print(f"  [panel warn] {type(p).__name__}.{hook}: {e}")

    def apply_theme(self):
        """Re-apply the workflow appearance settings from the settings popup.

        The canvas repaints its background, and the panels AROUND the canvas
        (palette, settings, log, code) get tinted to match that background's
        brightness — so a light photo gives light panels and a dark one gives
        dark panels, instead of the surroundings clashing with the image.
        """
        try:
            from home_screen import load_home_ui_settings
            s = load_home_ui_settings()
        except Exception:
            s = {}
        self.ui_settings = s

        # let the canvas pick up the new background/grid
        try:
            self.canvas.reload_theme()
        except Exception:
            pass

        no_bg = s.get("canvas_no_background", False)
        panel, text, border = self._panel_colors(s)
        if no_bg:
            # see-through: modules paint no fill of their own so the editor's
            # fog is what shows behind their text
            panel = "transparent"
        self._panel_css = (f"background: {panel}; color:{text}; "
                           f"font-family:monospace; font-size:9px; "
                           f"border:1px solid {border};")
        # The window is translucent, so each panel's container must paint an
        # opaque background or old frames stay on screen and the UI smears.
        try:
            from home_screen import GREY_BG as _grey
        except Exception:
            _grey = "#3a3a3a"
        # the user can pick the panel colour in the settings popup; the
        # no-background switch overrides it while it is on
        base = s.get("panel_color") or _grey
        # "No background" is app-wide, not canvas-only: the panels go
        # see-through too, with a dark fog behind them so their contents stay
        # readable against whatever is showing through.
        if no_bg:
            fog = int(s.get("fog_opacity", 150))
            fog = max(0, min(255, fog))
            panel_bg = f"rgba(0,0,0,{fog / 255:.3f})"
            frame_bg = "transparent"
        else:
            panel_bg = base
            frame_bg = base

        for side, box in getattr(self, "panels_by_side", {}).items():
            box.setStyleSheet(f"QWidget#panelbox_{side}{{background:{panel_bg};}}")
            # opaque only when we actually have a solid colour; a see-through
            # panel must NOT auto-fill or it paints over its own contents
            box.setAutoFillBackground(not no_bg)
        for m in getattr(self, "panels", []):
            if m.container is not None:
                m.container.setStyleSheet(
                    f"QWidget#modframe_{m.ID}{{background:{frame_bg};}}")
                m.container.setAutoFillBackground(not no_bg)
        # each panel then styles its own inner widget
        self._panels_notify("apply_theme", self._panel_css, (panel, text, border))
        self.update()

    def _panel_colors(self, s):
        """Work out panel colours from the chosen canvas background image.

        Samples the image's average brightness. Dark image -> dark translucent
        panels with light text; light image -> light panels with dark text.
        Falls back to the original dark styling when no image is set.
        """
        default = ("rgba(10,10,10,0.5)", "#ddd", "#444")
        if s.get("canvas_no_background", False):
            return default
        path = s.get("canvas_bg_image")
        if not path or not os.path.isfile(path):
            return default
        try:
            img = QImage(path)
            if img.isNull():
                return default
            # scale to a tiny thumbnail; averaging those pixels is fast and
            # gives the overall tone of the image
            thumb = img.scaled(16, 16, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            tot = n = 0
            for y in range(thumb.height()):
                for x in range(thumb.width()):
                    c = thumb.pixelColor(x, y)
                    tot += 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
                    n += 1
            if not n:
                return default
            lum = tot / n
        except Exception:
            return default

        if lum > 140:      # light image -> light panels, dark text
            return ("rgba(245,245,245,0.72)", "#181818", "#999")
        if lum > 70:       # mid -> neutral
            return ("rgba(40,40,40,0.62)", "#eee", "#666")
        return ("rgba(10,10,10,0.55)", "#ddd", "#444")   # dark image

    def open_project(self, name):
        self.current_project = name; self.proj_label.setText(name)
        try: wf = load_project(name)
        except Exception: wf = {"name": name, "nodes": [], "connections": {}}
        # servo projects generate Arduino code instead of running in the engine
        self.project_kind = wf.get("kind", "normal")
        self._apply_project_kind()
        self.load_palette()
        self.canvas.load_workflow(wf, self.meta_by_type)
        self.refresh_other_projects(); self.refresh_json(); self.show_node_settings(None)
        self._panels_notify("on_project_opened", name)
        self._refresh_deploy_button()   # Deploy vs Undeploy for this project

    def _apply_project_kind(self):
        """Swap the Run button for Export Code on servo (hardware) projects,
        and turn the whole editor red-themed."""
        servo = (getattr(self, "project_kind", "normal") == "servo")
        self.sim_btn.setVisible(servo)
        if servo:
            self.run_btn.setText("Export Code")
            self.run_btn.setStyleSheet(
                "QPushButton{background:rgba(255,107,107,0.15);color:#ff6b6b;"
                "border:1px solid #ff6b6b;border-radius:4px;padding:5px 12px;"
                "font-family:monospace;}")
            self.proj_label.setStyleSheet(
                "color:#ff6b6b;font-family:monospace;font-size:14px;")
            # logo goes RED and UPPERCASE
            if hasattr(self, "logo"):
                self.logo.setText("DuGS")
                self.logo.setStyleSheet(
                    "color:#ff6b6b; font-family:monospace; font-size:20px; font-weight:bold;")
        else:
            self.run_btn.setText("Run")
            self.run_btn.setStyleSheet("")
            self.proj_label.setStyleSheet(
                f"color:{ACCENT};font-family:monospace;font-size:14px;")
            if hasattr(self, "logo"):
                self.logo.setText("DuGS")
                self.logo.setStyleSheet(
                    f"color:{ACCENT}; font-family:monospace; font-size:20px;")

    def refresh_other_projects(self):
        self.other_projects.clear()
        for p in list_projects():
            if p == self.current_project: continue
            self.other_projects.addItem(QListWidgetItem(p))

    def switch_project(self, item):
        self.save(); self.open_project(item.text())

    def drop_node(self, item):
        nd = item.data(Qt.ItemDataRole.UserRole)
        if nd and not nd.get("__header__"):
            self.canvas.add_node(nd); self.mark_changed()

    def refresh_json(self):
        wf = self.canvas.to_workflow(self.current_project or "untitled")

        # robotics projects don't care about the workflow JSON — they want the
        # ARDUINO CODE the graph compiles to, updated live as you build.
        if getattr(self, "project_kind", "normal") == "servo":
            try:
                import os, codegen
                here = os.path.dirname(os.path.abspath(__file__))
                reg = codegen.discover_device_nodes(
                    None, os.path.join(here, "robotics_nodes"))
                self.json_view.setPlainText(codegen.generate(wf, reg))
            except Exception as e:
                self.json_view.setPlainText(f"// code generation failed:\n// {e}")
            self.json_label.setText("CODE")
            return

        self.json_label.setText("JSON")
        self.json_view.setText(json.dumps(wf, indent=2))

    def _copy_json(self):
        QApplication.clipboard().setText(self.json_view.toPlainText())
        btn = self.sender()
        if btn:
            btn.setText("✓ copied"); btn.setStyleSheet(f"font-size:9px; padding:0px 4px; border:1px solid {ACCENT}; color:{ACCENT}; border-radius:3px;")
            QTimer.singleShot(1200, lambda: (btn.setText("⎘ copy"), btn.setStyleSheet("font-size:9px; padding:0px 4px; border:1px solid #444; color:#888; border-radius:3px;")))

    def download_project(self):
        """Save the current workflow as a .json file the person picks, so they
        can move it to another machine or host it elsewhere."""
        from PyQt6.QtWidgets import QFileDialog
        if not self.current_project:
            return
        self.save()
        from storage import export_project, DOWNLOADS
        import os as _os
        start = _os.path.join(DOWNLOADS, f"{self.current_project}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Download workflow", start, "JSON files (*.json)")
        if not path:
            return
        try:
            export_project(self.current_project, path)
            self.results.setText(f"downloaded to {path}")
        except Exception as e:
            self.results.setText(f"download failed: {e}")

    def deploy(self):
        """Copy this workflow into the runner's projects/ folder so it keeps
        running with the app closed. If the project is already deployed, this
        removes it instead (the button reads 'Undeploy').

        Deploying opens a small dialog: a path field for the runner's folder, a
        Scan button that hunts the disk for it, and a Complete button.
        """
        from storage import undeploy_project, is_deployed

        if getattr(self, "project_kind", "normal") == "servo":
            self.results.setText(
                "Servo projects generate Arduino code — flash the board "
                "instead of deploying.")
            return
        if not self.current_project:
            return

        # already deployed -> this click UN-deploys it
        if is_deployed(self.current_project):
            try:
                undeploy_project(self.current_project)
                self.results.setText(f"undeployed '{self.current_project}'")
            except Exception as e:
                self.results.setText(f"undeploy failed: {e}")
            self._refresh_deploy_button()
            self._notify_projects_changed()
            return

        self.save()
        wf = self.canvas.to_workflow(self.current_project)
        if not wf.get("nodes"):
            self.results.setText("Nothing to deploy — the canvas is empty.")
            return

        self._open_deploy_dialog()

    def _open_deploy_dialog(self):
        """The small deploy popup: path field + Scan + Complete."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QLineEdit, QPushButton, QFileDialog)
        from storage import deploy_path, set_deploy_path, deploy_project

        dlg = QDialog(self)
        dlg.setWindowTitle("Deploy")
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel("Runner's projects folder:"))

        row = QHBoxLayout()
        path_edit = QLineEdit(deploy_path())      # pre-filled if already set
        path_edit.setPlaceholderText("/home/you/Deploy_DuGS/projects")
        row.addWidget(path_edit, 1)
        scan_btn = QPushButton("Scan")
        scan_btn.setToolTip("Hunt the disk for the runner's projects folder")
        row.addWidget(scan_btn)
        browse_btn = QPushButton("\u2026")
        browse_btn.setToolTip("Browse to the folder")
        browse_btn.setFixedWidth(32)
        row.addWidget(browse_btn)
        lay.addLayout(row)

        status = QLabel("")
        status.setStyleSheet("color:#888;font-family:monospace;font-size:11px;")
        lay.addWidget(status)

        def do_scan():
            status.setText("scanning\u2026")
            dlg.repaint()
            found = self._scan_for_runner()
            if found:
                path_edit.setText(found)
                status.setText(f"found: {found}")
            else:
                status.setText("no runner folder found — type or browse to it")
        scan_btn.clicked.connect(do_scan)

        def do_browse():
            folder = QFileDialog.getExistingDirectory(
                dlg, "Point at the runner's projects folder",
                path_edit.text() or "")
            if folder:
                path_edit.setText(folder)
        browse_btn.clicked.connect(do_browse)

        complete = QPushButton("Complete deploy")
        complete.setStyleSheet(
            "QPushButton{background:rgba(76,207,106,0.15);color:#4ccf6a;"
            "border:1px solid #4ccf6a;border-radius:4px;padding:6px 12px;"
            "font-family:monospace;}")
        lay.addWidget(complete)

        def do_complete():
            p = path_edit.text().strip()
            if not p:
                status.setText("enter a folder first (or Scan for it)")
                return
            import os as _os
            if not _os.path.isdir(p):
                status.setText(f"not a folder: {p}")
                return
            set_deploy_path(p)
            try:
                dest = deploy_project(self.current_project)
                self.results.setText(
                    f"deployed '{self.current_project}'\ninto {dest}\n"
                    f"the runner picks it up within a few seconds")
                dlg.accept()
            except Exception as e:
                status.setText(f"deploy failed: {e}")
        complete.clicked.connect(do_complete)

        dlg.exec()
        self._refresh_deploy_button()
        self._notify_projects_changed()

    def _scan_for_runner(self):
        """Hunt common locations for a runner's projects/ folder.

        A runner folder is recognised by a projects/ directory sitting next to
        dugs_runner.py — so we don't match every random 'projects' folder on
        the disk. Searches the home folder a few levels deep, which is fast
        enough and covers where people actually put it.
        """
        import os as _os
        roots = [_os.path.expanduser("~")]
        best = None
        for root in roots:
            for dirpath, dirnames, filenames in _os.walk(root):
                # don't descend into heavy/hidden trees — keeps the scan quick
                depth = dirpath[len(root):].count(_os.sep)
                if depth > 4:
                    dirnames[:] = []
                    continue
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(".") and d != "node_modules"]
                if "dugs_runner.py" in filenames and "projects" in dirnames:
                    return _os.path.join(dirpath, "projects")
                # weaker fallback: a projects/ folder holding workflow json
                if _os.path.basename(dirpath) == "projects" and best is None:
                    if any(f.endswith(".json") for f in filenames):
                        best = dirpath
        return best

    def _refresh_deploy_button(self):
        """Flip the button between Deploy and Undeploy for the current project."""
        try:
            from storage import is_deployed
            dep = bool(self.current_project) and is_deployed(self.current_project)
        except Exception:
            dep = False
        self.deploy_btn.setText("Undeploy" if dep else "Deploy")

    def _notify_projects_changed(self):
        """Ask the home screen to repaint so deployed names go green."""
        try:
            self.app.home.refresh()
        except Exception:
            pass

    def save(self):
        if not self.current_project: return
        wf = self.canvas.to_workflow(self.current_project)
        wf["kind"] = getattr(self, "project_kind", "normal")
        save_project(self.current_project, wf)
        has_webhook = any(n.get("type") == "webhook.trigger" for n in wf.get("nodes", []))
        if has_webhook:
            try:
                api_post("/webhooks/register", wf)
                self.results.setText(f"saved: {self.current_project}  (webhook registered)")
            except Exception as e:
                self.results.setText(f"saved: {self.current_project}  (webhook register failed: {e})")
        else:
            self.results.setText(f"saved: {self.current_project}")

    # ================= autosave + undo/redo =================
    def _save_layout(self):
        """Remember which panels are open and which modules each one holds, so
        the layout you build is the one you get back next launch."""
        if getattr(self, "_loading_layout", False):
            return          # still building; don't persist a half-built state
        try:
            layout = {}
            for side, box in getattr(self, "panels_by_side", {}).items():
                layout[side] = {
                    "open": box.isVisible(),
                    "modules": [m.ID for m in box.modules],
                    "sizes": box.stack.sizes(),
                }
            save_ui_state({
                "main": self._main_split.sizes(),
                "panels": layout,
            })
        except Exception:
            pass

    def _restore_layout(self):
        st = load_ui_state()
        try:
            if st.get("main"):
                self._main_split.setSizes(st["main"])
        except Exception:
            pass
        # put the saved modules back where they were. With nothing saved yet
        # (a first-ever launch) fall back to a sensible starting layout.
        by_id = {m.ID: m for m in getattr(self, "all_modules", [])}
        saved = st.get("panels")
        if not saved:
            saved = self._default_layout()
        for side, info in saved.items():
            box = getattr(self, "panels_by_side", {}).get(side)
            if box is None:
                continue
            for mid in info.get("modules", []):
                mod = by_id.get(mid)
                if mod is not None and mod.host is None:
                    box.add_module(mod)
                    self._populate_module(mod)
            if info.get("open"):
                self.toggle_panel(side, True)
            try:
                if info.get("sizes"):
                    box.stack.setSizes(info["sizes"])
            except Exception:
                pass

    def _snapshot(self):
        """Current workflow as a JSON string (used for undo history)."""
        return json.dumps(self.canvas.to_workflow(self.current_project or "untitled"))

    def mark_changed(self, push_undo=True):
        """Call after ANY edit (node add/move/delete, connection change,
        param edit, etc). Pushes an undo snapshot and schedules an autosave."""
        if self._suppress_snapshot:
            return
        if push_undo:
            snap = self._snapshot()
            # avoid duplicate consecutive snapshots
            if not self._undo_stack or self._undo_stack[-1] != snap:
                self._undo_stack.append(snap)
                if len(self._undo_stack) > self._undo_limit:
                    self._undo_stack.pop(0)
                self._redo_stack.clear()
        self.refresh_json()
        # on_workflow_changed existed on every Panel but nothing ever called
        # it — this is what makes it real, so any panel (like the project's
        # Tabel/Memory list) can stay live as nodes are added or removed,
        # not just after a save.
        self._panels_notify("on_workflow_changed")
        # debounce autosave so rapid edits don't hammer the disk
        self._autosave_timer.start(600)

    def _do_autosave(self):
        if not self.current_project:
            return
        try:
            wf = self.canvas.to_workflow(self.current_project)
            wf["kind"] = getattr(self, "project_kind", "normal")
            save_project(self.current_project, wf)
            has_webhook = any(n.get("type") == "webhook.trigger" for n in wf.get("nodes", []))
            if has_webhook:
                try: api_post("/webhooks/register", wf)
                except Exception: pass
        except Exception as e:
            self.results.setText(f"autosave failed: {e}")

    def _apply_snapshot(self, snap):
        self._suppress_snapshot = True
        try:
            wf = json.loads(snap)
            self.canvas.load_workflow(wf, self.meta_by_type)
            self.show_node_settings(None)
            self.refresh_json()
        finally:
            self._suppress_snapshot = False
        self._autosave_timer.start(600)

    def undo(self):
        if len(self._undo_stack) < 1:
            return
        # current state goes onto redo; restore previous
        current = self._snapshot()
        prev = self._undo_stack.pop()
        if prev == current and self._undo_stack:
            # top of stack equals current (snapshot was taken post-edit) -> step back one more
            self._redo_stack.append(current)
            prev = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._apply_snapshot(prev)

    def redo(self):
        if not self._redo_stack:
            return
        snap = self._redo_stack.pop()
        self._undo_stack.append(self._snapshot())
        self._apply_snapshot(snap)

    def _exec_order(self, wf):
        conns = wf.get("connections", {})
        has_incoming = set()
        for src, links in conns.items():
            for l in links: has_incoming.add(l["to"])
        names = [n["name"] for n in wf["nodes"]]
        starts = [n for n in names if n not in has_incoming]
        order = []; queue = list(starts); seen = set()
        while queue:
            cur = queue.pop(0)
            if cur in seen: continue
            seen.add(cur); order.append(cur)
            for l in conns.get(cur, []):
                if l["to"] not in seen: queue.append(l["to"])
        for n in names:
            if n not in seen: order.append(n)
        return order

    def _on_webhook_event(self, evt):
        """A webhook-triggered run is happening on the server. If it's the
        workflow currently open, replay its events on the canvas live."""
        kind = evt.get("kind")
        wf = evt.get("workflow")
        # only react to the project that's open in front of the user
        if self.current_project and wf and wf != self.current_project:
            return
        if kind == "webhook_run_start":
            self.results.clear()
            self._append_result(f"⚡ webhook fired → running '{wf}'")
            self.canvas.run_states.clear(); self.canvas.edge_counts.clear()
            self.canvas.running_node = None; self.canvas.update()
            return
        if kind == "webhook_run_error":
            self._append_result(f"webhook run error: {evt.get('error','')}")
            return
        # all other engine events (node_running/node_done/edge/results/...) are
        # handled by the exact same logic as a manual run
        self._on_run_event(evt)

    def closeEvent(self, e):
        try:
            self._evt_listener.stop()
        except Exception:
            pass
        super().closeEvent(e)

    def _on_run_event(self, evt):
        """Handle one live execution event on the GUI thread."""
        kind = evt.get("kind")
        c = self.canvas
        if kind == "start":
            c.run_states.clear(); c.edge_counts.clear()
            c.running_node = None; c.active_edge = None
            c.update()
        elif kind == "node_running":
            c.running_node = evt["node"]
            c.run_states[evt["node"]] = "running"
            c.update()
        elif kind == "node_done":
            c.run_states[evt["node"]] = "done"
            if c.running_node == evt["node"]:
                c.running_node = None
            # keep this node's output sample so the I/O panel can show it
            self._last_results[evt["node"]] = evt.get("sample", [])
            ms = evt.get("ms", 0)
            
            # Extract tokens_used from first sample item if present
            tokens_used = evt.get("sample", [{}])[0].get("tokens_used")
            if tokens_used is not None:
                log_line = f"{evt['node']}  →  {evt.get('items_out', 0)} item(s)  ({ms:.0f} ms)  [Tokens used: {tokens_used}]"
            else:
                log_line = f"{evt['node']}  →  {evt.get('items_out', 0)} item(s)  ({ms:.0f} ms)"
            
            self._append_result(log_line)
            # if this node is the one open in settings, refresh its I/O view
            if getattr(self, "_io_node", None) == evt["node"]:
                self._refresh_io_panel()
            c.update()
        elif kind == "node_error":
            c.run_states[evt["node"]] = "error"
            if c.running_node == evt["node"]:
                c.running_node = None
            self._append_result(f"{evt['node']}  ✗  ERROR: {evt.get('error','')}")
            c.update()
        elif kind == "edge":
            key = (evt["from"], evt.get("out", 0), evt["to"], evt.get("in", 0))
            c.edge_counts[key] = evt.get("items", 0)
            c.pulse_edge(key)   # animate a dot travelling the wire
            c.update()
        elif kind == "results":
            full = evt.get("results", {})
            # normalise: results[node] = [port0_items, port1_items, ...].
            # flatten to the first port's items for the I/O viewer.
            for nm, ports in full.items():
                if isinstance(ports, list) and ports and isinstance(ports[0], list):
                    self._last_results[nm] = [it.get("json", it) for it in ports[0]]
                elif isinstance(ports, list):
                    self._last_results[nm] = [it.get("json", it) for it in ports]
            c.running_node = None; c.active_edge = None; c.update()
            if getattr(self, "_io_node", None):
                self._refresh_io_panel()
        elif kind == "fatal":
            self._append_result(f"RUN FAILED: {evt.get('error','')}")
            c.running_node = None; c.update()

    def _append_result(self, line):
        prev = self.results.toPlainText() or getattr(self, "_last_log_text", "")
        # keep the log readable: cap to last ~40 lines
        lines = (prev.splitlines() + [line])[-40:]
        text = "\n".join(lines)
        # kept so the log survives being removed and added back as a module
        self._last_log_text = text
        self.results.setPlainText(text)
        sb = self.results.verticalScrollBar()
        sb.setValue(sb.maximum())

    def simulate(self):
        """Run the robotics graph virtually and show what the board would do."""
        if getattr(self, "_sim_worker", None) and self._sim_worker.isRunning():
            self._sim_worker.stop()
            self.sim_btn.setText("\u25b6 Simulate")
            return

        wf = self.canvas.to_workflow(self.current_project or "untitled")
        if not wf["nodes"]:
            self.results.setText("Canvas empty - drop some nodes first."); return

        self.results.clear()
        self.canvas.run_states.clear(); self.canvas.edge_counts.clear()
        self.canvas.running_node = None; self.canvas.update()
        self._sim_warnings = 0

        self.sim_btn.setText("\u25a0 Stop")
        self._sim_worker = SimWorker(wf, loops=None, realtime=True)
        self._sim_worker.event.connect(self._on_sim_event)
        self._sim_worker.finished.connect(lambda: self.sim_btn.setText("\u25b6 Simulate"))
        self._sim_worker.start()

    def _on_sim_event(self, e):
        """One thing the virtual board just did."""
        k = e.get("kind"); t = e.get("t", 0); node = e.get("node", "")
        c = self.canvas

        if node and k not in ("warn", "phase", "end", "power_on", "loop_start"):
            for prev in list(c.run_states):
                if c.run_states[prev] == "running":
                    c.run_states[prev] = "done"
            c.run_states[node] = "running"
            c.running_node = node
            c.update()

        if k == "power_on":
            self._append_result("\u26a1 POWER ON")
        elif k == "phase":
            ph = e.get("phase")
            self._append_result("- setup(): runs once -" if ph == "setup"
                                else "- loop(): repeats forever -")
        elif k == "loop_start":
            self._append_result(f"  pass {e.get('n', 0) + 1}")
        elif k == "servo":
            prev = e.get("prev")
            arrow = f"{prev} -> {e['angle']} deg" if prev is not None else f"-> {e['angle']} deg"
            self._append_result(f"{t:6.1f}s  SERVO pin {e['pin']}  {arrow}   [{node}]")
        elif k == "pin":
            self._append_result(f"{t:6.1f}s  PIN {e['pin']} = {e['value']}   [{node}]")
        elif k == "read":
            self._append_result(f"{t:6.1f}s  READ pin {e.get('pin')} -> {e.get('into')}   [{node}]")
        elif k == "wait":
            self._append_result(f"{t:6.1f}s  wait {e.get('ms', 0):.0f}ms   [{node}]")
        elif k == "screen":
            self._append_result(f"{t:6.1f}s  SCREEN: \"{e.get('text','')}\"   [{node}]")
        elif k == "random":
            self._append_result(f"{t:6.1f}s  RANDOM {e.get('into')} = {e.get('value')}   [{node}]")
        elif k == "var":
            self._append_result(f"{t:6.1f}s  {e.get('var')} {e.get('op')} -> {e.get('value')}   [{node}]")
        elif k == "button":
            self._append_result(f"{t:6.1f}s  BUTTON pin {e.get('pin')} ({e.get('note','')})   [{node}]")
        elif k == "repeat":
            self._append_result(f"{t:6.1f}s  repeat {e.get('iteration',0)+1}/{e.get('of','?')}   [{node}]")
        elif k == "warn":
            self._sim_warnings = getattr(self, "_sim_warnings", 0) + 1
            self._append_result(f"  !! {e.get('msg','')}" + (f"   [{node}]" if node else ""))
            if node:
                c.run_states[node] = "error"; c.update()
        elif k == "end":
            c.running_node = None; c.update()
            w = getattr(self, "_sim_warnings", 0)
            self._append_result(f"- finished ({w} warning{'s' if w != 1 else ''}) -")

    def run(self):
        wf = self.canvas.to_workflow(self.current_project or "untitled")
        if not wf["nodes"]:
            self.results.setText("Canvas empty - drop some nodes first."); return

        # ---- servo project: compile the graph to an Arduino sketch ----
        if getattr(self, "project_kind", "normal") == "servo":
            wf["kind"] = "servo"
            self.results.clear()
            try:
                res = api_post("/generate", wf)
            except Exception as e:
                self.results.setText(f"Export failed:\n{e}"); return
            if not res.get("ok"):
                self.results.setText(f"Export failed:\n{res.get('error')}"); return
            path = res.get("path", "")
            self._append_result(f"exported: {path}")
            self._append_result("open it in the Arduino IDE and upload.")
            self.json_view.setPlainText(res.get("code", ""))
            return

        self.results.clear()
        self.canvas.run_states.clear(); self.canvas.edge_counts.clear()
        self.canvas.running_node = None; self.canvas.update()
        self._run_worker = RunWorker(wf)
        self._run_worker.event.connect(self._on_run_event)
        self._run_worker.failed.connect(lambda e: self._append_result(f"Run failed:\n{e}"))
        self._run_worker.start()

    def run_from(self, node_name):
        # run the whole workflow but visually anchor on the chosen node;
        # the live stream will still light up everything as it executes.
        self.run()
