"""
Janela principal da aplicação Omie Label Automation.
Interface desktop profissional com tema escuro, dashboard de monitoramento,
console de logs em tempo real e controles integrados.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QSplitter, QGroupBox,
    QHeaderView, QStatusBar, QMessageBox, QApplication,
    QLineEdit, QDateEdit, QDialog, QScrollArea, QAbstractItemView,
    QProgressBar,
    QFormLayout, QFileDialog, QListWidget, QListWidgetItem, QAbstractSpinBox
)
from PySide6.QtCore import Qt, QTimer, Slot, QDate, QEvent
from PySide6.QtGui import QColor, QTextCursor

from app.core.config import config
from app.database.models import DatabaseManager, NormalizedInvoice
from app.api.omie_client import OmieClient
from app.api.printer_service import PrinterService
from app.api.zpl_generator import ZPLGenerator
from app.core.polling_worker import PollingWorker


# Colunas da tabela de NF-e
TABLE_COLUMNS = [
    "Nº NF-e", "Cliente", "UF", "Pedido", "Protocolo", "Vol.",
    "Template", "Data Emissão", "Status", "Ações"
]
TABLE_MIN_WIDTHS = {
    "Nº NF-e": 70,
    "Cliente": 140,
    "UF": 40,
    "Pedido": 75,
    "Protocolo": 85,
    "Vol.": 45,
    "Template": 80,
    "Data Emissão": 90,
    "Status": 80,
    "Ações": 60,
}

# Cores de status para destaque visual
STATUS_COLORS = {
    "IMPRESSO":  ("#2ea043", "#ffffff"),  # Verde
    "NOVO":      ("#388bfd", "#ffffff"),  # Azul
    "PENDENTE":  ("#d29922", "#ffffff"),  # Amarelo
    "ERRO":      ("#da3633", "#ffffff"),  # Vermelho
    "APROVADA":  ("#388bfd", "#ffffff"),  # Azul
}

CLARO_REQUISITANTE = "MUCIO 2121-3885"


def _is_claro_invoice(inv: NormalizedInvoice) -> bool:
    text = f"{inv.cliente_nome or ''} {inv.cliente_cnpj_cpf or ''} {inv.template_name or ''}".upper()
    digits = "".join(ch for ch in (inv.cliente_cnpj_cpf or "") if ch.isdigit())
    return "CLARO" in text or "TELMEX" in text or "CLARO_DIVIDIDA" in text or digits.startswith("40432548")


def _invoice_pedido_value(inv: NormalizedInvoice) -> str:
    return inv.numero_ordem or inv.oc or "XXXXX"


def _invoice_protocolo_value(inv: NormalizedInvoice) -> str:
    return inv.protocolo if _is_claro_invoice(inv) and inv.protocolo else "XXXXX"


def _invoice_template_display(inv: NormalizedInvoice) -> str:
    value = inv.model_name or inv.template_name or "default"
    return f"{value} *" if inv.label_note else value


def _invoice_template_tooltip(inv: NormalizedInvoice) -> str:
    lines = [f"Layout: {inv.template_name or 'default'}"]
    if inv.model_name:
        lines.append(f"Modelo: {inv.model_name}")
    if inv.label_note:
        lines.append(f"Observação: {inv.label_note}")
    return "\n".join(lines)


class InvoiceEditDialog(QDialog):
    """Edita os campos da etiqueta selecionada sem alterar o padrão do cliente."""

    def __init__(self, invoice: NormalizedInvoice, parent=None):
        super().__init__(parent)
        self.invoice = invoice
        self.updated_invoice = invoice
        self.setWindowTitle(f"Editar Etiqueta - NF-e {invoice.numero_nf}")
        self.resize(450, 430)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.txt_cliente = QLineEdit(invoice.cliente_nome or "")
        self.txt_uf = QLineEdit(invoice.cliente_uf or "")
        self.txt_uf.setMaxLength(2)
        self.txt_nf = QLineEdit(ZPLGenerator._format_nf(invoice.numero_nf))
        self.txt_nf.setReadOnly(True)
        self.txt_ordem = QLineEdit(_invoice_pedido_value(invoice) if _invoice_pedido_value(invoice) != "XXXXX" else "")
        self.txt_req = QLineEdit(CLARO_REQUISITANTE if _is_claro_invoice(invoice) else (invoice.requisitante or ""))
        self.txt_prot = QLineEdit(invoice.protocolo or "")
        self.txt_note = QTextEdit(invoice.label_note or "")
        self.txt_note.setFixedHeight(70)
        self.txt_note.setPlaceholderText("Observações impressas na etiqueta padrão")
        self.spn_vols = QSpinBox()
        self.spn_vols.setRange(1, 999)
        self.spn_vols.setSingleStep(1)
        self.spn_vols.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spn_vols.setKeyboardTracking(False)
        self.spn_vols.setValue(invoice.quantidade_volumes or 1)
        vol_box = QWidget()
        vol_layout = QHBoxLayout(vol_box)
        vol_layout.setContentsMargins(0, 0, 0, 0)
        vol_layout.setSpacing(4)
        vol_layout.addWidget(self.spn_vols, stretch=1)
        btn_vol_down = QPushButton("-")
        btn_vol_up = QPushButton("+")
        btn_vol_down.setFixedWidth(28)
        btn_vol_up.setFixedWidth(28)
        btn_vol_down.setToolTip("Diminuir volumes")
        btn_vol_up.setToolTip("Aumentar volumes")
        btn_vol_down.clicked.connect(lambda: self.spn_vols.stepDown())
        btn_vol_up.clicked.connect(lambda: self.spn_vols.stepUp())
        vol_layout.addWidget(btn_vol_down)
        vol_layout.addWidget(btn_vol_up)
        self.cmb_template = QComboBox()
        self.cmb_template.addItem("Padrao 110x70", "default")
        self.cmb_template.addItem("Claro dividida", "claro_dividida")
        idx = self.cmb_template.findData(invoice.template_name or "default")
        if idx >= 0:
            self.cmb_template.setCurrentIndex(idx)

        order_label = "Pedido:"
        form.addRow("Cliente:", self.txt_cliente)
        form.addRow("UF:", self.txt_uf)
        form.addRow("NF-e:", self.txt_nf)
        form.addRow(order_label, self.txt_ordem)
        form.addRow("Requisitante:", self.txt_req)
        form.addRow("Protocolo:", self.txt_prot)
        form.addRow("Observações:", self.txt_note)
        form.addRow("Volumes:", vol_box)
        form.addRow("Template:", self.cmb_template)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_save = QPushButton("Salvar")
        btn_save.setObjectName("btnPrint")
        btn_cancel.clicked.connect(self.reject)
        btn_save.clicked.connect(self._save)
        actions.addWidget(btn_cancel)
        actions.addWidget(btn_save)
        layout.addLayout(actions)

    def _save(self):
        data = {
            "cliente_nome": self.txt_cliente.text().strip(),
            "cliente_uf": self.txt_uf.text().strip().upper(),
            "numero_ordem": self.txt_ordem.text().strip() or None,
            "oc": self.txt_ordem.text().strip() or None,
            "requisitante": self.txt_req.text().strip() or None,
            "protocolo": self.txt_prot.text().strip() or None,
            "label_note": self.txt_note.toPlainText().strip() or None,
            "quantidade_volumes": self.spn_vols.value(),
            "template_name": self.cmb_template.currentData(),
        }
        self.updated_invoice = self.invoice.copy(update=data)
        self.accept()


class MainWindow(QMainWindow):
    """Janela principal do Omie Label Automation."""

    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.worker: PollingWorker | None = None
        self._invoice_id_map: dict[int, int] = {}   # row_index -> id_nfe
        self._invoice_map: dict[int, NormalizedInvoice] = {}
        self._logs_collapsed = True
        self._pending_restart = False
        self._stopping_worker = False
        self._is_fetching = False
        self._refreshing_printers = False
        self._loading_table_widths = False
        self._fitting_table_widths = False

        self._setup_window()
        self._build_ui()
        self._load_initial_data()

        # Contador de status bar
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_clock)
        self._status_timer.start(1000)

        QTimer.singleShot(0, self._collapse_logs)
        QTimer.singleShot(250, self._start_monitoring)

    # ------------------------------------------------------------------ #
    #  SETUP WINDOW
    # ------------------------------------------------------------------ #
    def _setup_window(self):
        self.setWindowTitle("Omie Label Automation  -  Monitor de Etiquetas Logisticas")
        self.setMinimumSize(1200, 720)
        self.resize(1400, 820)
        self.setObjectName("MainWindow")

    # ------------------------------------------------------------------ #
    #  BUILD UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 8, 12, 8)
        root_layout.setSpacing(8)

        # ---- HEADER ----
        root_layout.addWidget(self._build_header())

        # ---- CONTROLS ----
        root_layout.addWidget(self._build_controls())

        # ---- MAIN SPLITTER (tabela | logs) ----
        self._main_splitter = QSplitter(Qt.Vertical)
        self._main_splitter.setHandleWidth(4)
        self._main_splitter.addWidget(self._build_table_panel())
        self._main_splitter.addWidget(self._build_log_panel())
        self._main_splitter.setStretchFactor(0, 4)
        self._main_splitter.setStretchFactor(1, 1)
        root_layout.addWidget(self._main_splitter, stretch=1)

        # ---- STATUS BAR ----
        self._setup_status_bar()

    def _build_header(self) -> QWidget:
        widget = QWidget()
        widget.setFixedHeight(60)
        widget.setStyleSheet("background-color: #161b22; border-radius: 8px;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(16, 8, 16, 8)

        # Icone / titulo
        lbl_title = QLabel("OMIE LABEL AUTOMATION")
        lbl_title.setObjectName("labelTitle")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #58a6ff; letter-spacing: 1px;")

        lbl_sub = QLabel("Sistema de Impressao Automatica de Etiquetas Logisticas")
        lbl_sub.setObjectName("labelSubtitle")
        lbl_sub.setStyleSheet("font-size: 11px; color: #8b949e;")

        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(lbl_title)
        left.addWidget(lbl_sub)
        layout.addLayout(left)

        layout.addStretch()

        # Indicador de status do monitor
        self._lbl_monitor_dot = QLabel("● ATIVO")
        self._lbl_monitor_dot.setStyleSheet("color: #2ea043; font-size: 13px; font-weight: bold;")
        layout.addWidget(self._lbl_monitor_dot)

        return widget

    def _build_controls(self) -> QGroupBox:
        group = QGroupBox("Configuracoes")
        layout = QHBoxLayout(group)
        layout.setSpacing(12)

        # Selecao de impressora
        layout.addWidget(QLabel("Impressora:"))
        self._cmb_printer = QComboBox()
        self._cmb_printer.setMinimumWidth(280)
        self._cmb_printer.setToolTip("Selecione a Honeywell PC42t ou modo SIMULADO")
        self._refresh_printer_list()
        self._cmb_printer.currentTextChanged.connect(self._on_printer_changed)
        layout.addWidget(self._cmb_printer)

        layout.addSpacing(12)

        layout.addWidget(QLabel("Data:"))
        self._date_fetch = QDateEdit()
        self._date_fetch.setCalendarPopup(True)
        self._date_fetch.setDisplayFormat("dd/MM/yyyy")
        self._date_fetch.setDate(QDate.currentDate())
        self._date_fetch.setFixedWidth(120)
        layout.addWidget(self._date_fetch)

        self._btn_fetch_date = QPushButton("Buscar Data")
        self._btn_fetch_date.setFixedWidth(110)
        self._btn_fetch_date.clicked.connect(self._fetch_by_date)
        layout.addWidget(self._btn_fetch_date)

        layout.addWidget(QLabel("Filtro:"))
        self._txt_filter = QLineEdit()
        self._txt_filter.setPlaceholderText("NF, cliente, pedido, template...")
        self._txt_filter.setMinimumWidth(180)
        self._txt_filter.textChanged.connect(self._apply_table_filter)
        layout.addWidget(self._txt_filter)

        self._btn_models = QPushButton("Modelos Etiquetas")
        self._btn_models.setFixedWidth(130)
        self._btn_models.clicked.connect(self._open_model_dialog)
        layout.addWidget(self._btn_models)

        layout.addStretch()

        return group

    def _build_table_panel(self) -> QGroupBox:
        group = QGroupBox("Notas Fiscais Monitoradas")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)

        # Barra de acoes da tabela
        top_bar = QHBoxLayout()
        lbl_count = QLabel()
        self._lbl_table_count = lbl_count
        lbl_count.setStyleSheet("color: #8b949e; font-size: 11px;")
        top_bar.addWidget(lbl_count)

        self._progress_fetch = QProgressBar()
        self._progress_fetch.setFixedWidth(180)
        self._progress_fetch.setFixedHeight(14)
        self._progress_fetch.setTextVisible(True)
        self._progress_fetch.setVisible(False)
        top_bar.addWidget(self._progress_fetch)
        top_bar.addStretch()

        self._btn_preview_selected = QPushButton("Visualizar")
        self._btn_preview_selected.setFixedWidth(120)
        self._btn_preview_selected.setToolTip("Visualizar etiqueta da(s) NF-e(s) selecionada(s)")
        self._btn_preview_selected.clicked.connect(self._preview_selected)
        top_bar.addWidget(self._btn_preview_selected)

        self._btn_edit_selected = QPushButton("Editar")
        self._btn_edit_selected.setFixedWidth(100)
        self._btn_edit_selected.setToolTip("Edita os campos da etiqueta selecionada")
        self._btn_edit_selected.clicked.connect(self._edit_selected)
        top_bar.addWidget(self._btn_edit_selected)

        self._btn_print_selected = QPushButton("Imprimir Selecionadas")
        self._btn_print_selected.setObjectName("btnPrint")
        self._btn_print_selected.setFixedWidth(180)
        self._btn_print_selected.setToolTip("Imprime todas as linhas selecionadas")
        self._btn_print_selected.clicked.connect(self._print_selected)
        top_bar.addWidget(self._btn_print_selected)

        btn_clear = QPushButton("Limpar Tabela")
        btn_clear.setFixedWidth(120)
        btn_clear.setToolTip("Remove todas as entradas da tabela (nao apaga do banco de dados)")
        btn_clear.clicked.connect(self._clear_table)
        top_bar.addWidget(btn_clear)

        layout.addLayout(top_bar)

        # Tabela
        self._table = QTableWidget()
        self._table.setColumnCount(len(TABLE_COLUMNS))
        self._table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.sectionResized.connect(self._save_table_column_widths)
        self._table.viewport().installEventFilter(self)
        self._table.setMinimumHeight(200)

        # Larguras iniciais; depois o config.json prevalece.
        col_widths = [90, -1, 50, 115, 120, 55, 100, 115, 90, 55]
        for i, w in enumerate(col_widths):
            if w > 0:
                self._table.setColumnWidth(i, w)
        self._restore_table_column_widths()
        QTimer.singleShot(0, self._fit_table_columns_to_viewport)

        layout.addWidget(self._table)
        return group

    def _restore_table_column_widths(self):
        saved = getattr(config, "table_column_widths", {}) or {}
        if not isinstance(saved, dict):
            return
        self._loading_table_widths = True
        try:
            for name, width in saved.items():
                if name in TABLE_COLUMNS:
                    self._table.setColumnWidth(TABLE_COLUMNS.index(name), max(35, int(width)))
        finally:
            self._loading_table_widths = False

    @Slot(int, int, int)
    def _save_table_column_widths(self, *_):
        if self._loading_table_widths or self._fitting_table_widths:
            return
        QTimer.singleShot(0, self._fit_and_save_table_column_widths)

    def _fit_and_save_table_column_widths(self):
        self._fit_table_columns_to_viewport()
        widths = {
            name: self._table.columnWidth(index)
            for index, name in enumerate(TABLE_COLUMNS)
        }
        config.update_settings(table_column_widths=widths)

    def _fit_table_columns_to_viewport(self):
        if not hasattr(self, "_table"):
            return
        viewport_width = max(0, self._table.viewport().width() - 2)
        total = sum(self._table.columnWidth(i) for i in range(self._table.columnCount()))
        gap = viewport_width - total
        if abs(gap) <= 2:
            self._table.horizontalScrollBar().setValue(0)
            return
        cliente_col = TABLE_COLUMNS.index("Cliente")
        self._fitting_table_widths = True
        try:
            if gap > 0:
                self._table.setColumnWidth(cliente_col, self._table.columnWidth(cliente_col) + gap)
            else:
                remaining = -gap
                for name in ["Cliente", "Template", "Pedido", "Protocolo", "Data Emissão", "Nº NF-e"]:
                    col = TABLE_COLUMNS.index(name)
                    current = self._table.columnWidth(col)
                    minimum = TABLE_MIN_WIDTHS[name]
                    reducible = max(0, current - minimum)
                    if reducible <= 0:
                        continue
                    reduce_by = min(reducible, remaining)
                    self._table.setColumnWidth(col, current - reduce_by)
                    remaining -= reduce_by
                    if remaining <= 0:
                        break
            self._table.horizontalScrollBar().setValue(0)
        finally:
            self._fitting_table_widths = False

    def eventFilter(self, obj, event):
        if hasattr(self, "_table") and obj is self._table.viewport() and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._fit_table_columns_to_viewport)
        return super().eventFilter(obj, event)

    def _build_log_panel(self) -> QGroupBox:
        group = QGroupBox("Console de Logs em Tempo Real")
        self._log_group = group
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)

        top_bar = QHBoxLayout()
        self._btn_toggle_log = QPushButton("▼")
        self._btn_toggle_log.setFixedWidth(32)
        self._btn_toggle_log.setToolTip("Expandir ou recolher logs")
        self._btn_toggle_log.clicked.connect(self._toggle_logs)
        top_bar.addWidget(self._btn_toggle_log)
        top_bar.addStretch()
        btn_copy_log = QPushButton("Copiar Logs")
        btn_copy_log.setFixedWidth(100)
        btn_copy_log.clicked.connect(self._copy_logs)
        top_bar.addWidget(btn_copy_log)
        btn_clear_log = QPushButton("Limpar Log")
        btn_clear_log.setFixedWidth(100)
        btn_clear_log.clicked.connect(self._clear_log)
        top_bar.addWidget(btn_clear_log)
        layout.addLayout(top_bar)

        self._txt_log = QTextEdit()
        self._txt_log.setReadOnly(True)
        self._txt_log.setMinimumHeight(120)
        self._txt_log.setPlaceholderText("Os logs de monitoramento aparecerão aqui em tempo real...")
        layout.addWidget(self._txt_log)

        return group

    def _setup_status_bar(self):
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._lbl_clock = QLabel()
        self._lbl_clock.setStyleSheet("color: #8b949e; font-size: 11px;")
        self._status_bar.addPermanentWidget(self._lbl_clock)
        self._update_clock()

    # ------------------------------------------------------------------ #
    #  DADOS INICIAIS
    # ------------------------------------------------------------------ #
    def _load_initial_data(self):
        self._append_log("Sistema iniciado. Monitoramento em background ativo.", "INFO")
        self._update_table_count()

    # ------------------------------------------------------------------ #
    #  SLOTS E EVENTOS
    # ------------------------------------------------------------------ #
    @Slot()
    def _start_monitoring(self, force_refresh: bool = False):
        printer = self._cmb_printer.currentText()
        if not printer:
            self._append_log("Selecione uma impressora para ativar o monitoramento.", "WARN")
            self._lbl_monitor_dot.setText("● PARADO")
            self._lbl_monitor_dot.setStyleSheet("color: #6e7681; font-size: 13px; font-weight: bold;")
            self._is_fetching = False
            self._btn_fetch_date.setEnabled(True)
            return

        if not config.omie_app_key or not config.omie_app_secret:
            self._append_log(
                "Credenciais Omie (OMIE_APP_KEY / OMIE_APP_SECRET) nao encontradas.\n"
                "Verifique o arquivo .env na raiz do projeto.", "ERROR")
            self._lbl_monitor_dot.setText("● PARADO")
            self._lbl_monitor_dot.setStyleSheet("color: #6e7681; font-size: 13px; font-weight: bold;")
            self._is_fetching = False
            self._btn_fetch_date.setEnabled(True)
            return

        config.update_settings(auto_print=False, printer_name=printer)

        if self.worker and self.worker.isRunning():
            return

        start_date = self._date_fetch.date().toString("dd/MM/yyyy")
        self._is_fetching = True
        self._btn_fetch_date.setEnabled(False)
        self.worker = PollingWorker(
            printer_name=printer,
            db_manager=self.db,
            start_date=start_date,
            force_refresh=force_refresh,
            parent=self,
        )
        self.worker.new_invoice_found.connect(self._on_new_invoice)
        self.worker.invoice_updated.connect(self._on_invoice_updated)
        self.worker.invoice_printed.connect(self._on_invoice_printed)
        self.worker.log_message.connect(self._on_log_message)
        self.worker.progress_changed.connect(self._on_progress_changed)
        self.worker.cycle_started.connect(self._on_cycle_started)
        self.worker.cycle_finished.connect(self._on_cycle_finished)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

        self._lbl_monitor_dot.setText("● ATIVO")
        self._lbl_monitor_dot.setStyleSheet("color: #2ea043; font-size: 13px; font-weight: bold;")
        self._status_bar.showMessage(f"Monitor ativo | Data: {start_date} | Impressora: {printer}")

    @Slot()
    def _stop_monitoring(self):
        if self.worker and not self._stopping_worker:
            self._stopping_worker = True
            self.worker.stop()
            if not self.worker.wait(15000):
                self._append_log("Worker nao encerrou no prazo; finalizando thread.", "WARN")
                self.worker.terminate()
                self.worker.wait(3000)
            self.worker = None
            self._stopping_worker = False

        self._lbl_monitor_dot.setText("● PARADO")
        self._lbl_monitor_dot.setStyleSheet("color: #6e7681; font-size: 13px; font-weight: bold;")
        self._status_bar.showMessage("Monitor parado.")

    @Slot()
    def _restart_monitoring(self, force_refresh: bool = False):
        if self._is_fetching:
            self._append_log("Busca ja em andamento. Aguarde terminar.", "WARN")
            return
        self._is_fetching = True
        self._btn_fetch_date.setEnabled(False)
        self._clear_table()
        self._stop_monitoring()
        self._start_monitoring(force_refresh=force_refresh)

    @Slot()
    def _fetch_by_date(self):
        date_str = self._date_fetch.date().toString("dd/MM/yyyy")
        self._append_log(f"Busca manual para {date_str}. Cache sera revalidado.", "INFO")
        self._restart_monitoring(force_refresh=True)

    @Slot()
    def _on_date_changed(self, *_):
        pass

    @Slot()
    def _preview_selected(self):
        invoices = self._selected_invoices()
        if not invoices:
            QMessageBox.information(self, "Selecione", "Selecione uma NF-e na tabela para visualizar.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Previa de Etiquetas - {len(invoices)} selecionada(s)")
        dialog.resize(940, 680)
        layout = QVBoxLayout(dialog)
        nav_layout = QHBoxLayout()
        layout.addLayout(nav_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QLabel()
        content.setTextFormat(Qt.RichText)
        content.setStyleSheet("background: #d8dee9; padding: 20px;")
        previews = []
        claro_buffer: list[tuple[NormalizedInvoice, int, int]] = []

        def is_claro(inv: NormalizedInvoice) -> bool:
            return inv.template_name in {"claro", "claro_dividida"} or _is_claro_invoice(inv)

        def flush_claro():
            while claro_buffer:
                left = claro_buffer.pop(0)
                right = claro_buffer.pop(0) if claro_buffer else None
                if right:
                    previews.append(ZPLGenerator.preview_html_claro_pair(left[0], right[0], left[1], left[2], right[1], right[2]))
                else:
                    previews.append(ZPLGenerator.preview_html_claro_pair(left[0], None, left[1], left[2]))

        for inv in invoices:
            total = inv.quantidade_volumes or 1
            if is_claro(inv):
                for vol in range(1, total + 1):
                    claro_buffer.append((inv, vol, total))
                while len(claro_buffer) >= 2:
                    left = claro_buffer.pop(0)
                    right = claro_buffer.pop(0)
                    previews.append(ZPLGenerator.preview_html_claro_pair(left[0], right[0], left[1], left[2], right[1], right[2]))
            else:
                for vol in range(1, total + 1):
                    previews.append(ZPLGenerator.preview_html(inv, vol, total))
        flush_claro()
        btn_first = QPushButton("Primeira")
        btn_prev = QPushButton("Anterior")
        lbl_page = QLabel()
        btn_next = QPushButton("Proxima")
        btn_last = QPushButton("Ultima")
        for button in (btn_first, btn_prev, btn_next, btn_last):
            button.setFixedWidth(80)
        lbl_page.setAlignment(Qt.AlignCenter)
        lbl_page.setMinimumWidth(120)
        nav_layout.addStretch()
        nav_layout.addWidget(btn_first)
        nav_layout.addWidget(btn_prev)
        nav_layout.addWidget(lbl_page)
        nav_layout.addWidget(btn_next)
        nav_layout.addWidget(btn_last)
        nav_layout.addStretch()

        current_page = {"index": 0}

        def show_page(index: int):
            if not previews:
                return
            safe_index = max(0, min(index, len(previews) - 1))
            current_page["index"] = safe_index
            content.setText(previews[safe_index])
            dialog.setWindowTitle(f"Previa de Etiquetas - Pagina {safe_index + 1}/{len(previews)}")
            lbl_page.setText(f"Pagina {safe_index + 1} de {len(previews)}")
            btn_first.setEnabled(safe_index > 0)
            btn_prev.setEnabled(safe_index > 0)
            btn_next.setEnabled(safe_index < len(previews) - 1)
            btn_last.setEnabled(safe_index < len(previews) - 1)

        btn_first.clicked.connect(lambda: show_page(0))
        btn_prev.clicked.connect(lambda: show_page(current_page["index"] - 1))
        btn_next.clicked.connect(lambda: show_page(current_page["index"] + 1))
        btn_last.clicked.connect(lambda: show_page(len(previews) - 1))
        for widget in (btn_first, btn_prev, lbl_page, btn_next, btn_last):
            widget.setVisible(len(previews) > 1)

        show_page(0)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        dialog.exec()

    @Slot()
    def _edit_selected(self):
        invoices = self._selected_invoices()
        if len(invoices) != 1:
            QMessageBox.information(self, "Selecione", "Selecione exatamente uma NF-e para editar.")
            return

        dialog = InvoiceEditDialog(invoices[0], self)
        if dialog.exec() != QDialog.Accepted:
            return

        updated = dialog.updated_invoice
        updated = self._maybe_save_model_from_edit(invoices[0], updated)
        self._invoice_map[updated.id_nfe] = updated
        self._update_invoice_row_values(updated)
        self._append_log(f"Etiqueta da NF-e {ZPLGenerator._format_nf(updated.numero_nf)} editada para esta sessao.", "INFO")

    def _maybe_save_model_from_edit(self, original: NormalizedInvoice, updated: NormalizedInvoice) -> NormalizedInvoice:
        stable_changed = any([
            (original.cliente_nome or "") != (updated.cliente_nome or ""),
            (original.requisitante or "") != (updated.requisitante or ""),
            (original.label_note or "") != (updated.label_note or ""),
            (original.template_name or "default") != (updated.template_name or "default"),
        ])
        if not stable_changed:
            return updated

        answer = QMessageBox.question(
            self,
            "Criar modelo?",
            "Salvar estas alterações como modelo para este cliente nas próximas NF-e?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return updated

        source_client = (original.cliente_nome or updated.cliente_nome or "").strip()
        if not source_client:
            QMessageBox.warning(self, "Modelo", "Cliente vazio. Modelo nao criado.")
            return updated

        try:
            rules_path = Path(__file__).resolve().parent.parent / "core" / "rules.json"
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = json.load(f)

            edited_client = (updated.cliente_nome or "").strip()
            condition_value = edited_client if edited_client and edited_client.upper() in source_client.upper() else source_client
            key = condition_value.upper()
            old_rule = rules.get(key, {})
            overrides = dict(old_rule.get("overrides") or {})
            if updated.cliente_nome and updated.cliente_nome != original.cliente_nome:
                overrides["cliente_nome"] = updated.cliente_nome
            if updated.requisitante and updated.requisitante != original.requisitante:
                overrides["requisitante"] = updated.requisitante

            rules[key] = {
                "name": updated.cliente_nome or old_rule.get("name") or source_client,
                "template": updated.template_name or "default",
                "label_note": updated.label_note or "",
                "conditions": [{
                    "field": "cliente",
                    "operator": "contains",
                    "value": condition_value,
                }],
                "overrides": overrides,
                "mappings": old_rule.get("mappings") or self._default_model_mappings(),
            }
            with open(rules_path, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=4, ensure_ascii=False)

            refreshed = updated.model_copy(update={
                "model_name": rules[key]["name"],
                "template_name": rules[key]["template"],
                "label_note": rules[key].get("label_note") or None,
            })
            self.db.save_invoice_cache(refreshed)
            self._append_log(f"Modelo criado/atualizado para cliente: {condition_value}.", "INFO")
            return refreshed
        except Exception as e:
            QMessageBox.critical(self, "Erro ao salvar modelo", str(e))
            return updated

    @Slot()
    def _print_selected(self):
        invoices = self._selected_invoices()
        if not invoices:
            QMessageBox.information(self, "Selecione", "Selecione uma ou mais NF-es na tabela para imprimir.")
            return

        printer = self._cmb_printer.currentText()
        if not printer:
            QMessageBox.warning(self, "Aviso", "Selecione uma impressora.")
            return

        resp = QMessageBox.question(
            self,
            "Imprimir",
            f"Imprimir {len(invoices)} NF-e(s) selecionada(s) em '{printer}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if resp != QMessageBox.Yes:
            return

        from app.api.dp_generator import DPGenerator
        labels = DPGenerator.generate_batch(invoices) if config.use_dp else ZPLGenerator.generate_batch(invoices)
        success_count = 0
        for idx, zpl in enumerate(labels, 1):
            label_id = f"SELECAO_{idx}_{datetime.now().strftime('%H%M%S')}"
            if PrinterService.print_zpl(printer, zpl, label_id):
                success_count += 1

        all_success = success_count == len(labels)
        status = "IMPRESSO" if all_success else "ERRO"
        for inv in invoices:
            self.db.mark_nfe_as_processed(
                id_nfe=inv.id_nfe,
                numero_nf=inv.numero_nf,
                chave_nfe=inv.chave_nfe or "",
                cliente_nome=inv.cliente_nome,
                status=status,
                volumes=inv.quantidade_volumes
            )
            self._set_invoice_status(inv.id_nfe, status)

        self._append_log(
            f"Impressao concluida: {success_count}/{len(labels)} etiqueta(s) enviadas para '{printer}'.",
            "INFO" if all_success else "ERROR"
        )

    @Slot()
    def _refresh_printer_list(self):
        self._refreshing_printers = True
        current = self._cmb_printer.currentText()
        self._cmb_printer.clear()
        printers = PrinterService.list_printers()
        for p in printers:
            self._cmb_printer.addItem(p)
        selected = self._preferred_printer(printers, current)
        idx = self._cmb_printer.findText(selected) if selected else -1
        if idx >= 0:
            self._cmb_printer.setCurrentIndex(idx)
            config.update_settings(printer_name=selected)
        self._refreshing_printers = False

    @staticmethod
    def _preferred_printer(printers: list[str], current: str = "") -> str:
        if not printers:
            return ""

        def is_honeywell(name: str) -> bool:
            normalized = name.upper()
            return "HONEYWELL" in normalized or "PC42" in normalized

        def find_exact(name: str) -> str:
            return next((p for p in printers if p == name), "")

        if current and is_honeywell(current) and find_exact(current):
            return current
        for printer in printers:
            if is_honeywell(printer):
                return printer
        if config.printer_name and find_exact(config.printer_name):
            return config.printer_name
        for printer in printers:
            normalized = printer.upper()
            if "ONENOTE" not in normalized and "SIMULADO" not in normalized:
                return printer
        return printers[0]

    @Slot(str)
    def _on_printer_changed(self, printer: str):
        if self._refreshing_printers or not printer:
            return
        config.update_settings(printer_name=printer)
        self._status_bar.showMessage(f"Impressora selecionada: {printer}")

    @Slot(int)
    def _on_interval_changed(self, value: int):
        config.update_settings(polling_interval=value)

    @Slot(bool)
    def _on_autoprint_changed(self, checked: bool):
        config.update_settings(auto_print=checked)

    @Slot()
    def _clear_table(self):
        self._table.setRowCount(0)
        self._invoice_id_map.clear()
        self._invoice_map.clear()
        self._update_table_count()

    @Slot()
    def _clear_log(self):
        self._txt_log.clear()

    @Slot()
    def _copy_logs(self):
        QApplication.clipboard().setText(self._txt_log.toPlainText())
        self._status_bar.showMessage("Logs copiados para a area de transferencia.")

    def _download_danfe(self, invoice_id: int):
        inv = self._invoice_map.get(invoice_id)
        if not inv:
            return
        nf = "".join(ch for ch in str(inv.numero_nf or "") if ch.isdigit()) or str(inv.id_nfe)
        default_path = str(Path.home() / "Downloads" / f"DANFE_NF_{nf}.pdf")
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar DANFE",
            default_path,
            "PDF (*.pdf)"
        )
        if not target:
            self._append_log(f"Download do DANFE da NF-e {ZPLGenerator._format_nf(inv.numero_nf)} cancelado pelo usuario.", "INFO")
            return

        self._append_log(f"Baixando DANFE da NF-e {ZPLGenerator._format_nf(inv.numero_nf)} para: {target}", "INFO")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            client = OmieClient()
            cached_path = self._cached_danfe_path(inv.id_nfe)
            if cached_path:
                shutil.copyfile(cached_path, target)
                path = Path(target)
                self._append_log(f"DANFE copiado do cache local: {path}", "INFO")
            else:
                cooldown_msg = self._cooldown_message(inv.id_nfe)
                if cooldown_msg:
                    self._append_log(cooldown_msg, "WARN")
                    QMessageBox.warning(self, "Aguarde", cooldown_msg)
                    return
                path = client.baixar_danfe(inv, target_path=Path(target))
            self._append_log(f"DANFE salvo em: {path}", "INFO")
            enriched = client.enrich_invoice_from_danfe_pdf(inv, path)
            self._invoice_map[enriched.id_nfe] = enriched
            self.db.save_invoice_cache(enriched, enrichment_status="complete", danfe_path=str(path), last_error="", next_attempt_at="")
            self._update_invoice_row_values(enriched)
            self._append_log(
                f"Leitura DANFE NF-e {ZPLGenerator._format_nf(enriched.numero_nf)}: "
                f"UF={'OK' if enriched.cliente_uf else 'PENDENTE'} | "
                f"Pedido={'OK' if _invoice_pedido_value(enriched) != 'XXXXX' else 'PENDENTE'} | "
                f"Protocolo={'OK' if _invoice_protocolo_value(enriched) != 'XXXXX' else 'XXXXX'}",
                "INFO"
            )
        except Exception as e:
            wait = OmieClient.parse_redundant_wait_seconds(str(e))
            if wait:
                next_attempt = datetime.now().timestamp() + wait
                self.db.save_invoice_cache(
                    inv,
                    enrichment_status="cooldown",
                    last_error=str(e),
                    last_attempt_at=datetime.now().isoformat(),
                    next_attempt_at=datetime.fromtimestamp(next_attempt).isoformat(),
                )
                msg = f"Aguarde {wait}s para nova tentativa da NF-e {ZPLGenerator._format_nf(inv.numero_nf)}."
                self._append_log(msg, "WARN")
                QMessageBox.warning(self, "Aguarde", msg)
                return
            self._append_log(f"Erro ao baixar DANFE da NF-e {ZPLGenerator._format_nf(inv.numero_nf)}: {e}", "ERROR")
            QMessageBox.critical(self, "Erro ao baixar DANFE", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def _cached_danfe_path(self, invoice_id: int) -> Path | None:
        meta = self.db.get_invoice_cache_meta(invoice_id)
        raw = meta.get("danfe_path")
        if not raw:
            return None
        path = Path(raw)
        return path if path.exists() else None

    def _cooldown_message(self, invoice_id: int) -> str:
        meta = self.db.get_invoice_cache_meta(invoice_id)
        raw = meta.get("next_attempt_at")
        if not raw:
            return ""
        try:
            next_attempt = datetime.fromisoformat(raw)
        except ValueError:
            return ""
        remaining = int((next_attempt - datetime.now()).total_seconds())
        if remaining <= 0:
            return ""
        return f"Aguarde {remaining}s para nova tentativa."

    @Slot()
    def _toggle_logs(self):
        if self._logs_collapsed:
            self._expand_logs()
        else:
            self._collapse_logs()

    def _collapse_logs(self):
        if not hasattr(self, "_main_splitter"):
            return
        self._logs_collapsed = True
        self._txt_log.setVisible(False)
        self._btn_toggle_log.setText("▼")
        total = max(self._main_splitter.height(), 700)
        self._main_splitter.setSizes([total - 46, 46])

    def _expand_logs(self):
        if not hasattr(self, "_main_splitter"):
            return
        self._logs_collapsed = False
        self._txt_log.setVisible(True)
        self._btn_toggle_log.setText("▲")
        total = max(self._main_splitter.height(), 700)
        self._main_splitter.setSizes([int(total * 0.72), int(total * 0.28)])

    # ------------------------------------------------------------------ #
    #  SLOTS DO WORKER
    # ------------------------------------------------------------------ #
    @Slot(NormalizedInvoice)
    def _on_new_invoice(self, inv: NormalizedInvoice):
        status = "IMPRESSO" if self.db.is_nfe_processed(inv.id_nfe) else "PENDENTE"
        self._add_invoice_row(inv, status)

    @Slot(NormalizedInvoice)
    def _on_invoice_updated(self, inv: NormalizedInvoice):
        self._invoice_map[inv.id_nfe] = inv
        self._update_invoice_row_values(inv)

    @Slot(str, bool, str)
    def _on_invoice_printed(self, id_nfe: str, success: bool, status: str):
        self._set_invoice_status(id_nfe, status)

    @Slot(str, str)
    def _on_log_message(self, message: str, level: str):
        self._append_log(message, level)

    @Slot()
    def _on_cycle_started(self):
        self._status_bar.showMessage("Varrendo ERP Omie...")
        self._progress_fetch.setRange(0, 0)
        self._progress_fetch.setFormat("Carregando...")
        self._progress_fetch.setVisible(True)

    @Slot(int, int)
    def _on_progress_changed(self, done: int, total: int):
        total = max(total, 0)
        if total <= 0:
            self._progress_fetch.setRange(0, 0)
            self._progress_fetch.setFormat("Carregando...")
        else:
            self._progress_fetch.setRange(0, total)
            self._progress_fetch.setValue(min(done, total))
            self._progress_fetch.setFormat(f"{min(done, total)}/{total}")
        self._progress_fetch.setVisible(True)

    @Slot(int)
    def _on_cycle_finished(self, count: int):
        msg = f"Varredura concluida | {count} NF-e(s) carregada(s)"
        self._status_bar.showMessage(msg)
        self._is_fetching = False
        self._btn_fetch_date.setEnabled(True)
        self._progress_fetch.setVisible(False)

    @Slot()
    def _on_worker_finished(self):
        self._is_fetching = False
        self._btn_fetch_date.setEnabled(True)
        self._progress_fetch.setVisible(False)

    @Slot(str)
    def _on_error(self, msg: str):
        self._append_log(f"[ERRO CRITICO] {msg}", "ERROR")
        self._status_bar.showMessage(f"Erro: {msg}")

    # ------------------------------------------------------------------ #
    #  HELPERS
    # ------------------------------------------------------------------ #
    def _append_log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")

        color_map = {
            "INFO":  "#3fb950",
            "WARN":  "#d29922",
            "ERROR": "#f85149",
        }
        color = color_map.get(level.upper(), "#c9d1d9")
        prefix_map = {"INFO": "[INFO]", "WARN": "[AVISO]", "ERROR": "[ERRO]"}
        prefix = prefix_map.get(level.upper(), "[LOG]")

        html = (
            f'<span style="color:#484f58;">[{timestamp}]</span> '
            f'<span style="color:{color}; font-weight:bold;">{prefix}</span> '
            f'<span style="color:#c9d1d9;">{message}</span>'
        )
        self._txt_log.append(html)
        # Auto-scroll
        cursor = self._txt_log.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._txt_log.setTextCursor(cursor)

    def _add_invoice_row(self, inv: NormalizedInvoice, status: str):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._invoice_id_map[row] = inv.id_nfe
        self._invoice_map[inv.id_nfe] = inv

        values = [
            ZPLGenerator._format_nf(inv.numero_nf),
            inv.cliente_nome or "",
            inv.cliente_uf or "",
            _invoice_pedido_value(inv),
            _invoice_protocolo_value(inv),
            str(inv.quantidade_volumes),
            _invoice_template_display(inv),
            inv.data_emissao or "",
            status,
            "",
        ]

        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignCenter)
            if TABLE_COLUMNS[col] == "Template":
                item.setToolTip(_invoice_template_tooltip(inv))
            self._table.setItem(row, col, item)

        btn_download = QPushButton("Baixar")
        btn_download.setFlat(True)
        btn_download.setCursor(Qt.PointingHandCursor)
        btn_download.setFixedWidth(46)
        btn_download.setToolTip("Escolher local para salvar DANFE")
        btn_download.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #58a6ff; "
            "text-decoration: underline; padding: 0px; } "
            "QPushButton:hover { color: #79c0ff; }"
        )
        btn_download.clicked.connect(lambda _, invoice_id=inv.id_nfe: self._download_danfe(invoice_id))
        self._table.setCellWidget(row, TABLE_COLUMNS.index("Ações"), btn_download)

        self._colorize_status_cell(row, status)
        self._table.scrollToBottom()
        self._update_table_count()
        self._apply_table_filter()

    def _update_invoice_row_values(self, inv: NormalizedInvoice):
        for row, mapped_id in self._invoice_id_map.items():
            if mapped_id != inv.id_nfe:
                continue
            values = {
                "Nº NF-e": ZPLGenerator._format_nf(inv.numero_nf),
                "Cliente": inv.cliente_nome or "",
                "UF": inv.cliente_uf or "",
                "Pedido": _invoice_pedido_value(inv),
                "Protocolo": _invoice_protocolo_value(inv),
                "Vol.": str(inv.quantidade_volumes),
                "Template": _invoice_template_display(inv),
                "Data Emissão": inv.data_emissao or "",
            }
            for column_name, value in values.items():
                col = TABLE_COLUMNS.index(column_name)
                item = self._table.item(row, col)
                if item:
                    item.setText(value)
                    if column_name == "Template":
                        item.setToolTip(_invoice_template_tooltip(inv))
            break

    def _selected_invoices(self) -> list[NormalizedInvoice]:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        invoices: list[NormalizedInvoice] = []
        for row in rows:
            if self._table.isRowHidden(row):
                continue
            id_nfe = self._invoice_id_map.get(row)
            if id_nfe is not None and id_nfe in self._invoice_map:
                invoices.append(self._invoice_map[id_nfe])
        return invoices

    def _set_invoice_status(self, id_nfe: int | str, status: str):
        for row, mapped_id in self._invoice_id_map.items():
            if str(mapped_id) == str(id_nfe):
                col_status = TABLE_COLUMNS.index("Status")
                item = self._table.item(row, col_status)
                if item:
                    item.setText(status)
                self._colorize_status_cell(row, status)
                break

    @Slot(str)
    def _apply_table_filter(self, *_):
        query = self._txt_filter.text().strip().lower() if hasattr(self, "_txt_filter") else ""
        visible_count = 0
        for row in range(self._table.rowCount()):
            row_text = " ".join(
                self._table.item(row, col).text().lower()
                for col in range(self._table.columnCount())
                if self._table.item(row, col)
            )
            hidden = bool(query and query not in row_text)
            self._table.setRowHidden(row, hidden)
            if not hidden:
                visible_count += 1
        total = self._table.rowCount()
        self._lbl_table_count.setText(f"{visible_count}/{total} nota(s) visiveis")

    @Slot()
    def _open_model_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Modelos Etiquetas")
        dialog.resize(820, 500)
        root = QHBoxLayout(dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(14)

        rules_path = Path(__file__).resolve().parent.parent / "core" / "rules.json"
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
        except Exception:
            rules = {}

        selected_key: str | None = None

        left_panel = QGroupBox("Modelos")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 12, 10, 10)
        left_layout.setSpacing(8)

        btn_new = QPushButton("+ Novo Modelo")
        btn_new.setToolTip("Criar uma nova regra de modelo")
        left_layout.addWidget(btn_new)

        model_list = QListWidget()
        model_list.setSelectionMode(QAbstractItemView.SingleSelection)
        model_list.setMinimumWidth(280)
        left_layout.addWidget(model_list, stretch=1)

        order_actions = QHBoxLayout()
        btn_up = QPushButton("↑")
        btn_down = QPushButton("↓")
        btn_delete = QPushButton("🗑")
        btn_up.setFixedWidth(34)
        btn_down.setFixedWidth(34)
        btn_delete.setFixedWidth(38)
        btn_up.setToolTip("Mover modelo para cima")
        btn_down.setToolTip("Mover modelo para baixo")
        btn_delete.setToolTip("Remover modelo selecionado")
        order_actions.addWidget(btn_up)
        order_actions.addWidget(btn_down)
        order_actions.addStretch()
        order_actions.addWidget(btn_delete)
        left_layout.addLayout(order_actions)
        root.addWidget(left_panel, stretch=1)

        right_panel = QGroupBox("Configuração")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 14, 12)
        right_layout.setSpacing(10)

        right_layout.addWidget(QLabel("Nome do modelo"))
        txt_name = QLineEdit()
        txt_name.setPlaceholderText("Ex: Modelo Cliente XPTO")
        right_layout.addWidget(txt_name)

        right_layout.addWidget(QLabel("Regra de identificação"))
        form = QFormLayout()
        cmb_field = QComboBox()
        cmb_field.addItem("Nome do Cliente", "cliente")
        cmb_field.addItem("Texto complementar da NF/DANFE", "texto")
        cmb_field.addItem("Cidade", "cidade")
        cmb_field.addItem("UF", "uf")
        cmb_operator = QComboBox()
        cmb_operator.addItem("contém", "contains")
        cmb_operator.addItem("começa com", "starts_with")
        cmb_operator.addItem("igual a", "equals")
        txt_condition = QLineEdit()
        txt_condition.setPlaceholderText("Ex: Claro, Petrobras, Rio, RJ")
        form.addRow("Campo:", cmb_field)
        form.addRow("Comparação:", cmb_operator)
        form.addRow("Valor:", txt_condition)
        right_layout.addLayout(form)

        right_layout.addWidget(QLabel("Layout da etiqueta"))
        cmb_template = QComboBox()
        cmb_template.addItem("Padrao 110x70", "default")
        cmb_template.addItem("Claro dividida", "claro_dividida")
        cmb_template.addItem("GSK", "gsk")
        right_layout.addWidget(cmb_template)

        right_layout.addWidget(QLabel("Observação"))
        txt_note = QTextEdit()
        txt_note.setFixedHeight(68)
        txt_note.setPlaceholderText("Observação impressa na etiqueta padrão")
        right_layout.addWidget(txt_note)

        right_layout.addStretch()
        actions = QHBoxLayout()
        btn_default = QPushButton("Tornar Padrão")
        btn_save = QPushButton("Salvar")
        btn_cancel = QPushButton("Cancelar")
        actions.addStretch()
        actions.addWidget(btn_default)
        actions.addWidget(btn_save)
        actions.addWidget(btn_cancel)
        right_layout.addLayout(actions)
        root.addWidget(right_panel, stretch=2)

        def model_name_for(key: str, rule: dict) -> str:
            return rule.get("name") or ("Padrão 110x70" if key == "DEFAULT" else key.title())

        def first_condition_for(key: str, rule: dict) -> dict:
            conditions = rule.get("conditions") or []
            if conditions:
                return conditions[0]
            return {"field": "cliente", "operator": "contains", "value": "" if key == "DEFAULT" else key}

        def condition_summary(key: str, rule: dict) -> str:
            condition = first_condition_for(key, rule)
            field_label = self._model_field_label(condition.get("field", "cliente"))
            operator_label = self._model_operator_label(condition.get("operator", "contains"))
            value = str(condition.get("value", "")).strip()
            return f"{field_label} {operator_label} {value}".strip()

        def ordered_rule_keys() -> list[str]:
            def order_key(item: tuple[str, dict]):
                key, rule = item
                name = model_name_for(key, rule).upper()
                sort_order = rule.get("sort_order")
                if isinstance(sort_order, int):
                    return (0, sort_order, name)
                return (1, name)
            return [key for key, _ in sorted(rules.items(), key=order_key)]

        def refresh_list(select_key: str | None = None):
            model_list.clear()
            for key in ordered_rule_keys():
                rule = rules[key]
                name = model_name_for(key, rule)
                template = rule.get("template", "default")
                summary = condition_summary(key, rule)
                item = QListWidgetItem(f"{name}\n{template} · {summary}")
                item.setData(Qt.UserRole, key)
                model_list.addItem(item)
                if key == select_key:
                    model_list.setCurrentItem(item)
            if model_list.currentRow() < 0 and model_list.count() > 0:
                model_list.setCurrentRow(0)
            update_default_button()

        def rule_signature(key: str, rule: dict) -> tuple[str, str, str]:
            condition = first_condition_for(key, rule)
            return (
                str(condition.get("field") or "cliente").lower(),
                str(condition.get("operator") or "contains").lower(),
                str(condition.get("value") or "").strip().upper(),
            )

        def same_signature_keys(key: str | None) -> list[str]:
            if not key or key not in rules:
                return []
            signature = rule_signature(key, rules[key])
            return [candidate for candidate, rule in rules.items() if rule_signature(candidate, rule) == signature]

        def is_default_for_selected() -> bool:
            if not selected_key or selected_key not in rules:
                return False
            group = same_signature_keys(selected_key)
            return len(group) <= 1 or bool(rules[selected_key].get("default_for_client"))

        def update_default_button():
            if not selected_key or selected_key not in rules:
                btn_default.setText("Tornar Padrão")
                btn_default.setEnabled(False)
                return
            if is_default_for_selected():
                btn_default.setText("Padrão")
                btn_default.setEnabled(False)
            else:
                btn_default.setText("Tornar Padrão")
                btn_default.setEnabled(True)

        def load_selected():
            nonlocal selected_key
            item = model_list.currentItem()
            if not item:
                return
            selected_key = item.data(Qt.UserRole)
            rule = rules.get(selected_key, {})
            condition = first_condition_for(selected_key, rule)
            txt_name.setText(model_name_for(selected_key, rule))
            field_idx = cmb_field.findData(condition.get("field", "cliente"))
            op_idx = cmb_operator.findData(condition.get("operator", "contains"))
            cmb_field.setCurrentIndex(field_idx if field_idx >= 0 else 0)
            cmb_operator.setCurrentIndex(op_idx if op_idx >= 0 else 0)
            txt_condition.setText(str(condition.get("value", "")))
            idx = cmb_template.findData(rule.get("template", "default"))
            if idx >= 0:
                cmb_template.setCurrentIndex(idx)
            txt_note.setPlainText(str(rule.get("label_note") or ""))
            update_default_button()

        def clear_form():
            nonlocal selected_key
            selected_key = None
            txt_name.clear()
            txt_condition.clear()
            txt_note.clear()
            cmb_field.setCurrentIndex(0)
            cmb_operator.setCurrentIndex(0)
            cmb_template.setCurrentIndex(0)
            model_list.clearSelection()
            txt_name.setFocus()
            update_default_button()

        def save_model():
            nonlocal selected_key
            condition_value = txt_condition.text().strip()
            is_default = selected_key == "DEFAULT"
            if not condition_value and not is_default:
                QMessageBox.warning(dialog, "Campo obrigatorio", "Informe o valor da condição.")
                return

            new_key = "DEFAULT" if is_default else condition_value.upper()
            condition = {
                "field": cmb_field.currentData(),
                "operator": cmb_operator.currentData(),
                "value": condition_value,
            }
            try:
                old_rule = rules.get(selected_key or new_key, {})
                if selected_key and selected_key != new_key and selected_key in rules and selected_key != "DEFAULT":
                    rules.pop(selected_key)
                rules[new_key] = {
                    "name": txt_name.text().strip() or ("Padrão 110x70" if new_key == "DEFAULT" else new_key.title()),
                    "template": cmb_template.currentData(),
                    "label_note": txt_note.toPlainText().strip(),
                    "default_for_client": bool(old_rule.get("default_for_client")),
                    "conditions": [condition],
                    "mappings": old_rule.get("mappings") or self._default_model_mappings(),
                }
                with open(rules_path, "w", encoding="utf-8") as f:
                    json.dump(rules, f, indent=4, ensure_ascii=False)
                selected_key = new_key
                refresh_list(select_key=new_key)
                self._append_log(f"Modelo salvo: {rules[new_key]['name']} ({rules[new_key]['template']}).", "INFO")
                self._reapply_models_to_visible_invoices()
            except Exception as e:
                QMessageBox.critical(dialog, "Erro ao salvar", str(e))

        def persist_rules():
            with open(rules_path, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=4, ensure_ascii=False)

        def move_selected(delta: int):
            nonlocal selected_key
            row = model_list.currentRow()
            if row < 0:
                return
            keys = ordered_rule_keys()
            target = row + delta
            if target < 0 or target >= len(keys):
                return
            keys[row], keys[target] = keys[target], keys[row]
            for index, key in enumerate(keys, start=1):
                rules[key]["sort_order"] = index * 10
            persist_rules()
            selected_key = keys[target]
            refresh_list(select_key=selected_key)

        def make_selected_default():
            if not selected_key or selected_key not in rules:
                return
            for key in same_signature_keys(selected_key):
                rules[key]["default_for_client"] = key == selected_key
            persist_rules()
            refresh_list(select_key=selected_key)

        def delete_selected():
            nonlocal selected_key
            if not selected_key or selected_key not in rules:
                return
            selected_rule = rules[selected_key]
            protected_keys = {"DEFAULT", "CLARO", "TELMEX"}
            is_protected_claro = (
                selected_rule.get("template") == "claro_dividida"
                and str(selected_rule.get("name") or "").strip().upper() == "CLARO DIVIDIDA"
            )
            if selected_key in protected_keys or is_protected_claro:
                QMessageBox.information(dialog, "Modelo protegido", "DEFAULT e Claro Dividida nao podem ser removidos.")
                return

            model_name = model_name_for(selected_key, selected_rule)
            answer = QMessageBox.question(
                dialog,
                "Confirmar exclusao",
                f"Remover o modelo '{model_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

            old_row = model_list.currentRow()
            rules.pop(selected_key, None)
            persist_rules()
            selected_key = None
            refresh_list()
            if model_list.count() > 0:
                model_list.setCurrentRow(min(old_row, model_list.count() - 1))
            self._append_log(f"Modelo removido: {model_name}.", "INFO")
            self._reapply_models_to_visible_invoices()

        btn_new.clicked.connect(clear_form)
        btn_up.clicked.connect(lambda: move_selected(-1))
        btn_down.clicked.connect(lambda: move_selected(1))
        btn_delete.clicked.connect(delete_selected)
        btn_default.clicked.connect(make_selected_default)
        btn_cancel.clicked.connect(dialog.reject)
        btn_save.clicked.connect(save_model)
        model_list.currentItemChanged.connect(lambda *_: load_selected())
        refresh_list()
        load_selected()
        dialog.exec()

    def _reapply_models_to_visible_invoices(self):
        client = OmieClient()
        changed = 0
        for invoice_id, inv in list(self._invoice_map.items()):
            refreshed = client.apply_rules_to_invoice(inv)
            if refreshed != inv:
                self._invoice_map[invoice_id] = refreshed
                self.db.save_invoice_cache(refreshed)
                self._update_invoice_row_values(refreshed)
                changed += 1
        if changed:
            self._append_log(f"Modelos reaplicados em {changed} NF-e(s) visiveis.", "INFO")

    @staticmethod
    def _model_field_label(field: str) -> str:
        return {
            "cliente": "Nome do Cliente",
            "texto": "Texto complementar",
            "cidade": "Cidade",
            "uf": "UF",
        }.get(str(field), "Nome do Cliente")

    @staticmethod
    def _model_operator_label(operator: str) -> str:
        return {
            "contains": "contém",
            "starts_with": "começa com",
            "equals": "igual a",
        }.get(str(operator), "contém")

    @staticmethod
    def _default_model_mappings() -> dict:
        return {
            "oc": {
                "source": "observacoes",
                "regex": "\\b(?:OC|O/C|ORDEM\\s+DE\\s+COMPRA|PEDIDO\\s+DE\\s+COMPRA|PEDIDO\\s+COMPRA|PEDIDO)\\b\\s*[:=/\\-\\s]?\\s*([A-Za-z0-9][A-Za-z0-9./\\-]*)"
            },
            "requisitante": {
                "source": "observacoes",
                "regex": "(?:\\bA/C\\b|\\bAC\\s+DE\\b|\\bAOS\\s+CUIDADOS\\s+DE\\b|\\bREQUISITANTE\\b|\\bSOLICITANTE\\b)\\s*[:\\-\\s]?\\s*([^|\\n;]+)"
            },
            "numero_ordem": {
                "source": "observacoes",
                "regex": "(?:\\bN[º°O]?\\s*(?:DO\\s*)?PEDIDO\\b|\\bNUMERO\\s+(?:DO\\s*)?PEDIDO\\b|\\bN[º°O]?\\s*ORDEM\\b|\\bNUMERO\\s+DA\\s+ORDEM\\b|\\bNRO\\s*ORDEM\\b|\\bORDEM\\s+DE\\s+COMPRA\\b|\\bPEDIDO\\s+DE\\s+COMPRA\\b|\\bPEDIDO\\s+COMPRA\\b|\\bPEDIDO\\b)\\s*[:=/\\-\\s]?\\s*([A-Za-z0-9][A-Za-z0-9./\\-]*)"
            }
        }

    def _save_client_model(self, dialog: QDialog, condition: dict | str, template_name: str, model_name: str = "", close_dialog: bool = True):
        if isinstance(condition, dict):
            condition_value = str(condition.get("value") or "").strip()
            condition_field = str(condition.get("field") or "cliente")
            condition_operator = str(condition.get("operator") or "contains")
        else:
            condition_value = str(condition or "").strip()
            condition_field = "cliente"
            condition_operator = "contains"

        key = condition_value.upper()
        if not key:
            QMessageBox.warning(dialog, "Campo obrigatorio", "Informe o valor da condição.")
            return

        rules_path = Path(__file__).resolve().parent.parent / "core" / "rules.json"
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
            rules[key] = {
                "name": model_name.strip() or key.title(),
                "template": template_name,
                "conditions": [{
                    "field": condition_field,
                    "operator": condition_operator,
                    "value": condition_value,
                }],
                "mappings": self._default_model_mappings()
            }
            with open(rules_path, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=4, ensure_ascii=False)
            self._append_log(f"Modelo salvo para cliente '{key}' usando template '{template_name}'.", "INFO")
            if close_dialog:
                dialog.accept()
        except Exception as e:
            QMessageBox.critical(dialog, "Erro ao salvar", str(e))

    def _colorize_status_cell(self, row: int, status: str):
        col_status = TABLE_COLUMNS.index("Status")
        item = self._table.item(row, col_status)
        if not item:
            return
        bg, fg = STATUS_COLORS.get(status.upper(), ("#30363d", "#e6edf3"))
        item.setBackground(QColor(bg))
        item.setForeground(QColor(fg))
        font = item.font()
        font.setBold(True)
        item.setFont(font)

    def _update_table_count(self):
        total = self._table.rowCount()
        visible = sum(1 for row in range(total) if not self._table.isRowHidden(row))
        self._lbl_table_count.setText(f"{visible}/{total} nota(s) visiveis")

    def _update_clock(self):
        now = datetime.now().strftime("%d/%m/%Y  %H:%M:%S")
        self._lbl_clock.setText(now)

    # ------------------------------------------------------------------ #
    #  FECHAMENTO
    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Confirmar Saida",
                "O monitor esta ativo. Deseja realmente fechar?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.worker.stop()
            if not self.worker.wait(15000):
                self.worker.terminate()
                self.worker.wait(3000)
            self.worker = None
        event.accept()
