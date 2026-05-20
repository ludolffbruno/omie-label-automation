import sqlite3
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
        return sqlite3.connect(self.db_absolute_path)

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
            conn.commit()

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
