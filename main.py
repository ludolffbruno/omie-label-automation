import argparse
import sys
from datetime import datetime
from loguru import logger

# Initialize logging configuration
import app.core.logger
from app.core.config import config
from app.database.models import DatabaseManager, NormalizedInvoice
from app.api.omie_client import OmieClient, OmieClientError
from app.api.printer_service import PrinterService
from app.api.zpl_generator import ZPLGenerator


def list_local_printers():
    """Lists system printers via PrinterService."""
    printers = PrinterService.list_printers()
    try:
        import win32print
        default = win32print.GetDefaultPrinter()
    except Exception:
        default = "N/A"
    return printers, default


def main():
    parser = argparse.ArgumentParser(description="Omie Label Automation - CLI Tool")
    parser.add_argument(
        "--test-connection", 
        action="store_true", 
        help="Validate API key settings and test connection to Omie API."
    )
    parser.add_argument(
        "--fetch-date", 
        type=str, 
        help="Fetch and print normalized invoices faturadas since this registration date (format: DD/MM/YYYY)."
    )
    parser.add_argument(
        "--list-printers", 
        action="store_true", 
        help="List available printers on this system."
    )
    parser.add_argument(
        "--print-test-label",
        type=str,
        metavar="PRINTER_NAME",
        help="Send a test ZPL label directly to the specified printer (use SIMULADO_ZEBRA_01 for simulation)."
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Abre a interface grafica de monitoramento (padrao quando nenhum argumento e fornecido)."
    )
    parser.add_argument(
        "--status", 
        action="store_true", 
        help="Print current system configuration status."
    )

    args = parser.parse_args()

    # Se nenhum argumento for passado, abre a interface grafica
    if len(sys.argv) == 1 or args.ui:
        _launch_ui()
        return

    # 1. Print Status
    if args.status:
        valid, msg = config.validate()
        print("\n=== SYSTEM STATUS ===")
        print(f"Config Validation: {msg}")
        print(f"Omie API URL:      {config.omie_api_url}")
        print(f"App Key Loaded:    {'YES' if config.omie_app_key else 'NO'}")
        print(f"App Secret Loaded: {'YES' if config.omie_app_secret else 'NO'}")
        print(f"Polling Interval:  {config.polling_interval}s")
        print(f"Auto Print:        {config.auto_print}")
        print(f"Database Path:     {config.db_path}")
        print(f"Logs Directory:    {config.log_dir}")
        print("=====================\n")

    # 2. List printers
    if args.list_printers:
        printers, default = list_local_printers()
        print("\n=== IMPRESSORAS DO SISTEMA ===")
        print(f"Impressora Padrao: {default}")
        print("Impressoras Disponiveis:")
        for idx, printer in enumerate(printers, 1):
            marker = " (PADRAO)" if printer == default else ""
            print(f"  {idx}. {printer}{marker}")
        print("==============================\n")

    # 2b. Print test label
    if args.print_test_label:
        printer_name = args.print_test_label
        print(f"\n[IMPRESSAO] Enviando etiqueta de teste para: '{printer_name}'...")
        
        # Build a fake test invoice
        test_invoice = NormalizedInvoice(
            id_nfe=0,
            numero_nf="000TEST",
            chave_nfe="35260500000000000000550010000503390000000001",
            cliente_nome="CLIENTE DE TESTE LTDA",
            cliente_cnpj_cpf="00.000.000/0001-00",
            cliente_uf="SP",
            pedido_venda="PED-TESTE-001",
            quantidade_volumes=1,
            status="APROVADA",
            data_emissao=datetime.now().strftime("%d/%m/%Y"),
            template_name="default",
            oc="OC-TESTE-9999",
            requisitante="Joao Testador"
        )
        
        labels = ZPLGenerator.generate(test_invoice)
        success = PrinterService.print_zpl(printer_name, labels[0], "TESTE")
        
        if success:
            print("[OK] Etiqueta de teste enviada com sucesso!")
            if "SIMULADO" in printer_name.upper():
                print("  (Arquivo ZPL salvo em temp_labels/ - modo simulado)")
        else:
            print("[ERRO] Falha ao enviar etiqueta de teste.")
            sys.exit(1)

    # 3. Test Connection
    if args.test_connection:
        valid, msg = config.validate()
        if not valid:
            logger.error(f"Configuration is invalid: {msg}")
            sys.exit(1)
            
        print("\nTesting Omie API connection...")
        client = OmieClient()
        # Querying with a dummy call or a page request for today to test keys
        today_str = datetime.now().strftime("%d/%m/%Y")
        try:
            # We list 1 record to check authentication validity
            res = client.list_nfes(page=1, records_per_page=1, start_date=today_str)
            print("[OK] Connection Successful! Omie API authenticated correctly.")
            logger.info("Connection test succeeded.")
        except OmieClientError as e:
            print(f"[ERRO] Connection Failed: {e}")
            logger.error(f"Connection test failed: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"[ERRO] Connection failed with unexpected error: {e}")
            logger.error(f"Unexpected connection error: {e}")
            sys.exit(1)

    # 4. Fetch Invoices from Date
    if args.fetch_date:
        valid, msg = config.validate()
        if not valid:
            logger.error(f"Configuration is invalid: {msg}")
            sys.exit(1)
            
        date_str = args.fetch_date
        # Simple date format validation
        try:
            datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            print("[ERRO] Invalid date format. Please use DD/MM/YYYY.")
            sys.exit(1)

        print(f"\nFetching new invoices faturadas since {date_str}...")
        client = OmieClient()
        db_mgr = DatabaseManager()
        
        try:
            invoices = client.fetch_all_new_nfes(start_date=date_str)
            print(f"Found {len(invoices)} invoices.")
            print("-" * 50)
            
            for inv in invoices:
                # Show details
                print(f"NF: {inv.numero_nf} | Client: {inv.cliente_nome} ({inv.cliente_uf})")
                print(f"  Chave: {inv.chave_nfe}")
                print(f"  Volumes: {inv.quantidade_volumes} | Template: {inv.template_name}")
                print(f"  Pedido Venda: {inv.pedido_venda} | OC: {inv.oc or 'N/A'}")
                print(f"  Requisitante: {inv.requisitante or 'N/A'} | Ordem No: {inv.numero_ordem or 'N/A'}")
                print(f"  Status: {inv.status} | Data: {inv.data_emissao}")
                
                # Check database status
                already_processed = db_mgr.is_nfe_processed(inv.id_nfe)
                print(f"  Processed status: {'[ALREADY PROCESSED]' if already_processed else '[NEW]'}")
                print("-" * 50)
                
        except Exception as e:
            print(f"[ERRO] Error fetching invoices: {e}")
            logger.error(f"Fetch failed: {e}")
            sys.exit(1)


def _launch_ui():
    """Inicializa a interface grafica PySide6 com tema escuro premium."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from app.ui.ui_main import MainWindow
    from app.ui.styles import STYLESHEET

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Omie Label Automation")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("Antigravity")
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
