import pytest
from unittest.mock import patch, MagicMock
from app.api.printer_service import PrinterService, HAS_WIN32


def test_list_printers():
    # Test that SIMULADO printers are always included
    printers = PrinterService.list_printers()
    assert any("SIMULADO_ZEBRA" in p for p in printers)


@patch("app.api.printer_service.HAS_WIN32", False)
def test_list_printers_no_win32():
    # If win32 is not present, only simulation printers should be returned
    printers = PrinterService.list_printers()
    assert printers == ["SIMULADO_ZEBRA_01", "SIMULADO_ZEBRA_02"]


def test_print_zpl_simulated():
    # Test simulation printing writing to temp_labels/
    result = PrinterService.print_zpl("SIMULADO_ZEBRA_01", "^XA^FDTest^XZ", "test_sim")
    assert result is True


def test_print_zpl_win32_success(monkeypatch):
    """Tests that raw ZPL is correctly sent through win32print when available."""
    import app.api.printer_service as ps_module
    
    # Simulate win32print presence
    monkeypatch.setattr(ps_module, "HAS_WIN32", True)
    
    mock_handle = 12345
    mock_job_id = 1
    
    with patch("win32print.OpenPrinter", return_value=mock_handle) as mock_open, \
         patch("win32print.StartDocPrinter", return_value=mock_job_id) as mock_start_doc, \
         patch("win32print.StartPagePrinter") as mock_start_page, \
         patch("win32print.WritePrinter") as mock_write, \
         patch("win32print.EndPagePrinter") as mock_end_page, \
         patch("win32print.EndDocPrinter") as mock_end_doc, \
         patch("win32print.GetPrinter", return_value={}) as mock_get_printer, \
         patch("win32print.GetJob", side_effect=Exception("Mock GetJob Exception")) as mock_get_job, \
         patch("win32print.ClosePrinter") as mock_close:
        
        result = PrinterService.print_zpl("Physical Zebra Printer", "^XA^FDPhysical Test^XZ", "test_phys")
    
    assert result is True
    mock_open.assert_called_once_with("Physical Zebra Printer")
    mock_start_doc.assert_called_once()
    mock_write.assert_called_once()
    mock_close.assert_called_once_with(mock_handle)
