import json
import re
from pathlib import Path
from typing import Any, Optional, Dict, List
from urllib.parse import unquote
import tempfile
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from loguru import logger

from app.core.config import config
from app.database.models import NormalizedInvoice

UF_BY_STATE_NAME = {
    "ACRE": "AC",
    "ALAGOAS": "AL",
    "AMAPA": "AP",
    "AMAPÁ": "AP",
    "AMAZONAS": "AM",
    "BAHIA": "BA",
    "CEARA": "CE",
    "CEARÁ": "CE",
    "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES",
    "ESPÍRITO SANTO": "ES",
    "GOIAS": "GO",
    "GOIÁS": "GO",
    "MARANHAO": "MA",
    "MARANHÃO": "MA",
    "MATO GROSSO": "MT",
    "MATO GROSSO DO SUL": "MS",
    "MINAS GERAIS": "MG",
    "PARA": "PA",
    "PARÁ": "PA",
    "PARAIBA": "PB",
    "PARAÍBA": "PB",
    "PARANA": "PR",
    "PARANÁ": "PR",
    "PERNAMBUCO": "PE",
    "PIAUI": "PI",
    "PIAUÍ": "PI",
    "RIO DE JANEIRO": "RJ",
    "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS",
    "RONDONIA": "RO",
    "RONDÔNIA": "RO",
    "RORAIMA": "RR",
    "SANTA CATARINA": "SC",
    "SAO PAULO": "SP",
    "SÃO PAULO": "SP",
    "SERGIPE": "SE",
    "TOCANTINS": "TO",
}


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
                    "oc": {
                        "source": "observacoes",
                        "regex": r"\b(?:OC|O/C|ORDEM\s+DE\s+COMPRA|PEDIDO\s+DE\s+COMPRA|PEDIDO\s+COMPRA|PEDIDO)\b\s*[:=/\-\s]?\s*([A-Za-z0-9][A-Za-z0-9./\-]*)"
                    },
                    "requisitante": {
                        "source": "observacoes",
                        "regex": r"(?:\bA/C\b|\bAC\s+DE\b|\bAOS\s+CUIDADOS\s+DE\b|\bREQUISITANTE\b|\bSOLICITANTE\b)\s*[:\-\s]?\s*([^|\n;]+)"
                    },
                    "numero_ordem": {
                        "source": "observacoes",
                        "regex": r"(?:\bN[º°O]?\s*(?:DO\s*)?PEDIDO\b|\bNUMERO\s+(?:DO\s*)?PEDIDO\b|\bN[º°O]?\s*ORDEM\b|\bNUMERO\s+DA\s+ORDEM\b|\bNRO\s*ORDEM\b|\bORDEM\s+DE\s+COMPRA\b|\bPEDIDO\s+DE\s+COMPRA\b|\bPEDIDO\s+COMPRA\b|\bPEDIDO\b)\s*[:=/\-\s]?\s*([A-Za-z0-9][A-Za-z0-9./\-]*)"
                    }
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
                    if self._is_transient_fault(fault):
                        logger.warning(f"Omie API transient fault: {fault} (Code: {code})")
                    else:
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

    def list_nfes(
        self,
        page: int = 1,
        records_per_page: int = 50,
        status: str = "APROVADA",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Lists emitted product invoices (NF-e) using the NFConsultar service.
        
        Args:
            page: Page number to query (starts at 1).
            records_per_page: Number of records per page (max 500).
            status: Filter by status. APROVADA maps to Omie's non-canceled filter.
            start_date: Filter by emission date (DD/MM/YYYY).
            end_date: Final emission date. Defaults to start_date for exact-day searches.
        """
        param_obj = {
            "pagina": page,
            "registros_por_pagina": records_per_page,
            "ordenar_por": "DATA_LANCAMENTO",
            "ordem_decrescente": "S",
            "cDetalhesPedido": "S",
            "tpNF": "1",
        }
        status_filter = self._map_status_filter(status)
        if status_filter:
            param_obj["filtrar_por_status"] = status_filter
        if start_date:
            param_obj["dEmiInicial"] = start_date
            param_obj["dEmiFinal"] = end_date or start_date

        logger.info(f"Fetching NFes: Page {page}, Status={status}, StartDate={start_date}, EndDate={end_date or start_date}")
        return self._post("ListarNF", [param_obj])

    def fetch_all_new_nfes(self, start_date: str, status: str = "APROVADA", log_callback=None) -> List[NormalizedInvoice]:
        """Queries emitted invoices for one emission date, handles pagination, and normalizes them."""
        normalized_list: List[NormalizedInvoice] = []
        page = 1
        
        while True:
            try:
                response = self.list_nfes(page=page, records_per_page=50, status=status, start_date=start_date, end_date=start_date)
                
                # NFConsultar returns nfCadastro. listagemNfe is kept for old mocks/import API compatibility.
                nfe_list = response.get("nfCadastro") or response.get("listagemNfe", [])
                logger.info(f"Page {page}: Found {len(nfe_list)} invoices.")
                
                for raw_nfe in nfe_list:
                    try:
                        if not self._is_output_invoice(raw_nfe):
                            msg = f"Ignorando NF-e de entrada/fornecedor {self._get_invoice_number_for_log(raw_nfe)}."
                            logger.info(msg)
                            self._emit_log(log_callback, msg, "INFO")
                            continue
                        normalized = self.normalize_invoice(raw_nfe)
                        normalized_list.append(normalized)
                    except Exception as e:
                        # Log error for a specific invoice parsing and continue
                        nfe_num = self._get_invoice_number_for_log(raw_nfe)
                        msg = f"Erro ao normalizar NF-e {nfe_num}: {e}"
                        logger.error(msg)
                        self._emit_log(log_callback, msg, "ERROR")
                
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

    @staticmethod
    def _emit_log(log_callback, message: str, level: str = "INFO") -> None:
        if log_callback:
            log_callback(message, level)

    @staticmethod
    def _is_transient_fault(message: str) -> bool:
        text = str(message or "").lower()
        return "broken response" in text or "application server" in text or "rate" in text or "timeout" in text

    @staticmethod
    def parse_redundant_wait_seconds(message: str) -> Optional[int]:
        match = re.search(r"Aguarde\s+(\d+)\s+segundos", str(message or ""), re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _field_status_message(self, inv: NormalizedInvoice) -> str:
        nf = self._format_nf_for_log(inv.numero_nf)
        pedido = inv.numero_ordem or inv.oc
        protocolo = "OK" if inv.protocolo else "PENDENTE"
        if not self._is_claro_cliente(inv.cliente_nome, inv.cliente_cnpj_cpf):
            protocolo = "padrao=XXXXX"
        return (
            f"Campos NF-e {nf}: "
            f"UF={'OK' if inv.cliente_uf else 'PENDENTE'} | "
            f"Pedido={'OK' if pedido else 'PENDENTE'} | "
            f"Protocolo={protocolo}"
        )

    @staticmethod
    def _format_nf_for_log(numero_nf: Any) -> str:
        digits = re.sub(r"\D", "", str(numero_nf or ""))
        return f"{int(digits):,}".replace(",", ".") if digits else str(numero_nf or "")

    def consultar_nf(self, n_cod_nf: int) -> Dict[str, Any]:
        """Returns full NF-e details by Omie's internal NF code."""
        response = self._post("ConsultarNF", [{"nCodNF": int(n_cod_nf)}])
        data = response.get("nfCadastro") or response
        if isinstance(data, list):
            return data[0] if data and isinstance(data[0], dict) else {}
        return data if isinstance(data, dict) else {}

    def obter_url_danfe(self, invoice: NormalizedInvoice) -> str:
        """Obtém a URL do DANFE pela API Omie, sem fallback por navegador."""
        util_url = "https://app.omie.com.br/api/v1/produtos/notafiscalutil/"
        param = {"nCodNF": invoice.id_nfe}
        payload = {
            "call": "GetUrlDanfe",
            "app_key": self.app_key,
            "app_secret": self.app_secret,
            "param": [param],
        }
        with httpx.Client(timeout=30.0) as client:
            response = client.post(util_url, json=payload, headers={"Content-Type": "application/json"})
            data = response.json()
            if "faultstring" in data:
                raise OmieClientError(data.get("faultstring", "Nao foi possivel obter a URL do DANFE via API."))
            response.raise_for_status()

        for key in ("cUrlDanfe", "urlDanfe", "url", "cUrl", "danfe"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return self._normalize_signed_url(value.strip())
        raise OmieClientError("Nao foi possivel obter a URL do DANFE via API.")

    @staticmethod
    def _normalize_signed_url(url: str) -> str:
        raw = url.strip()
        lower = raw.lower()
        value = unquote(raw) if lower.startswith(("http%3a", "https%3a")) else raw
        return value.replace("+", "%2B")

    def baixar_danfe(
        self,
        invoice: NormalizedInvoice,
        target_dir: Optional[Path] = None,
        target_path: Optional[Path] = None,
    ) -> Path:
        """Baixa o PDF do DANFE para Downloads e retorna o caminho local."""
        url = self.obter_url_danfe(invoice)
        if target_path:
            path = Path(target_path)
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            downloads = target_dir or (Path.home() / "Downloads")
            downloads.mkdir(parents=True, exist_ok=True)
            nf = re.sub(r"\D", "", str(invoice.numero_nf or "")) or str(invoice.id_nfe)
            path = downloads / f"DANFE_NF_{nf}.pdf"
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            path.write_bytes(response.content)
        return path

    def enrich_invoice_from_danfe_pdf(self, invoice: NormalizedInvoice, pdf_path: Path) -> NormalizedInvoice:
        """Extracts label fields from a downloaded DANFE PDF without another Omie request."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            logger.warning(f"Could not extract DANFE text from {pdf_path}: {e}")
            return invoice

        if not text.strip():
            return invoice

        fields = self._extract_label_fields(text)
        uf = invoice.cliente_uf or self._extract_customer_uf({"texto_danfe": text}, {})
        pedido = self._standardize_pedido(fields)
        protocolo = invoice.protocolo
        if self._is_claro_cliente(invoice.cliente_nome, invoice.cliente_cnpj_cpf):
            protocolo = fields.get("protocolo") or protocolo

        return invoice.copy(update={
            "cliente_uf": uf or invoice.cliente_uf,
            "oc": fields.get("oc") or invoice.oc,
            "numero_ordem": pedido or invoice.numero_ordem,
            "requisitante": "MUCIO 2121-3885" if self._is_claro_cliente(invoice.cliente_nome, invoice.cliente_cnpj_cpf) else (fields.get("requisitante") or invoice.requisitante),
            "protocolo": protocolo,
            "observacoes": invoice.observacoes or text[:4000],
        })

    def enrich_invoice_from_danfe_download(
        self,
        invoice: NormalizedInvoice,
        temp_dir: Optional[Path] = None,
    ) -> tuple[NormalizedInvoice, Optional[Path]]:
        target_dir = temp_dir or Path(tempfile.gettempdir()) / "omie_label_danfes"
        path = self.baixar_danfe(invoice, target_dir=target_dir)
        return self.enrich_invoice_from_danfe_pdf(invoice, path), path

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
        cliente_uf = self._extract_customer_uf(raw_nfe, destinatario)
        
        pedido_venda = pedido.get("cNumeroPedido", "") or cabecalho.get("cNumeroPedido", "")
        pedido_cliente = pedido.get("cCodItemOutro", "") # Or external code from items if any
        
        # Determine client rules configuration
        client_rules = self._match_client_rules(cliente_nome, cliente_cnpj_cpf, raw_nfe, cliente_uf)
        template_name = client_rules.get("template", "default")
        model_name = client_rules.get("name") or None
        overrides = client_rules.get("overrides") or {}
        mappings = client_rules.get("mappings", {})
        
        # Raw observations and searchable text extracted from the NF-e response
        observacoes = info_adic.get("cObs", "")
        obs_fields = self._extract_label_fields(observacoes)
        search_text = self._build_search_text(raw_nfe)
        generic_fields = self._extract_label_fields(search_text)
        
        # Extract custom fields based on template mappings
        extracted = {}
        for field, mapping in mappings.items():
            extracted[field] = self._extract_value(mapping, raw_nfe, observacoes, pedido_venda)
        for field, value in obs_fields.items():
            if value:
                extracted[field] = value
        for field, value in generic_fields.items():
            if not extracted.get(field):
                extracted[field] = value
        if not extracted.get("numero_ordem") and extracted.get("oc"):
            extracted["numero_ordem"] = extracted["oc"]
        pedido_padronizado = self._standardize_pedido(extracted)
        protocolo = extracted.get("protocolo") or cabecalho.get("cProtocolo") or None
        if self._is_claro_cliente(cliente_nome, cliente_cnpj_cpf):
            template_name = "claro_dividida"
            extracted["requisitante"] = "MUCIO 2121-3885"
        else:
            protocolo = None
        if overrides.get("requisitante"):
            extracted["requisitante"] = str(overrides.get("requisitante")).strip()
            
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
            cliente_nome=str(overrides.get("cliente_nome") or cliente_nome).strip(),
            cliente_cnpj_cpf=cliente_cnpj_cpf,
            cliente_uf=cliente_uf,
            pedido_venda=pedido_venda,
            pedido_cliente=pedido_cliente or None,
            oc=extracted.get("oc") or None,
            requisitante=extracted.get("requisitante") or None,
            numero_ordem=pedido_padronizado or None,
            quantidade_volumes=quantidade_volumes,
            protocolo=protocolo,
            status=cabecalho.get("cStatus", "APROVADA"),
            data_emissao=cabecalho.get("dEmis", ""),
            template_name=template_name,
            model_name=model_name,
            label_note=client_rules.get("label_note") or None,
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
        cliente_uf = self._extract_customer_uf(raw_nfe, destinatario)

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
        obs_fields = self._extract_label_fields(observacoes)
        search_text = self._build_search_text(raw_nfe)
        generic_fields = self._extract_label_fields(search_text)

        client_rules = self._match_client_rules(cliente_nome, cliente_cnpj_cpf, raw_nfe, cliente_uf)
        template_name = client_rules.get("template", "default")
        model_name = client_rules.get("name") or None
        overrides = client_rules.get("overrides") or {}
        mappings = client_rules.get("mappings", {})
        extracted = {
            field: self._extract_value(mapping, raw_nfe, observacoes, pedido_venda)
            for field, mapping in mappings.items()
        }
        for field, value in obs_fields.items():
            if value:
                extracted[field] = value
        for field, value in generic_fields.items():
            if not extracted.get(field):
                extracted[field] = value
        if not extracted.get("numero_ordem") and extracted.get("oc"):
            extracted["numero_ordem"] = extracted["oc"]
        pedido_padronizado = self._standardize_pedido(extracted)
        protocolo = extracted.get("protocolo") or raw_nfe.get("protNFe", {}).get("nProt") or None
        if self._is_claro_cliente(cliente_nome, cliente_cnpj_cpf):
            template_name = "claro_dividida"
            extracted["requisitante"] = "MUCIO 2121-3885"
        else:
            protocolo = None
        if overrides.get("requisitante"):
            extracted["requisitante"] = str(overrides.get("requisitante")).strip()

        return NormalizedInvoice(
            id_nfe=int(id_nfe),
            numero_nf=numero_nf,
            chave_nfe=chave_nfe,
            cliente_nome=str(overrides.get("cliente_nome") or cliente_nome).strip(),
            cliente_cnpj_cpf=cliente_cnpj_cpf,
            cliente_uf=cliente_uf,
            pedido_venda=pedido_venda,
            pedido_cliente=pedido_cliente or None,
            oc=extracted.get("oc") or None,
            requisitante=extracted.get("requisitante") or None,
            numero_ordem=pedido_padronizado or None,
            quantidade_volumes=self._extract_volume_count(raw_nfe),
            protocolo=protocolo,
            status="CANCELADA" if ide.get("dCan") else "APROVADA",
            data_emissao=ide.get("dEmi", ""),
            template_name=template_name,
            model_name=model_name,
            label_note=client_rules.get("label_note") or None,
            observacoes=observacoes or None
        )

    def _needs_detail_enrichment(self, inv: NormalizedInvoice) -> bool:
        pedido = inv.numero_ordem or inv.oc
        if not inv.cliente_uf or not pedido or not inv.observacoes:
            return True
        return self._is_claro_cliente(inv.cliente_nome, inv.cliente_cnpj_cpf) and not inv.protocolo

    def apply_rules_to_invoice(self, inv: NormalizedInvoice) -> NormalizedInvoice:
        """Reapplies current rules to an already normalized/cache invoice without Omie calls."""
        raw = {
            "cliente_nome": inv.cliente_nome,
            "observacoes": inv.observacoes or "",
            "infAdic": {"infCpl": inv.observacoes or ""},
            "info": {"cObs": inv.observacoes or ""},
        }
        rules = self._match_client_rules(inv.cliente_nome, inv.cliente_cnpj_cpf, raw, inv.cliente_uf)
        template_name = rules.get("template", inv.template_name or "default")
        model_name = rules.get("name") or None
        overrides = rules.get("overrides") or {}
        if self._is_claro_cliente(inv.cliente_nome, inv.cliente_cnpj_cpf):
            template_name = "claro_dividida"
        return inv.model_copy(update={
            "cliente_nome": str(overrides.get("cliente_nome") or inv.cliente_nome).strip(),
            "requisitante": str(overrides.get("requisitante") or inv.requisitante or "").strip() or None,
            "template_name": template_name,
            "model_name": model_name,
            "label_note": rules.get("label_note") or None,
        })

    @staticmethod
    def _is_output_invoice(raw_nfe: Dict[str, Any]) -> bool:
        candidates = [
            raw_nfe.get("ide", {}).get("tpNF"),
            raw_nfe.get("cabecalho", {}).get("tpNF"),
            raw_nfe.get("tpNF"),
        ]
        for value in candidates:
            if value not in (None, ""):
                return str(value).strip() == "1"
        return True

    @staticmethod
    def _standardize_pedido(extracted: Dict[str, str], pedido_cliente: str = "", pedido_venda: str = "") -> str:
        return (
            extracted.get("numero_ordem")
            or extracted.get("oc")
            or ""
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

    def _extract_customer_uf(self, raw_nfe: Dict[str, Any], destinatario: Dict[str, Any]) -> str:
        """Finds the recipient UF across old and current Omie response shapes."""
        candidates = [
            destinatario.get("cUF"),
            destinatario.get("UF"),
            destinatario.get("uf"),
            destinatario.get("estado"),
            destinatario.get("cEstado"),
        ]
        endereco = destinatario.get("endereco")
        if isinstance(endereco, dict):
            candidates.extend([endereco.get("cUF"), endereco.get("UF"), endereco.get("uf")])
        ender_dest = raw_nfe.get("enderDest")
        if isinstance(ender_dest, dict):
            candidates.extend([ender_dest.get("UF"), ender_dest.get("cUF"), ender_dest.get("uf")])
        dest = raw_nfe.get("dest")
        if isinstance(dest, dict) and isinstance(dest.get("enderDest"), dict):
            candidates.extend([
                dest["enderDest"].get("UF"),
                dest["enderDest"].get("cUF"),
                dest["enderDest"].get("uf"),
            ])

        for value in candidates:
            uf = self._normalize_uf(value)
            if uf:
                return uf

        text = self._build_search_text(raw_nfe)
        match = re.search(r"\b(?:UF|ESTADO)\b\s*[:=\-]?\s*([A-Z]{2})\b", text, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        match = re.search(r"\b(?:UF|ESTADO)\b\s*[:=\-]?\s*([A-Za-zÀ-ÿ ]{4,24})\b", text, re.IGNORECASE)
        return self._normalize_uf(match.group(1)) if match else ""

    @staticmethod
    def _normalize_uf(value: Any) -> str:
        text = str(value or "").strip().upper()
        if re.fullmatch(r"[A-Z]{2}", text):
            return text
        return UF_BY_STATE_NAME.get(text, "")

    def _extract_label_fields(self, text: str) -> Dict[str, str]:
        """Extracts common label fields from free text in the NF-e."""
        if not text:
            return {}

        patterns = {
            "numero_ordem": [
                r"(?:\bN[º°O]?\s*(?:DO\s*)?PEDIDO\b|\bNUMERO\s+(?:DO\s*)?PEDIDO\b|\bN[º°O]?\s*ORDEM\b|\bNUMERO\s+DA\s+ORDEM\b|\bNRO\s*ORDEM\b|\bORDEM\s+DE\s+COMPRA\b|\bPEDIDO\s+DE\s+COMPRA\b|\bPEDIDO\s+COMPRA\b|\bPEDIDO\b)\s*[:=/\-\s]?\s*([A-Za-z0-9][A-Za-z0-9./\-]*)",
            ],
            "oc": [
                r"(?:\bOC\b|\bO/C\b|\bORDEM\s+DE\s+COMPRA\b|\bPEDIDO\s+DE\s+COMPRA\b|\bPEDIDO\s+COMPRA\b|\bPEDIDO\b)\s*[:=/\-\s]?\s*([A-Za-z0-9][A-Za-z0-9./\-]*)",
            ],
            "protocolo": [
                r"\b(?:PROTOCOLO|PROT\.?)\b\s*[:=/\-\s]+\s*(?!DE\b)([0-9][0-9.\-]{4,})",
            ],
            "requisitante": [
                r"(?:REQUISITANTE|SOLICITANTE|A/C|AC\s+DE|AOS\s+CUIDADOS\s+DE|AC/)\s*[:\-\s]?\s*([^|\n;]+)",
            ],
        }

        extracted: Dict[str, str] = {}
        for field, field_patterns in patterns.items():
            for pattern in field_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    extracted[field] = self._clean_extracted_value(match.group(1))
                    break
        return extracted

    @staticmethod
    def _is_claro_cliente(client_name: str, cnpj_cpf: str = "") -> bool:
        text = f"{client_name or ''} {cnpj_cpf or ''}".upper()
        digits = re.sub(r"\D", "", cnpj_cpf or "")
        return "CLARO" in text or "TELMEX" in text or digits.startswith("40432548")

    def _match_client_rules(
        self,
        client_name: str,
        cnpj_cpf: str = "",
        raw_nfe: Optional[Dict[str, Any]] = None,
        cliente_uf: str = "",
    ) -> Dict[str, Any]:
        """Matches client rules using friendly conditions saved by the UI."""
        if self._is_claro_cliente(client_name, cnpj_cpf):
            return self.rules.get("CLARO", {"template": "claro_dividida", "mappings": {}})
        client_name_upper = client_name.upper()
        for key, rules in self.rules.items():
            if key == "DEFAULT":
                continue
            if self._rule_conditions_match(rules, key, client_name_upper, raw_nfe, cliente_uf):
                logger.debug(f"Matched customer rules for: {key}")
                return rules
        return self.rules.get("DEFAULT", {})

    def _rule_conditions_match(
        self,
        rules: Dict[str, Any],
        key: str,
        client_name_upper: str,
        raw_nfe: Optional[Dict[str, Any]],
        cliente_uf: str,
    ) -> bool:
        conditions = rules.get("conditions") or []
        if not conditions:
            return key.upper() in client_name_upper

        search_text = self._build_search_text(raw_nfe or {}).upper() if raw_nfe else client_name_upper
        values = {
            "cliente": client_name_upper,
            "texto": search_text,
            "cidade": search_text,
            "uf": (cliente_uf or "").upper(),
        }
        for condition in conditions:
            field = str(condition.get("field") or "cliente").lower()
            operator = str(condition.get("operator") or "contains").lower()
            expected = str(condition.get("value") or "").strip().upper()
            current = values.get(field, search_text)
            if not expected:
                return False
            if operator == "equals" and current != expected:
                return False
            if operator == "starts_with" and not current.startswith(expected):
                return False
            if operator == "contains" and expected not in current:
                return False
        return True

    @staticmethod
    def _clean_extracted_value(value: Any) -> str:
        cleaned = str(value or "").strip()
        cleaned = re.sub(r"[;\|/,\s\-]+$", "", cleaned).strip()
        if cleaned.upper() in {"DE", "DA", "DO", "DAS", "DOS"}:
            return ""
        if re.fullmatch(r"\d+/0", cleaned):
            cleaned = cleaned.split("/", 1)[0]
        return cleaned

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
                return self._clean_extracted_value(match.group(1))
            return ""
            
        return self._clean_extracted_value(val) if val else ""
