import sys
import os
import io
import re
import pandas as pd
import json
from datetime import datetime

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QStackedWidget, 
                             QLineEdit, QMessageBox, QFrame, QCheckBox, QSpacerItem, 
                             QSizePolicy, QFileDialog, QTableWidget, QTableWidgetItem,
                             QComboBox, QHeaderView, QGroupBox, QFormLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QIcon

# ReportLab & Barcode Imports
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import barcode
from barcode.writer import ImageWriter

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

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("批量条码生成工具")
        self.resize(1000, 700) # Slightly larger for table

        # Data Storage
        # Data Storage
        self.df = None
        
        # Settings
        self.settings = QSettings("MyCompany", "BarcodeGenerator")
        self.load_settings()

        # Main Central Widget
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
        self.btn_settings = QPushButton("设置")
        
        for btn in [self.btn_home, self.btn_settings]:
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
        self.combo_paper.setCurrentIndex(2) # Default to 100x100mm
        self.combo_paper.setCursor(Qt.CursorShape.PointingHandCursor)
        
        print_layout.addWidget(lbl_paper)
        print_layout.addWidget(self.combo_paper)
        
        sidebar_layout.addWidget(self.print_group)

        # Spacer
        sidebar_layout.addItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        # --- Right Content Area ---
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("ContentArea")

        # Page 1: Home
        self.page_home = QWidget()
        self.setup_home_page()
        self.content_stack.addWidget(self.page_home)

        # Page 2: Settings (Placeholder)
        self.page_settings = QWidget()
        self.setup_settings_page()
        self.content_stack.addWidget(self.page_settings)

        # Add Sidebar and Content to Main Layout
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.content_stack)

        # Connect Navigation
        self.btn_home.clicked.connect(lambda: self.content_stack.setCurrentWidget(self.page_home))
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

        self.btn_generate = QPushButton("生成 PDF")
        self.btn_generate.setObjectName("SuccessButton")
        self.btn_generate.setFixedWidth(120)
        self.btn_generate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_generate.clicked.connect(self.generate_pdf)
        
        top_bar.addWidget(title)
        top_bar.addStretch()
        top_bar.addWidget(self.btn_import)
        top_bar.addWidget(self.btn_generate)

        layout.addLayout(top_bar)

        # Table for Data Display
        self.table = QTableWidget()
        headers = [item["name"] for item in self.column_mapping]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        
        layout.addWidget(self.table)

        # Status Label
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color: #888;")
        layout.addWidget(self.lbl_status)

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

    def setup_settings_page(self):
        layout = QVBoxLayout(self.page_settings)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        
        # --- Column Mapping Table ---
        lbl_mapping = QLabel("Excel 列名映射与字段设置")
        lbl_mapping.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(lbl_mapping)

        # Table Setup
        self.settings_table = QTableWidget()
        self.settings_table.setColumnCount(3)
        self.settings_table.setHorizontalHeaderLabels(["显示名称 (Display Name)", "Excel 列名 (Excel Header)", "显示顺序 (Sort Order)"])
        self.settings_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.settings_table.verticalHeader().setDefaultSectionSize(45) # Increase row height
        self.settings_table.setMinimumHeight(400) # Increase visible table height
        self.settings_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.settings_table.setAlternatingRowColors(True)
        # Populate Table
        self.refresh_settings_table()
        
        layout.addWidget(self.settings_table)

        # Table Buttons
        btn_layout = QHBoxLayout()
        self.btn_add_col = QPushButton("添加列")
        self.btn_add_col.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_col.clicked.connect(self.add_column_row)
        
        self.btn_del_col = QPushButton("删除选中列")
        self.btn_del_col.setCursor(Qt.CursorShape.PointingHandCursor)
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
        
        # Populate Combos
        self.update_source_combos()
        
        # Set current selection
        self.set_combo_text(self.combo_barcode_source, self.barcode_source)
        self.set_combo_text(self.combo_qty_source, self.qty_source)

        form_layout.addRow("条码数据来源 (Barcode Source):", self.combo_barcode_source)
        form_layout.addRow("数量来源 (Quantity Source):", self.combo_qty_source)
        
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

    def refresh_settings_table(self):
        self.settings_table.setRowCount(0)
        for item in self.column_mapping:
            row = self.settings_table.rowCount()
            self.settings_table.insertRow(row)
            self.settings_table.setItem(row, 0, QTableWidgetItem(item.get("name", "")))
            self.settings_table.setItem(row, 1, QTableWidgetItem(item.get("header", "")))
            self.settings_table.setItem(row, 2, QTableWidgetItem(str(item.get("order", 10))))
            
    def add_column_row(self):
        row = self.settings_table.rowCount()
        self.settings_table.insertRow(row)
        self.settings_table.setItem(row, 0, QTableWidgetItem("New Field"))
        self.settings_table.setItem(row, 1, QTableWidgetItem(""))
        self.settings_table.setItem(row, 2, QTableWidgetItem("10"))
        self.update_source_combos()

    def delete_column_row(self):
        current_row = self.settings_table.currentRow()
        if current_row >= 0:
            self.settings_table.removeRow(current_row)
            self.update_source_combos()
        else:
            QMessageBox.warning(self, "提示", "请先选择要删除的行")

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
        
        # 3. Save to QSettings
        self.settings.setValue("column_mapping", json.dumps(self.column_mapping))
        self.settings.setValue("barcode_source", self.barcode_source)
        self.settings.setValue("qty_source", self.qty_source)
        
        QMessageBox.information(self, "成功", "设置已保存！")

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
            
            # Fill Table
            self.table.setRowCount(0)
            
            # Set Table Headers based on Mapping Names
            headers = [item["name"] for item in self.column_mapping]
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            
            for index, row in self.df.iterrows():
                row_idx = self.table.rowCount()
                self.table.insertRow(row_idx)
                
                for col_idx, item in enumerate(self.column_mapping):
                    col_header = item["header"]
                    val = str(row[col_header])
                    self.table.setItem(row_idx, col_idx, QTableWidgetItem(val))
            
            self.lbl_status.setText(f"已加载 {len(self.df)} 行数据")

        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"无法读取文件: {str(e)}")

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
            self.draw_label(c, item, x + 5*mm, y + 5*mm, cell_w - 10*mm, cell_h - 10*mm)
            
            count += 1

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
            
            text = ""
            if k.upper() in ["PO", "PO#"]:
                text = f"{k}: {v}"
            elif k == "INV": # specific fallback
                 text = f"INV: {v}"
            else:
                 text = str(v)
            
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
        size_inv = 30 if is_large else 12
        
        font_info = CHINESE_FONT_NAME
        size_info = 17 if is_large else 7 # Slightly smaller for compact lists if needed
        line_spacing = size_info + 4

        # Printing Logic
        if is_large:
            # Layout:
            # Top Right: INV (if exists)
            # Center: Barcode
            # Bottom Stack: Footer Fields
            
            # Dimensions
            bc_w = 84 * mm 
            bc_h = 30 * mm
            gap = 2 * mm # Reduced from 5mm 
            
            # 1. INV (Top Right-ish area, but centralized in Y for standard layout? No, previous was top)
            # Let's Calculate total height required
            
            footer_lines = len(footer_texts)
            footer_h = footer_lines * line_spacing
            
            # Total content height
            total_h = footer_h + gap + bc_h + gap + (size_inv if inv_val else 0)
             # Vertical Margin to center content
            margin_y = (h - total_h) / 2
            
            curr_y = y + margin_y
            
            # Draw Footer (Bottom Up)
            c.setFont(font_info, size_info)
            # footer_texts[0] is sorting priority 0 (SKU) -> We want this at TOP.
            # footer_texts[-1] is sorting priority 20 (PI) -> We want this at BOTTOM.
            
            # Loop reversed: LAST item (-1) is drawn FIRST at curr_y (BOTTOM).
            # This matches our sorting! (Last item = PI = Bottom).
            # First item (0) = SKU = drawn LAST at Top.
            
            for i in reversed(range(footer_lines)):
                text = footer_texts[i]
                c.drawCentredString(cx, curr_y, text)
                curr_y += line_spacing
                
            # Barcode
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
            
            # INV
            if inv_val:
                c.setFont(font_inv, size_inv)
                inv_y = bc_bottom + bc_h + gap
                c.drawCentredString(cx, inv_y, f"INV: {inv_val}")

        else:
             # Small Label Logic (Similar but tighter)
             # Support dynamic fields?
             c.setFont(font_info, size_info)
             
             # Draw INV at Top
             if inv_val:
                 c.setFont(font_inv, size_inv)
                 c.drawCentredString(cx, y + h - size_inv, f"INV: {inv_val}")
             
             # Footer fields at bottom
             c.setFont(font_info, size_info)
             curr_y = y + 5
             for i in reversed(range(len(footer_fields))):
                text = footer_fields[i]
                c.drawCentredString(cx, curr_y, text)
                curr_y += line_spacing
                
            # Barcode in middle?
            # Small layout is tricky with dynamic fields. 
            # Ideally we just hope they fit.

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

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
