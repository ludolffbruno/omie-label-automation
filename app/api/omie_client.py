import json
import re
from pathlib import Path
from typing import Any, Optional, Dict, List
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from loguru import logger

from app.core.config import config
from app.database.models import NormalizedInvoice


class OmieClientError(Exception):
    """Custom exception representing errors during Omie API calls."""
    pass


class OmieClient:
    """Client for interacting with the Omie ERP API."""
    
    def __init__(self):
        self.app_key = config.omie_app_key
        self.app_secret = config.omie_app_secret
        self.api_url = config.omie_api_url
        self.rules_path = Path(__file__).resolve().parent.parent / "core" / "rules.json"
        self.rules = self.load_rules()

    def load_rules(self) -> Dict[str, Any]:
        """Loads client-specific field extraction rules from rules.json."""
        if self.rules_path.exists():
            try:
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load rules.json: {e}")
        
        logger.warning("rules.json not found or failed to load. Using built-in fallback rules.")
        # Fallback rules in case of file missing
        return {
            "DEFAULT": {
                "template": "default",
                "mappings": {
                    "oc": {"source": "pedido_venda"},
                    "requisitante": {"source": "observacoes", "regex": "(?:A/C|REQUISITANTE):?\\s*([^|\\n;]+)"},
                    "numero_ordem": {"source": "pedido_venda"}
                }
            }
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=True
    )
    def _post(self, call: str, param: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Performs a JSON-RPC POST request to the Omie API with retry logic."""
        if not self.app_key or not self.app_secret:
            raise OmieClientError("Credentials (app_key or app_secret) are not configured.")

        payload = {
            "call": call,
            "app_key": self.app_key,
            "app_secret": self.app_secret,
            "param": param
        }
        
        headers = {
            "Content-Type": "application/json"
        }

        logger.debug(f"Calling API: {call} at {self.api_url}")
        
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(self.api_url, json=payload, headers=headers)
                
                try:
                    data = response.json()
                except json.JSONDecodeError as e:
                    response.raise_for_status()
                    logger.error(f"Failed to parse JSON response: {e}")
                    raise OmieClientError(f"Invalid JSON response from server: {e}") from e

                # Omie returns validation/business faults as JSON, often with HTTP 500.
                # Do not retry these, otherwise the API may block the duplicate request.
                if "faultstring" in data:
                    fault = data.get("faultstring", "Unknown Omie API error")
                    code = data.get("faultcode", "")
                    logger.error(f"Omie API returned fault: {fault} (Code: {code})")
                    raise OmieClientError(f"Omie API Error: {fault} (Code: {code})")

                response.raise_for_status()
                return data
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP Status Error: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"HTTP Request Connection Error: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise OmieClientError(f"Invalid JSON response from server: {e}")

    def list_nfes(self, page: int = 1, records_per_page: int = 50, status: str = "APROVADA", start_date: Optional[str] = None) -> Dict[str, Any]:
        """Lists emitted product invoices (NF-e) using the NFConsultar service.
        
        Args:
            page: Page number to query (starts at 1).
            records_per_page: Number of records per page (max 500).
            status: Filter by status. APROVADA maps to Omie's non-canceled filter.
            start_date: Filter by emission date (DD/MM/YYYY) starting from this date.
        """
        param_obj = {
            "pagina": page,
            "registros_por_pagina": records_per_page,
            "ordenar_por": "DATA_LANCAMENTO",
            "ordem_decrescente": "S",
            "cDetalhesPedido": "S",
        }
        status_filter = self._map_status_filter(status)
        if status_filter:
            param_obj["filtrar_por_status"] = status_filter
        if start_date:
            param_obj["dEmiInicial"] = start_date

        logger.info(f"Fetching NFes: Page {page}, Status={status}, StartDate={start_date}")
        return self._post("ListarNF", [param_obj])

    def fetch_all_new_nfes(self, start_date: str, status: str = "APROVADA") -> List[NormalizedInvoice]:
        """Queries all invoices since a specific registration date, handles pagination, and normalizes them."""
        normalized_list: List[NormalizedInvoice] = []
        page = 1
        
        while True:
            try:
                response = self.list_nfes(page=page, records_per_page=50, status=status, start_date=start_date)
                
                # NFConsultar returns nfCadastro. listagemNfe is kept for old mocks/import API compatibility.
                nfe_list = response.get("nfCadastro") or response.get("listagemNfe", [])
                logger.info(f"Page {page}: Found {len(nfe_list)} invoices.")
                
                for raw_nfe in nfe_list:
                    try:
                        normalized = self.normalize_invoice(raw_nfe)
                        normalized_list.append(normalized)
                    except Exception as e:
                        # Log error for a specific invoice parsing and continue
                        nfe_num = self._get_invoice_number_for_log(raw_nfe)
                        logger.error(f"Error normalizing invoice {nfe_num}: {e}")
                
                # Pagination metadata check
                total_pages = response.get("total_de_paginas") or response.get("nTotPaginas", 1)
                if page >= total_pages or len(nfe_list) == 0:
                    break
                page += 1
                
            except OmieClientError as e:
                # If Omie returns empty list error code (e.g. no results), it raises fault string
                # Example: "Não existem registros para os filtros informados."
                if "Não existem registros" in str(e) or "nenhuma nota" in str(e).lower():
                    logger.info("No records found for the filter criteria.")
                    break
                logger.error(f"Error fetching invoices list: {e}")
                raise
                
        return normalized_list

    def normalize_invoice(self, raw_nfe: Dict[str, Any]) -> NormalizedInvoice:
        """Transforms raw Omie invoice structure into a NormalizedInvoice object."""
        if "cabecalho" not in raw_nfe:
            return self._normalize_nfconsultar_invoice(raw_nfe)

        cabecalho = raw_nfe.get("cabecalho", {})
        destinatario = raw_nfe.get("destinatario", {})
        transportadora = raw_nfe.get("transportadora", {})
        info_adic = raw_nfe.get("informacoes_adicionais", {})
        pedido = raw_nfe.get("pedido", {})
        
        id_nfe = cabecalho.get("nIdNfe")
        if id_nfe is None:
            raise ValueError("NFe ID (nIdNfe) is missing in response header.")
            
        numero_nf = cabecalho.get("cNumero", "")
        chave_nfe = cabecalho.get("cChaveNFe", "")
        cliente_nome = destinatario.get("cNome", "").strip()
        cliente_cnpj_cpf = destinatario.get("cCNPJCPF", "").strip()
        cliente_uf = destinatario.get("cUF", "").strip()
        
        pedido_venda = pedido.get("cNumeroPedido", "") or cabecalho.get("cNumeroPedido", "")
        pedido_cliente = pedido.get("cCodItemOutro", "") # Or external code from items if any
        
        # Determine client rules configuration
        client_rules = self._match_client_rules(cliente_nome)
        template_name = client_rules.get("template", "default")
        mappings = client_rules.get("mappings", {})
        
        # Raw observations and searchable text extracted from the NF-e response
        observacoes = info_adic.get("cObs", "")
        search_text = self._build_search_text(raw_nfe)
        generic_fields = self._extract_label_fields(search_text)
        
        # Extract custom fields based on template mappings
        extracted = {}
        for field, mapping in mappings.items():
            extracted[field] = self._extract_value(mapping, raw_nfe, observacoes, pedido_venda)
        for field, value in generic_fields.items():
            if not extracted.get(field):
                extracted[field] = value
            
        # Parse volumes quantity
        volumes_raw = transportadora.get("nQtdVol", 1)
        try:
            quantidade_volumes = int(volumes_raw)
            if quantidade_volumes <= 0:
                quantidade_volumes = 1
        except (ValueError, TypeError):
            quantidade_volumes = 1
            
        return NormalizedInvoice(
            id_nfe=id_nfe,
            numero_nf=numero_nf,
            chave_nfe=chave_nfe,
            cliente_nome=cliente_nome,
            cliente_cnpj_cpf=cliente_cnpj_cpf,
            cliente_uf=cliente_uf,
            pedido_venda=pedido_venda,
            pedido_cliente=pedido_cliente or None,
            oc=extracted.get("oc") or None,
            requisitante=extracted.get("requisitante") or None,
            numero_ordem=extracted.get("numero_ordem") or None,
            quantidade_volumes=quantidade_volumes,
            protocolo=cabecalho.get("cProtocolo") or extracted.get("protocolo") or None,
            status=cabecalho.get("cStatus", "APROVADA"),
            data_emissao=cabecalho.get("dEmis", ""),
            template_name=template_name,
            observacoes=observacoes or None
        )

    def _normalize_nfconsultar_invoice(self, raw_nfe: Dict[str, Any]) -> NormalizedInvoice:
        """Normalizes the current Omie NFConsultar/ListarNF response shape."""
        ide = raw_nfe.get("ide", {})
        compl = raw_nfe.get("compl", {})
        destinatario = raw_nfe.get("nfDestInt", {}) or raw_nfe.get("destinatario", {})
        pedido = raw_nfe.get("pedido", {})
        info = raw_nfe.get("info", {})

        id_nfe = compl.get("nIdNF") or raw_nfe.get("nCodNF") or raw_nfe.get("nIdNF")
        if id_nfe is None:
            raise ValueError("NFe ID (compl.nIdNF) is missing in NFConsultar response.")

        numero_nf = str(ide.get("nNF") or raw_nfe.get("nNF") or "")
        chave_nfe = str(compl.get("cChaveNFe") or raw_nfe.get("cChaveNFe") or "")
        cliente_nome = str(destinatario.get("cRazao") or destinatario.get("cNome") or "").strip()
        cliente_cnpj_cpf = str(destinatario.get("cnpj_cpf") or destinatario.get("cCNPJCPF") or "").strip()
        cliente_uf = str(
            destinatario.get("cUF")
            or destinatario.get("uf")
            or raw_nfe.get("enderDest", {}).get("UF")
            or ""
        ).strip()

        pedido_venda = str(
            pedido.get("cNumPedido")
            or pedido.get("cNumeroPedido")
            or raw_nfe.get("cNumPedido")
            or ""
        )
        pedido_cliente = str(
            raw_nfe.get("cNumeroPedidoCliente")
            or pedido.get("cNumeroPedidoCliente")
            or pedido.get("cCodItemOutro")
            or ""
        )
        observacoes = str(
            raw_nfe.get("informacoes_adicionais", {}).get("cObs")
            or raw_nfe.get("infAdic", {}).get("infCpl")
            or info.get("cObs")
            or raw_nfe.get("cObs")
            or ""
        )
        search_text = self._build_search_text(raw_nfe)
        generic_fields = self._extract_label_fields(search_text)

        client_rules = self._match_client_rules(cliente_nome)
        template_name = client_rules.get("template", "default")
        mappings = client_rules.get("mappings", {})
        extracted = {
            field: self._extract_value(mapping, raw_nfe, observacoes, pedido_venda)
            for field, mapping in mappings.items()
        }
        for field, value in generic_fields.items():
            if not extracted.get(field):
                extracted[field] = value

        return NormalizedInvoice(
            id_nfe=int(id_nfe),
            numero_nf=numero_nf,
            chave_nfe=chave_nfe,
            cliente_nome=cliente_nome,
            cliente_cnpj_cpf=cliente_cnpj_cpf,
            cliente_uf=cliente_uf,
            pedido_venda=pedido_venda,
            pedido_cliente=pedido_cliente or None,
            oc=extracted.get("oc") or None,
            requisitante=extracted.get("requisitante") or None,
            numero_ordem=extracted.get("numero_ordem") or None,
            quantidade_volumes=self._extract_volume_count(raw_nfe),
            protocolo=raw_nfe.get("protNFe", {}).get("nProt") or extracted.get("protocolo") or None,
            status="CANCELADA" if ide.get("dCan") else "APROVADA",
            data_emissao=ide.get("dEmi", ""),
            template_name=template_name,
            observacoes=observacoes or None
        )

    @staticmethod
    def _map_status_filter(status: Optional[str]) -> Optional[str]:
        """Maps app status names to Omie's ListarNF status filter."""
        if not status:
            return None
        normalized = status.strip().upper()
        if normalized in {"C", "CANCELADA", "CANCELADO"}:
            return "C"
        if normalized in {"N", "APROVADA", "APROVADO", "NAO_CANCELADA", "NÃO_CANCELADA"}:
            return "N"
        return None

    @staticmethod
    def _extract_volume_count(raw_nfe: Dict[str, Any]) -> int:
        candidates = [
            raw_nfe.get("transportadora", {}).get("nQtdVol"),
            raw_nfe.get("transp", {}).get("vol", {}).get("qVol") if isinstance(raw_nfe.get("transp", {}).get("vol"), dict) else None,
            raw_nfe.get("qVol"),
            raw_nfe.get("nQtdVol"),
        ]
        for value in candidates:
            try:
                count = int(value)
                if count > 0:
                    return count
            except (TypeError, ValueError):
                continue
        return 1

    @staticmethod
    def _get_invoice_number_for_log(raw_nfe: Dict[str, Any]) -> str:
        return str(
            raw_nfe.get("cabecalho", {}).get("cNumero")
            or raw_nfe.get("ide", {}).get("nNF")
            or raw_nfe.get("cNumeroNFe")
            or "UNKNOWN"
        )

    def _build_search_text(self, raw_nfe: Dict[str, Any]) -> str:
        """Builds a plain-text corpus from the NF-e payload for label-field extraction."""
        parts: List[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, (dict, list)):
                        walk(child)
                    elif child not in (None, ""):
                        parts.append(f"{key}: {child}")
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif value not in (None, ""):
                parts.append(str(value))

        walk(raw_nfe)
        return "\n".join(parts)

    def _extract_label_fields(self, text: str) -> Dict[str, str]:
        """Extracts common label fields from free text in the NF-e."""
        if not text:
            return {}

        patterns = {
            "numero_ordem": [
                r"(?:N[º°O]?\s*ORDEM|NUMERO\s+DA\s+ORDEM|NRO\s*ORDEM|ORDEM)\s*[:\-]?\s*([A-Za-z0-9./\-]+)",
            ],
            "oc": [
                r"(?:OC|PEDIDO\s+COMPRA|ORDEM\s+DE\s+COMPRA)\s*[:\-]?\s*([A-Za-z0-9./\-]+)",
            ],
            "protocolo": [
                r"(?:PROTOCOLO|PROT\.?)\s*[:\-]?\s*([A-Za-z0-9./\-]+)",
            ],
            "requisitante": [
                r"(?:REQUISITANTE|SOLICITANTE|A/C|AC/)\s*[:\-]?\s*([^|\n;]+)",
            ],
        }

        extracted: Dict[str, str] = {}
        for field, field_patterns in patterns.items():
            for pattern in field_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    extracted[field] = match.group(1).strip()
                    break
        return extracted

    def _match_client_rules(self, client_name: str) -> Dict[str, Any]:
        """Matches client name to client rules in rules.json (case-insensitive substring match)."""
        client_name_upper = client_name.upper()
        for key, rules in self.rules.items():
            if key != "DEFAULT" and key in client_name_upper:
                logger.debug(f"Matched customer rules for: {key}")
                return rules
        return self.rules.get("DEFAULT", {})

    def _extract_value(self, mapping: Dict[str, Any], raw_nfe: Dict[str, Any], observacoes: str, pedido_venda: str) -> str:
        """Extracts field value from the raw invoice structure based on rule settings."""
        source = mapping.get("source")
        regex_pattern = mapping.get("regex")
        
        val = ""
        if source == "observacoes":
            val = observacoes
        elif source == "pedido_venda":
            val = pedido_venda
        elif source == "pedido_cliente":
            # Extract from raw pedido client field if populated
            val = raw_nfe.get("pedido", {}).get("cNumeroPedido", "")
        else:
            # Fallback to dictionary path traversal
            parts = source.split(".")
            curr = raw_nfe
            for part in parts:
                if isinstance(curr, dict):
                    curr = curr.get(part, {})
                else:
                    curr = ""
                    break
            if not isinstance(curr, (dict, list)):
                val = str(curr)

        if regex_pattern and val:
            match = re.search(regex_pattern, val, re.IGNORECASE)
            if match:
                # Return the captured group, stripped
                return match.group(1).strip()
            return ""
            
        return str(val).strip() if val else ""
