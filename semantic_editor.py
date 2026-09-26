"""
semantic_editor.py — build and edit one Semantic Table.

Same skin as the other screens (grey/image/see-through background, the gear
icon opening the shared settings popup, the DuGS wordmark as the back
button).

Left: the intents in this table -- one row per result, showing how many
phrasings it has. Right: the selected intent's result on top, and every way
of saying it underneath, one per line.

The whole point of this screen is the variations box. More phrasings you
write down = better matching. There is no cleverness to substitute for it,
so the box is the biggest thing on screen and the hint under it says so.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QListWidget,
    QListWidgetItem, QTextEdit, QLineEdit, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import Qt

from theme import DIM
from storage import (
    load_semantic_table, save_semantic_table, semantic_variation_count,
)
from home_screen import (
    load_home_ui_settings, HomeSettingsDialog,
    DEFAULT_BUTTON_COLOR, DEFAULT_LOGO_COLOR,
    button_style, paint_flat_or_image_bg, register_themed_screen,
)
import semantic_search


class SemanticTableEditor(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.table_name = None
        self._intents = []
        self._selected = -1
        self.settings = load_home_ui_settings()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(8)

        # ---- top bar ----
        bar = QHBoxLayout()
        self.dugs = QLabel("DuGS")
        self.dugs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dugs.mousePressEvent = lambda _e: self.app.go_home()
        bar.addWidget(self.dugs)
        self.title = QLabel("-")
        bar.addSpacing(16)
        bar.addWidget(self.title)
        bar.addStretch()
        self.count_label = QLabel("")
        bar.addWidget(self.count_label)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(10)

        # ---- left: the intents ----
        left = QVBoxLayout()
        left.setSpacing(4)
        left_header = QHBoxLayout()
        self.add_btn = QPushButton("+ Intent")
        self.add_btn.clicked.connect(self._add_intent)
        left_header.addWidget(self.add_btn)
        self.del_btn = QPushButton("\U0001f5d1 Delete")
        self.del_btn.clicked.connect(self._delete_intent)
        left_header.addWidget(self.del_btn)
        left_header.addStretch()
        left.addLayout(left_header)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select_intent)
        left.addWidget(self.list, 1)
        body.addLayout(left, 1)

        # ---- right: the selected intent ----
        right = QVBoxLayout()
        right.setSpacing(4)

        self.result_label = QLabel("RESULT  (what this intent outputs)")
        right.addWidget(self.result_label)
        self.result_edit = QLineEdit()
        self.result_edit.setPlaceholderText("weather_check")
        right.addWidget(self.result_edit)

        self.var_label = QLabel("VARIATIONS  (one per line)")
        right.addWidget(self.var_label)
        self.var_edit = QTextEdit()
        self.var_edit.setPlaceholderText(
            "weather today\nwhat's the temperature\nis it raining\n"
            "forecast please")
        right.addWidget(self.var_edit, 1)

        save_row = QHBoxLayout()
        self.test_edit = QLineEdit()
        self.test_edit.setPlaceholderText("type a phrase to test the whole table\u2026")
        self.test_edit.returnPressed.connect(self._test_match)
        save_row.addWidget(self.test_edit, 1)
        self.test_btn = QPushButton("Test")
        self.test_btn.clicked.connect(self._test_match)
        save_row.addWidget(self.test_btn)
        self.save_btn = QPushButton("Save intent")
        self.save_btn.clicked.connect(self._save_intent)
        save_row.addWidget(self.save_btn)
        right.addLayout(save_row)

        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        right.addWidget(self.test_result)

        body.addLayout(right, 2)
        root.addLayout(body, 1)

        self.hint = QLabel(
            "the more ways you write the same thing, the better it matches \u00b7 "
            "typos and word order are handled for you")
        self.hint.setStyleSheet(f"color:{DIM};font-family:monospace;font-size:9px;")
        root.addWidget(self.hint)

        bottom = QHBoxLayout()
        self.settings_btn = QPushButton("\u2699")
        self.settings_btn.setFixedSize(38, 38)
        self.settings_btn.setToolTip("Home screen settings")
        self.settings_btn.clicked.connect(self.open_settings)
        bottom.addWidget(self.settings_btn)
        bottom.addStretch()
        root.addLayout(bottom)

        self.apply_theme()
        register_themed_screen(self)

    # -- theme ---------------------------------------------------------------
    def apply_theme(self):
        btn = self.settings.get("button_color", DEFAULT_BUTTON_COLOR)
        logo = self.settings.get("logo_color", DEFAULT_LOGO_COLOR)
        self.dugs.setStyleSheet(
            f"color:{logo};font-family:monospace;font-size:20px;font-weight:bold;")
        self.title.setStyleSheet(f"color:{btn};font-family:monospace;font-size:14px;")
        for b in (self.settings_btn,):
            b.setStyleSheet(button_style(btn, circular=True))
        for b in (self.add_btn, self.save_btn, self.test_btn):
            b.setStyleSheet(button_style(btn))
        self.del_btn.setStyleSheet(button_style("#ff6b6b"))
        list_css = ("QListWidget{background:rgba(58,58,58,0.55);color:#eee;"
                   "font-family:monospace;border:1px solid #555;}"
                   "QListWidget::item:selected{background:rgba(255,255,255,0.14);}")
        self.list.setStyleSheet(list_css)
        edit_css = ("background:rgba(58,58,58,0.55);color:#eee;"
                   "font-family:monospace;font-size:12px;"
                   "border:1px solid #555;border-radius:4px;padding:5px;")
        self.result_edit.setStyleSheet(edit_css)
        self.var_edit.setStyleSheet(edit_css)
        self.test_edit.setStyleSheet(edit_css)
        for lbl in (self.result_label, self.var_label):
            lbl.setStyleSheet(f"color:{DIM};font-family:monospace;font-size:9px;")
        self.count_label.setStyleSheet(f"color:{DIM};font-family:monospace;font-size:10px;")
        self.test_result.setStyleSheet(
            f"color:{btn};font-family:monospace;font-size:11px;")
        self.update()

    def open_settings(self):
        HomeSettingsDialog(self, self).exec()

    def paintEvent(self, event):
        if not paint_flat_or_image_bg(self, event, self.settings):
            super().paintEvent(event)
            return
        super().paintEvent(event)

    # -- data ----------------------------------------------------------------
    def open(self, name):
        self.table_name = name
        self.title.setText(name)
        self._selected = -1
        self._reload()

    def _reload(self):
        if not self.table_name:
            return
        data = load_semantic_table(self.table_name)
        self._intents = data.get("intents") or []
        total = semantic_variation_count(self.table_name)
        self.count_label.setText(
            f"{len(self._intents)} intent{'s' if len(self._intents) != 1 else ''} "
            f"\u00b7 {total} variation{'s' if total != 1 else ''}")

        self.list.blockSignals(True)
        self.list.clear()
        for i, intent in enumerate(self._intents):
            n = len(intent.get("variations") or [])
            self.list.addItem(QListWidgetItem(
                f"{intent.get('result', '(no result)')}   \u00b7   {n}"))
        self.list.blockSignals(False)

        if 0 <= self._selected < len(self._intents):
            self.list.setCurrentRow(self._selected)
            self._select_intent(self._selected)
        else:
            self.result_edit.clear()
            self.var_edit.clear()

    def _select_intent(self, row):
        if row < 0 or row >= len(self._intents):
            return
        self._selected = row
        intent = self._intents[row]
        self.result_edit.setText(str(intent.get("result", "")))
        self.var_edit.setPlainText("\n".join(intent.get("variations") or []))

    def _save_intent(self):
        if not self.table_name or not (0 <= self._selected < len(self._intents)):
            return
        variations = [ln.strip() for ln in self.var_edit.toPlainText().splitlines()
                     if ln.strip()]
        data = load_semantic_table(self.table_name)
        intents = data.setdefault("intents", [])
        if self._selected < len(intents):
            intents[self._selected] = {
                "result": self.result_edit.text().strip(),
                "variations": variations,
            }
            save_semantic_table(self.table_name, data)
        self._reload()

    def _add_intent(self):
        if not self.table_name:
            return
        name, ok = QInputDialog.getText(self, "New intent", "Result name:")
        if not ok or not name.strip():
            return
        data = load_semantic_table(self.table_name)
        data.setdefault("intents", []).append(
            {"result": name.strip(), "variations": []})
        save_semantic_table(self.table_name, data)
        self._selected = len(data["intents"]) - 1
        self._reload()

    def _delete_intent(self):
        if not self.table_name or not (0 <= self._selected < len(self._intents)):
            return
        intent = self._intents[self._selected]
        if QMessageBox.question(
                self, "Delete",
                f"Delete intent '{intent.get('result')}' and its "
                f"{len(intent.get('variations') or [])} variations?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        data = load_semantic_table(self.table_name)
        intents = data.setdefault("intents", [])
        if self._selected < len(intents):
            intents.pop(self._selected)
            save_semantic_table(self.table_name, data)
        self._selected = -1
        self._reload()

    def _test_match(self):
        """Try a phrase against the whole table right here, so you can see
        straight away whether a new variation actually helps -- rather than
        running a workflow to find out."""
        if not self.table_name:
            return
        q = self.test_edit.text().strip()
        if not q:
            return
        table = load_semantic_table(self.table_name)
        hits = semantic_search.search(q, table, threshold=0.0, top_k=3)
        if not hits:
            self.test_result.setText("no intents in this table yet")
            return
        best = hits[0]
        lines = [f"\u2192 {best['result']}   ({best['confidence']:.2f}  via "
                f"\"{best['matched']}\")"]
        if len(hits) > 1:
            runners = ", ".join(
                f"{h['result']} {h['confidence']:.2f}" for h in hits[1:])
            lines.append(f"   next closest: {runners}")
        if best["confidence"] < 0.45:
            lines.append("   below the usual 0.45 threshold \u2014 add this "
                        "phrasing as a variation")
        self.test_result.setText("\n".join(lines))
