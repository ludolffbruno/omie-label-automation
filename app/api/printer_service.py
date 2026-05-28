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
        
        # Se a configuracao de usar GDI estiver ativada, redireciona para a impressao GDI
        try:
            from app.core.config import config
            if config.use_gdi and clean_printer_name and "SIMULADO" not in clean_printer_name:
                return PrinterService.print_gdi(printer_name, zpl_content, label_identifier)
        except Exception as config_err:
            logger.warning(f"Erro ao verificar configuracao de GDI: {config_err}")

        if not clean_printer_name or "SIMULADO" in clean_printer_name or not HAS_WIN32:
            return PrinterService._print_simulated(printer_name or "SIMULADO_FALLBACK", zpl_content, label_identifier)
        
        try:
            logger.info(f"Sending raw ZPL payload to physical printer '{printer_name}'...")
            
            # Encode ZPL using cp1252 (fallback to ASCII compatible)
            raw_bytes = zpl_content.encode("cp1252", errors="ignore")
            
            # If it's a network shared printer path (UNC), try writing directly to bypass local spooler issues
            if printer_name.startswith(r"\\"):
                try:
                    logger.info(f"Attempting direct UNC print to '{printer_name}'...")
                    with open(printer_name, "wb") as f:
                        f.write(raw_bytes)
                    logger.info(f"Successfully sent direct UNC print to '{printer_name}'.")
                    return True
                except Exception as unc_err:
                    logger.warning(f"Direct UNC print failed ({unc_err}). Falling back to win32print API.")

            # Fallback to win32print API
            h_printer = win32print.OpenPrinter(printer_name)
            try:
                # Log printer properties for advanced debugging
                try:
                    p_info = win32print.GetPrinter(h_printer, 2)
                    logger.info("=== CONFIGURACOES DA IMPRESSORA NO WINDOWS ===")
                    logger.info(f"  Nome: {p_info.get('pPrinterName')}")
                    logger.info(f"  Porta: {p_info.get('pPortName')}")
                    logger.info(f"  Driver: {p_info.get('pDriverName')}")
                    logger.info(f"  Processador de Impressao: {p_info.get('pPrintProcessor')}")
                    logger.info(f"  Tipo de Dados Padrao: {p_info.get('pDatatype')}")
                    logger.info(f"  Status da Impressora: {p_info.get('Status')}")
                    logger.info(f"  Trabalhos pendentes na fila: {p_info.get('cJobs')}")
                    logger.info("==============================================")
                except Exception as p_err:
                    logger.warning(f"Nao foi possivel obter detalhes da impressora: {p_err}")

                # Start document
                doc_info = (f"OmieLabel-{label_identifier}", None, "RAW")
                job_id = win32print.StartDocPrinter(h_printer, 1, doc_info)
                logger.info(f"Job {job_id} iniciado no spooler do Windows.")
                
                win32print.StartPagePrinter(h_printer)
                win32print.WritePrinter(h_printer, raw_bytes)
                win32print.EndPagePrinter(h_printer)
                win32print.EndDocPrinter(h_printer)
                
                logger.info(f"Job {job_id} enviado para '{printer_name}' via win32print.")
                
                # Monitor job in the spooler queue for 5 seconds
                logger.info(f"Monitorando status do Job {job_id} na fila...")
                for attempt in range(10):
                    time.sleep(0.5)
                    try:
                        job_info = win32print.GetJob(h_printer, job_id, 1)
                        status_num = job_info.get("Status", 0)
                        status_str = job_info.get("pStatus", "")
                        pos = job_info.get("Position", 0)
                        logger.info(f"  [Tentativa {attempt+1}] Status Num: {status_num}, Status Texto: '{status_str}', Posicao: {pos}")
                        
                        # Check status flags
                        if status_num & win32print.JOB_STATUS_ERROR:
                            logger.error("  [ERRO] O spooler do Windows reportou erro no trabalho de impressao.")
                        if status_num & win32print.JOB_STATUS_OFFLINE:
                            logger.warning("  [AVISO] A impressora parece estar offline para o spooler.")
                        if status_num & win32print.JOB_STATUS_PAUSED:
                            logger.warning("  [AVISO] Fila de impressao pausada.")
                        if status_num & win32print.JOB_STATUS_PAPEROUT:
                            logger.error("  [ERRO] Impressora sem papel.")
                    except Exception:
                        # If GetJob fails, the job was processed and removed from queue (normal for fast printers)
                        logger.info("  [FILA] Job foi finalizado e removido da fila com sucesso.")
                        break
                
                return True
            finally:
                win32print.ClosePrinter(h_printer)
                
        except Exception as e:
            logger.error(f"Failed to print to physical printer '{printer_name}': {e}")
            return False

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

    @staticmethod
    def print_gdi(printer_name: str, zpl_content: str, label_identifier: str = "label") -> bool:
        """Renderiza o conteudo ZPL como graficos e envia para a impressora via PySide6 GDI."""
        try:
            from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
            from PySide6.QtGui import QPainter
        except ImportError:
            logger.error("PySide6 nao esta instalado ou importavel. Nao e possivel usar GDI.")
            return False

        logger.info(f"Renderizando ZPL graficamente via GDI para impressora '{printer_name}'...")
        
        # Certifica-se de que a QApplication existe
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication([])
        except Exception as q_err:
            logger.warning(f"Erro ao inicializar QApplication para GDI: {q_err}")

        printer_info = None
        for info in QPrinterInfo.availablePrinters():
            if info.printerName() == printer_name:
                printer_info = info
                break
        
        if not printer_info:
            logger.warning(f"Impressora '{printer_name}' nao encontrada pelo Qt. Usando a padrao.")
            printer_info = QPrinterInfo.defaultPrinter()

        try:
            printer = QPrinter(printer_info)
            printer.setResolution(203) # Honeywell PC42t e 203 DPI

            # Definir layout de pagina personalizado 110mm x 70mm
            from PySide6.QtGui import QPageSize, QPageLayout
            from PySide6.QtCore import QSizeF, QMarginsF
            page_size = QPageSize(QSizeF(110, 70), QPageSize.Millimeter)
            layout = QPageLayout(page_size, QPageLayout.Portrait, QMarginsF(0, 0, 0, 0))
            printer.setPageLayout(layout)

            painter = QPainter()
            if not painter.begin(printer):
                logger.error("Nao foi possivel iniciar o QPainter na impressora.")
                return False

            try:
                # Renderiza o ZPL usando o parser
                PrinterService._draw_zpl(painter, zpl_content)
            finally:
                painter.end()

            logger.success(f"Etiqueta GDI enviada com sucesso para '{printer_info.printerName()}'.")
            return True
        except Exception as e:
            logger.error(f"Erro ao imprimir via GDI: {e}")
            return False

    @staticmethod
    def _draw_zpl(painter, zpl_content: str):
        import re
        from PySide6.QtGui import QFont, QColor
        from PySide6.QtCore import QRectF, Qt

        # Estado do parser
        x, y = 0, 0
        font_height = 16
        font_bold = False
        
        fb_width = None
        fb_align = "L"
        
        barcode_pending = False
        barcode_height = 50
        barcode_vertical = False
        module_width = 2
        
        tokens = re.split(r"[\^~]", zpl_content)
        
        for token in tokens:
            token = token.strip()
            if not token:
                continue
                
            match = re.match(r"^([A-Z0-9]{2,3})(.*)$", token, re.DOTALL)
            if not match:
                match = re.match(r"^([A-Z])(.*)$", token, re.DOTALL)
                if not match:
                    continue
                    
            cmd = match.group(1).upper()
            args_str = match.group(2).strip()
            
            args = [a.strip() for a in args_str.split(",") if a.strip()]
            
            if cmd == "FO":
                if len(args) >= 2:
                    try:
                        x = int(args[0])
                        y = int(args[1])
                    except ValueError:
                        pass
                elif len(args) == 1:
                    try:
                        x = int(args[0])
                    except ValueError:
                        pass
            elif cmd in ("A0N", "A0B", "A0"):
                h = 20
                parts = args_str.split(",")
                if len(parts) >= 2:
                    try:
                        h = int(parts[1])
                    except ValueError:
                        pass
                font_height = max(7, int(h * 0.35))
                font_bold = h > 26
            elif cmd == "FB":
                if len(args) >= 1:
                    try:
                        fb_width = int(args[0])
                    except ValueError:
                        pass
                if len(args) >= 4:
                    fb_align = args[3].upper()
            elif cmd == "BY":
                if len(args) >= 1:
                    try:
                        module_width = int(args[0])
                    except ValueError:
                        pass
            elif cmd in ("BC", "BCN", "BCB"):
                barcode_pending = True
                barcode_vertical = (cmd == "BCB" or (len(args) >= 4 and args[3] == "B"))
                if len(args) >= 1:
                    try:
                        barcode_height = int(args[0])
                    except ValueError:
                        pass
            elif cmd == "GB":
                w, h, t = 0, 0, 1
                if len(args) >= 1:
                    try:
                        w = int(args[0])
                    except ValueError:
                        pass
                if len(args) >= 2:
                    try:
                        h = int(args[1])
                    except ValueError:
                        pass
                if len(args) >= 3:
                    try:
                        t = int(args[2])
                    except ValueError:
                        pass
                    
                pen = painter.pen()
                pen.setWidth(t)
                pen.setColor(QColor("black"))
                painter.setPen(pen)
                
                if h <= 4:
                    painter.drawLine(x, y, x + w, y)
                elif w <= 4:
                    painter.drawLine(x, y, x, y + h)
                else:
                    painter.drawRect(x, y, w, h)
            elif cmd == "FD":
                text = args_str
                if text.endswith("FS"):
                    text = text[:-2].strip()
                    
                if text.startswith("=====") or text.endswith("====="):
                    continue
                    
                text = text.replace(r"\&", "\n")
                
                if barcode_pending:
                    PrinterService._draw_barcode(painter, x, y, barcode_height, text, barcode_vertical, module_width)
                    barcode_pending = False
                else:
                    font = QFont("Arial", font_height)
                    font.setBold(font_bold)
                    painter.setFont(font)
                    
                    pen = painter.pen()
                    pen.setColor(QColor("black"))
                    painter.setPen(pen)
                    
                    if fb_width:
                        align_flag = Qt.AlignLeft
                        if fb_align == "C":
                            align_flag = Qt.AlignCenter
                        elif fb_align == "R":
                            align_flag = Qt.AlignRight
                            
                        rect = QRectF(x, y, fb_width, font_height * 3)
                        painter.drawText(rect, align_flag | Qt.TextWordWrap, text)
                        fb_width = None
                    else:
                        painter.drawText(x, y + font_height, text)

    @staticmethod
    def _draw_barcode(painter, x, y, height, text, is_vertical=False, module_width=2):
        from PySide6.QtGui import QColor
        try:
            patterns = PrinterService._encode_code128(text)
        except Exception:
            return
            
        current_x = x
        current_y = y
        
        if is_vertical:
            painter.save()
            painter.translate(x, y)
            painter.rotate(90)
            current_x = 0
            current_y = 0
            
        pen = painter.pen()
        pen.setWidth(0)
        painter.setPen(pen)
        
        for pat_idx in patterns:
            if pat_idx not in CODE128_PATTERNS:
                continue
            widths = CODE128_PATTERNS[pat_idx]
            for elem_idx, w in enumerate(widths):
                is_bar = elem_idx % 2 == 0
                w_pixels = w * module_width
                
                if is_bar:
                    painter.fillRect(current_x, current_y, w_pixels, height, QColor("black"))
                current_x += w_pixels
                
        if is_vertical:
            painter.restore()

    @staticmethod
    def _encode_code128(text: str) -> list[int]:
        is_numeric = text.isdigit() and len(text) % 2 == 0
        
        encoded = []
        if is_numeric:
            encoded.append(105)
            checksum = 105
            weight = 1
            for i in range(0, len(text), 2):
                val = int(text[i:i+2])
                encoded.append(val)
                checksum += val * weight
                weight += 1
        else:
            encoded.append(104)
            checksum = 104
            weight = 1
            for char in text:
                val = ord(char) - 32
                if 0 <= val <= 102:
                    encoded.append(val)
                    checksum += val * weight
                else:
                    encoded.append(0)
                    checksum += 0 * weight
                weight += 1
                    
        checksum_val = checksum % 103
        encoded.append(checksum_val)
        encoded.append(106)
        return encoded


# Tabela de codificação Code 128 padrão
CODE128_PATTERNS = {
    0: (2, 1, 2, 2, 2, 2), 1: (2, 2, 2, 1, 2, 2), 2: (2, 2, 2, 2, 2, 1), 3: (1, 2, 1, 2, 2, 3),
    4: (1, 2, 1, 3, 2, 2), 5: (1, 3, 1, 2, 2, 2), 6: (1, 2, 2, 2, 1, 3), 7: (1, 2, 2, 3, 1, 2),
    8: (1, 3, 2, 2, 1, 2), 9: (2, 2, 1, 2, 1, 3), 10: (2, 2, 1, 3, 1, 2), 11: (2, 3, 1, 2, 1, 2),
    12: (1, 1, 2, 2, 3, 2), 13: (1, 2, 2, 1, 3, 2), 14: (1, 2, 2, 2, 3, 1), 15: (1, 1, 3, 2, 2, 2),
    16: (1, 2, 3, 1, 2, 2), 17: (1, 2, 3, 2, 2, 1), 18: (2, 2, 3, 2, 1, 1), 19: (2, 2, 1, 1, 3, 2),
    20: (2, 2, 1, 2, 3, 1), 21: (2, 1, 3, 2, 1, 2), 22: (2, 2, 3, 1, 1, 2), 23: (3, 1, 2, 1, 3, 1),
    24: (3, 1, 1, 2, 2, 2), 25: (3, 2, 1, 1, 2, 2), 26: (3, 2, 1, 2, 2, 1), 27: (3, 1, 2, 2, 1, 2),
    28: (3, 2, 2, 1, 1, 2), 29: (3, 2, 2, 2, 1, 1), 30: (2, 1, 2, 1, 2, 3), 31: (2, 1, 2, 3, 2, 1),
    32: (2, 3, 2, 1, 2, 1), 33: (1, 1, 1, 3, 2, 3), 34: (1, 3, 1, 1, 2, 3), 35: (1, 3, 1, 3, 2, 1),
    36: (1, 1, 2, 3, 1, 3), 37: (1, 3, 2, 1, 1, 3), 38: (1, 3, 2, 3, 1, 1), 39: (2, 1, 1, 3, 1, 3),
    40: (2, 3, 1, 1, 1, 3), 41: (2, 3, 1, 3, 1, 1), 42: (1, 1, 2, 1, 3, 3), 43: (1, 1, 2, 3, 3, 1),
    44: (1, 3, 2, 1, 3, 1), 45: (1, 1, 3, 1, 2, 3), 46: (1, 1, 3, 3, 2, 1), 47: (1, 3, 3, 1, 2, 1),
    48: (3, 1, 3, 1, 2, 1), 49: (2, 1, 1, 3, 3, 1), 50: (2, 3, 1, 1, 3, 1), 51: (2, 1, 3, 1, 1, 3),
    52: (2, 1, 3, 3, 1, 1), 53: (2, 1, 3, 1, 3, 1), 54: (3, 1, 1, 1, 2, 3), 55: (3, 1, 1, 3, 2, 1),
    56: (3, 3, 1, 1, 2, 1), 57: (3, 1, 2, 1, 1, 3), 58: (3, 1, 2, 3, 1, 1), 59: (3, 3, 2, 1, 1, 1),
    60: (3, 1, 4, 1, 1, 1), 61: (2, 2, 1, 4, 1, 1), 62: (4, 3, 1, 1, 1, 1), 63: (1, 1, 1, 2, 2, 4),
    64: (1, 1, 1, 4, 2, 2), 65: (1, 2, 1, 1, 2, 4), 66: (1, 2, 1, 4, 2, 1), 67: (1, 4, 1, 1, 2, 2),
    68: (1, 4, 1, 2, 2, 1), 69: (1, 1, 2, 2, 1, 4), 70: (1, 1, 2, 4, 1, 2), 71: (1, 2, 2, 2, 1, 4),
    72: (1, 2, 2, 4, 1, 1), 73: (1, 4, 2, 2, 1, 1), 74: (1, 4, 2, 1, 1, 2), 75: (2, 1, 2, 2, 1, 4),
    76: (2, 1, 2, 4, 1, 2), 77: (2, 4, 2, 1, 1, 1), 78: (4, 1, 1, 2, 1, 2), 79: (4, 2, 1, 1, 1, 2),
    80: (4, 2, 1, 2, 1, 1), 81: (2, 1, 2, 1, 4, 1), 82: (2, 1, 4, 1, 2, 1), 83: (2, 4, 1, 1, 2, 1),
    84: (1, 2, 1, 1, 4, 2), 85: (1, 2, 1, 2, 4, 1), 86: (1, 4, 1, 2, 4, 1), 87: (1, 1, 4, 2, 1, 2),
    88: (1, 2, 4, 1, 1, 2), 89: (1, 2, 4, 2, 1, 1), 90: (4, 1, 1, 2, 2, 1), 91: (4, 1, 1, 1, 2, 2),
    92: (4, 2, 1, 1, 2, 1), 93: (1, 1, 1, 4, 4, 1), 94: (1, 1, 1, 1, 4, 4), 95: (1, 1, 4, 1, 1, 4),
    96: (1, 1, 4, 4, 1, 1), 97: (4, 1, 1, 1, 4, 1), 98: (4, 1, 1, 4, 1, 1), 99: (1, 1, 1, 1, 4, 1),
    100: (1, 1, 1, 4, 1, 1), 101: (4, 1, 1, 1, 1, 1), 102: (1, 1, 1, 1, 1, 1),
    103: (2, 1, 1, 4, 1, 2),
    104: (2, 1, 1, 2, 1, 4),
    105: (2, 1, 1, 2, 3, 2),
    106: (2, 3, 3, 1, 1, 1, 2)
}
