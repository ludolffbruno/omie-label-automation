import os
import sys
import time
from pathlib import Path
from typing import List
from loguru import logger

# Try importing win32print, handle cases where it's not available (e.g., non-Windows development environments)
try:
    import win32print
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class PrinterService:
    """Manages raw thermal printing to Honeywell/ZPL printers and provides fallback simulation."""

    @staticmethod
    def list_printers() -> List[str]:
        """Lists available printer names. Falls back to simulated list if not on Windows."""
        if not HAS_WIN32:
            logger.info("Non-Windows OS or win32print not available. Returning simulated printers.")
            return ["SIMULADO_ZEBRA_01", "SIMULADO_ZEBRA_02"]
        
        try:
            # Enum local and network printers
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            printers_info = win32print.EnumPrinters(flags, None, 1)
            printer_names = [info[2] for info in printers_info]
            
            # Always include simulation options
            printer_names.extend(["SIMULADO_ZEBRA_01", "SIMULADO_ZEBRA_02"])
            return printer_names
        except Exception as e:
            logger.error(f"Error enumerating Windows printers: {e}")
            return ["SIMULADO_ZEBRA_01", "SIMULADO_ZEBRA_02"]

    @staticmethod
    def print_zpl(printer_name: str, zpl_content: str, label_identifier: str = "label") -> bool:
        """Sends raw ZPL data to the printer, or simulates it if a simulated printer is selected.
        
        Args:
            printer_name: Name of the printer to spool to.
            zpl_content: The raw ZPL string to print.
            label_identifier: A descriptor for filename/logs (e.g. invoice key or number).
        """
        # Clean name for comparison
        clean_printer_name = printer_name.upper() if printer_name else ""
        
        if not clean_printer_name or "SIMULADO" in clean_printer_name or not HAS_WIN32:
            return PrinterService._print_simulated(printer_name or "SIMULADO_FALLBACK", zpl_content, label_identifier)
        
        try:
            logger.info(f"Sending raw ZPL payload to physical printer '{printer_name}'...")
            
            # Open printer
            h_printer = win32print.OpenPrinter(printer_name)
            try:
                # Start document
                # (DocName, OutputFile, DataType) -> DataType must be "RAW" for ZPL
                doc_info = (f"OmieLabel-{label_identifier}", None, "RAW")
                job_id = win32print.StartDocPrinter(h_printer, 1, doc_info)
                
                win32print.StartPagePrinter(h_printer)
                
                # ZPL strings need to be encoded to raw bytes
                raw_bytes = zpl_content.encode("utf-8")
                win32print.WritePrinter(h_printer, raw_bytes)
                
                win32print.EndPagePrinter(h_printer)
                win32print.EndDocPrinter(h_printer)
                
                logger.info(f"Successfully spooled job {job_id} to '{printer_name}'.")
                return True
            finally:
                win32print.ClosePrinter(h_printer)
                
        except Exception as e:
            logger.error(f"Failed to print to physical printer '{printer_name}': {e}. Falling back to simulation.")
            return PrinterService._print_simulated(f"{printer_name}_FAILED_FALLBACK", zpl_content, label_identifier)

    @staticmethod
    def _print_simulated(printer_name: str, zpl_content: str, label_identifier: str) -> bool:
        """Saves ZPL content to a local file in the project directory for inspection/debugging."""
        try:
            # Create a 'temp_labels' folder in the project workspace
            output_dir = Path(__file__).resolve().parent.parent.parent / "temp_labels"
            output_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = int(time.time())
            filename = f"{label_identifier}_{timestamp}.zpl"
            file_path = output_dir / filename
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(zpl_content)
                
            logger.info(f"[SIMULATED PRINT] Sent ZPL label to '{printer_name}'. Saved to file: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to write simulated ZPL file: {e}")
            return False
