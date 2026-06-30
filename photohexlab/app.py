from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photohex.document import PhotoHexDocument
from photohex.jpeg_parser import JpegSegment, segment_for_offset

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SAMPLE_PATH = ROOT / "samples" / "sample_gradient.jpg"
BYTES_PER_ROW = 16
ROWS = 16

SEGMENT_COLORS = {
    "SOI": QColor(90, 45, 45),
    "EOI": QColor(90, 45, 45),
    "APP0": QColor(38, 65, 70),
    "APP1": QColor(38, 65, 70),
    "DQT": QColor(80, 65, 30),
    "DHT": QColor(78, 42, 70),
    "SOF0": QColor(42, 70, 45),
    "SOF2": QColor(42, 70, 45),
    "SOS": QColor(60, 55, 95),
    "Scan Data": QColor(86, 88, 0),
}
CHANGED_COLOR = QColor(120, 85, 0)
PROTECTED_COLOR = QColor(75, 35, 35)


@dataclass
class SavedByteControl:
    offset: int
    row: int
    value_label: QLabel
    slider: QSlider


def ensure_sample() -> Path:
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SAMPLE_PATH.exists():
        return SAMPLE_PATH
    w, h = 960, 620
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    for y in range(h):
        for x in range(w):
            r = int(60 + 150 * x / w)
            g = int(30 + 180 * y / h)
            b = int(180 - 90 * x / w + 40 * y / h)
            img.putpixel((x, y), (r, g, max(0, min(255, b))))
    for i in range(0, w, 80):
        draw.line((i, 0, w - i // 2, h), fill=(240, 240, 220), width=2)
    draw.rectangle((64, 64, 500, 230), outline=(255, 255, 255), width=4)
    draw.text((84, 90), "PHOTOHEX LAB", fill=(255, 255, 255))
    draw.text((84, 130), "edit bytes -> decode preview -> log observations", fill=(255, 255, 255))
    img.save(SAMPLE_PATH, quality=92)
    return SAMPLE_PATH


def pil_to_pixmap(img: Image.Image, max_size: tuple[int, int]) -> QPixmap:
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
    pixmap = QPixmap.fromImage(qimg)
    return pixmap.scaled(max_size[0], max_size[1], Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class MainWindow(QMainWindow):
    def __init__(self, auto_test: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("PhotoHex Lab MVP")
        self.resize(1680, 980)
        self._updating_table = False
        self._updating_slider = False
        self.current_page_offset = 0
        self.doc = PhotoHexDocument(ensure_sample())
        self.saved_bytes: list[SavedByteControl] = []

        self.segment_tree = QTreeWidget()
        self.segment_tree.setHeaderLabels(["Segment", "Range", "Risk", "Details"])
        self.segment_tree.itemClicked.connect(self.on_segment_clicked)

        self.hex_table = QTableWidget(ROWS, BYTES_PER_ROW + 2)
        headers = ["Offset"] + [f"{i:X}" for i in range(BYTES_PER_ROW)] + ["ASCII"]
        self.hex_table.setHorizontalHeaderLabels(headers)
        self.hex_table.verticalHeader().setVisible(False)
        self.hex_table.itemChanged.connect(self.on_hex_item_changed)
        self.hex_table.itemSelectionChanged.connect(self.update_range_from_selection)
        self.hex_table.setMinimumWidth(900)
        self.hex_table.setColumnWidth(0, 82)
        for i in range(1, BYTES_PER_ROW + 1):
            self.hex_table.setColumnWidth(i, 46)
        self.hex_table.setColumnWidth(BYTES_PER_ROW + 1, 155)

        self.original_label = QLabel()
        self.mutated_label = QLabel()
        for label in (self.original_label, self.mutated_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumSize(390, 260)
            label.setStyleSheet("QLabel { background: #111; border: 1px solid #444; }")
        self.decode_status = QLabel("Ready")

        self.log_table = QTableWidget(0, 7)
        self.log_table.setHorizontalHeaderLabels(["Offset", "Before", "After", "Region", "Result", "Note", "Time"])

        open_btn = QPushButton("Open JPEG")
        reset_btn = QPushButton("Reset")
        save_btn = QPushButton("Save Mutated")
        save_log_btn = QPushButton("Save Log")
        undo_btn = QPushButton("Undo Last")
        prev_btn = QPushButton("Prev Page")
        next_btn = QPushButton("Next Page")
        jump_btn = QPushButton("Jump")
        patch_btn = QPushButton("Patch Byte")
        save_byte_btn = QPushButton("Save Offset Slider")
        apply_range_btn = QPushButton("Apply Range Op")

        open_btn.clicked.connect(self.open_image)
        reset_btn.clicked.connect(self.reset_document)
        save_btn.clicked.connect(self.save_mutated)
        save_log_btn.clicked.connect(self.save_log)
        undo_btn.clicked.connect(self.undo_last)
        prev_btn.clicked.connect(lambda: self.page_delta(-ROWS * BYTES_PER_ROW))
        next_btn.clicked.connect(lambda: self.page_delta(ROWS * BYTES_PER_ROW))
        jump_btn.clicked.connect(self.jump_to_offset)
        patch_btn.clicked.connect(self.patch_byte_from_form)
        save_byte_btn.clicked.connect(self.save_current_offset_slider)
        apply_range_btn.clicked.connect(self.apply_range_operation_from_form)

        self.jump_edit = QLineEdit("0")
        self.offset_edit = QLineEdit("0")
        self.value_edit = QLineEdit("FF")
        self.note_edit = QLineEdit("")

        self.range_start_edit = QLineEdit("0")
        self.range_end_edit = QLineEdit("0")
        self.range_value_edit = QLineEdit("10")
        self.operation_combo = QComboBox()
        self.operation_combo.addItems(["xor", "add", "sub", "set"])
        self.quick_set_combo = QComboBox()
        self.quick_set_combo.addItems(["", "00", "7F", "80", "FE"])
        self.quick_set_combo.currentTextChanged.connect(self.quick_set_changed)
        self.skip_ff_check = QCheckBox("skip FF bytes")
        self.skip_ff_check.setChecked(True)
        self.protect_markers_check = QCheckBox("protect markers/lengths")
        self.protect_markers_check.setChecked(True)

        topbar = QHBoxLayout()
        for w in (open_btn, reset_btn, save_btn, save_log_btn, undo_btn, prev_btn, next_btn):
            topbar.addWidget(w)
        topbar.addStretch(1)
        topbar.addWidget(QLabel("Jump offset:"))
        topbar.addWidget(self.jump_edit)
        topbar.addWidget(jump_btn)

        patch_group = QGroupBox("Single-byte patch")
        patch_form = QFormLayout()
        patch_form.addRow("Patch offset", self.offset_edit)
        patch_form.addRow("New byte", self.value_edit)
        patch_form.addRow("Note", self.note_edit)
        patch_form.addRow(patch_btn)
        patch_form.addRow(save_byte_btn)
        patch_group.setLayout(patch_form)

        range_group = QGroupBox("Selected/range byte operations")
        range_form = QFormLayout()
        range_form.addRow("Start", self.range_start_edit)
        range_form.addRow("End", self.range_end_edit)
        range_form.addRow("Operation", self.operation_combo)
        range_form.addRow("Value", self.range_value_edit)
        range_form.addRow("Quick SET", self.quick_set_combo)
        range_form.addRow(self.skip_ff_check)
        range_form.addRow(self.protect_markers_check)
        range_form.addRow(apply_range_btn)
        range_group.setLayout(range_form)

        self.saved_group = QGroupBox("Saved byte sliders")
        self.saved_group.setMinimumHeight(155)
        self.saved_table = QTableWidget(0, 3)
        self.saved_table.setHorizontalHeaderLabels(["Offset", "Slider", "Value"])
        self.saved_table.verticalHeader().setVisible(False)
        self.saved_table.setColumnWidth(0, 92)
        self.saved_table.setColumnWidth(1, 300)
        self.saved_table.setColumnWidth(2, 56)
        self.saved_layout = QVBoxLayout()
        self.saved_layout.addWidget(self.saved_table)
        self.saved_group.setLayout(self.saved_layout)

        left = QVBoxLayout()
        left.addWidget(QLabel("JPEG structure"))
        left.addWidget(self.segment_tree, stretch=2)
        left.addWidget(patch_group)
        left.addWidget(range_group)
        left.addWidget(self.saved_group, stretch=2)
        left.addWidget(QLabel("Mutation log"))
        left.addWidget(self.log_table, stretch=2)

        previews = QHBoxLayout()
        orig_col = QVBoxLayout()
        mut_col = QVBoxLayout()
        orig_col.addWidget(QLabel("Original"))
        orig_col.addWidget(self.original_label)
        mut_col.addWidget(QLabel("Mutated / last valid decode"))
        mut_col.addWidget(self.mutated_label)
        previews.addLayout(orig_col)
        previews.addLayout(mut_col)

        center = QVBoxLayout()
        center.addWidget(QLabel("Hex editor — color-coded by JPEG segment / protected bytes / changed bytes"))
        center.addWidget(self.hex_table, stretch=2)
        center.addLayout(previews, stretch=1)
        center.addWidget(self.decode_status)

        root = QHBoxLayout()
        root.addLayout(left, stretch=1)
        root.addLayout(center, stretch=2)
        container = QWidget()
        wrapper = QVBoxLayout()
        wrapper.addLayout(topbar)
        wrapper.addLayout(root, stretch=1)
        container.setLayout(wrapper)
        self.setCentralWidget(container)

        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #202020; color: #e7e7e7; font-size: 13px; }
            QPushButton, QLineEdit, QComboBox { background: #303030; border: 1px solid #666; padding: 6px; }
            QPushButton:hover { background: #3c3c3c; }
            QTreeWidget, QTableWidget { background: #151515; color: #f0f0f0; border: 1px solid #555; }
            QGroupBox { border: 1px solid #555; margin-top: 8px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QHeaderView::section { background: #2f2f2f; color: #efefef; padding: 4px; border: 1px solid #555; }
            """
        )

        self.refresh_all()
        if auto_test:
            QTimer.singleShot(350, self.auto_test_sequence)

    def refresh_all(self) -> None:
        self.populate_segment_tree()
        self.populate_hex_table()
        self.refresh_previews()
        self.refresh_log_table()
        self.refresh_saved_byte_values()

    def color_for_offset(self, offset: int) -> QColor:
        if offset < len(self.doc.original_bytes) and self.doc.data[offset] != self.doc.original_bytes[offset]:
            return CHANGED_COLOR
        if self.doc.is_protected_offset(offset):
            return PROTECTED_COLOR
        seg = segment_for_offset(self.doc.segments, offset)
        if seg is None:
            return QColor(30, 30, 30)
        return SEGMENT_COLORS.get(seg.name, QColor(42, 42, 42))

    def populate_segment_tree(self) -> None:
        self.segment_tree.clear()
        for seg in self.doc.segments:
            rng = f"0x{seg.start:06X}-0x{seg.end - 1:06X}"
            item = QTreeWidgetItem([seg.name, rng, seg.risk, seg.details])
            item.setData(0, Qt.ItemDataRole.UserRole, seg.start)
            item.setBackground(0, SEGMENT_COLORS.get(seg.name, QColor(42, 42, 42)))
            self.segment_tree.addTopLevelItem(item)
        self.segment_tree.expandAll()

    def populate_hex_table(self) -> None:
        self._updating_table = True
        data = self.doc.data
        page_size = ROWS * BYTES_PER_ROW
        max_start = max(0, len(data) - page_size)
        self.current_page_offset = max(0, min(self.current_page_offset, max_start))
        for row in range(ROWS):
            base = self.current_page_offset + row * BYTES_PER_ROW
            self.hex_table.setItem(row, 0, QTableWidgetItem(f"{base:06X}"))
            ascii_chars = []
            for col in range(BYTES_PER_ROW):
                idx = base + col
                item = QTableWidgetItem("")
                if idx < len(data):
                    value = data[idx]
                    item.setText(f"{value:02X}")
                    seg = segment_for_offset(self.doc.segments, idx)
                    protected = self.doc.is_protected_offset(idx)
                    item.setToolTip(f"Offset 0x{idx:06X} | {seg.name if seg else 'Unknown'} | {'protected' if protected else 'editable'}")
                    item.setBackground(self.color_for_offset(idx))
                    if protected:
                        item.setForeground(QColor(255, 210, 210))
                    ch = chr(value) if 32 <= value < 127 else "."
                    ascii_chars.append(ch)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    ascii_chars.append(" ")
                self.hex_table.setItem(row, col + 1, item)
            ascii_item = QTableWidgetItem("".join(ascii_chars))
            ascii_item.setFlags(ascii_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.hex_table.setItem(row, BYTES_PER_ROW + 1, ascii_item)
        self._updating_table = False

    def refresh_previews(self) -> None:
        original = Image.open(self.doc.path).convert("RGB")
        mutated = self.doc.decode_image(strict=False)
        self.original_label.setPixmap(pil_to_pixmap(original, (500, 330)))
        self.mutated_label.setPixmap(pil_to_pixmap(mutated, (500, 330)))
        self.decode_status.setText(self.doc.last_decode_message + f" | file bytes {len(self.doc.data)} | edits {len(self.doc.mutations)}")

    def refresh_log_table(self) -> None:
        self.log_table.setRowCount(len(self.doc.mutations))
        for row, m in enumerate(self.doc.mutations):
            values = [f"0x{m.offset:06X}", f"{m.before:02X}", f"{m.after:02X}", m.region, m.decode_result, m.note, m.timestamp]
            for col, value in enumerate(values):
                self.log_table.setItem(row, col, QTableWidgetItem(value))

    def on_segment_clicked(self, item: QTreeWidgetItem) -> None:
        start = int(item.data(0, Qt.ItemDataRole.UserRole))
        self.current_page_offset = start - (start % BYTES_PER_ROW)
        self.populate_hex_table()
        self.jump_edit.setText(hex(start))
        self.offset_edit.setText(hex(start))
        self.range_start_edit.setText(hex(start))
        self.range_end_edit.setText(hex(start))

    def on_hex_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table:
            return
        row, col = item.row(), item.column()
        if col == 0 or col == BYTES_PER_ROW + 1:
            return
        offset = self.current_page_offset + row * BYTES_PER_ROW + (col - 1)
        text = item.text().strip().upper()
        try:
            value = int(text, 16)
            if value < 0 or value > 255 or len(text) > 2:
                raise ValueError
        except Exception:
            self.status_message(f"Invalid byte: {text}")
            self.populate_hex_table()
            return
        if self.protect_markers_check.isChecked() and self.doc.is_protected_offset(offset):
            self.status_message(f"Protected byte not edited: 0x{offset:06X}")
            self.populate_hex_table()
            return
        if self.skip_ff_check.isChecked() and value == 0xFF:
            self.status_message("FF output skipped; use FE or disable skip FF")
            self.populate_hex_table()
            return
        note = self.note_edit.text().strip()
        self.doc.edit_byte(offset, value, note=note)
        self.current_page_offset = max(0, offset - (offset % BYTES_PER_ROW))
        self.refresh_all()

    def update_range_from_selection(self) -> None:
        if self._updating_table:
            return
        offsets = []
        for item in self.hex_table.selectedItems():
            if 1 <= item.column() <= BYTES_PER_ROW:
                offsets.append(self.current_page_offset + item.row() * BYTES_PER_ROW + (item.column() - 1))
        if offsets:
            self.range_start_edit.setText(hex(min(offsets)))
            self.range_end_edit.setText(hex(max(offsets)))
            self.offset_edit.setText(hex(offsets[0]))

    def status_message(self, text: str) -> None:
        self.decode_status.setText(text)

    def page_delta(self, delta: int) -> None:
        self.current_page_offset = max(0, self.current_page_offset + delta)
        self.populate_hex_table()

    def parse_offset_text(self, text: str) -> int:
        text = text.strip().lower()
        return int(text, 16) if text.startswith("0x") else int(text, 0)

    def parse_byte_text(self, text: str) -> int:
        text = text.strip().lower().replace("0x", "")
        value = int(text, 16)
        if not 0 <= value <= 255:
            raise ValueError("Byte must be 00-FF")
        return value

    def jump_to_offset(self) -> None:
        try:
            offset = self.parse_offset_text(self.jump_edit.text())
            self.current_page_offset = max(0, offset - (offset % BYTES_PER_ROW))
            self.populate_hex_table()
        except Exception as exc:
            QMessageBox.warning(self, "Jump error", str(exc))

    def patch_byte_from_form(self) -> None:
        try:
            offset = self.parse_offset_text(self.offset_edit.text())
            value = self.parse_byte_text(self.value_edit.text())
            if self.protect_markers_check.isChecked() and self.doc.is_protected_offset(offset):
                raise ValueError("Protected marker/length byte. Disable protection only for deliberate destructive tests.")
            if self.skip_ff_check.isChecked() and value == 0xFF:
                raise ValueError("FF output blocked by skip-FF option")
            note = self.note_edit.text().strip()
            self.doc.edit_byte(offset, value, note)
            self.current_page_offset = max(0, offset - (offset % BYTES_PER_ROW))
            self.refresh_all()
        except Exception as exc:
            QMessageBox.warning(self, "Patch error", str(exc))

    def quick_set_changed(self, value: str) -> None:
        if value:
            self.operation_combo.setCurrentText("set")
            self.range_value_edit.setText(value)

    def apply_range_operation_from_form(self) -> None:
        try:
            start = self.parse_offset_text(self.range_start_edit.text())
            end = self.parse_offset_text(self.range_end_edit.text())
            op = self.operation_combo.currentText()
            operand = self.parse_byte_text(self.range_value_edit.text())
            mutations = self.doc.apply_range_operation(
                start,
                end,
                op,
                operand,
                skip_ff=self.skip_ff_check.isChecked(),
                protect_markers=self.protect_markers_check.isChecked(),
                note=self.note_edit.text().strip(),
            )
            self.current_page_offset = max(0, min(start, end) - (min(start, end) % BYTES_PER_ROW))
            self.refresh_all()
            self.status_message(f"Applied {op.upper()} to {len(mutations)} bytes | {self.doc.last_decode_message}")
        except Exception as exc:
            QMessageBox.warning(self, "Range operation error", str(exc))

    def save_current_offset_slider(self) -> None:
        try:
            offset = self.parse_offset_text(self.offset_edit.text())
            if not 0 <= offset < len(self.doc.data):
                raise ValueError("Offset out of range")
            if any(ctrl.offset == offset for ctrl in self.saved_bytes):
                raise ValueError("Offset already saved")
            row = self.saved_table.rowCount()
            self.saved_table.insertRow(row)
            value_label = QLabel(f"{self.doc.data[offset]:02X}")
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 255)
            slider.setValue(self.doc.data[offset])
            slider.valueChanged.connect(lambda value, off=offset: self.slider_value_changed(off, value))
            self.saved_table.setItem(row, 0, QTableWidgetItem(f"0x{offset:06X}"))
            self.saved_table.setCellWidget(row, 1, slider)
            self.saved_table.setCellWidget(row, 2, value_label)
            self.saved_bytes.append(SavedByteControl(offset, row, value_label, slider))
            self.status_message(f"Saved slider for 0x{offset:06X}")
        except Exception as exc:
            QMessageBox.warning(self, "Save slider error", str(exc))

    def slider_value_changed(self, offset: int, value: int) -> None:
        if self._updating_slider:
            return
        try:
            if self.protect_markers_check.isChecked() and self.doc.is_protected_offset(offset):
                self.status_message(f"Protected byte not edited: 0x{offset:06X}")
                self.refresh_saved_byte_values()
                return
            if self.skip_ff_check.isChecked() and value == 0xFF:
                value = 0xFE
            self.doc.edit_byte(offset, value, note=f"slider 0x{offset:06X}")
            self.current_page_offset = max(0, offset - (offset % BYTES_PER_ROW))
            self.refresh_all()
        except Exception as exc:
            self.status_message(str(exc))

    def refresh_saved_byte_values(self) -> None:
        self._updating_slider = True
        for ctrl in self.saved_bytes:
            if 0 <= ctrl.offset < len(self.doc.data):
                value = self.doc.data[ctrl.offset]
                ctrl.value_label.setText(f"{value:02X}")
                ctrl.slider.setValue(value)
        self._updating_slider = False

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open JPEG", str(ROOT), "JPEG images (*.jpg *.jpeg)")
        if path:
            self.doc = PhotoHexDocument(path)
            self.current_page_offset = 0
            self.saved_bytes.clear()
            self.saved_table.setRowCount(0)
            self.refresh_all()

    def reset_document(self) -> None:
        self.doc.reset()
        self.current_page_offset = 0
        self.refresh_all()

    def save_mutated(self) -> None:
        OUTPUT_DIR.mkdir(exist_ok=True)
        out = OUTPUT_DIR / f"mutated_{self.doc.path.stem}.jpg"
        self.doc.save_mutated(out)
        self.status_message(f"Saved mutated JPEG to {out}")

    def save_log(self) -> None:
        OUTPUT_DIR.mkdir(exist_ok=True)
        out = OUTPUT_DIR / f"mutation_log_{self.doc.path.stem}.json"
        self.doc.save_log(out)
        self.status_message(f"Saved log to {out}")

    def undo_last(self) -> None:
        undone = self.doc.undo_last()
        if undone:
            self.current_page_offset = max(0, undone.offset - (undone.offset % BYTES_PER_ROW))
            self.refresh_all()

    def auto_test_sequence(self) -> None:
        scan_seg = next((s for s in self.doc.segments if s.name == "Scan Data"), None)
        if scan_seg and scan_seg.start + 80 < scan_seg.end:
            start = scan_seg.start + 32
            end = start + 12
            self.range_start_edit.setText(hex(start))
            self.range_end_edit.setText(hex(end))
            self.operation_combo.setCurrentText("xor")
            self.range_value_edit.setText("10")
            self.note_edit.setText("auto-test XOR selected scan-data range")
            self.apply_range_operation_from_form()
            self.offset_edit.setText(hex(start))
            self.save_current_offset_slider()
            first_slider = self.saved_bytes[0].slider
            first_slider.setValue((first_slider.value() + 7) & 0xFE)
        OUTPUT_DIR.mkdir(exist_ok=True)
        self.grab().save(str(OUTPUT_DIR / "ui_capture.png"))
        self.save_mutated()
        self.save_log()
        QApplication.instance().quit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-test", action="store_true")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    window = MainWindow(auto_test=args.auto_test)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
