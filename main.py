import sys
import os
import io
import re
import sqlite3
import pandas as pd
import json
from datetime import datetime

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QStackedWidget, 
                             QLineEdit, QMessageBox, QFrame, QCheckBox, QSpacerItem, 
                             QSizePolicy, QFileDialog, QTableWidget, QTableWidgetItem,
                             QComboBox, QHeaderView, QGroupBox, QFormLayout, QMenu,
                             QStyle, QStyleOptionHeader, QDialog, QInputDialog, QSpinBox, QDoubleSpinBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QRect, QPointF, QPoint, QCoreApplication, QTimer
from PyQt6.QtGui import QIcon, QAction, QPainter, QColor, QPixmap, QImage, QFontMetrics, QPageLayout, QPageSize, QFont
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog, QPrinterInfo

# ReportLab & Barcode Imports
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import barcode
from barcode.writer import ImageWriter
# Removed unused import requests

# Attempt to register Chinese Font
CHINESE_FONT_NAME = "Helvetica" # Fallback
try:
    # Try Microsoft YaHei first (Standard on Win 10/11)
    font_path = "C:/Windows/Fonts/msyh.ttc"
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont('MsYaHei', font_path))
        CHINESE_FONT_NAME = 'MsYaHei'
    else:
        # Try SimHei
        font_path = "C:/Windows/Fonts/simhei.ttf"
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('SimHei', font_path))
            CHINESE_FONT_NAME = 'SimHei'
except Exception as e:
    print(f"Font loading warning: {e}")

CHINESE_FONT_PATH = "C:/Windows/Fonts/msyh.ttc"  # Microsoft YaHei

class FilterHeader(QHeaderView):
    filterClicked = pyqtSignal(int, QPoint)

    def __init__(self, orientation, parent, target_table_widget):
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self.target_table_widget = target_table_widget
        
    def paintSection(self, painter, rect, logicalIndex):
        painter.save()
        super().paintSection(painter, rect, logicalIndex)
        painter.restore()

        # Skip icon for last three columns (Preview, Status, Action)
        if logicalIndex >= self.model().columnCount() - 3:
            return

        # Draw Filter Icon (Manual Polygon)
        # ... (Same logic, simple icon)
        
        # Check filter status
        # Since this class is reusable, we need access to 'column_filters'
        # Pass a callback or access parent? Parent might be QTableWidget. 
        # The filter logic currently in MainWindow relies on 'self.table'. 
        # History table probably doesn't need filtering yet, or if it does, duplication is needed.
        # For now, let's keep drawing the icon.
        
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        
        # Position right side
        icon_size = 12
        margin = 5
        x = rect.right() - icon_size - margin
        y = rect.center().y() - icon_size // 2
        
        # Draw Funnel
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#888"))
        painter.setPen(Qt.PenStyle.NoPen)
        
        points = [
            QPointF(x, y),
            QPointF(x + icon_size, y),
            QPointF(x + icon_size // 2 + 2, y + icon_size // 2 + 2), # Stem
            QPointF(x + icon_size // 2 + 2, y + icon_size),
            QPointF(x + icon_size // 2 - 2, y + icon_size),
            QPointF(x + icon_size // 2 - 2, y + icon_size // 2 + 2)
        ]
        painter.drawPolygon(points)
        painter.restore()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.RightButton:
            return # Let context menu handle?
        
        # Check if click is on the filter icon area (rightmost 25px)
        logicalIndex = self.logicalIndexAt(event.pos())
        
        # Disable interaction for Last Three Columns (Preview, Status, Action)
        if logicalIndex >= self.model().columnCount() - 3:
            return

        if logicalIndex >= 0:
            # Manual rect calculation
            pos = self.sectionViewportPosition(logicalIndex)
            width = self.sectionSize(logicalIndex)
            rect = QRect(pos, 0, width, self.height())
            if event.pos().x() > rect.right() - 25:
                # Trigger Filter Signal
                self.filterClicked.emit(logicalIndex, self.mapToGlobal(event.pos()))

class BarcodePreviewThread(QThread):
    batch_ready = pyqtSignal(list) # List of (row_index, QPixmap)

    def __init__(self, tasks, column_mapping, barcode_source, qty_source, paper_type):
        super().__init__()
        self.tasks = tasks # List of (row_index, item_data_dict)
        self.column_mapping = column_mapping
        self.barcode_source = barcode_source
        self.qty_source = qty_source
        self.paper_type = paper_type
        self._is_running = True

    def run(self):
        batch = []
        try:
            Code = barcode.get_barcode_class('code128')
            writer = ImageWriter()
            # Basic options (no text support needed in lib as we draw manually)
            options = {
                "module_width": 0.3, 
                "module_height": 10, 
                "quiet_zone": 1, 
                "font_size": 1, 
                "text_distance": 0,
                "write_text": False
            }
        except Exception:
            return
            
        # Preview dimensions
        is_100x100 = "100x100" in self.paper_type
        
        # Fixed Canvas Size for Preview
        if is_100x100:
            w, h = 300, 300 # 1:1 Aspect Ratio
            base_font_size = 10
            inv_font_size = 14
        else:
            # Default or 70x40 (1.75 ratio) or A4 (approx 70x40 labels)
            w, h = 280, 160 # 1.75 Ratio (70:40)
            base_font_size = 9
            inv_font_size = 10

        for row_idx, item_data in self.tasks:
            if not self._is_running:
                break
            
            painter = None
            try:
                # 1. Prepare Content
                barcode_val = str(item_data.get(self.barcode_source, ""))
                
                # Normalize SKU
                normalized_sku = ""
                for char in barcode_val:
                    code_point = ord(char)
                    if 0xFF01 <= code_point <= 0xFF5E:
                        normalized_sku += chr(code_point - 0xFEE0)
                    elif code_point == 0x3000:
                        normalized_sku += " "
                    else:
                        normalized_sku += char
                barcode_content = re.sub(r'[^\x00-\x7F]+', '', normalized_sku).strip()
                if not barcode_content: 
                    barcode_content = "INVALID"

                # Find INV and Footer
                inv_val = ""
                inv_key = ""
                for k in item_data.keys():
                    if "INV" in k.upper():
                        inv_val = str(item_data[k])
                        inv_key = k
                        break
                
                footer_fields = []
                for k, v in item_data.items():
                    if k == self.qty_source: continue
                    if k == inv_key: continue
                    # Explicitly include barcode source in footer if it exists as text?
                    # Generally yes, unless it's strictly barcode-only. 
                    
                    # Auto-append Week Number to P/I
                    if k == "P/I":
                        try:
                            week_num = datetime.now().isocalendar()[1]
                            v = f"{v}  {week_num}"
                        except:
                            pass

                    priority = 10
                    for m in self.column_mapping:
                        if m["name"] == k:
                            priority = m.get("order", 10)
                            break
                    footer_fields.append((priority, str(v)))
                
                footer_fields.sort(key=lambda x: x[0])
                footer_texts = [f[1] for f in footer_fields]

                # --- PRE-CALCULATE BARCODE (Risk Zone) ---
                rv = io.BytesIO()
                code = Code(barcode_content, writer=writer)
                code.write(rv, options=options)
                bc_img_data = rv.getvalue()

                # 2. Draw Label
                image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.white)
                
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.GlobalColor.black)
                
                # Logic to match ReportLab (mm to px conversion)
                # Preview Thread Resolutions:
                # 100x100mm -> 300x300px (3 px/mm)
                # 70x40mm   -> 280x160px (4 px/mm)
                
                short_side = min(w, h)
                px_per_mm = short_side / 100.0 if is_100x100 else short_side / 40.0
                
                # Constants (Must match draw_label in PDF)
                if is_100x100:
                    bc_h_mm = 30
                    bc_w_mm = 84
                    font_inv_pt = 30
                    font_info_pt = 17
                    gap_mm = 2
                else:
                    bc_h_mm = 12
                    bc_w_mm = 60
                    font_inv_pt = 14
                    font_info_pt = 10
                    gap_mm = 1
                
                # Convert to Pixels
                # 1 pt = 0.3527 mm
                # Pixel Size = Point Size * 0.3527 * px_per_mm
                pt_to_mm = 0.352778
                
                size_inv_px = max(1, int(font_inv_pt * pt_to_mm * px_per_mm))
                size_info_px = max(1, int(font_info_pt * pt_to_mm * px_per_mm))
                line_spacing_px = size_info_px + int(4 * 0.35 * px_per_mm) # approx +4pt spacing
                
                bc_h_px = int(bc_h_mm * px_per_mm)
                bc_w_px = int(bc_w_mm * px_per_mm)
                gap_px = int(gap_mm * px_per_mm)

                # Font Config
                font_normal = QFont()
                font_normal.setPixelSize(size_info_px)
                painter.setFont(font_normal)
                
                # Calculate Footer Height
                h_footer = len(footer_texts) * line_spacing_px
                
                font_inv = QFont()
                font_inv.setPixelSize(size_inv_px)
                font_inv.setBold(True)
                fm_inv = QFontMetrics(font_inv)
                h_inv = fm_inv.height() if inv_val else 0
                
                # Total Content Height
                # Stack: [INV] - gap - [Barcode] - gap - [Footer Stack]
                total_h = h_inv + (gap_px if inv_val else 0) + bc_h_px + gap_px + h_footer
                
                # Start Y (Centered)
                start_y = (h - total_h) / 2
                curr_y = start_y
                
                # A. Draw INV
                if inv_val:
                    painter.setFont(font_inv)
                    # Adjust Rect for text centering
                    rect = QRect(0, int(curr_y), w, h_inv)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, inv_val)
                    curr_y += h_inv + gap_px
                    
                # B. Draw Barcode
                bc_img = QImage()
                bc_img.loadFromData(bc_img_data)
                
                target_rect = QRect(int((w - bc_w_px)/2), int(curr_y), int(bc_w_px), int(bc_h_px))
                painter.drawImage(target_rect, bc_img.scaled(target_rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                
                curr_y += bc_h_px + gap_px
                
                # C. Draw Footer
                # PDF Logic: Reversed loop from Bottom (PI) to Top (SKU).
                # Here we draw Top Down.
                # footer_texts is sorted by Priority (0=SKU, 20=PI).
                # We want SKU at Top of Footer, PI at Bottom.
                # So we simply iterate forward: 0, 1, 2...
                
                painter.setFont(font_normal)
                for text in footer_texts:
                    rect = QRect(0, int(curr_y), w, line_spacing_px)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
                    curr_y += line_spacing_px
                
                painter.end()
                painter = None
 
                batch.append((row_idx, image))
                
                if len(batch) >= 5:
                    self.batch_ready.emit(batch)
                    batch = []
                    QThread.msleep(10)
                    
                    
            except Exception as e:
                # print(f"Preview Gen Error: {e}")
                if painter and painter.isActive():
                    painter.end()
                continue
                
        if batch:
            self.batch_ready.emit(batch)

    def stop(self):
        self._is_running = False
        self.wait()


# --- Database Abstraction ---

class AbstractDatabase:
    def add_batch(self, filename): raise NotImplementedError
    def add_item(self, batch_id, content_data): raise NotImplementedError
    def update_item_status(self, item_id, status): raise NotImplementedError
    def get_batches(self): raise NotImplementedError
    def get_batch_items(self, batch_id): raise NotImplementedError
    def delete_batch(self, batch_id): raise NotImplementedError

class LocalDatabase(AbstractDatabase):
    def __init__(self, db_name="barcode_history.db"):
        self.conn = sqlite3.connect(db_name)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER,
                content_json TEXT,
                status TEXT DEFAULT '⏳',
                FOREIGN KEY(batch_id) REFERENCES batches(id)
            )
        ''')
        self.conn.commit()

    def add_batch(self, filename):
        cursor = self.conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO batches (filename, imported_at) VALUES (?, ?)", (filename, now_str))
        self.conn.commit()
        return cursor.lastrowid

    def add_item(self, batch_id, content_data):
        cursor = self.conn.cursor()
        json_str = json.dumps(content_data, ensure_ascii=False)
        cursor.execute("INSERT INTO batch_items (batch_id, content_json, status) VALUES (?, ?, ?)", 
                       (batch_id, json_str, '⏳'))
        self.conn.commit()
        return cursor.lastrowid

    def update_item_status(self, item_id, status):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE batch_items SET status = ? WHERE id = ?", (status, item_id))
        self.conn.commit()

    def get_batches(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, filename, imported_at FROM batches ORDER BY id DESC")
        return cursor.fetchall()

    def get_batch_items(self, batch_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, content_json, status FROM batch_items WHERE batch_id = ?", (batch_id,))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "data": json.loads(r[1]),
                "status": r[2]
            })
        return result

    def delete_batch(self, batch_id):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM batch_items WHERE batch_id = ?", (batch_id,))
        cursor.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
        self.conn.commit()

class SupabaseDatabase(AbstractDatabase):
    def __init__(self, url, key):
        self.enabled = False
        try:
            from supabase import create_client, Client
            self.client: Client = create_client(url, key)
            self.enabled = True
        except ImportError:
            print("Supabase lib not installed. Run: pip install supabase")
        except Exception as e:
            print(f"Supabase Connection Error: {e}")

    def add_batch(self, filename):
        if not self.enabled: return 0
        try:
            # Use client's local time with timezone info to ensure accuracy
            now_iso = datetime.now().astimezone().isoformat()
            data = {"filename": filename, "imported_at": now_iso}
            res = self.client.table("batches").insert(data).execute()
            if res.data:
                return res.data[0]['id']
        except Exception as e:
            print(f"Supabase Add Batch Error: {e}")
        return 0

    def add_item(self, batch_id, content_data):
        if not self.enabled: return 0
        try:
            json_str = json.dumps(content_data, ensure_ascii=False)
            data = {"batch_id": batch_id, "content_json": json_str, "status": '⏳'}
            res = self.client.table("batch_items").insert(data).execute()
            if res.data:
                return res.data[0]['id']
        except Exception as e:
            print(f"Supabase Add Item Error: {e}")
        return 0

    def update_item_status(self, item_id, status):
        if not self.enabled: return
        try:
            self.client.table("batch_items").update({"status": status}).eq("id", item_id).execute()
        except: pass

    def get_batches(self):
        if not self.enabled: return []
        try:
            res = self.client.table("batches").select("*").order("id", desc=True).execute()
            # Convert to tuple list to match local interface: (id, filename, imported_at)
            results = []
            for r in res.data:
                # Format Timestamp (ISO -> YYYY-MM-DD HH:MM:SS)
                ts_str = r['imported_at']
                try:
                    # Supabase returns ISO 8601 (e.g., 2026-02-03T11:57:10+00:00)
                    if "T" in str(ts_str):
                        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        # Convert to Local (naive) for display consistency with LocalDB
                        ts_str = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass # Keep original if parse fails
                
                results.append((r['id'], r['filename'], ts_str))
            return results
        except Exception as e:
            print(f"Supabase Get Batches Error: {e}")
            return []

    def get_batch_items(self, batch_id):
        if not self.enabled: return []
        try:
            res = self.client.table("batch_items").select("*").eq("batch_id", batch_id).execute()
            result = []
            for r in res.data:
                result.append({
                    "id": r['id'],
                    "data": json.loads(r['content_json']),
                    "status": r['status']
                })
            return result
        except Exception as e:
            print(f"Supabase Get Items Error: {e}")
            return []

    def delete_batch(self, batch_id):
        if not self.enabled: return
        try:
            self.client.table("batch_items").delete().eq("batch_id", batch_id).execute()
            self.client.table("batches").delete().eq("id", batch_id).execute()
        except: pass

class DatabaseManagerWrapper:
    def __init__(self, settings, parent=None):
        self.settings = settings
        self.parent = parent
        self.local_db = LocalDatabase()
        self.cloud_db = None
        self.use_cloud = False
        self.reload_config()

    def reload_config(self):
        self.use_cloud = self.settings.value("cloud_enabled", "false") == "true"
        url = self.settings.value("cloud_url", "")
        key = self.settings.value("cloud_key", "")
        
        if self.use_cloud and url and key:
            self.cloud_db = SupabaseDatabase(url, key)
            if not self.cloud_db.enabled and self.parent:
                # Notify only if intended to use but failed impoort
                 pass 
        else:
            self.cloud_db = None

    @property
    def active_db(self):
        if self.use_cloud and self.cloud_db and self.cloud_db.enabled:
            return self.cloud_db
        return self.local_db

    def __getattr__(self, name):
        return getattr(self.active_db, name)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Init DB - Moved after settings load

        self.setWindowTitle("批量条码生成工具")
        self.resize(1000, 700) # Slightly larger for table

        # Data Storage
        # Data Storage
        self.df = None
        self.preview_thread = None
        self.column_mapping = [] # Initialize empty
        self.barcode_source = ""
        self.qty_source = ""
        
        # Settings
        self.settings = QSettings("MyCompany", "BarcodeGenerator")
        self.load_settings()

        # Init DB Wrapper
        self.db = DatabaseManagerWrapper(self.settings, self)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main Layout (Horizontal)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Left Sidebar ---
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 20, 10, 20)
        sidebar_layout.setSpacing(15)

        # Navigation Buttons
        self.btn_home = QPushButton("首页")
        self.btn_history = QPushButton("历史记录")
        self.btn_settings = QPushButton("设置")
        
        for btn in [self.btn_home, self.btn_history, self.btn_settings]:
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            sidebar_layout.addWidget(btn)

        # --- Sidebar: Print Settings Area ---
        # Adding a separator or spacing
        sidebar_layout.addSpacing(20)
        
        self.print_group = QGroupBox("打印设置")
        self.print_group.setObjectName("PrintSettingsGroup")
        print_layout = QVBoxLayout(self.print_group)
        
        lbl_paper = QLabel("选择纸张:")
        self.combo_paper = QComboBox()
        self.combo_paper.addItems(["A4 (21个/页 3x7矩阵)", "热敏纸 (70x40mm)", "热敏纸 (100x100mm)"])

        # Restore saved paper setting
        last_paper = self.settings.value("last_paper_type", "热敏纸 (100x100mm)")
        idx = self.combo_paper.findText(last_paper)
        if idx >= 0:
            self.combo_paper.setCurrentIndex(idx)
        else:
            self.combo_paper.setCurrentIndex(2) # Default fallback
            
        self.combo_paper.setCursor(Qt.CursorShape.PointingHandCursor)
        self.combo_paper.setCursor(Qt.CursorShape.PointingHandCursor)
        self.combo_paper.setCursor(Qt.CursorShape.PointingHandCursor)
        self.combo_paper.currentTextChanged.connect(lambda text: self.settings.setValue("last_paper_type", text))
        # Regenerate previews on change
        self.combo_paper.currentIndexChanged.connect(self.regenerate_previews)
        # Regenerate previews on change
        self.combo_paper.currentIndexChanged.connect(self.regenerate_previews)
        
        print_layout.addWidget(lbl_paper)
        print_layout.addWidget(self.combo_paper)
        
        # Printer Selection
        lbl_printer = QLabel("选择打印机:")
        self.combo_printer = QComboBox()
        self.combo_printer.addItem("系统默认 (System Default)")
        self.combo_printer.addItems(QPrinterInfo.availablePrinterNames())
        
        # Restore saved printer
        saved_printer = self.settings.value("target_printer_name", "系统默认 (System Default)")
        idx = self.combo_printer.findText(saved_printer)
        if idx >= 0: self.combo_printer.setCurrentIndex(idx)
        self.combo_printer.currentTextChanged.connect(lambda text: self.settings.setValue("target_printer_name", text))
        
        print_layout.addWidget(lbl_printer)
        print_layout.addWidget(self.combo_printer)
        
        # Margins (X, Y)
        lbl_margin = QLabel("偏移调整 (mm):")
        margin_layout = QHBoxLayout()
        
        # Margin X
        self.spin_margin_x = QDoubleSpinBox()
        self.spin_margin_x.setRange(-50.0, 50.0)
        self.spin_margin_x.setSuffix(" mm")
        self.spin_margin_x.setSingleStep(0.5)
        self.spin_margin_x.setValue(float(self.settings.value("print_margin_x", 0.0)))
        self.spin_margin_x.valueChanged.connect(lambda val: self.settings.setValue("print_margin_x", val))
        self.spin_margin_x.setToolTip("X轴偏移 (+)向右, (-)向左")
        
        # Margin Y
        self.spin_margin_y = QDoubleSpinBox()
        self.spin_margin_y.setRange(-50.0, 50.0)
        self.spin_margin_y.setSuffix(" mm")
        self.spin_margin_y.setSingleStep(0.5)
        self.spin_margin_y.setValue(float(self.settings.value("print_margin_y", 0.0)))
        self.spin_margin_y.valueChanged.connect(lambda val: self.settings.setValue("print_margin_y", val))
        self.spin_margin_y.setToolTip("Y轴偏移 (+)向下, (-)向上")

        margin_layout = QVBoxLayout()
        
        # X Row
        row_x = QHBoxLayout()
        row_x.addWidget(QLabel("X:"))
        row_x.addWidget(self.spin_margin_x)
        margin_layout.addLayout(row_x)
        
        # Y Row
        row_y = QHBoxLayout()
        row_y.addWidget(QLabel("Y:"))
        row_y.addWidget(self.spin_margin_y)
        margin_layout.addLayout(row_y)
        
        print_layout.addWidget(lbl_margin)
        print_layout.addLayout(margin_layout)
        
        sidebar_layout.addWidget(self.print_group)

        # Spacer
        sidebar_layout.addItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        # --- Right Content Area ---
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("ContentArea")

        # Page 1: Home
        self.page_home = QWidget()
        self.column_filters = {} # {col_index: set(allowed_values)}
        
        self.setup_home_page()
        self.content_stack.addWidget(self.page_home)


        
        # Page 2: History
        self.page_history = QWidget()
        self.setup_history_page()
        self.content_stack.addWidget(self.page_history)

        # Page 3: Settings (Placeholder)
        self.page_settings = QWidget()
        
        # Settings Tabs
        self.settings_tabs = QStackedWidget() # Use internal stack or QTabWidget
        # Let's use QTabWidget for Settings (Misc, Cloud)
        from PyQt6.QtWidgets import QTabWidget
        self.tab_widget = QTabWidget()
        
        self.tab_general = QWidget()
        self.setup_settings_page_general(self.tab_general)
        self.tab_widget.addTab(self.tab_general, "常规设置 (General)")
        
        self.tab_cloud = QWidget()
        self.setup_settings_page_cloud(self.tab_cloud)
        self.tab_widget.addTab(self.tab_cloud, "云端同步 (Cloud)")
        
        # Original Settings Layout Wrapper
        settings_layout = QVBoxLayout(self.page_settings)
        settings_layout.addWidget(self.tab_widget)
        
        self.content_stack.addWidget(self.page_settings)
        
        # Add Sidebar and Content to Main Layout
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.content_stack)
        
        # Connect Navigation
        self.btn_home.clicked.connect(lambda: self.content_stack.setCurrentWidget(self.page_home))
        self.btn_history.clicked.connect(lambda: (self.refresh_history_batches(), self.content_stack.setCurrentWidget(self.page_history)))
        self.btn_settings.clicked.connect(lambda: self.content_stack.setCurrentWidget(self.page_settings))

        # Default Page
        self.btn_home.click()

        # Apply Styles
        self.apply_styles()

    def setup_home_page(self):
        layout = QVBoxLayout(self.page_home)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # Top Bar: Title and Actions
        top_bar = QHBoxLayout()
        
        title = QLabel("批量 SKU 条码生成")
        title.setObjectName("PageTitle")
        
        self.btn_import = QPushButton("导入 Excel")
        self.btn_import.setObjectName("ActionButton")
        self.btn_import.setFixedWidth(120)
        self.btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import.clicked.connect(self.import_excel)

        self.btn_clear = QPushButton("清空数据")
        self.btn_clear.setIcon(QIcon(":/icons/clear.png")) # Fallback if no icon, text is enough
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #d9534f; 
                color: white; 
                border-radius: 6px; 
                padding: 6px 16px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #c9302c; }
        """)
        self.btn_clear.clicked.connect(self.clear_data_action)
        
        # Search Box
        self.btn_generate = QPushButton("生成 PDF")
        self.btn_generate.setObjectName("SuccessButton")
        self.btn_generate.setFixedWidth(120)
        self.btn_generate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_generate.clicked.connect(self.generate_pdf)
        
        top_bar.addWidget(title)
        top_bar.addStretch()
        
        # Search Box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索 (Search)...")
        self.search_input.setFixedWidth(200)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.apply_filters)
        top_bar.addWidget(self.search_input)
        
        top_bar.addWidget(self.btn_import)
        top_bar.addWidget(self.btn_clear)
        top_bar.addWidget(self.btn_generate)

        layout.addLayout(top_bar)

        # Table for Data Display
        self.table = QTableWidget()
        headers = [item["name"] for item in self.column_mapping] + ["Preview", "Status", "Action"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        
        # Custom Filter Header
        self.filter_header = FilterHeader(Qt.Orientation.Horizontal, self.table, self.table)
        self.filter_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(120)
        # Resize Preview Column (Last Index) to Fixed
        preview_col_idx = len(headers) - 1
        self.filter_header.setSectionResizeMode(preview_col_idx, QHeaderView.ResizeMode.Interactive)
        
        self.filter_header.filterClicked.connect(self.show_header_menu_by_index)
        self.table.setHorizontalHeader(self.filter_header)
        
        preview_col = len(headers) - 3 # Preview
        status_col = len(headers) - 2 # Status
        action_col = len(headers) - 1 # Action
        
        self.table.setColumnWidth(preview_col, 150) # Width for barcode
        self.table.setColumnWidth(status_col, 80) # Width for Status
        self.table.setColumnWidth(action_col, 100) # Width for action
        self.table.verticalHeader().setDefaultSectionSize(70) # Height for barcode
        
        # Double Click to View
        self.table.cellDoubleClicked.connect(self.show_preview_dialog)

        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        
        layout.addWidget(self.table)

        # Status Area (Bottom)
        status_layout = QHBoxLayout()
        
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color: #888;")
        status_layout.addWidget(self.lbl_status)
        
        status_layout.addStretch()
        
        self.lbl_printer_status = QLabel("🖨️ 打印机：检测中...")
        self.lbl_printer_status.setStyleSheet("color: #888;")
        status_layout.addWidget(self.lbl_printer_status)
        
        layout.addLayout(status_layout)
        
        # Printer Check Timer (Auto-detect every 5s)
        self.printer_timer = QTimer(self)
        self.printer_timer.timeout.connect(self.check_printer_status)
        self.printer_timer.start(5000)
        
        # Initial Check
        self.check_printer_status()

    def check_printer_status(self):
        default_printer = QPrinterInfo.defaultPrinterName()
        if default_printer:
            self.lbl_printer_status.setText(f"🖨️ 打印机：Ready ({default_printer})")
            self.lbl_printer_status.setStyleSheet("color: #888;") # Gray
        else:
            self.lbl_printer_status.setText("🖨️ 打印机：未连接 (No Printer)")
            self.lbl_printer_status.setStyleSheet("color: #d9534f;") # Red

    def load_settings(self):
        # Default Mappings in JSON format if not found
        default_mapping = [
            {"name": "P/I", "header": "P/I柜号", "order": 20},
            {"name": "SKU", "header": "工厂SKU", "order": 0},
            {"name": "INV", "header": "INV号", "order": 10},
            {"name": "PO", "header": "PO/号", "order": 10},
            {"name": "Quantity", "header": "Quantity", "order": 10}
        ]
        
        json_mapping = self.settings.value("column_mapping", json.dumps(default_mapping))
        try:
            self.column_mapping = json.loads(json_mapping)
        except json.JSONDecodeError:
            self.column_mapping = default_mapping
            
        # Ensure we have valid structure
        if not isinstance(self.column_mapping, list):
            self.column_mapping = default_mapping

        # Load Source Selections
        self.barcode_source = self.settings.value("barcode_source", "P/I")
        self.qty_source = self.settings.value("qty_source", "Quantity")
        self.label_multiplier = int(self.settings.value("label_multiplier", 1))

    def setup_settings_page_general(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # --- Column Mapping Table ---
        lbl_mapping = QLabel("Excel 列名映射与字段设置")
        lbl_mapping.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(lbl_mapping)
        
        # ... (Table setup remains same logic but attached to 'layout')
        # Table Setup
        self.settings_table = QTableWidget()
        self.settings_table.setColumnCount(3)
        self.settings_table.setHorizontalHeaderLabels(["显示名称 (Display Name)", "Excel 列名 (Excel Header)", "显示顺序 (Sort Order)"])
        self.settings_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.settings_table.verticalHeader().setDefaultSectionSize(45) 
        self.settings_table.setMinimumHeight(400) 
        self.settings_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.settings_table.setAlternatingRowColors(True)
        self.refresh_settings_table()
        
        layout.addWidget(self.settings_table)
        
        # Table Buttons
        btn_layout = QHBoxLayout()
        self.btn_add_col = QPushButton("添加列")
        self.btn_add_col.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_col.setStyleSheet("background-color: #4CAF50; color: white; border-radius: 4px; padding: 6px 12px; font-weight: bold;")
        self.btn_add_col.clicked.connect(self.add_column_row)
        
        self.btn_del_col = QPushButton("删除选中列")
        self.btn_del_col.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del_col.setStyleSheet("background-color: #f44336; color: white; border-radius: 4px; padding: 6px 12px; font-weight: bold;")
        self.btn_del_col.clicked.connect(self.delete_column_row)
        
        btn_layout.addWidget(self.btn_add_col)
        btn_layout.addWidget(self.btn_del_col)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # --- Source Selection ---
        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 20, 0, 0)
        
        self.combo_barcode_source = QComboBox()
        self.combo_qty_source = QComboBox()
        self.update_source_combos()
        self.set_combo_text(self.combo_barcode_source, self.barcode_source)
        self.set_combo_text(self.combo_qty_source, self.qty_source)

        form_layout.addRow("条码数据来源 (Barcode Source):", self.combo_barcode_source)
        form_layout.addRow("数量来源 (Quantity Source):", self.combo_qty_source)
        
        # Multiplier Setting
        self.spin_multiplier = QSpinBox()
        self.spin_multiplier.setRange(1, 100)
        self.spin_multiplier.setValue(self.label_multiplier)
        form_layout.addRow("打印数量倍数 (Label Multiplier):", self.spin_multiplier)
        
        layout.addLayout(form_layout)
        
        # Save Button
        layout.addSpacing(20)
        btn_save = QPushButton("保存设置")
        btn_save.setObjectName("ActionButton")
        btn_save.setFixedWidth(120)
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(self.save_settings)
        
        layout.addWidget(btn_save)
        layout.addStretch()

    def setup_settings_page_cloud(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        title = QLabel("云端同步设置 (Supabase Cloud)")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)
        
        desc = QLabel("启用云端同步后，历史记录将保存到在线数据库，实现多台电脑数据共享。")
        desc.setStyleSheet("color: #666;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        form = QFormLayout()
        
        self.chk_cloud_enable = QCheckBox("启用云端同步 (Enable Cloud Sync)")
        self.chk_cloud_enable.setChecked(self.settings.value("cloud_enabled", "false") == "true")
        
        self.input_cloud_url = QLineEdit()
        self.input_cloud_url.setPlaceholderText("https://xyz.supabase.co")
        self.input_cloud_url.setText(self.settings.value("cloud_url", ""))
        
        self.input_cloud_key = QLineEdit()
        self.input_cloud_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_cloud_key.setPlaceholderText("eyJhbg...")
        self.input_cloud_key.setText(self.settings.value("cloud_key", ""))
        
        form.addRow(self.chk_cloud_enable)
        form.addRow("Project URL:", self.input_cloud_url)
        form.addRow("Anon Key:", self.input_cloud_key)
        
        layout.addLayout(form)
        
        btn_save_cloud = QPushButton("保存连接设置")
        btn_save_cloud.setObjectName("ActionButton")
        btn_save_cloud.setFixedWidth(150)
        btn_save_cloud.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_cloud.clicked.connect(self.save_cloud_settings)
        
        layout.addWidget(btn_save_cloud)
        layout.addStretch()

    def save_cloud_settings(self):
        enabled = self.chk_cloud_enable.isChecked()
        url = self.input_cloud_url.text().strip()
        key = self.input_cloud_key.text().strip()
        
        self.settings.setValue("cloud_enabled", "true" if enabled else "false")
        self.settings.setValue("cloud_url", url)
        self.settings.setValue("cloud_key", key)
        
        # Trigger reload in DB wrapper
        self.db.reload_config()
        
        if enabled and (not url or not key):
             QMessageBox.warning(self, "提示", "请填写完整的 URL 和 Key")
        else:
             QMessageBox.information(self, "保存成功", "云端设置已保存！\n(如果启用，请尝试刷新历史记录查看连接状态)")

    def refresh_settings_table(self):
        self.settings_table.setRowCount(0)
        for item in self.column_mapping:
            row = self.settings_table.rowCount()
            self.settings_table.insertRow(row)
            self.settings_table.setItem(row, 0, QTableWidgetItem(item.get("name", "")))
            self.settings_table.setItem(row, 1, QTableWidgetItem(item.get("header", "")))
            self.settings_table.setItem(row, 2, QTableWidgetItem(str(item.get("order", 10))))
            
    def add_column_row(self):
        try:
            row = self.settings_table.rowCount()
            self.settings_table.insertRow(row)
            self.settings_table.setItem(row, 0, QTableWidgetItem("New Field"))
            self.settings_table.setItem(row, 1, QTableWidgetItem(""))
            self.settings_table.setItem(row, 2, QTableWidgetItem("10"))
            self.settings_table.scrollToBottom()
            self.settings_table.selectRow(row)
            self.update_source_combos()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"添加列失败: {str(e)}")

    def delete_column_row(self):
        current_row = self.settings_table.currentRow()
        if current_row >= 0:
            try:
                self.settings_table.removeRow(current_row)
                self.update_source_combos()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除列失败: {str(e)}")
        else:
            QMessageBox.warning(self, "提示", "请先点击选中要删除的行\n(Please select a row first)")

    def update_source_combos(self):
        # Preserve current selection if possible
        curr_bc = self.combo_barcode_source.currentText()
        curr_qty = self.combo_qty_source.currentText()
        
        names = []
        for r in range(self.settings_table.rowCount()):
            item = self.settings_table.item(r, 0)
            if item:
                names.append(item.text())
        
        self.combo_barcode_source.clear()
        self.combo_barcode_source.addItems(names)
        
        self.combo_qty_source.clear()
        self.combo_qty_source.addItems(names)
        
        self.set_combo_text(self.combo_barcode_source, curr_bc)
        self.set_combo_text(self.combo_qty_source, curr_qty)

    def set_combo_text(self, combo, text):
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_settings(self):
        # 1. Rebuild Mapping from Table
        new_mapping = []
        for r in range(self.settings_table.rowCount()):
            name = self.settings_table.item(r, 0).text().strip()
            header = self.settings_table.item(r, 1).text().strip()
            order_str = self.settings_table.item(r, 2).text().strip()
            try:
                order = int(order_str)
            except ValueError:
                order = 10
                
            if name and header:
                new_mapping.append({"name": name, "header": header, "order": order})
        
        if not new_mapping:
            QMessageBox.warning(self, "警告", "请至少配置一个有效的列映射！")
            return

        self.column_mapping = new_mapping
        
        # 2. Get Sources
        self.barcode_source = self.combo_barcode_source.currentText()
        self.qty_source = self.combo_qty_source.currentText()
        self.label_multiplier = self.spin_multiplier.value()
        
        # 3. Save to QSettings
        self.settings.setValue("column_mapping", json.dumps(self.column_mapping))
        self.settings.setValue("barcode_source", self.barcode_source)
        self.settings.setValue("qty_source", self.qty_source)
        self.settings.setValue("label_multiplier", self.label_multiplier)
        
        QMessageBox.information(self, "成功", "设置已保存！")

    def setup_history_page(self):
        self.history_column_filters = {} # Init filters for history
        layout = QHBoxLayout(self.page_history)
        
        # Left: Batch List
        left_panel = QFrame()
        left_layout = QVBoxLayout(left_panel)
        
        # Header with Search
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("导入记录 (Batches)"))
        
        self.history_search_input = QLineEdit()
        self.history_search_input.setPlaceholderText("Search history...")
        self.history_search_input.setClearButtonEnabled(True)
        self.history_search_input.textChanged.connect(self.filter_history_batches)
        header_layout.addWidget(self.history_search_input)
        
        left_layout.addLayout(header_layout)

        self.history_batch_list = QTableWidget()
        self.history_batch_list.setColumnCount(4)
        self.history_batch_list.setHorizontalHeaderLabels(["ID", "时间", "文件名", "操作"])
        self.history_batch_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_batch_list.itemClicked.connect(self.load_history_details)
        left_layout.addWidget(self.history_batch_list)
        
        # Right: Detail View
        right_panel = QFrame()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("详细内容 (Details)"))
        self.history_detail_table = QTableWidget()
        # Columns setup in load_history_details
        
        # Custom Filter Header for History Table
        self.history_filter_header = FilterHeader(Qt.Orientation.Horizontal, self.history_detail_table, self.history_detail_table)
        self.history_filter_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.history_detail_table.horizontalHeader().setDefaultSectionSize(120)
        self.history_filter_header.filterClicked.connect(self.show_history_header_menu_by_index)
        self.history_detail_table.setHorizontalHeader(self.history_filter_header)
        # Double Click to View
        self.history_detail_table.cellDoubleClicked.connect(self.show_history_preview_dialog)

        right_layout.addWidget(self.history_detail_table)
        
        layout.addWidget(left_panel, 1)
        layout.addWidget(right_panel, 3) # More space for details
        
    def refresh_history_batches(self):
        batches = self.db.get_batches()
        self.history_batch_list.setRowCount(0)
        self.history_batch_list.setColumnWidth(0, 40)
        self.history_batch_list.setColumnWidth(1, 140)
        self.history_batch_list.setColumnWidth(3, 60)
        
        for b in batches:
            row = self.history_batch_list.rowCount()
            self.history_batch_list.insertRow(row)
            batch_id = b[0]
            # ID
            item_id = QTableWidgetItem(str(batch_id))
            item_id.setToolTip(str(batch_id)) # Add tooltip
            self.history_batch_list.setItem(row, 0, item_id)
            # Time
            item_time = QTableWidgetItem(str(b[2]))
            item_time.setToolTip(str(b[2])) # Add tooltip
            self.history_batch_list.setItem(row, 1, item_time)
            # Filename
            item_filename = QTableWidgetItem(str(b[1]))
            item_filename.setToolTip(str(b[1])) # Add tooltip
            self.history_batch_list.setItem(row, 2, item_filename)
            
            # Delete Button
            btn_del = QPushButton("×")
            btn_del.setFixedWidth(30)
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setStyleSheet("background-color: #d9534f; color: white; border-radius: 4px; font-weight: bold; padding: 0px;")
            btn_del.clicked.connect(lambda _, bid=batch_id: self.delete_batch_action(bid))
            
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(btn_del)
            self.history_batch_list.setCellWidget(row, 3, container)

    def filter_history_batches(self, text):
        text = text.lower().strip()
        for row in range(self.history_batch_list.rowCount()):
            # Check ID (col 0) and Filename (col 2)
            item_id = self.history_batch_list.item(row, 0)
            item_name = self.history_batch_list.item(row, 2)
            
            match = False
            if item_id and text in item_id.text().lower():
                match = True
            if item_name and text in item_name.text().lower():
                match = True
                
            self.history_batch_list.setRowHidden(row, not match)

    def delete_batch_action(self, batch_id):
        reply = QMessageBox.question(self, "确认删除", "确定要删除这条记录吗？\n删除后无法恢复。", 
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                   QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_batch(batch_id)
            self.refresh_history_batches()
            # Clear details if needed
            self.history_detail_table.setRowCount(0)
            self.lbl_status.setText("记录已删除")
            
    def load_history_details(self, item):
        row = item.row()
        batch_id_item = self.history_batch_list.item(row, 0)
        if not batch_id_item: return
        batch_id = int(batch_id_item.text())
        
        items = self.db.get_batch_items(batch_id)
        self.history_detail_table.setRowCount(0)
        
        # Reset filters when loading new batch
        self.history_column_filters = {}
        
        # Dynamic Columns (Same as Home)
        headers = [item["name"] for item in self.column_mapping] + ["Preview", "Status", "Action"]
        self.history_detail_table.setColumnCount(len(headers))
        self.history_detail_table.setHorizontalHeaderLabels(headers)
        self.history_detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.history_detail_table.horizontalHeader().setDefaultSectionSize(120)
        
        # Adjust Column Widths
        preview_col = len(headers) - 3
        status_col = len(headers) - 2
        action_col = len(headers) - 1
        self.history_detail_table.setColumnWidth(preview_col, 150)
        self.history_detail_table.setColumnWidth(status_col, 80)
        self.history_detail_table.setColumnWidth(action_col, 100)
        self.history_detail_table.verticalHeader().setDefaultSectionSize(70)
        
        preview_tasks = []
        
        for it in items:
            row_idx = self.history_detail_table.rowCount()
            self.history_detail_table.insertRow(row_idx)
            
            data = it['data']
            status = it['status']
            db_item_id = it['id']
            
            col_idx = 0
            for col_map in self.column_mapping:
                # Use data from JSON
                val = data.get(col_map["name"], "")
                item_val = QTableWidgetItem(str(val))
                item_val.setToolTip(str(val)) # Add Tooltip
                self.history_detail_table.setItem(row_idx, col_idx, item_val)
                col_idx += 1
                
            # Preview Column
            preview_col_idx = col_idx
            preview_item = QTableWidgetItem("Waiting...")
            # Store Data for Print/Preview
            preview_item.setData(Qt.ItemDataRole.UserRole, data)
            preview_item.setData(Qt.ItemDataRole.UserRole + 1, db_item_id)
            
            self.history_detail_table.setItem(row_idx, preview_col_idx, preview_item)
            
            # Status
            status_col_idx = col_idx + 1
            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_detail_table.setItem(row_idx, status_col_idx, status_item)
            
            # Action (Print)
            action_col_idx = col_idx + 2
            container = QWidget()
            btn_layout = QVBoxLayout(container)
            btn_layout.setContentsMargins(5, 5, 5, 5)
            btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            btn_print = QPushButton("🖨️ Print")
            btn_print.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_print.setStyleSheet("background-color: #007acc; color: white; border-radius: 4px; padding: 4px 8px;")
            # Connect to generic print function
            # Pass (table_widget, row_index)
            btn_print.clicked.connect(lambda _, t=self.history_detail_table, r=row_idx: self.print_generic_row(t, r))
            
            btn_layout.addWidget(btn_print)
            self.history_detail_table.setCellWidget(row_idx, action_col_idx, container)
            
            # Collect for preview
            preview_tasks.append((row_idx, data))

        # Start History Preview Thread
        if preview_tasks:
            self.history_preview_thread = BarcodePreviewThread(
                preview_tasks, 
                self.column_mapping, 
                self.barcode_source, 
                self.qty_source, 
                self.combo_paper.currentText()
            )
            self.history_preview_thread.batch_ready.connect(self.update_history_preview)
            self.history_preview_thread.start()

    def show_history_preview_dialog(self, row, column):
        # 1. Get Data
        preview_col = self.history_detail_table.columnCount() - 3
        item = self.history_detail_table.item(row, preview_col)
        if not item: return
        
        item_data = item.data(Qt.ItemDataRole.UserRole)
        if not item_data: return
        
        # 2. Prepare Data
        barcode_val = str(item_data.get(self.barcode_source, ""))
        normalized_sku = ""
        for char in barcode_val:
            code_point = ord(char)
            if 0xFF01 <= code_point <= 0xFF5E:
                normalized_sku += chr(code_point - 0xFEE0)
            elif code_point == 0x3000:
                normalized_sku += " "
            else:
                normalized_sku += char
        barcode_content = re.sub(r'[^\x00-\x7F]+', '', normalized_sku).strip() or "INVALID"
        
        inv_val = ""
        inv_key = ""
        for k in item_data.keys():
            if "INV" in k.upper():
                inv_val = str(item_data[k])
                inv_key = k
                break
        
        footer_fields = []
        for k, v in item_data.items():
            if k == self.qty_source: continue
            if k == inv_key: continue
            
            # Auto-append Week Number to P/I
            if k == "P/I":
                try:
                    week_num = datetime.now().isocalendar()[1]
                    v = f"{v}  {week_num}"
                except:
                    pass

            priority = 10
            for m in self.column_mapping:
                if m["name"] == k:
                    priority = m.get("order", 10)
                    break
            footer_fields.append((priority, str(v)))
        
        footer_fields.sort(key=lambda x: x[0])
        footer_texts = [f[1] for f in footer_fields]
        
        paper_type = self.combo_paper.currentText()
        is_100x100 = "100x100" in paper_type
        
        # 3. Generate Image
        w, h = (500, 500) if is_100x100 else (560, 320) # 2x scale for preview dialog
        
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.white)
        
        painter = QPainter(image)
        self.draw_label_on_qpainter(painter, w, h, barcode_content, inv_val, footer_texts, is_100x100)
        painter.end()
        
        # 4. Show Dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("预览 (Preview Detail)")
        vbox = QVBoxLayout(dialog)
        
        lbl = QLabel()
        lbl.setPixmap(QPixmap.fromImage(image))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("border: 1px solid #ccc;")
        
        vbox.addWidget(lbl)
        dialog.exec()
            
    def update_history_preview(self, batch):
        preview_col = self.history_detail_table.columnCount() - 3
        for row, image in batch:
            lbl = QLabel()
            # Convert QImage to QPixmap in Main Thread
            pixmap = QPixmap.fromImage(image)
            
            # Scale pixmap to fit cell height (keep aspect ratio)
            h = 60 # Set to row height - padding
            scaled = pixmap.scaledToHeight(h, Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(scaled)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setContentsMargins(5, 5, 5, 5) # Padding
            self.history_detail_table.setCellWidget(row, preview_col, lbl)
    
    def import_excel(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择 Excel 文件", "", "Excel Files (*.xlsx);;All Files (*)")
        if not file_path:
            return

        try:
            self.df = pd.read_excel(file_path)
            
            # Validation
            # Get all required headers from mapping
            required_headers = {item["header"] for item in self.column_mapping}
            
            if not required_headers.issubset(self.df.columns):
                missing = required_headers - set(self.df.columns)
                QMessageBox.critical(self, "格式错误", f"Excel 缺少必要列:\n{', '.join(missing)}\n\n请在设置中检查列名映射。")
                self.df = None
                return
            
            # Prepare for UI
            df = self.df.fillna("")
            
            # Save Batch to DB
            batch_id = self.db.add_batch(os.path.basename(file_path))

            # Populate Table
            self.table.setRowCount(0)
            
            # Stop existing thread
            if self.preview_thread and self.preview_thread.isRunning():
                self.preview_thread.stop()
            
            # Display Keys
            display_keys = [item["name"] for item in self.column_mapping]
            key_to_header = {item["name"]: item["header"] for item in self.column_mapping}
            
            # Barcode Header
            barcode_header = self.barcode_source
            
            for index, row in df.iterrows():
                row_idx = self.table.rowCount()
                self.table.insertRow(row_idx)
                
                # Build Item Data Dictionary for DB and Row
                # We need all column data
                
                # Re-map row to dict based on headers? row is already series
                # But we want to use our Display Names as keys? 
                # Actually, standardizing on the Excel Header keys or our mapping keys?
                # Let's use the keys defined in column_mapping for consistency
                
                item_snapshot = {}
                
                col_idx = 0
                for col_map in self.column_mapping:
                    excel_header = col_map["header"]
                    val = str(row.get(excel_header, "")) if pd.notna(row.get(excel_header, "")) else ""
                    
                    item_val = QTableWidgetItem(val)
                    item_val.setToolTip(val) # Add Tooltip
                    self.table.setItem(row_idx, col_idx, item_val)
                    
                    # Store in snapshot
                    item_snapshot[col_map["name"]] = val
                    
                    col_idx += 1
                
                # Save Item to DB
                db_item_id = self.db.add_item(batch_id, item_snapshot)
                
                # Preview Column (Count - 3)
                preview_col_idx = col_idx 
                preview_item = QTableWidgetItem("Waiting...")
                # Store DB Item ID in UserRole of Preview Item for easy access
                preview_item.setData(Qt.ItemDataRole.UserRole, item_snapshot)
                preview_item.setData(Qt.ItemDataRole.UserRole + 1, db_item_id) 
                
                self.table.setItem(row_idx, preview_col_idx, preview_item)

                # Status Column (Count - 2)
                status_col_idx = col_idx + 1
                status_item = QTableWidgetItem("⏳")
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_idx, status_col_idx, status_item)

                # Action Column (Count - 1)
                action_col_idx = col_idx + 2
                container = QWidget()
                btn_layout = QVBoxLayout(container)
                btn_layout.setContentsMargins(5, 5, 5, 5)
                btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                
                btn_print_row = QPushButton("🖨️ Print")
                btn_print_row.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_print_row.setStyleSheet("background-color: #007acc; color: white; border-radius: 4px; padding: 4px 8px;")
                btn_print_row.clicked.connect(lambda _, r=row_idx: self.print_row_action(r))
                
                btn_layout.addWidget(btn_print_row)
                self.table.setCellWidget(row_idx, action_col_idx, container)
                
                    
            # Start Preview Generation
            preview_tasks = []
            for r in range(self.table.rowCount()):
                 # Get item data from preview item
                 # Preview is at Column Count - 3
                 p_item = self.table.item(r, self.table.columnCount() - 3)
                 if p_item:
                     d = p_item.data(Qt.ItemDataRole.UserRole)
                     if d:
                        preview_tasks.append((r, d))
            
            if self.preview_thread and self.preview_thread.isRunning():
                self.preview_thread.stop()
            
            if preview_tasks:
                self.start_preview_thread(preview_tasks)
            
            total_qty = 0
            if self.qty_source in self.df.columns:
                try:
                    total_qty = pd.to_numeric(self.df[self.qty_source], errors='coerce').fillna(0).sum()
                    total_qty = int(total_qty)
                except:
                    total_qty = 0
                    
            self.lbl_status.setText(f"已加载 {len(self.df)} 行数据，总计数量 {total_qty}")

        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"无法读取文件: {str(e)}")

    def start_preview_thread(self, tasks):
        paper_type = self.combo_paper.currentText()
        self.preview_thread = BarcodePreviewThread(
            tasks, 
            self.column_mapping, 
            self.barcode_source, 
            self.qty_source,
            paper_type
        )
        self.preview_thread.batch_ready.connect(self.update_barcode_preview)
        self.preview_thread.start()

    def regenerate_previews(self):
        # Stop existing
        if hasattr(self, 'preview_thread') and self.preview_thread and self.preview_thread.isRunning():
            self.preview_thread.stop()
            
        # Collect tasks
        preview_tasks = []
        # Preview is at Column Count - 3
        preview_col = self.table.columnCount() - 3
        
        for r in range(self.table.rowCount()):
             p_item = self.table.item(r, preview_col)
             if p_item:
                 # Reset to waiting
                 p_item.setIcon(QIcon())
                 p_item.setText("Generating...")
                 
                 d = p_item.data(Qt.ItemDataRole.UserRole)
                 if d:
                    preview_tasks.append((r, d))
                    
        if preview_tasks:
            self.start_preview_thread(preview_tasks)

    def update_barcode_preview(self, batch):
        # Preview is at Column Count - 3 (Status is -2, Action is -1)
        preview_col = self.table.columnCount() - 3
        for row, image in batch:
            lbl = QLabel()
            # Convert QImage to QPixmap in Main Thread
            pixmap = QPixmap.fromImage(image)
            
            # Scale pixmap to fit cell height (keep aspect ratio)
            h = 60 # Set to row height - padding
            scaled = pixmap.scaledToHeight(h, Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(scaled)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setContentsMargins(5, 5, 5, 5) # Padding
            lbl.setPixmap(scaled)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setContentsMargins(5, 5, 5, 5) # Padding
            self.table.setCellWidget(row, preview_col, lbl)

    def show_preview_dialog(self, row, col):
        # Check if it is the preview column (Count - 3)
        if col != self.table.columnCount() - 3:
            return
        
        item = self.table.item(row, col)
        if not item:
            return
            
        item_data = item.data(Qt.ItemDataRole.UserRole)
        # Handle backward compatibility or wait state
        if not item_data or not isinstance(item_data, dict):
            return
            
        # Extract Barcode Content
        barcode_val = str(item_data.get(self.barcode_source, ""))
        normalized_sku = ""
        for char in barcode_val:
            code_point = ord(char)
            if 0xFF01 <= code_point <= 0xFF5E:
                normalized_sku += chr(code_point - 0xFEE0)
            elif code_point == 0x3000:
                normalized_sku += " "
            else:
                normalized_sku += char
        barcode_content = re.sub(r'[^\x00-\x7F]+', '', normalized_sku).strip() or "INVALID"
        
        # Prepare Footer Texts & INV
        inv_val = ""
        inv_key = ""
        for k in item_data.keys():
            if "INV" in k.upper():
                inv_val = str(item_data[k])
                inv_key = k
                break
        
        footer_fields = []
        for k, v in item_data.items():
            if k == self.qty_source: continue
            if k == inv_key: continue
            
            # Auto-append Week Number to P/I
            if k == "P/I":
                try:
                    week_num = datetime.now().isocalendar()[1]
                    v = f"{v}  {week_num}"
                except:
                    pass
            
            priority = 10
            for m in self.column_mapping:
                if m["name"] == k:
                    priority = m.get("order", 10)
                    break
            footer_fields.append((priority, str(v)))
        
        footer_fields.sort(key=lambda x: x[0])
        footer_texts = [f[1] for f in footer_fields]
        
        # Render High Res Image for Dialog
        paper_type = self.combo_paper.currentText()
        is_100x100 = "100x100" in paper_type
        
        if is_100x100:
            w, h = 600, 600 # 1:1
        else:
            w, h = 560, 320 # 1.75 (70:40) * 8 scale
            
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.white)
        
        painter = QPainter(image)
        try:
             self.draw_label_on_qpainter(painter, w, h, barcode_content, inv_val, footer_texts, is_100x100)
        finally:
            painter.end()
        

            
        # Show Dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(f"详情预览: {barcode_content}")
        vbox = QVBoxLayout(dialog)
        
        lbl = QLabel()
        lbl.setPixmap(QPixmap.fromImage(image))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(lbl)
        
        # Add Print Button
        btn_print = QPushButton("🖨️ Print")
        btn_print.setStyleSheet("font-size: 18px; padding: 10px; font-weight: bold; background-color: #007acc; color: white; border-radius: 4px;")
        btn_print.setCursor(Qt.CursorShape.PointingHandCursor)
        vbox.addWidget(btn_print)
        
        def do_print_action():
            # Get default qty
            try:
                raw_qty = item_data.get(self.qty_source, "1")
                # Handle potential float string "1.0"
                def_qty = int(float(str(raw_qty)))
            except (ValueError, TypeError):
                def_qty = 1
            
            # Clamp default to range
            if def_qty < 1: def_qty = 1
            if def_qty > 1000: def_qty = 1000
                
            qty, ok = QInputDialog.getInt(dialog, "Action", "请输入打印数量：", def_qty, 1, 1000)
            if ok:
                # Placeholder for print logic
                print(f"User confirmed print: {qty} copies")
                # Optionally close after print? 
                # dialog.accept() 

        btn_print.clicked.connect(do_print_action)
        
        dialog.exec()

    def generate_pdf(self):
        if self.df is None or self.df.empty:
            QMessageBox.warning(self, "提示", "请先导入数据！")
            return

        # Output Path
        save_path, _ = QFileDialog.getSaveFileName(self, "保存 PDF", f"Barcodes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf", "PDF Files (*.pdf)")
        if not save_path:
            return

        paper_type = self.combo_paper.currentText()
        is_a4 = "A4" in paper_type

        try:
            c = canvas.Canvas(save_path)
            
            # Prepare Data Loop
            tasks = []
            
            # Find header for Qty source
            qty_header = next((item["header"] for item in self.column_mapping if item["name"] == self.qty_source), None)
            
            if not qty_header:
                 QMessageBox.critical(self, "配置错误", f"找不到数量来源字段: {self.qty_source}")
                 return

            for _, row in self.df.iterrows():
                # Build dictionary for this item based on mapping names
                item_data = {}
                for mapping in self.column_mapping:
                    item_data[mapping["name"]] = str(row[mapping["header"]])
                
                # Get Quantity
                try:
                    q_val = row[qty_header]
                    qty = int(q_val) if pd.notna(q_val) else 1
                except ValueError:
                    qty = 1
                    
                tasks.extend([item_data] * qty)

            if is_a4:
                self.generate_a4_layout(c, tasks)
            elif "100x100" in paper_type:
                 self.generate_thermal_layout(c, tasks, 100, 100)
            else:
                self.generate_thermal_layout(c, tasks, 70, 40)

            c.save()
            QMessageBox.information(self, "成功", f"PDF 已生成！\n路径: {save_path}")
            self.lbl_status.setText(f"生成成功: {save_path}")

        except Exception as e:
            QMessageBox.critical(self, "生成失败", f"PDF 生成过程中出错:\n{str(e)}")
            import traceback
            traceback.print_exc()

    def generate_barcode_image(self, sku_text):
        """Generates a Code128 barcode as a PIL Image in BytesIO."""
        # Use simple writer
        rv = io.BytesIO()
        code = barcode.get('code128', sku_text, writer=ImageWriter())
        # Disable text rendering inside barcode lib if we want custom control, 
        # but usually default is fine. 'write' saves to file-like object.
        code.write(rv, options={"write_text": False, "module_height": 10.0, "module_width": 0.3, "quiet_zone": 1.0})
        rv.seek(0)
        return rv

    def generate_a4_layout(self, c, tasks):
        # A4: 210 x 297 mm
        width, height = A4
        
        # Grid Config
        cols = 3
        rows = 7
        cell_w = width / cols
        cell_h = height / rows
        
        # Margins & Padding
        count = 0
        
        for item in tasks:
            # Check for new page
            if count > 0 and count % (cols * rows) == 0:
                c.showPage()
            
            # Current position index in the page
            idx_on_page = count % (cols * rows)
            r = idx_on_page // cols
            col = idx_on_page % cols
            
            # Coordinates (from bottom-left)
            x = col * cell_w
            y = height - ((r + 1) * cell_h)
            
            # Draw content in this cell
            self.draw_label(c, item, 5*mm, 5*mm, cell_w - 10*mm, cell_h - 10*mm)
            
            count += 1

    def clear_data_action(self):
        if self.table.rowCount() == 0:
            return
            
        reply = QMessageBox.question(self, "确认清空", "确定要清空当前所有数据吗？", 
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                   QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.table.setRowCount(0)
            self.df = None
            self.lbl_status.setText("数据已清空")
            # Also stop preview thread if running
            if self.preview_thread and self.preview_thread.isRunning():
                self.preview_thread.stop()





    def populate_column_filter_menu(self, menu, col_index, table_widget):
        # Get unique values
        values = set()
        for row in range(table_widget.rowCount()):
            item = table_widget.item(row, col_index)
            if item:
                values.add(item.text())
        
        sorted_values = sorted(list(values))
        
        # "Select All" Action
        action_all = QAction("全部显示 (Select All)", self)
        action_all.triggered.connect(lambda: self.set_column_filter(col_index, None))
        menu.addAction(action_all)

        # "Deselect All" Action
        action_none = QAction("全部取消 (Deselect All)", self)
        # Pass empty set to filter out everything
        action_none.triggered.connect(lambda: self.set_column_filter(col_index, set()))
        menu.addAction(action_none)
        
        menu.addSeparator()
        
        current_filter = self.column_filters.get(col_index, None)
        
        for val in sorted_values:
            action = QAction(str(val), self)
            action.setCheckable(True)
            is_checked = (current_filter is None) or (val in current_filter)
            action.setChecked(is_checked)
            action.triggered.connect(lambda checked, c=col_index, v=val: self.toggle_column_filter(c, v, checked))
            menu.addAction(action)

    def show_header_menu_by_index(self, col_index, global_pos):
        if col_index < 0:
            return
            
        menu = QMenu(self)
        self.populate_column_filter_menu(menu, col_index, self.table) # Always use self.table for filtering
        
        menu.exec(global_pos)

    def show_history_header_menu_by_index(self, col_index, global_pos):
        if col_index < 0: return

        menu = QMenu(self)
        
        # Populate using history table data
        self.populate_history_column_filter_menu(menu, col_index)
        
        menu.exec(global_pos)

    def populate_history_column_filter_menu(self, menu, col_index):
        # Similar to populate_column_filter_menu but for history table logic
        table_widget = self.history_detail_table
        
        # Get unique values
        values = set()
        for row in range(table_widget.rowCount()):
            item = table_widget.item(row, col_index)
            if item:
                values.add(item.text())
        
        sorted_values = sorted(list(values))
        
        # "Select All" Action
        action_all = QAction("全部显示 (Select All)", self)
        # Use simple lambda for reset
        action_all.triggered.connect(lambda: self.set_history_column_filter(col_index, None))
        menu.addAction(action_all)

        # "Deselect All" Action
        action_none = QAction("全部取消 (Deselect All)", self)
        action_none.triggered.connect(lambda: self.set_history_column_filter(col_index, set()))
        menu.addAction(action_none)
        
        menu.addSeparator()
        
        current_filter = self.history_column_filters.get(col_index, None)
        
        for val in sorted_values:
            action = QAction(str(val), self)
            action.setCheckable(True)
            is_checked = (current_filter is None) or (val in current_filter)
            action.setChecked(is_checked)
            action.triggered.connect(lambda checked, c=col_index, v=val: self.toggle_history_column_filter(c, v, checked))
            menu.addAction(action)

    def set_history_column_filter(self, col_index, allowed_values):
        if allowed_values is None:
            if col_index in self.history_column_filters:
                del self.history_column_filters[col_index]
        else:
            self.history_column_filters[col_index] = allowed_values
        self.apply_history_filters()

    def toggle_history_column_filter(self, col_index, value, is_checked):
        if col_index not in self.history_column_filters:
            # Init with all values if starting fresh
            all_values = set()
            for row in range(self.history_detail_table.rowCount()):
                item = self.history_detail_table.item(row, col_index)
                if item:
                    all_values.add(item.text())
            self.history_column_filters[col_index] = all_values
        
        if is_checked:
            self.history_column_filters[col_index].add(value)
        else:
            if value in self.history_column_filters[col_index]:
                self.history_column_filters[col_index].remove(value)
                
        self.apply_history_filters()

    def apply_history_filters(self):
        # No search box for history yet, just column filters
        for row in range(self.history_detail_table.rowCount()):
            match = True
            for col_idx, allowed_values in self.history_column_filters.items():
                item = self.history_detail_table.item(row, col_idx)
                val = item.text() if item else ""
                if val not in allowed_values:
                    match = False
                    break
            self.history_detail_table.setRowHidden(row, not match)

    def set_column_filter(self, col_index, allowed_values):
        if allowed_values is None:
            # Clear filter for this column
            if col_index in self.column_filters:
                del self.column_filters[col_index]
        else:
            self.column_filters[col_index] = allowed_values
        self.apply_filters()

    def toggle_column_filter(self, col_index, value, is_checked):
        # If no filter exists yet, it means "All Selected".
        # If user unchecks one, we must initialize the set with ALL OTHER values.
        if col_index not in self.column_filters:
            # Get all current values
            all_values = set()
            for row in range(self.table.rowCount()):
                item = self.table.item(row, col_index)
                if item:
                    all_values.add(item.text())
            self.column_filters[col_index] = all_values
        
        if is_checked:
            self.column_filters[col_index].add(value)
        else:
            if value in self.column_filters[col_index]:
                self.column_filters[col_index].remove(value)
                
        # If filter set is empty, maybe keep it empty (show nothing) or reset?
        # Usually exact match filter: empty set = show nothing.
        
        self.apply_filters()

    def apply_filters(self):
        search_text = self.search_input.text().lower()
        
        for row in range(self.table.rowCount()):
            # 1. Global Search Check
            global_match = False
            if not search_text:
                global_match = True
            else:
                for col in range(self.table.columnCount() - 1): # Skip last column (Preview)
                    item = self.table.item(row, col)
                    if item and search_text in item.text().lower():
                        global_match = True
                        break
            
            # 2. Column Filter Check
            col_match = True
            for col_idx, allowed_values in self.column_filters.items():
                item = self.table.item(row, col_idx)
                val = item.text() if item else ""
                if val not in allowed_values:
                    col_match = False
                    break
            
            # Show if BOTH match
            self.table.setRowHidden(row, not (global_match and col_match))

    def generate_thermal_layout(self, c, tasks, w_mm, h_mm):
        # Thermal: w_mm x h_mm
        page_w = w_mm * mm
        page_h = h_mm * mm
        c.setPageSize((page_w, page_h))
        
        for i, item in enumerate(tasks):
            if i > 0:
                c.showPage()
            
            self.draw_label(c, item, 2*mm, 2*mm, page_w - 4*mm, page_h - 4*mm)

    def draw_label(self, c, item_data, x, y, w, h):
        """Draws SKU info and barcode within the bounding box (x,y,w,h)."""
        
        # 1. Identify Data
        barcode_val = item_data.get(self.barcode_source, "")
        
        # Identify INV field (contains "INV")
        # If multiple, take first found, or specific logic?
        # Let's assume user names it "INV" or "Voice No" etc.
        inv_val = ""
        inv_key = ""
        for k in item_data.keys():
            if "INV" in k.upper():
                inv_val = item_data[k]
                inv_key = k
                break
        
        # Collect other fields for footer (bottom stack)
        # Exclude Barcode Source (it's the barcode) and INV (it's top right)
        # Also exclude Quantity (usually not printed) - though user might want it?
        # Let's exclude Quantity Source if it is defined.
        
        footer_fields = []
        # Store tuples of (priority, text) for sorting
        # Priority: 0 (Top/SKU), 10 (Middle), 20 (Bottom/PI)
        
        for k, v in item_data.items():
            if k == self.qty_source:
                continue
            if k == inv_key: # Already handled as top-right INV
                continue
            
            # Auto-append Week Number to P/I
            if k == "P/I":
                try:
                    week_num = datetime.now().isocalendar()[1]
                    v = f"{v}  {week_num}"
                except:
                    pass
            
            text = str(v)
            # Removed automatic prefixing logic for PO and INV as per user request
            
            # Determine Priority from Mapping
            # Find the mapping item corresponding to this key (display name)
            # Efficient lookup? We can build a dict {name: order} once.
            
            # For now, just linear search or better yet, pre-calculate mapping dict.
            # But here we are inside the loop.
            
            # Let's get order. Default to 10.
            priority = 10
            for m in self.column_mapping:
                if m["name"] == k:
                    priority = m.get("order", 10)
                    break
            
            footer_fields.append((priority, text))
            
        # Sort fields: Lower priority first (Top of stack in list)
        # We want to draw them from Top to Bottom?
        # WAIT.
        # Layout Logic:
        # Loop reversed(range(lines)):
        #   draw text at curr_y
        #   curr_y += spacing (moving UP)
        
        # Current logic draws from Bottom UP.
        # curr_y starts at margin_y (BOTTOM of content area).
        # We draw text, then move UP.
        
        # So the FIRST item drawn (at bottom) should be the LAST item in our sorted list?
        # NO.
        # Let's trace:
        # User wants Order 1 (Top), Order 2 (Middle), Order 3 (Bottom).
        # footer_fields sorted: [ (1, Text1), (2, Text2), (3, Text3) ]
        
        # Drawing Loop: reversed(range(3)) -> indices 2, 1, 0.
        # i=2 (Text3): Drawn at curr_y (BOTTOM). Matches user desire (Order 3 is bottom).
        # i=1 (Text2): Drawn above Text3.
        # i=0 (Text1): Drawn above Text2. Match user desire (Order 1 is top).
        
        # Conclusion: Logic holds. Sort by order ascending. Loop reversed.
        
        footer_fields.sort(key=lambda x: x[0])
        
        # Extract text only
        footer_texts = [f[1] for f in footer_fields]
        
        # 2. Clean SKU for Barcode
        normalized_sku = ""
        for char in barcode_val:
            code_point = ord(char)
            if 0xFF01 <= code_point <= 0xFF5E:
                normalized_sku += chr(code_point - 0xFEE0)
            elif code_point == 0x3000:
                normalized_sku += " "
            else:
                normalized_sku += char
        
        barcode_content = re.sub(r'[^\x00-\x7F]+', '', normalized_sku).strip()
        if not barcode_content:
            barcode_content = "INVALID"

        cx = x + w / 2
        
        # Font Configuration
        is_large = h > 50 * mm
        
        font_inv = CHINESE_FONT_NAME
        font_info = CHINESE_FONT_NAME
        # Consolidate Draw Logic for both Large and Small
        # We use a Stacked Center approach for both.
        
        # Determine Constants
        if is_large:
            bc_w = 84 * mm 
            bc_h = 30 * mm
            inv_size_pt = 30
            info_size_pt = 17
            gap = 2 * mm
        else:
            bc_w = 60 * mm
            bc_h = 12 * mm
            inv_size_pt = 14
            info_size_pt = 10
            gap = 1 * mm
            
        line_spacing = info_size_pt + 4
        
        # Calculate Total Height (ReportLab points)
        # Fonts
        c.setFont(font_inv, inv_size_pt)
        h_inv = inv_size_pt if inv_val else 0 # approx cap height
        
        c.setFont(font_info, info_size_pt)
        h_footer = len(footer_texts) * line_spacing
        
        total_h = h_inv + (gap if inv_val else 0) + bc_h + gap + h_footer
        
        # Center Vertically
        margin_y = (h - total_h) / 2
        curr_y = y + margin_y
        
        # 1. Draw Footer (Stack grows Up, but we draw items independently based on order)
        # We want sorting order: 0 (Top) -> N (Bottom).
        # curr_y is layout bottom matches "Bottom" of visual stack.
        # But ReportLab Y increases UP.
        # So "Bottom" of visual stack is LOW Y.
        # "Top" of visual stack is HIGH Y.
        
        # footer_texts is [Priority0, Priority10, Priority20].
        # We want Priority20 at Bottom (Low Y).
        # We want Priority0 at Top (High Y).
        
        # So we draw Priority20 first (at curr_y), then move UP.
        # This means iterating Reversed(footer_texts).
        # Last item (High Priority Value, Bottom) -> Draw at curr_y.
        
        c.setFont(font_info, info_size_pt)
        for i in reversed(range(len(footer_texts))):
            text = footer_texts[i]
            # Center X
            c.drawCentredString(cx, curr_y + 2, text) # +2 for baseline hack? ReportLab draws at baseline. 
            curr_y += line_spacing
            
        # 2. Draw Barcode
        bc_bottom = curr_y + gap
        bc_x_pos = cx - (bc_w / 2)
        
        try:
            img_buffer = self.generate_barcode_image(barcode_content)
            img = ImageReader(img_buffer)
            c.drawImage(img, bc_x_pos, bc_bottom, width=bc_w, height=bc_h, mask='auto', preserveAspectRatio=False, anchor='c')
        except Exception as e:
            print(f"Barcode gen failed: {e}")
            c.setFont("Helvetica", 8)
            c.drawCentredString(cx, bc_bottom + bc_h/2, "Error")
            
        # 3. Draw INV
        if inv_val:
            c.setFont(font_inv, inv_size_pt)
            inv_y = bc_bottom + bc_h + gap
            # Draw at baseline approx
            c.drawCentredString(cx, inv_y + (inv_size_pt * 0.2), inv_val)

    def apply_styles(self):
        style_sheet = """
        QWidget {
            background-color: #2b2b2b;
            color: #ffffff;
            font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            font-size: 14px;
        }

        /* Sidebar */
        QFrame#Sidebar {
            background-color: #1e1e1e;
            border-right: 1px solid #333;
        }
        
        QPushButton {
            background-color: transparent;
            border: none;
            padding: 10px 15px;
            text-align: left;
            font-size: 14px;
            color: #bbb;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #2a2a2a;
            color: white;
        }
        QPushButton:checked {
            background-color: #37373d;
            color: #4cc2ff;
            font-weight: bold;
        }

        /* GroupBox */
        QGroupBox#PrintSettingsGroup {
            border: 1px solid #444;
            border-radius: 6px;
            margin-top: 20px;
            font-weight: bold;
            color: #ddd;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 3px;
        }

        /* ComboBox */
        QComboBox {
            background-color: #333;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 5px;
            color: white;
        }
        QComboBox::drop-down {
            border: none;
        }

        /* Main Content */
        QLabel#PageTitle {
            font-size: 24px;
            font-weight: bold;
            color: #ffffff;
        }
        
        /* Action Buttons */
        QPushButton#ActionButton {
            background-color: #007acc;
            color: white;
            border-radius: 4px;
            padding: 8px 16px;
            text-align: center;
            font-weight: bold;
        }
        QPushButton#ActionButton:hover { background-color: #0098ff; }

        QPushButton#SuccessButton {
            background-color: #2da042;
            color: white;
            border-radius: 4px;
            padding: 8px 16px;
            text-align: center;
            font-weight: bold;
        }
        QPushButton#SuccessButton:hover { background-color: #3fb950; }

        /* Table */
        QTableWidget {
            background-color: #1e1e1e;
            border: 1px solid #444;
            gridline-color: #333;
            color: #ddd;
        }
        QHeaderView::section {
            background-color: #2d2d2d;
            padding: 4px;
            border: 1px solid #444;
            font-weight: bold;
            color: #fff;
        }
        QTableWidget::item {
            padding: 5px;
        }
        QTableWidget::item:selected {
            background-color: #094771;
            color: white;
        }
        
        QLineEdit {
            background-color: #3c3c3c;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 6px;
        }
        """
        self.setStyleSheet(style_sheet)
            
    def draw_label_on_qpainter(self, painter, w, h, barcode_content, inv_val, footer_texts, is_100x100):
        if w <= 0 or h <= 0:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.GlobalColor.black)
        
        # Resolution logic
        # Use min(w, h) to ensure consistent scale regardless of orientation (Landscape vs Portrait)
        # For 100x100, both are same.
        # For 70x40, 40mm is the shorter side.
        short_side = min(w, h)
        px_per_mm = short_side / 100.0 if is_100x100 else short_side / 40.0
        
        if is_100x100:
            bc_h_mm = 30
            bc_w_mm = 84
            font_inv_pt = 30
            font_info_pt = 17
            gap_mm = 2
        else:
            bc_h_mm = 12
            bc_w_mm = 60
            font_inv_pt = 14
            font_info_pt = 10
            gap_mm = 1

        pt_to_mm = 0.352778
        
        size_inv_px = max(1, int(font_inv_pt * pt_to_mm * px_per_mm))
        size_info_px = max(1, int(font_info_pt * pt_to_mm * px_per_mm))
        line_spacing_px = size_info_px + int(4 * 0.35 * px_per_mm)
        
        bc_h_px = int(bc_h_mm * px_per_mm)
        bc_w_px = int(bc_w_mm * px_per_mm)
        gap_px = int(gap_mm * px_per_mm)
        
        # Font Config
        font_normal = QFont()
        font_normal.setPixelSize(size_info_px)
        painter.setFont(font_normal)
        
        h_footer = len(footer_texts) * line_spacing_px
        
        h_footer = len(footer_texts) * line_spacing_px
        
        font_inv = QFont()
        font_inv.setPixelSize(size_inv_px)
        font_inv.setBold(True)
        fm_inv = QFontMetrics(font_inv)
        h_inv = fm_inv.height() if inv_val else 0
        
        # Total
        total_h = h_inv + (gap_px if inv_val else 0) + bc_h_px + gap_px + h_footer
        
        # Start Y
        start_y = (h - total_h) / 2
        curr_y = start_y
        
        # A. INV
        if inv_val:
            painter.setFont(font_inv)
            rect = QRect(0, int(curr_y), w, h_inv)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, inv_val)
            curr_y += h_inv + gap_px
            
        # B. Barcode
        try:
            Code = barcode.get_barcode_class('code128')
            writer = ImageWriter()
            options = {
                "module_width": 0.5, 
                "module_height": 20, 
                "quiet_zone": 2, 
                "font_size": 0, 
                "text_distance": 0,
                "write_text": False
            }
            rv = io.BytesIO()
            code = Code(barcode_content, writer=writer)
            code.write(rv, options=options)
            
            bc_img = QImage()
            bc_img.loadFromData(rv.getvalue())
            
            # Draw
            target_rect = QRect(int((w - bc_w_px)//2), int(curr_y), int(bc_w_px), int(bc_h_px))
            painter.drawImage(target_rect, bc_img.scaled(target_rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            
            curr_y += bc_h_px + gap_px
            
        except Exception as e:
            print(f"Detail Gen Error: {e}")

        # C. Footer
        painter.setFont(font_normal)
        for text in footer_texts:
            rect = QRect(0, int(curr_y), w, line_spacing_px)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            curr_y += line_spacing_px

    # --- Unified Printing Logic ---
    def print_row_action(self, row):
        # Home Page Print
        self.print_generic_row(self.table, row)
        
    def print_generic_row(self, table_widget, row):
        # 1. Get Item Data
        preview_col = table_widget.columnCount() - 3
        item = table_widget.item(row, preview_col)
        if not item: return
        
        item_data = item.data(Qt.ItemDataRole.UserRole)
        if not item_data: return
        
        # 2. Get Default Qty
        try:
            raw_qty = item_data.get(self.qty_source, "1")
            def_qty = int(float(str(raw_qty)))
        except (ValueError, TypeError):
            def_qty = 1
        
        # Apply Multiplier
        def_qty = def_qty * self.label_multiplier
        
        if def_qty < 1: def_qty = 1
        if def_qty > 10000: def_qty = 10000
            
        # 3. Ask Qty
        qty, ok = QInputDialog.getInt(self, "打印", "请输入打印数量：", def_qty, 1, 10000)
        if not ok: return
        
        # 4. Print Dialog
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        
        # Apply Logic:
        # A. Priority 1: User Selected "Target Printer" in Sidebar
        # B. Priority 2: "System Default" (default behavior)
        
        target_printer = self.combo_printer.currentText()
        if target_printer and target_printer != "系统默认 (System Default)":
            printer.setPrinterName(target_printer)
        else:
            # Fallback to saved last used or default
            last_printer = self.settings.value("last_printer_name", "")
            if last_printer:
                printer.setPrinterName(last_printer)
            
        # Set Doc Name
        raw_barcode = str(item_data.get(self.barcode_source, "Barcode"))
        printer.setDocName(f"Barcode_{raw_barcode}")
        # Set default copies to match user input
        printer.setCopyCount(qty)
        
        dialog = QPrintDialog(printer, self)
        # Attempt to set copy count AGAIN after dialog creation to force it
        printer.setCopyCount(qty)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.do_physical_print(printer, qty, item_data, table_widget, row)

    def do_physical_print(self, printer, qty, item_data, table_widget, row_idx):
        # Prepare Data Content
        barcode_val = str(item_data.get(self.barcode_source, ""))
        normalized_sku = ""
        for char in barcode_val:
            code_point = ord(char)
            if 0xFF01 <= code_point <= 0xFF5E:
                normalized_sku += chr(code_point - 0xFEE0)
            elif code_point == 0x3000:
                normalized_sku += " "
            else:
                normalized_sku += char
        barcode_content = re.sub(r'[^\x00-\x7F]+', '', normalized_sku).strip() or "INVALID"
        
        inv_val = ""
        inv_key = ""
        for k in item_data.keys():
            if "INV" in k.upper():
                inv_val = str(item_data[k])
                inv_key = k
                break
        
        footer_fields = []
        for k, v in item_data.items():
            if k == self.qty_source: continue
            if k == inv_key: continue
            
            # Auto-append Week Number to P/I
            if k == "P/I":
                try:
                    week_num = datetime.now().isocalendar()[1]
                    v = f"{v}  {week_num}"
                except:
                    pass

            priority = 10
            for m in self.column_mapping:
                if m["name"] == k:
                    priority = m.get("order", 10)
                    break
            footer_fields.append((priority, str(v)))
        
        footer_fields.sort(key=lambda x: x[0])
        footer_texts = [f[1] for f in footer_fields]
        
        paper_type = self.combo_paper.currentText()
        is_100x100 = "100x100" in paper_type

        # Printer Setup
        # Manual Loop Implementation
        # 1. Get copies from printer (set by Dialog)
        param_copies = printer.copyCount()
        
        # Fallback Logic:
        # If the dialog returned 1, but the user explicitly requested more (qty > 1),
        # AND it's likely the dialog just failed to sync/update (common Windows/Qt issue),
        # then trust the user's initial input.
        if param_copies <= 1 and qty > 1:
            param_copies = qty
            
        if param_copies < 1: param_copies = 1
        
        # 2. Reset driver copies to 1 to avoid multiplication if driver handles it too
        printer.setCopyCount(1)
        
        # Save printer name only if successful
        self.settings.setValue("last_printer_name", printer.printerName())
        
        painter = QPainter()
        
        if not painter.begin(printer):
            QMessageBox.critical(self, "错误", "无法启动打印任务")
            return
            
        try:
            # We need to loop Qty manually
            # Update Status to "Printing..."
            status_col = table_widget.columnCount() - 2
            
            for i in range(param_copies):
                # Update Progress
                status_item = table_widget.item(row_idx, status_col)
                if status_item:
                    status_item.setText(f"{param_copies} / {i+1}")
                QCoreApplication.processEvents()
                
                if i > 0:
                    printer.newPage()
                
                # Render Logic
                rect = printer.pageRect(QPrinter.Unit.DevicePixel)
                w = int(rect.width())
                h = int(rect.height())
                
                # Apply Margins (X, Y)
                margin_x_mm = self.spin_margin_x.value()
                margin_y_mm = self.spin_margin_y.value()
                
                # Convert mm to pixels based on Printer DPI
                # DPI = Dots Per Inch
                # 1 inch = 25.4 mm
                dpi_x = printer.logicalDpiX()
                dpi_y = printer.logicalDpiY()
                
                off_x = int((margin_x_mm / 25.4) * dpi_x)
                off_y = int((margin_y_mm / 25.4) * dpi_y)
                
                painter.save()
                painter.translate(off_x, off_y)
                
                self.draw_label_on_qpainter(painter, w, h, barcode_content, inv_val, footer_texts, is_100x100)
                
                painter.restore()
                
                # Yield to ensure UI updates are visible
                # Also slight throttling prevents UI freeze on weaker machines
                import time
                time.sleep(0.005) 
            
            # Finish
            status_item = table_widget.item(row_idx, status_col)
            if status_item:
                status_item.setText("✅ 已打印")
                
            # Update DB Status
            preview_col = table_widget.columnCount() - 3
            p_item = table_widget.item(row_idx, preview_col)
            if p_item:
                db_id = p_item.data(Qt.ItemDataRole.UserRole + 1)
                if db_id:
                    self.db.update_item_status(db_id, "✅ 已打印")
            
        except Exception as e:
            QMessageBox.critical(self, "打印出错", str(e))
            status_item = table_widget.item(row_idx, status_col)
            if status_item:
                status_item.setText("❌")
        finally:
            painter.end()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
