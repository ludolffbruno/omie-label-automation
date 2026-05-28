"""
Worker de polling em background (QThread) que:
1. Busca novas NF-e aprovadas no ERP Omie
2. Verifica anti-duplicação no SQLite local
3. Gera ZPL e envia para a impressora
4. Emite sinais Qt para atualizar a UI em tempo real
"""

from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker
from loguru import logger

from app.api.omie_client import OmieClient, OmieClientError
from app.api.zpl_generator import ZPLGenerator
from app.api.dp_generator import DPGenerator
from app.api.printer_service import PrinterService
from app.database.models import DatabaseManager, NormalizedInvoice
from app.core.config import config

TODAY_POLLING_INTERVAL_SECONDS = 600


class PollingWorker(QThread):
    """Thread de polling que monitora o ERP Omie e dispara impressões automaticamente."""

    # Sinais Qt emitidos para a UI principal
    new_invoice_found = Signal(NormalizedInvoice)          # Nova NF-e detectada
    invoice_updated = Signal(NormalizedInvoice)            # NF-e enriquecida por DANFE/cache
    invoice_printed = Signal(str, bool, str)               # (id_nfe como str para evitar overflow, sucesso, mensagem)
    log_message = Signal(str, str)                         # (mensagem, nivel: INFO|WARN|ERROR)
    progress_changed = Signal(int, int)                    # (processadas, total)
    cycle_started = Signal()                               # Início de ciclo de polling
    cycle_finished = Signal(int)                           # Fim de ciclo (qtd. novas notas)
    error_occurred = Signal(str)                           # Erro crítico

    def __init__(self, printer_name: str, db_manager: DatabaseManager, start_date: str, force_refresh: bool = False, parent=None):
        super().__init__(parent)
        self.printer_name = printer_name
        self.db = db_manager
        self.start_date = start_date
        self.force_refresh = force_refresh
        self._stop_flag = False
        self._mutex = QMutex()
        self.client = OmieClient()
        self._seen_ids: set[int] = set()
        self._stats = {
            "found": 0,
            "danfe_processed": 0,
            "pending": 0,
            "errors": 0,
            "cache_hits": 0,
        }

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

            if not self._is_today_filter():
                self.log_message.emit("Data passada consultada uma vez. Monitor automatico pausado para evitar chamadas desnecessarias.", "INFO")
                break

            # Aguarda o intervalo configurado, verificando parada a cada segundo
            interval = TODAY_POLLING_INTERVAL_SECONDS
            for _ in range(interval):
                if self._should_stop():
                    break
                self.msleep(1000)

        self.log_message.emit("Monitor encerrado.", "INFO")

    def _run_cycle(self):
        """Executa um único ciclo de consulta e atualização."""
        self.cycle_started.emit()
        msg = f"Iniciando varredura para {self.start_date} - {datetime.now().strftime('%H:%M:%S')}"
        self.log_message.emit(msg, "INFO")
        self._stats = {"found": 0, "danfe_processed": 0, "pending": 0, "errors": 0, "cache_hits": 0}

        try:
            cached = self.db.get_cached_invoices_by_date(self.start_date)
            if isinstance(cached, list) and cached and not self._is_today_filter() and not self.force_refresh:
                invoices = [self._sanitize_cached_invoice(inv) for inv in cached]
                self.log_message.emit(f"Cache local usado para {self.start_date}: {len(invoices)} NF-e(s). Sem chamada Omie.", "INFO")
            else:
                invoices = self.client.fetch_all_new_nfes(start_date=self.start_date, log_callback=self.log_message.emit)
        except OmieClientError as e:
            err_msg = f"Erro ao consultar Omie: {e}"
            logger.error(err_msg)
            self.log_message.emit(err_msg, "ERROR")
            self.cycle_finished.emit(0)
            return

        new_count = 0
        self._stats["found"] = len(invoices)
        self.log_message.emit(f"{len(invoices)} NF-e(s) encontradas para {self.start_date}.", "INFO")
        self.progress_changed.emit(0, len(invoices))
        processed = 0
        for inv in invoices:
            if self._should_stop():
                break

            # Anti-duplicação do ciclo: notas já impressas ainda são exibidas na UI.
            if inv.id_nfe in self._seen_ids:
                continue

            self._seen_ids.add(inv.id_nfe)
            new_count += 1
            self.new_invoice_found.emit(inv)
            self.db.save_invoice_cache(inv, enrichment_status="pending" if self._needs_danfe(inv) else "complete")
            enriched = self._enrich_and_cache_invoice(inv)
            if enriched.id_nfe == inv.id_nfe and enriched != inv:
                self.invoice_updated.emit(enriched)
            processed += 1
            self.progress_changed.emit(processed, len(invoices))

        if new_count == 0:
            self.log_message.emit("Nenhuma nova NF-e encontrada.", "INFO")

        self._emit_summary()
        self.cycle_finished.emit(new_count)

    def _is_today_filter(self) -> bool:
        return self.start_date == datetime.now().strftime("%d/%m/%Y")

    def _needs_danfe(self, inv: NormalizedInvoice) -> bool:
        pedido = inv.numero_ordem or inv.oc
        return not inv.cliente_uf or not pedido or (inv.template_name == "claro_dividida" and not inv.protocolo)

    @staticmethod
    def _invalid_cached_value(value: str | None) -> bool:
        return str(value or "").strip().upper() in {"DE", "DA", "DO", "DAS", "DOS"}

    def _sanitize_cached_invoice(self, inv: NormalizedInvoice) -> NormalizedInvoice:
        updates = {}
        if self._invalid_cached_value(inv.numero_ordem):
            updates["numero_ordem"] = None
        if self._invalid_cached_value(inv.oc):
            updates["oc"] = None
        if self._invalid_cached_value(inv.protocolo):
            updates["protocolo"] = None
        return inv.model_copy(update=updates) if updates else inv

    def _enrich_and_cache_invoice(self, inv: NormalizedInvoice) -> NormalizedInvoice:
        if not self._needs_danfe(inv):
            self.db.save_invoice_cache(inv, enrichment_status="complete")
            return inv

        meta = self.db.get_invoice_cache_meta(inv.id_nfe)
        cached_pdf = meta.get("danfe_path")
        if cached_pdf and Path(cached_pdf).exists():
            enriched = self.client.enrich_invoice_from_danfe_pdf(inv, Path(cached_pdf))
            self.db.save_invoice_cache(enriched, enrichment_status="complete", danfe_path=cached_pdf, last_error="")
            self._stats["cache_hits"] += 1
            return enriched

        next_attempt = self._parse_iso(meta.get("next_attempt_at"))
        if next_attempt and next_attempt > datetime.now():
            self._stats["pending"] += 1
            return inv

        try:
            enriched, path = self.client.enrich_invoice_from_danfe_download(inv)
            self.db.save_invoice_cache(
                enriched,
                enrichment_status="complete",
                danfe_path=str(path) if path else cached_pdf,
                last_error="",
                last_attempt_at=datetime.now().isoformat(),
                next_attempt_at="",
            )
            self._stats["danfe_processed"] += 1
            self.msleep(500)
            return enriched
        except Exception as e:
            wait = self.client.parse_redundant_wait_seconds(str(e))
            next_attempt_at = (datetime.now() + timedelta(seconds=wait)).isoformat() if wait else ""
            self.db.save_invoice_cache(
                inv,
                enrichment_status="cooldown" if wait else "error",
                last_error=str(e),
                last_attempt_at=datetime.now().isoformat(),
                next_attempt_at=next_attempt_at,
            )
            self._stats["pending" if wait else "errors"] += 1
            return inv

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _emit_summary(self):
        self.log_message.emit(
            f"Resumo da busca: {self._stats['found']} NF-e(s) encontradas | "
            f"{self._stats['danfe_processed']} DANFE(s) processados | "
            f"{self._stats['cache_hits']} cache | "
            f"{self._stats['pending']} pendente(s)/cooldown | "
            f"{self._stats['errors']} erro(s).",
            "INFO" if self._stats["errors"] == 0 else "WARN",
        )

    def _print_invoice(self, inv: NormalizedInvoice):
        """Gera etiquetas, imprime cada volume e registra no banco de dados."""
        try:
            if config.use_dp:
                labels = DPGenerator.generate(inv)
            else:
                labels = ZPLGenerator.generate(inv)
            all_success = True

            for vol_idx, zpl_content in enumerate(labels, 1):
                label_id = f"{inv.numero_nf}_v{vol_idx}"
                ok = PrinterService.print_zpl(self.printer_name, zpl_content, label_id)
                if not ok:
                    all_success = False
                    self.log_message.emit(
                        f"Falha ao imprimir volume {vol_idx}/{len(labels)} da NF-e {ZPLGenerator._format_nf(inv.numero_nf)}", "ERROR"
                    )
                # Rate limit de 1 segundo entre etiquetas impressas
                if vol_idx < len(labels):
                    self.msleep(1000)

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
                    f"NF-e {ZPLGenerator._format_nf(inv.numero_nf)} impressa: {len(labels)} vol(s) | Impressora: {self.printer_name}", "INFO"
                )
            self.invoice_printed.emit(str(inv.id_nfe), all_success, status)

        except Exception as e:
            err_msg = f"Erro ao imprimir NF-e {inv.numero_nf}: {e}"
            logger.error(err_msg)
            self.log_message.emit(err_msg, "ERROR")
            self.invoice_printed.emit(str(inv.id_nfe), False, "ERRO")
