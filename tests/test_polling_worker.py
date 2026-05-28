from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication

from app.core.polling_worker import PollingWorker
from app.database.models import NormalizedInvoice


def test_run_cycle_only_loads_invoices_without_printing():
    QCoreApplication.instance() or QCoreApplication([])
    invoice = NormalizedInvoice(
        id_nfe=50100,
        numero_nf="050100",
        chave_nfe="",
        cliente_nome="CLIENTE SA",
        cliente_cnpj_cpf="",
        cliente_uf="SP",
        numero_ordem="12345",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="20/05/2026",
    )
    db = MagicMock()
    db.get_cached_invoices_by_date.return_value = []
    worker = PollingWorker(printer_name="SIMULADO_ZEBRA_01", db_manager=db, start_date="20/05/2026")
    worker.client = MagicMock()
    worker.client.fetch_all_new_nfes.return_value = [invoice]

    with patch.object(worker, "_print_invoice") as mock_print:
        worker._run_cycle()

    mock_print.assert_not_called()


def test_past_date_uses_cache_without_omie_request():
    QCoreApplication.instance() or QCoreApplication([])
    invoice = NormalizedInvoice(
        id_nfe=50101,
        numero_nf="050101",
        chave_nfe="",
        cliente_nome="CLIENTE SA",
        cliente_cnpj_cpf="",
        cliente_uf="RJ",
        numero_ordem="12345",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="19/05/2026",
    )
    db = MagicMock()
    db.get_cached_invoices_by_date.return_value = [invoice]
    worker = PollingWorker(printer_name="SIMULADO_ZEBRA_01", db_manager=db, start_date="19/05/2026")
    worker.client = MagicMock()

    worker._run_cycle()

    worker.client.fetch_all_new_nfes.assert_not_called()


def test_force_refresh_bypasses_past_date_cache():
    QCoreApplication.instance() or QCoreApplication([])
    cached_invoice = NormalizedInvoice(
        id_nfe=50104,
        numero_nf="050104",
        chave_nfe="",
        cliente_nome="CLIENTE SA",
        cliente_cnpj_cpf="",
        cliente_uf="RJ",
        numero_ordem="DE",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="19/05/2026",
    )
    fresh_invoice = cached_invoice.model_copy(update={"numero_ordem": "6602115510"})
    db = MagicMock()
    db.get_cached_invoices_by_date.return_value = [cached_invoice]
    worker = PollingWorker(
        printer_name="SIMULADO_ZEBRA_01",
        db_manager=db,
        start_date="19/05/2026",
        force_refresh=True,
    )
    worker.client = MagicMock()
    worker.client.fetch_all_new_nfes.return_value = [fresh_invoice]

    worker._run_cycle()

    worker.client.fetch_all_new_nfes.assert_called_once()


def test_cached_de_value_is_invalidated():
    invoice = NormalizedInvoice(
        id_nfe=50105,
        numero_nf="050105",
        chave_nfe="",
        cliente_nome="GSK",
        cliente_cnpj_cpf="",
        cliente_uf="RJ",
        numero_ordem="DE",
        oc="DE",
        protocolo="DE",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="20/05/2026",
    )
    worker = PollingWorker(printer_name="SIMULADO_ZEBRA_01", db_manager=MagicMock(), start_date="20/05/2026")

    sanitized = worker._sanitize_cached_invoice(invoice)

    assert sanitized.numero_ordem is None
    assert sanitized.oc is None
    assert sanitized.protocolo is None


def test_run_cycle_logs_summary_without_per_note_limit_spam():
    QCoreApplication.instance() or QCoreApplication([])
    invoice = NormalizedInvoice(
        id_nfe=50102,
        numero_nf="050102",
        chave_nfe="",
        cliente_nome="CLIENTE SA",
        cliente_cnpj_cpf="",
        cliente_uf="SP",
        numero_ordem="12345",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="20/05/2026",
    )
    db = MagicMock()
    db.get_cached_invoices_by_date.return_value = []
    worker = PollingWorker(printer_name="SIMULADO_ZEBRA_01", db_manager=db, start_date="20/05/2026")
    worker.client = MagicMock()
    worker.client.fetch_all_new_nfes.return_value = [invoice]
    logs = []
    worker.log_message.connect(lambda message, level: logs.append(message))

    worker._run_cycle()

    assert any("Resumo da busca" in message for message in logs)
    assert not any("Limite de" in message for message in logs)


def test_run_cycle_emits_progress():
    QCoreApplication.instance() or QCoreApplication([])
    invoice = NormalizedInvoice(
        id_nfe=50103,
        numero_nf="050103",
        chave_nfe="",
        cliente_nome="CLIENTE SA",
        cliente_cnpj_cpf="",
        cliente_uf="SP",
        numero_ordem="12345",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="20/05/2026",
    )
    db = MagicMock()
    db.get_cached_invoices_by_date.return_value = []
    worker = PollingWorker(printer_name="SIMULADO_ZEBRA_01", db_manager=db, start_date="20/05/2026")
    worker.client = MagicMock()
    worker.client.fetch_all_new_nfes.return_value = [invoice]
    progress = []
    worker.progress_changed.connect(lambda done, total: progress.append((done, total)))

    worker._run_cycle()

    assert progress == [(0, 1), (1, 1)]
