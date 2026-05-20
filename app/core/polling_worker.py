"""
Worker de polling em background (QThread) que:
1. Busca novas NF-e aprovadas no ERP Omie
2. Verifica anti-duplicação no SQLite local
3. Gera ZPL e envia para a impressora
4. Emite sinais Qt para atualizar a UI em tempo real
"""

from datetime import datetime, timedelta
from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker
from loguru import logger

from app.api.omie_client import OmieClient, OmieClientError
from app.api.zpl_generator import ZPLGenerator
from app.api.printer_service import PrinterService
from app.database.models import DatabaseManager, NormalizedInvoice
from app.core.config import config


class PollingWorker(QThread):
    """Thread de polling que monitora o ERP Omie e dispara impressões automaticamente."""

    # Sinais Qt emitidos para a UI principal
    new_invoice_found = Signal(NormalizedInvoice)          # Nova NF-e detectada
    invoice_printed = Signal(int, bool, str)               # (id_nfe, sucesso, mensagem)
    log_message = Signal(str, str)                         # (mensagem, nivel: INFO|WARN|ERROR)
    cycle_started = Signal()                               # Início de ciclo de polling
    cycle_finished = Signal(int)                           # Fim de ciclo (qtd. novas notas)
    error_occurred = Signal(str)                           # Erro crítico

    def __init__(self, printer_name: str, db_manager: DatabaseManager, parent=None):
        super().__init__(parent)
        self.printer_name = printer_name
        self.db = db_manager
        self._stop_flag = False
        self._mutex = QMutex()
        self.client = OmieClient()
        self._seen_ids: set[int] = set()

    def stop(self):
        """Solicita a parada segura do worker após o ciclo atual."""
        with QMutexLocker(self._mutex):
            self._stop_flag = True

    def _should_stop(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._stop_flag

    def run(self):
        """Loop principal do worker - executa ciclos de polling em intervalos configurados."""
        self._stop_flag = False
        self.log_message.emit("Monitor iniciado.", "INFO")

        while not self._should_stop():
            try:
                self._run_cycle()
            except Exception as e:
                msg = f"Erro inesperado no ciclo de polling: {e}"
                logger.error(msg)
                self.error_occurred.emit(msg)

            # Aguarda o intervalo configurado, verificando parada a cada segundo
            interval = config.polling_interval
            for _ in range(interval):
                if self._should_stop():
                    break
                self.msleep(1000)

        self.log_message.emit("Monitor encerrado.", "INFO")

    def _run_cycle(self):
        """Executa um único ciclo de consulta, geração de ZPL e impressão."""
        self.cycle_started.emit()
        msg = f"Iniciando varredura - {datetime.now().strftime('%H:%M:%S')}"
        self.log_message.emit(msg, "INFO")

        # Busca notas dos últimos 7 dias como janela padrão
        start_date = (datetime.now() - timedelta(days=7)).strftime("%d/%m/%Y")

        try:
            invoices = self.client.fetch_all_new_nfes(start_date=start_date)
        except OmieClientError as e:
            err_msg = f"Erro ao consultar Omie: {e}"
            logger.error(err_msg)
            self.log_message.emit(err_msg, "ERROR")
            self.cycle_finished.emit(0)
            return

        new_count = 0
        for inv in invoices:
            if self._should_stop():
                break

            # Anti-duplicação: pula se já processado
            if self.db.is_nfe_processed(inv.id_nfe) or inv.id_nfe in self._seen_ids:
                continue

            self._seen_ids.add(inv.id_nfe)
            new_count += 1
            self.new_invoice_found.emit(inv)
            self.log_message.emit(
                f"Nova NF-e: {inv.numero_nf} | {inv.cliente_nome} | {inv.quantidade_volumes} vol(s)", "INFO"
            )

            # Só imprime automaticamente se configurado
            if config.auto_print and self.printer_name:
                self._print_invoice(inv)

        if new_count == 0:
            self.log_message.emit("Nenhuma nova NF-e encontrada.", "INFO")

        self.cycle_finished.emit(new_count)

    def _print_invoice(self, inv: NormalizedInvoice):
        """Gera ZPL, imprime cada volume e registra no banco de dados."""
        try:
            labels = ZPLGenerator.generate(inv)
            all_success = True

            for vol_idx, zpl_content in enumerate(labels, 1):
                label_id = f"{inv.numero_nf}_v{vol_idx}"
                ok = PrinterService.print_zpl(self.printer_name, zpl_content, label_id)
                if not ok:
                    all_success = False
                    self.log_message.emit(
                        f"Falha ao imprimir volume {vol_idx}/{len(labels)} da NF-e {inv.numero_nf}", "ERROR"
                    )

            status = "IMPRESSO" if all_success else "ERRO"
            self.db.mark_nfe_as_processed(
                id_nfe=inv.id_nfe,
                numero_nf=inv.numero_nf,
                chave_nfe=inv.chave_nfe or "",
                cliente_nome=inv.cliente_nome,
                status=status,
                volumes=inv.quantidade_volumes
            )

            if all_success:
                self.log_message.emit(
                    f"NF-e {inv.numero_nf} impressa: {len(labels)} vol(s) | Impressora: {self.printer_name}", "INFO"
                )
            self.invoice_printed.emit(inv.id_nfe, all_success, status)

        except Exception as e:
            err_msg = f"Erro ao imprimir NF-e {inv.numero_nf}: {e}"
            logger.error(err_msg)
            self.log_message.emit(err_msg, "ERROR")
            self.invoice_printed.emit(inv.id_nfe, False, "ERRO")
