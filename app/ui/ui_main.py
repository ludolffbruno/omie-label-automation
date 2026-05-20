"""
Janela principal da aplicação Omie Label Automation.
Interface desktop profissional com tema escuro, dashboard de monitoramento,
console de logs em tempo real e controles integrados.
"""

import sys
import json
from datetime import datetime
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QSplitter, QGroupBox,
    QHeaderView, QStatusBar, QMessageBox, QCheckBox, QApplication,
    QLineEdit, QDateEdit, QDialog, QScrollArea, QAbstractItemView
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QDate
from PySide6.QtGui import QFont, QColor, QIcon, QTextCursor
from loguru import logger

from app.core.config import config
from app.database.models import DatabaseManager, NormalizedInvoice
from app.api.omie_client import OmieClient
from app.api.printer_service import PrinterService
from app.api.zpl_generator import ZPLGenerator
from app.core.polling_worker import PollingWorker


# Colunas da tabela de NF-e
TABLE_COLUMNS = [
    "Nº NF-e", "Cliente", "UF", "Pedido", "Ordem/OC", "Req.", "Volumes",
    "Template", "Data Emissão", "Status"
]

# Cores de status para destaque visual
STATUS_COLORS = {
    "IMPRESSO":  ("#2ea043", "#ffffff"),  # Verde
    "NOVO":      ("#388bfd", "#ffffff"),  # Azul
    "PENDENTE":  ("#d29922", "#ffffff"),  # Amarelo
    "ERRO":      ("#da3633", "#ffffff"),  # Vermelho
    "APROVADA":  ("#388bfd", "#ffffff"),  # Azul
}


class MainWindow(QMainWindow):
    """Janela principal do Omie Label Automation."""

    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.worker: PollingWorker | None = None
        self._invoice_id_map: dict[int, int] = {}   # row_index -> id_nfe
        self._invoice_map: dict[int, NormalizedInvoice] = {}

        self._setup_window()
        self._build_ui()
        self._load_initial_data()

        # Contador de status bar
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_clock)
        self._status_timer.start(1000)

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
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)
        splitter.addWidget(self._build_table_panel())
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, stretch=1)

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
        self._lbl_monitor_dot = QLabel("● PARADO")
        self._lbl_monitor_dot.setStyleSheet("color: #6e7681; font-size: 13px; font-weight: bold;")
        layout.addWidget(self._lbl_monitor_dot)

        return widget

    def _build_controls(self) -> QGroupBox:
        group = QGroupBox("Configuracoes de Monitoramento")
        layout = QHBoxLayout(group)
        layout.setSpacing(12)

        # Selecao de impressora
        layout.addWidget(QLabel("Impressora:"))
        self._cmb_printer = QComboBox()
        self._cmb_printer.setMinimumWidth(280)
        self._cmb_printer.setToolTip("Selecione a Honeywell PC42t ou modo SIMULADO")
        self._refresh_printer_list()
        layout.addWidget(self._cmb_printer)

        btn_reload_printers = QPushButton("Atualizar")
        btn_reload_printers.setToolTip("Recarregar lista de impressoras do sistema")
        btn_reload_printers.setFixedWidth(80)
        btn_reload_printers.clicked.connect(self._refresh_printer_list)
        layout.addWidget(btn_reload_printers)

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

        self._btn_models = QPushButton("Modelos")
        self._btn_models.setFixedWidth(90)
        self._btn_models.clicked.connect(self._open_model_dialog)
        layout.addWidget(self._btn_models)

        layout.addSpacing(12)

        # Intervalo de polling
        layout.addWidget(QLabel("Intervalo (s):"))
        self._spn_interval = QSpinBox()
        self._spn_interval.setRange(10, 3600)
        self._spn_interval.setValue(config.polling_interval)
        self._spn_interval.setSuffix(" s")
        self._spn_interval.setFixedWidth(90)
        self._spn_interval.setToolTip("Intervalo entre varreduras automaticas")
        self._spn_interval.valueChanged.connect(self._on_interval_changed)
        layout.addWidget(self._spn_interval)

        layout.addSpacing(20)

        # Auto-print
        self._chk_autoprint = QCheckBox("Imprimir automaticamente")
        self._chk_autoprint.setChecked(config.auto_print)
        self._chk_autoprint.setToolTip("Se ativo, imprime automaticamente toda NF-e nova encontrada")
        self._chk_autoprint.toggled.connect(self._on_autoprint_changed)
        layout.addWidget(self._chk_autoprint)

        layout.addStretch()

        # Botoes de acao
        self._btn_start = QPushButton("Iniciar Monitor")
        self._btn_start.setObjectName("btnStart")
        self._btn_start.setFixedWidth(140)
        self._btn_start.clicked.connect(self._start_monitoring)
        layout.addWidget(self._btn_start)

        self._btn_stop = QPushButton("Parar Monitor")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setFixedWidth(140)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_monitoring)
        layout.addWidget(self._btn_stop)

        self._btn_refresh = QPushButton("Verificar Agora")
        self._btn_refresh.setObjectName("btnRefresh")
        self._btn_refresh.setFixedWidth(140)
        self._btn_refresh.setToolTip("Forca uma varredura manual imediata")
        self._btn_refresh.clicked.connect(self._manual_check)
        layout.addWidget(self._btn_refresh)

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
        top_bar.addStretch()

        self._btn_preview_selected = QPushButton("Visualizar")
        self._btn_preview_selected.setFixedWidth(120)
        self._btn_preview_selected.setToolTip("Mostra uma previa da primeira NF-e selecionada")
        self._btn_preview_selected.clicked.connect(self._preview_selected)
        top_bar.addWidget(self._btn_preview_selected)

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
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setMinimumHeight(200)

        # Larguras fixas para algumas colunas
        col_widths = [80, -1, 45, 120, 120, 150, 65, 100, 110, 90]
        for i, w in enumerate(col_widths):
            if w > 0:
                self._table.setColumnWidth(i, w)

        layout.addWidget(self._table)
        return group

    def _build_log_panel(self) -> QGroupBox:
        group = QGroupBox("Console de Logs em Tempo Real")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)

        top_bar = QHBoxLayout()
        top_bar.addStretch()
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
        self._append_log("Sistema iniciado. Configure a impressora e clique em 'Iniciar Monitor'.", "INFO")
        self._update_table_count()

    # ------------------------------------------------------------------ #
    #  SLOTS E EVENTOS
    # ------------------------------------------------------------------ #
    @Slot()
    def _start_monitoring(self):
        printer = self._cmb_printer.currentText()
        if not printer:
            QMessageBox.warning(self, "Aviso", "Selecione uma impressora antes de iniciar o monitor.")
            return

        if not config.omie_app_key or not config.omie_app_secret:
            QMessageBox.critical(self, "Erro de Configuracao",
                "Credenciais Omie (OMIE_APP_KEY / OMIE_APP_SECRET) nao encontradas.\n"
                "Verifique o arquivo .env na raiz do projeto.")
            return

        config.update_settings(
            polling_interval=self._spn_interval.value(),
            auto_print=self._chk_autoprint.isChecked(),
            printer_name=printer
        )

        self.worker = PollingWorker(printer_name=printer, db_manager=self.db, parent=self)
        self.worker.new_invoice_found.connect(self._on_new_invoice)
        self.worker.invoice_printed.connect(self._on_invoice_printed)
        self.worker.log_message.connect(self._on_log_message)
        self.worker.cycle_started.connect(self._on_cycle_started)
        self.worker.cycle_finished.connect(self._on_cycle_finished)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.start()

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_refresh.setEnabled(False)
        self._lbl_monitor_dot.setText("● ATIVO")
        self._lbl_monitor_dot.setStyleSheet("color: #2ea043; font-size: 13px; font-weight: bold;")
        self._status_bar.showMessage(f"Monitor ativo | Impressora: {printer} | Intervalo: {self._spn_interval.value()}s")

    @Slot()
    def _stop_monitoring(self):
        if self.worker:
            self.worker.stop()
            self.worker.wait(3000)
            self.worker = None

        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_refresh.setEnabled(True)
        self._lbl_monitor_dot.setText("● PARADO")
        self._lbl_monitor_dot.setStyleSheet("color: #6e7681; font-size: 13px; font-weight: bold;")
        self._status_bar.showMessage("Monitor parado.")

    @Slot()
    def _manual_check(self):
        """Dispara uma varredura manual sem iniciar o loop contínuo."""
        if self.worker and self.worker.isRunning():
            return
        printer = self._cmb_printer.currentText()
        if not printer:
            QMessageBox.warning(self, "Aviso", "Selecione uma impressora antes de verificar.")
            return
        config.update_settings(
            polling_interval=self._spn_interval.value(),
            auto_print=self._chk_autoprint.isChecked(),
            printer_name=printer
        )
        # Cria worker temporario para 1 ciclo
        one_shot = PollingWorker(printer_name=printer, db_manager=self.db, parent=self)
        one_shot.new_invoice_found.connect(self._on_new_invoice)
        one_shot.invoice_printed.connect(self._on_invoice_printed)
        one_shot.log_message.connect(self._on_log_message)
        one_shot.cycle_started.connect(self._on_cycle_started)
        one_shot.cycle_finished.connect(self._on_cycle_finished)
        one_shot.error_occurred.connect(self._on_error)
        # Para após 1 ciclo
        one_shot.cycle_finished.connect(lambda _: one_shot.stop())
        one_shot.start()
        self._btn_refresh.setEnabled(False)
        QTimer.singleShot(config.polling_interval * 1000 + 5000, lambda: self._btn_refresh.setEnabled(True))

    @Slot()
    def _fetch_by_date(self):
        if not config.omie_app_key or not config.omie_app_secret:
            QMessageBox.critical(self, "Erro de Configuracao",
                "Credenciais Omie (OMIE_APP_KEY / OMIE_APP_SECRET) nao encontradas.")
            return

        date_str = self._date_fetch.date().toString("dd/MM/yyyy")
        self._append_log(f"Buscando NF-es faturadas em/a partir de {date_str}...", "INFO")
        self._btn_fetch_date.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            invoices = OmieClient().fetch_all_new_nfes(start_date=date_str)
            self._clear_table()
            for inv in invoices:
                status = "IMPRESSO" if self.db.is_nfe_processed(inv.id_nfe) else "NOVO"
                self._add_invoice_row(inv, status)
            self._append_log(f"Busca concluida: {len(invoices)} NF-e(s) carregadas.", "INFO")
            self._status_bar.showMessage(f"{len(invoices)} NF-e(s) carregadas para {date_str}.")
        except Exception as e:
            self._append_log(f"Erro ao buscar NF-es: {e}", "ERROR")
            QMessageBox.critical(self, "Erro na busca", str(e))
        finally:
            QApplication.restoreOverrideCursor()
            self._btn_fetch_date.setEnabled(True)

    @Slot()
    def _preview_selected(self):
        invoices = self._selected_invoices()
        if not invoices:
            QMessageBox.information(self, "Selecione", "Selecione uma NF-e na tabela para visualizar.")
            return
        inv = invoices[0]
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Previa da Etiqueta - NF-e {inv.numero_nf}")
        dialog.resize(980, 720)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QLabel()
        content.setTextFormat(Qt.RichText)
        content.setStyleSheet("background: #d8dee9; padding: 20px;")
        content.setText(ZPLGenerator.preview_html(inv))
        scroll.setWidget(content)
        layout.addWidget(scroll)
        dialog.exec()

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

        labels = ZPLGenerator.generate_batch(invoices)
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
        current = self._cmb_printer.currentText()
        self._cmb_printer.clear()
        printers = PrinterService.list_printers()
        for p in printers:
            self._cmb_printer.addItem(p)
        # Tenta restaurar a seleção anterior
        idx = self._cmb_printer.findText(current)
        if idx >= 0:
            self._cmb_printer.setCurrentIndex(idx)
        elif config.printer_name:
            idx2 = self._cmb_printer.findText(config.printer_name)
            if idx2 >= 0:
                self._cmb_printer.setCurrentIndex(idx2)

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

    # ------------------------------------------------------------------ #
    #  SLOTS DO WORKER
    # ------------------------------------------------------------------ #
    @Slot(NormalizedInvoice)
    def _on_new_invoice(self, inv: NormalizedInvoice):
        self._add_invoice_row(inv, "PENDENTE")

    @Slot(int, bool, str)
    def _on_invoice_printed(self, id_nfe: int, success: bool, status: str):
        self._set_invoice_status(id_nfe, status)

    @Slot(str, str)
    def _on_log_message(self, message: str, level: str):
        self._append_log(message, level)

    @Slot()
    def _on_cycle_started(self):
        self._status_bar.showMessage("Varrendo ERP Omie...")

    @Slot(int)
    def _on_cycle_finished(self, count: int):
        msg = f"Varredura concluida | {count} nova(s) NF-e(s) | Proxima em {config.polling_interval}s"
        self._status_bar.showMessage(msg)
        self._btn_refresh.setEnabled(True)

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

        order_value = inv.numero_ordem or inv.oc or inv.pedido_cliente or inv.pedido_venda or ""
        req_value = inv.requisitante or ""
        values = [
            inv.numero_nf or "",
            inv.cliente_nome or "",
            inv.cliente_uf or "",
            inv.pedido_venda or "",
            order_value,
            req_value,
            str(inv.quantidade_volumes),
            inv.template_name or "default",
            inv.data_emissao or "",
            status,
        ]

        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, col, item)

        self._colorize_status_cell(row, status)
        self._table.scrollToBottom()
        self._update_table_count()
        self._apply_table_filter()

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

    def _set_invoice_status(self, id_nfe: int, status: str):
        for row, mapped_id in self._invoice_id_map.items():
            if mapped_id == id_nfe:
                col_status = len(TABLE_COLUMNS) - 1
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
        dialog.setWindowTitle("Modelo por Cliente")
        dialog.resize(460, 220)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("Chave do cliente (parte do nome que vem na NF-e):"))
        txt_client = QLineEdit()
        txt_client.setPlaceholderText("Ex: PETROBRAS, VALE, SICPA")
        layout.addWidget(txt_client)

        layout.addWidget(QLabel("Modelo:"))
        cmb_template = QComboBox()
        cmb_template.addItem("Padrao 110x70", "default")
        cmb_template.addItem("Claro dividida", "claro_dividida")
        layout.addWidget(cmb_template)

        layout.addWidget(QLabel("Extracao: o sistema procura Nº ORDEM, OC, PROTOCOLO, A/C e REQUISITANTE no texto da NF-e."))

        actions = QHBoxLayout()
        btn_save = QPushButton("Salvar Modelo")
        btn_cancel = QPushButton("Cancelar")
        actions.addStretch()
        actions.addWidget(btn_save)
        actions.addWidget(btn_cancel)
        layout.addLayout(actions)

        btn_cancel.clicked.connect(dialog.reject)
        btn_save.clicked.connect(lambda: self._save_client_model(dialog, txt_client.text(), cmb_template.currentData()))
        dialog.exec()

    def _save_client_model(self, dialog: QDialog, client_key: str, template_name: str):
        key = client_key.strip().upper()
        if not key:
            QMessageBox.warning(dialog, "Campo obrigatorio", "Informe uma chave de cliente.")
            return

        rules_path = Path(__file__).resolve().parent.parent / "core" / "rules.json"
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
            rules[key] = {
                "template": template_name,
                "mappings": {
                    "oc": {
                        "source": "observacoes",
                        "regex": "(?:OC|PEDIDO COMPRA|ORDEM DE COMPRA):?\\s*([A-Za-z0-9./\\-]+)"
                    },
                    "requisitante": {
                        "source": "observacoes",
                        "regex": "(?:A/C|AC/|REQUISITANTE|SOLICITANTE):?\\s*([^|\\n;]+)"
                    },
                    "numero_ordem": {
                        "source": "observacoes",
                        "regex": "(?:N[º°O]?\\s*ORDEM|NUMERO DA ORDEM|ORDEM):?\\s*([A-Za-z0-9./\\-]+)"
                    }
                }
            }
            with open(rules_path, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=4, ensure_ascii=False)
            self._append_log(f"Modelo salvo para cliente '{key}' usando template '{template_name}'.", "INFO")
            dialog.accept()
        except Exception as e:
            QMessageBox.critical(dialog, "Erro ao salvar", str(e))

    def _colorize_status_cell(self, row: int, status: str):
        col_status = len(TABLE_COLUMNS) - 1
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
            self.worker.wait(2000)
        event.accept()
