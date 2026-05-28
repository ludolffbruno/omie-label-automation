import sqlite3
import json
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from pathlib import Path
from app.core.config import config

class NormalizedInvoice(BaseModel):
    """Normalized Invoice Model representing data needed for logistics labels."""
    id_nfe: int = Field(..., description="Omie Internal NFe ID")
    numero_nf: str = Field(..., description="Invoice Number (cNumero)")
    chave_nfe: str = Field(..., description="44-character Access Key (cChaveNFe)")
    cliente_nome: str = Field(..., description="Client Name")
    cliente_cnpj_cpf: str = Field(..., description="Client CNPJ/CPF")
    cliente_uf: str = Field(..., description="Client State (UF)")
    pedido_venda: Optional[str] = Field(None, description="Omie Sales Order Number")
    pedido_cliente: Optional[str] = Field(None, description="Client Purchase Order Number")
    
    # Custom rule extracted fields
    oc: Optional[str] = Field(None, description="Extracted Purchase Order / OC")
    requisitante: Optional[str] = Field(None, description="Extracted Requester / A/C")
    numero_ordem: Optional[str] = Field(None, description="Extracted Order Number / No Ordem")
    
    # Logistics
    quantidade_volumes: int = Field(1, description="Quantity of volumes/boxes")
    protocolo: Optional[str] = Field(None, description="SEFAZ Authorization Protocol")
    status: str = Field(..., description="Invoice status (e.g. APROVADA)")
    data_emissao: str = Field(..., description="Invoice emission date (DD/MM/YYYY or YYYY-MM-DD)")
    
    # Layout and Metadata
    template_name: str = Field("default", description="Template to use (claro, gsk, default)")
    model_name: Optional[str] = Field(None, description="Friendly model name applied by rules")
    label_note: Optional[str] = Field(None, description="Manual note printed on standard labels")
    observacoes: Optional[str] = Field(None, description="Raw observations text (cObs)")
    processed_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Timestamp of parsing")


class DatabaseManager:
    """Helper to manage the SQLite database storage for processed NFe."""
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.db_path
        # Resolve absolute path if relative
        p = Path(self.db_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent.parent / p
        self.db_absolute_path = str(p)
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_absolute_path, timeout=30.0)

    def init_db(self):
        """Initializes tables if they do not exist."""
        # Ensure parent directory exists
        Path(self.db_absolute_path).parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # processed_nfe table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_nfe (
                    id_nfe INTEGER PRIMARY KEY,
                    numero_nf TEXT NOT NULL,
                    chave_nfe TEXT NOT NULL,
                    cliente_nome TEXT,
                    data_processamento TEXT NOT NULL,
                    status TEXT NOT NULL,
                    volumes_impressos INTEGER DEFAULT 0
                )
            """)
            
            # general print logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS nfe_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_nfe INTEGER,
                    tipo TEXT NOT NULL, -- INFO, WARNING, ERROR, SUCCESS
                    mensagem TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            
            # customer cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS customer_cache (
                    codigo_cliente INTEGER PRIMARY KEY,
                    estado TEXT,
                    razao_social TEXT,
                    cnpj_cpf TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # order cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_cache (
                    codigo_pedido INTEGER PRIMARY KEY,
                    obs_pedido TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS invoice_cache (
                    id_nfe INTEGER PRIMARY KEY,
                    data_emissao TEXT NOT NULL,
                    numero_nf TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    enrichment_status TEXT DEFAULT 'pending',
                    danfe_path TEXT,
                    last_error TEXT,
                    last_attempt_at TEXT,
                    next_attempt_at TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            self._ensure_invoice_cache_columns(cursor)
            conn.commit()

    def _ensure_invoice_cache_columns(self, cursor):
        cursor.execute("PRAGMA table_info(invoice_cache)")
        existing = {row[1] for row in cursor.fetchall()}
        columns = {
            "enrichment_status": "TEXT DEFAULT 'pending'",
            "danfe_path": "TEXT",
            "last_error": "TEXT",
            "last_attempt_at": "TEXT",
            "next_attempt_at": "TEXT",
        }
        for name, definition in columns.items():
            if name not in existing:
                cursor.execute(f"ALTER TABLE invoice_cache ADD COLUMN {name} {definition}")

    def is_nfe_processed(self, id_nfe: int) -> bool:
        """Checks if the NFe has already been processed."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM processed_nfe WHERE id_nfe = ?", (id_nfe,))
            return cursor.fetchone() is not None

    def mark_nfe_as_processed(self, id_nfe: int, numero_nf: str, chave_nfe: str, cliente_nome: str, status: str, volumes: int = 0):
        """Marks NFe as processed to prevent duplicate prints."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO processed_nfe (id_nfe, numero_nf, chave_nfe, cliente_nome, data_processamento, status, volumes_impressos)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (id_nfe, numero_nf, chave_nfe, cliente_nome, now, status, volumes))
            conn.commit()

    def log_event(self, id_nfe: Optional[int], tipo: str, mensagem: str):
        """Logs event messages to the database."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO nfe_log (id_nfe, tipo, mensagem, timestamp)
                VALUES (?, ?, ?, ?)
            """, (id_nfe, tipo, mensagem, now))
            conn.commit()

    def get_customer_state(self, codigo_cliente: int) -> Optional[str]:
        """Gets customer state (UF) from persistent cache."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT estado FROM customer_cache WHERE codigo_cliente = ?", (codigo_cliente,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def save_customer_state(self, codigo_cliente: int, estado: str, razao_social: str = "", cnpj_cpf: str = ""):
        """Saves customer state (UF) to persistent cache."""
        try:
            now = datetime.now().isoformat()
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO customer_cache (codigo_cliente, estado, razao_social, cnpj_cpf, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (codigo_cliente, estado, razao_social, cnpj_cpf, now))
                conn.commit()
        except Exception:
            pass

    def get_order_obs(self, codigo_pedido: int) -> Optional[str]:
        """Gets order observations from persistent cache."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT obs_pedido FROM order_cache WHERE codigo_pedido = ?", (codigo_pedido,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def save_order_obs(self, codigo_pedido: int, obs_pedido: str):
        """Saves order observations to persistent cache."""
        try:
            now = datetime.now().isoformat()
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO order_cache (codigo_pedido, obs_pedido, updated_at)
                    VALUES (?, ?, ?)
                """, (codigo_pedido, obs_pedido, now))
                conn.commit()
        except Exception:
            pass

    def get_cached_invoices_by_date(self, data_emissao: str) -> list[NormalizedInvoice]:
        """Returns normalized invoices cached for an emission date."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT payload_json FROM invoice_cache WHERE data_emissao = ? ORDER BY numero_nf DESC",
                    (data_emissao,),
                )
                invoices = []
                for (payload_json,) in cursor.fetchall():
                    invoices.append(NormalizedInvoice(**json.loads(payload_json)))
                return invoices
        except Exception:
            return []

    def get_invoice_cache_meta(self, id_nfe: int) -> dict:
        """Returns cached enrichment metadata for one NF-e."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT enrichment_status, danfe_path, last_error, last_attempt_at, next_attempt_at
                    FROM invoice_cache WHERE id_nfe = ?
                """, (id_nfe,))
                row = cursor.fetchone()
                if not row:
                    return {}
                return {
                    "enrichment_status": row[0],
                    "danfe_path": row[1],
                    "last_error": row[2],
                    "last_attempt_at": row[3],
                    "next_attempt_at": row[4],
                }
        except Exception:
            return {}

    def save_invoice_cache(
        self,
        invoice: NormalizedInvoice,
        enrichment_status: Optional[str] = None,
        danfe_path: Optional[str] = None,
        last_error: Optional[str] = None,
        last_attempt_at: Optional[str] = None,
        next_attempt_at: Optional[str] = None,
    ):
        """Saves one normalized invoice for later date searches."""
        try:
            now = datetime.now().isoformat()
            current = self.get_invoice_cache_meta(invoice.id_nfe)
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO invoice_cache (
                        id_nfe, data_emissao, numero_nf, payload_json,
                        enrichment_status, danfe_path, last_error, last_attempt_at, next_attempt_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    invoice.id_nfe,
                    invoice.data_emissao,
                    invoice.numero_nf,
                    invoice.model_dump_json(),
                    enrichment_status if enrichment_status is not None else current.get("enrichment_status", "pending"),
                    danfe_path if danfe_path is not None else current.get("danfe_path"),
                    last_error if last_error is not None else current.get("last_error"),
                    last_attempt_at if last_attempt_at is not None else current.get("last_attempt_at"),
                    next_attempt_at if next_attempt_at is not None else current.get("next_attempt_at"),
                    now,
                ))
                conn.commit()
        except Exception:
            pass
