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
    QFormLayout, QFileDialog
)
from PySide6.QtCore import Qt, QTimer, Slot, QDate
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


class InvoiceEditDialog(QDialog):
    """Edita os campos da etiqueta selecionada sem alterar o padrão do cliente."""

    def __init__(self, invoice: NormalizedInvoice, parent=None):
        super().__init__(parent)
        self.invoice = invoice
        self.updated_invoice = invoice
        self.setWindowTitle(f"Editar Etiqueta - NF-e {invoice.numero_nf}")
        self.resize(420, 360)

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
        self.spn_vols = QSpinBox()
        self.spn_vols.setRange(1, 999)
        self.spn_vols.setValue(invoice.quantidade_volumes or 1)
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
        form.addRow("Volumes:", self.spn_vols)
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
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setMinimumHeight(200)

        # Larguras fixas para algumas colunas
        col_widths = [90, -1, 50, 115, 120, 55, 100, 115, 90, 55]
        for i, w in enumerate(col_widths):
            if w > 0:
                self._table.setColumnWidth(i, w)

        layout.addWidget(self._table)
        return group

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
        self._invoice_map[updated.id_nfe] = updated
        self._update_invoice_row_values(updated)
        self._append_log(f"Etiqueta da NF-e {ZPLGenerator._format_nf(updated.numero_nf)} editada para esta sessao.", "INFO")

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
            inv.template_name or "default",
            inv.data_emissao or "",
            status,
            "",
        ]

        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignCenter)
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
                "Template": inv.template_name or "default",
                "Data Emissão": inv.data_emissao or "",
            }
            for column_name, value in values.items():
                col = TABLE_COLUMNS.index(column_name)
                item = self._table.item(row, col)
                if item:
                    item.setText(value)
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
        dialog.resize(760, 430)
        layout = QVBoxLayout(dialog)

        rules_path = Path(__file__).resolve().parent.parent / "core" / "rules.json"
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
        except Exception:
            rules = {}

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Modelo", "Campo", "Condição", "Layout"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(table)

        def refresh_table():
            table.setRowCount(0)
            for key, rule in sorted(rules.items()):
                row = table.rowCount()
                table.insertRow(row)
                model_name = rule.get("name") or ("Padrão 110x70" if key == "DEFAULT" else key.title())
                table.setItem(row, 0, QTableWidgetItem(model_name))
                conditions = rule.get("conditions") or []
                if conditions:
                    condition = conditions[0]
                    field_label = self._model_field_label(condition.get("field", "cliente"))
                    operator_label = self._model_operator_label(condition.get("operator", "contains"))
                    condition_text = f"{operator_label} {condition.get('value', '')}".strip()
                else:
                    field_label = "Nome do Cliente"
                    condition_text = "" if key == "DEFAULT" else f"contém {key}"
                table.setItem(row, 1, QTableWidgetItem(field_label))
                table.setItem(row, 2, QTableWidgetItem(condition_text))
                table.setItem(row, 3, QTableWidgetItem(rule.get("template", "default")))

        refresh_table()

        layout.addWidget(QLabel("Nome do modelo:"))
        txt_name = QLineEdit()
        txt_name.setPlaceholderText("Ex: Modelo Cliente XPTO")
        layout.addWidget(txt_name)

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
        layout.addLayout(form)

        layout.addWidget(QLabel("Modelo:"))
        cmb_template = QComboBox()
        cmb_template.addItem("Padrao 110x70", "default")
        cmb_template.addItem("Claro dividida", "claro_dividida")
        layout.addWidget(cmb_template)

        layout.addWidget(QLabel("Correspondência: use textos simples como Cliente contém Claro, Cidade contém Rio ou UF igual a RJ."))

        def load_selected():
            indexes = table.selectionModel().selectedRows()
            if not indexes:
                return
            row = indexes[0].row()
            model_name = table.item(row, 0).text()
            key = "DEFAULT"
            for candidate_key, candidate_rule in rules.items():
                candidate_name = candidate_rule.get("name") or ("Padrão 110x70" if candidate_key == "DEFAULT" else candidate_key.title())
                if candidate_name == model_name:
                    key = candidate_key
                    break
            rule = rules.get(key, {})
            conditions = rule.get("conditions") or []
            condition = conditions[0] if conditions else {"field": "cliente", "operator": "contains", "value": "" if key == "DEFAULT" else key}
            txt_name.setText(rule.get("name") or table.item(row, 0).text())
            field_idx = cmb_field.findData(condition.get("field", "cliente"))
            op_idx = cmb_operator.findData(condition.get("operator", "contains"))
            cmb_field.setCurrentIndex(field_idx if field_idx >= 0 else 0)
            cmb_operator.setCurrentIndex(op_idx if op_idx >= 0 else 0)
            txt_condition.setText(str(condition.get("value", "")))
            idx = cmb_template.findData(rule.get("template", "default"))
            if idx >= 0:
                cmb_template.setCurrentIndex(idx)

        table.itemSelectionChanged.connect(load_selected)

        actions = QHBoxLayout()
        btn_new = QPushButton("Novo")
        btn_save = QPushButton("Salvar")
        btn_cancel = QPushButton("Cancelar")
        actions.addStretch()
        actions.addWidget(btn_new)
        actions.addWidget(btn_save)
        actions.addWidget(btn_cancel)
        layout.addLayout(actions)

        def clear_form():
            txt_name.clear()
            txt_condition.clear()
            cmb_field.setCurrentIndex(0)
            cmb_operator.setCurrentIndex(0)
            cmb_template.setCurrentIndex(0)
            table.clearSelection()

        def save_model():
            condition = {
                "field": cmb_field.currentData(),
                "operator": cmb_operator.currentData(),
                "value": txt_condition.text(),
            }
            self._save_client_model(dialog, condition, cmb_template.currentData(), txt_name.text(), close_dialog=False)
            try:
                with open(rules_path, "r", encoding="utf-8") as f:
                    rules.clear()
                    rules.update(json.load(f))
                refresh_table()
            except Exception:
                pass

        btn_new.clicked.connect(clear_form)
        btn_cancel.clicked.connect(dialog.reject)
        btn_save.clicked.connect(save_model)
        dialog.exec()

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
