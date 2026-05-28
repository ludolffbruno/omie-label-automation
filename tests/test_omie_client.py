import pytest
from unittest.mock import patch, MagicMock
import httpx
import tempfile
import os

from app.core.config import config
from app.database.models import DatabaseManager, NormalizedInvoice
from app.api.omie_client import OmieClient, OmieClientError

# Mock config for testing
config.omie_app_key = "test_key"
config.omie_app_secret = "test_secret"


@pytest.fixture
def temp_db():
    """Fixture to provide a clean SQLite database in a temporary file."""
    fd, path = tempfile.mkstemp()
    os.close(fd)
    db_mgr = DatabaseManager(db_path=path)
    yield db_mgr
    # Clean up the temp file after test
    try:
        os.remove(path)
    except OSError:
        pass


@pytest.fixture
def omie_client():
    return OmieClient()


def test_database_manager_insert_and_check(temp_db):
    """Test that DatabaseManager inserts invoices and accurately checks for duplicates."""
    # Initially it should not be processed
    assert not temp_db.is_nfe_processed(12345)
    
    # Mark it as processed
    temp_db.mark_nfe_as_processed(
        id_nfe=12345,
        numero_nf="0000050339",
        chave_nfe="35260500000000000000550010000503391000000001",
        cliente_nome="CLARO S.A.",
        status="APROVADA",
        volumes=2
    )
    
    # Now it should show as processed
    assert temp_db.is_nfe_processed(12345)
    
    # Test logging event
    temp_db.log_event(12345, "INFO", "Etiqueta impressa com sucesso")


def test_normalize_invoice_claro_rules(omie_client):
    """Tests custom extraction rules for CLARO client from observations."""
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1001,
            "cNumero": "000001",
            "cChaveNFe": "35260500000000000000550010000503391000001001",
            "cStatus": "APROVADA",
            "dEmis": "20/05/2026",
            "cProtocolo": "135260000001"
        },
        "destinatario": {
            "cNome": "CLARO DISTRIBUICAO S/A",
            "cCNPJCPF": "40.432.548/0001-01",
            "cUF": "SP"
        },
        "transportadora": {
            "nQtdVol": 3
        },
        "informacoes_adicionais": {
            "cObs": "Inf. Contribuinte: PROCON RJ / PEDIDO 5500594041 / PROTOCOLO 0023623450 / A/C BRUNO LUDOLFF"
        },
        "pedido": {
            "cNumeroPedido": "999"
        }
    }
    
    normalized = omie_client.normalize_invoice(raw_nfe)
    
    assert normalized.id_nfe == 1001
    assert normalized.cliente_nome == "CLARO DISTRIBUICAO S/A"
    assert normalized.template_name == "claro_dividida"
    assert normalized.oc == "5500594041"
    assert normalized.requisitante == "MUCIO 2121-3885"
    assert normalized.numero_ordem == "5500594041"
    assert normalized.protocolo == "0023623450"
    assert normalized.quantidade_volumes == 3


def test_normalize_invoice_gsk_rules(omie_client):
    """Tests custom extraction rules for GSK client."""
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1002,
            "cNumero": "000002",
            "cChaveNFe": "35260500000000000000550010000503391000001002",
            "cStatus": "APROVADA",
            "dEmis": "20/05/2026"
        },
        "destinatario": {
            "cNome": "GSK FARMACEUTICA LTDA",
            "cCNPJCPF": "33.247.743/0001-10",
            "cUF": "RJ"
        },
        "transportadora": {
            "nQtdVol": 0  # Invalid volume should default to 1
        },
        "informacoes_adicionais": {
            "cObs": "GSK-OC: GSK-776655 | SOLICITANTE: ANA SOUZA"
        },
        "pedido": {
            "cNumeroPedido": "PED-GSK-99"
        }
    }
    
    normalized = omie_client.normalize_invoice(raw_nfe)
    
    assert normalized.cliente_nome == "GSK FARMACEUTICA LTDA"
    assert normalized.template_name == "gsk"
    assert normalized.oc == "GSK-776655"
    assert normalized.requisitante == "ANA SOUZA"
    assert normalized.numero_ordem == "GSK-776655"
    assert normalized.quantidade_volumes == 1  # 0 volume default fallback


def test_normalize_invoice_glaxosmithkline_rules(omie_client):
    """Tests that the formal GlaxoSmithKline name also maps to the GSK template."""
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1006,
            "cNumero": "000006",
            "cChaveNFe": "35260500000000000000550010000503391000001006",
            "cStatus": "APROVADA",
            "dEmis": "20/05/2026"
        },
        "destinatario": {
            "cNome": "GLAXOSMITHKLINE BRASIL LTDA",
            "cCNPJCPF": "33.247.743/0001-10",
            "cUF": "RJ"
        },
        "transportadora": {},
        "informacoes_adicionais": {
            "cObs": "GSK-OC: GSK-112233 | SOLICITANTE: MARIA"
        },
        "pedido": {
            "cNumeroPedido": "PED-GSK-100"
        }
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.template_name == "gsk"
    assert normalized.oc == "GSK-112233"
    assert normalized.requisitante == "MARIA"


def test_normalize_invoice_default_rules(omie_client):
    """Tests fallback to DEFAULT rules when no client matches."""
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1003,
            "cNumero": "000003",
            "cChaveNFe": "35260500000000000000550010000503391000001003",
            "cStatus": "APROVADA",
            "dEmis": "20/05/2026"
        },
        "destinatario": {
            "cNome": "OUTRO CLIENTE S.A.",
            "cCNPJCPF": "12.345.678/0001-99",
            "cUF": "MG"
        },
        "transportadora": {},  # Missing volume should default to 1
        "informacoes_adicionais": {
            "cObs": "A/C: CARLOS SILVA"
        },
        "pedido": {
            "cNumeroPedido": "PED-OUTRO-55"
        }
    }
    
    normalized = omie_client.normalize_invoice(raw_nfe)
    
    assert normalized.template_name == "default"
    assert normalized.oc is None
    assert normalized.requisitante == "CARLOS SILVA"
    assert normalized.numero_ordem is None
    assert normalized.quantidade_volumes == 1


def test_normalize_invoice_extracts_oc_and_requester_without_omie_order(omie_client):
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1010,
            "cNumero": "000010",
            "cChaveNFe": "35260500000000000000550010000503391000001010",
            "cStatus": "APROVADA",
            "dEmis": "26/05/2026"
        },
        "destinatario": {
            "cNome": "ESSILOR LAB RIO PRODUTOS OTICOS LTDA",
            "cCNPJCPF": "12.345.678/0001-99",
            "cUF": "RJ"
        },
        "transportadora": {},
        "informacoes_adicionais": {
            "cObs": "DADOS BANCARIOS / OC 636866/0 AC DE JEFERSON SILVA"
        },
        "pedido": {
            "cNumeroPedido": "12862"
        }
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.oc == "636866"
    assert normalized.numero_ordem == "636866"
    assert normalized.requisitante == "JEFERSON SILVA"


def test_label_field_extraction_ignores_omie_order_and_reads_danfe_text(omie_client):
    fields = omie_client._extract_label_fields(
        "INFORMACOES COMPLEMENTARES\nORDEM DE COMPRA 6602115510\n"
        "PROTOCOLO DE AUTORIZACAO DE USO\n233260223225130 - 19/05/2026"
    )

    assert omie_client._standardize_pedido(fields, pedido_cliente="12862", pedido_venda="12794") == "6602115510"
    assert fields.get("protocolo") is None


def test_claro_protocol_does_not_capture_header_de(omie_client):
    fields = omie_client._extract_label_fields(
        "PROTOCOLO DE AUTORIZACAO DE USO\n233260223128888 - 19/05/2026\n"
        "PEDIDO 5500596097/ PROTOCOLO 0023618220/ A/C MUCIO 2121-3885"
    )

    assert fields["numero_ordem"] == "5500596097"
    assert fields["protocolo"] == "0023618220"


def test_clean_extracted_value_rejects_preposition_de(omie_client):
    assert omie_client._clean_extracted_value("DE") == ""


def test_normalize_invoice_extracts_numero_do_pedido_as_pedido(omie_client):
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1012,
            "cNumero": "000012",
            "cChaveNFe": "35260500000000000000550010000503391000001012",
            "cStatus": "APROVADA",
            "dEmis": "26/05/2026"
        },
        "destinatario": {
            "cNome": "CLIENTE PEDIDO LTDA",
            "cCNPJCPF": "12.345.678/0001-99",
            "cUF": "SP"
        },
        "transportadora": {},
        "informacoes_adicionais": {
            "cObs": "Texto complementar: Nº do pedido 998877 / SOLICITANTE: MARIA"
        },
        "pedido": {}
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.numero_ordem == "998877"
    assert normalized.oc == "998877"
    assert normalized.requisitante == "MARIA"


def test_normalize_invoice_recovers_uf_from_nested_destination(omie_client):
    raw_nfe = {
        "ide": {
            "nNF": "000013",
            "dEmi": "26/05/2026"
        },
        "compl": {
            "nIdNF": 1013,
            "cChaveNFe": "35260500000000000000550010000503391000001013"
        },
        "nfDestInt": {
            "cRazao": "CLIENTE SEM UF DIRETA LTDA",
            "cnpj_cpf": "12.345.678/0001-99"
        },
        "dest": {
            "enderDest": {
                "UF": "RJ"
            }
        },
        "pedido": {},
        "infAdic": {
            "infCpl": "OC 12345"
        }
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.cliente_uf == "RJ"


def test_friendly_model_conditions_match_without_exposing_regex(omie_client):
    omie_client.rules["CLIENTE RJ"] = {
        "name": "Cliente RJ",
        "template": "gsk",
        "conditions": [
            {"field": "uf", "operator": "equals", "value": "RJ"}
        ],
        "mappings": {}
    }
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1014,
            "cNumero": "000014",
            "cChaveNFe": "35260500000000000000550010000503391000001014",
            "cStatus": "APROVADA",
            "dEmis": "26/05/2026"
        },
        "destinatario": {
            "cNome": "QUALQUER CLIENTE LTDA",
            "cCNPJCPF": "12.345.678/0001-99",
            "cUF": "RJ"
        },
        "transportadora": {},
        "informacoes_adicionais": {},
        "pedido": {}
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.template_name == "gsk"


def test_normalize_invoice_telmex_uses_claro_template_and_requester(omie_client):
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1011,
            "cNumero": "000011",
            "cChaveNFe": "35260500000000000000550010000503391000001011",
            "cStatus": "APROVADA",
            "dEmis": "26/05/2026"
        },
        "destinatario": {
            "cNome": "TELMEX DO BRASIL S/A",
            "cCNPJCPF": "40.432.548/0001-01",
            "cUF": "RJ"
        },
        "transportadora": {},
        "informacoes_adicionais": {
            "cObs": "PEDIDO 5500594041 / PROTOCOLO 0023623450 / A/C OUTRA PESSOA"
        },
        "pedido": {
            "cNumeroPedido": "12794"
        }
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.template_name == "claro_dividida"
    assert normalized.numero_ordem == "5500594041"
    assert normalized.protocolo == "0023623450"
    assert normalized.requisitante == "MUCIO 2121-3885"


def test_normalize_invoice_extracts_label_fields_from_nf_text(omie_client):
    """Tests generic extraction of label fields from free text in the NF-e."""
    raw_nfe = {
        "cabecalho": {
            "nIdNfe": 1007,
            "cNumero": "000007",
            "cChaveNFe": "35260500000000000000550010000503391000001007",
            "cStatus": "APROVADA",
            "dEmis": "20/05/2026"
        },
        "destinatario": {
            "cNome": "CLIENTE TEXTO LTDA",
            "cCNPJCPF": "12.345.678/0001-99",
            "cUF": "RJ"
        },
        "transportadora": {},
        "informacoes_adicionais": {
            "cObs": "Nº ORDEM: 4500216102 | PROTOCOLO: 0023595237 | A/C: LUCIANO SILVA"
        },
        "pedido": {}
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.numero_ordem == "4500216102"
    assert normalized.requisitante == "LUCIANO SILVA"
    assert normalized.protocolo is None


def test_normalize_invoice_nfconsultar_shape(omie_client):
    """Tests normalization for Omie's NFConsultar/ListarNF response shape."""
    raw_nfe = {
        "ide": {
            "nNF": "000005",
            "dEmi": "20/05/2026"
        },
        "compl": {
            "nIdNF": 1005,
            "cChaveNFe": "35260500000000000000550010000503391000001005"
        },
        "nfDestInt": {
            "cRazao": "CLARO DISTRIBUICAO S/A",
            "cnpj_cpf": "40.432.548/0001-01",
            "cUF": "SP"
        },
        "pedido": {
            "cNumPedido": "PV-123",
            "cNumeroPedidoCliente": "CLI-123"
        },
        "infAdic": {
            "infCpl": "OC: PO-123 | A/C: BRUNO LUDOLFF | ORDEM: ORD-555"
        },
        "transp": {
            "vol": {
                "qVol": 4
            }
        }
    }

    normalized = omie_client.normalize_invoice(raw_nfe)

    assert normalized.id_nfe == 1005
    assert normalized.numero_nf == "000005"
    assert normalized.template_name == "claro_dividida"
    assert normalized.oc == "PO-123"
    assert normalized.requisitante == "MUCIO 2121-3885"
    assert normalized.numero_ordem == "ORD-555"
    assert normalized.quantidade_volumes == 4


@patch("httpx.Client.post")
def test_client_post_success(mock_post, omie_client):
    """Tests a successful POST request returning records."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "pagina": 1,
        "nTotPaginas": 1,
        "registros": 1,
        "total_de_registros": 1,
        "listagemNfe": [
            {
                "cabecalho": {
                    "nIdNfe": 1004,
                    "cNumero": "000004",
                    "cChaveNFe": "35260500000000000000550010000503391000001004",
                    "cStatus": "APROVADA",
                    "dEmis": "20/05/2026"
                },
                "destinatario": {
                    "cNome": "CLIENTE MOCK",
                    "cCNPJCPF": "00.000.000/0001-00",
                    "cUF": "SP"
                }
            }
        ]
    }
    mock_post.return_value = mock_response

    response = omie_client.list_nfes(page=1)
    
    assert response["nTotPaginas"] == 1
    assert len(response["listagemNfe"]) == 1
    assert response["listagemNfe"][0]["cabecalho"]["nIdNfe"] == 1004


@patch("httpx.Client.post")
def test_list_nfes_uses_nfconsultar_payload(mock_post, omie_client):
    """Tests that ListarNF uses Omie's current NFConsultar request fields."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"pagina": 2, "total_de_paginas": 1, "nfCadastro": []}
    mock_post.return_value = mock_response

    omie_client.list_nfes(page=2, records_per_page=10, start_date="01/05/2026")

    payload = mock_post.call_args.kwargs["json"]
    param = payload["param"][0]

    assert payload["call"] == "ListarNF"
    assert param["pagina"] == 2
    assert param["registros_por_pagina"] == 10
    assert param["filtrar_por_status"] == "N"
    assert param["dEmiInicial"] == "01/05/2026"
    assert param["dEmiFinal"] == "01/05/2026"
    assert param["tpNF"] == "1"
    assert "cStatus" not in param
    assert "dRegInicial" not in param


@patch("httpx.Client.post")
def test_obter_url_danfe_uses_notafiscalutil_geturldanfe(mock_post, omie_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"cUrlDanfe": "https%3A//example.com/danfe.pdf"}
    mock_post.return_value = mock_response

    inv = NormalizedInvoice(
        id_nfe=123,
        numero_nf="000123",
        chave_nfe="",
        cliente_nome="CLIENTE",
        cliente_cnpj_cpf="",
        cliente_uf="SP",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="20/05/2026",
    )

    assert omie_client.obter_url_danfe(inv) == "https://example.com/danfe.pdf"
    payload = mock_post.call_args.kwargs["json"]
    param = payload["param"][0]
    assert payload["call"] == "GetUrlDanfe"
    assert param == {"nCodNF": 123}
    assert "cChaveNFe" not in param
    assert "cNF" not in param


def test_parse_redundant_wait_seconds(omie_client):
    msg = "ERROR: Consumo redundante detectado. Aguarde 48 segundos para tentar novamente (REDUNDANT)."
    assert omie_client.parse_redundant_wait_seconds(msg) == 48
    assert omie_client.parse_redundant_wait_seconds("outro erro") is None


@patch("httpx.Client.post")
def test_client_post_api_error(mock_post, omie_client):
    """Tests that Omie API fault strings raise OmieClientError."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "faultstring": "Erro de autenticacao: Credenciais invalidas.",
        "faultcode": "SOAP-ENV:Client-99"
    }
    mock_post.return_value = mock_response

    with pytest.raises(OmieClientError) as excinfo:
        omie_client.list_nfes()
        
    assert "Credenciais invalidas" in str(excinfo.value)


@patch("httpx.Client.post")
def test_fetch_all_new_nfes_pagination(mock_post, omie_client):
    """Tests page parsing and iteration behavior during fetch_all_new_nfes."""
    # First call returns Page 1 of 2
    mock_resp_1 = MagicMock()
    mock_resp_1.status_code = 200
    mock_resp_1.json.return_value = {
        "nPagina": 1,
        "nTotPaginas": 2,
        "nRegistros": 1,
        "listagemNfe": [
            {
                "cabecalho": {"nIdNfe": 2001, "cNumero": "001", "cStatus": "APROVADA", "dEmis": "20/05/2026"},
                "destinatario": {"cNome": "CLI1", "cUF": "SP"},
                "informacoes_adicionais": {"cObs": "OC 1001"},
            }
        ]
    }
    
    # Second call returns Page 2 of 2
    mock_resp_2 = MagicMock()
    mock_resp_2.status_code = 200
    mock_resp_2.json.return_value = {
        "nPagina": 2,
        "nTotPaginas": 2,
        "nRegistros": 1,
        "listagemNfe": [
            {
                "cabecalho": {"nIdNfe": 2002, "cNumero": "002", "cStatus": "APROVADA", "dEmis": "20/05/2026"},
                "destinatario": {"cNome": "CLI2", "cUF": "RJ"},
                "informacoes_adicionais": {"cObs": "OC 1002"},
            }
        ]
    }

    mock_post.side_effect = [mock_resp_1, mock_resp_2]

    invoices = omie_client.fetch_all_new_nfes(start_date="20/05/2026")
    
    assert len(invoices) == 2
    assert invoices[0].id_nfe == 2001
    assert invoices[1].id_nfe == 2002
    assert mock_post.call_count == 2


@patch("app.api.omie_client.OmieClient.consultar_nf")
@patch("httpx.Client.post")
def test_fetch_all_new_nfes_skips_incoming_before_detail(mock_post, mock_consultar, omie_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "total_de_paginas": 1,
        "nfCadastro": [
            {
                "ide": {"nNF": "000111", "dEmi": "20/05/2026", "tpNF": "0"},
                "compl": {"nIdNF": 111},
                "nfDestInt": {"cRazao": "FORNECEDOR APPLE"},
            },
            {
                "ide": {"nNF": "000222", "dEmi": "20/05/2026", "tpNF": "1"},
                "compl": {"nIdNF": 222},
                "nfDestInt": {"cRazao": "CLIENTE SA"},
                "dest": {"enderDest": {"UF": "SP"}},
                "infAdic": {"infCpl": "OC 222"},
            },
        ],
    }
    mock_post.return_value = mock_response

    invoices = omie_client.fetch_all_new_nfes(start_date="20/05/2026")

    assert [inv.id_nfe for inv in invoices] == [222]
    mock_consultar.assert_not_called()
