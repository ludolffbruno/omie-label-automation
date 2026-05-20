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
            "cObs": "PEDIDO COMPRA: PO-98765-XYZ | A/C: BRUNO LUDOLFF | ORDEM: ORD-321"
        },
        "pedido": {
            "cNumeroPedido": "999"
        }
    }
    
    normalized = omie_client.normalize_invoice(raw_nfe)
    
    assert normalized.id_nfe == 1001
    assert normalized.cliente_nome == "CLARO DISTRIBUICAO S/A"
    assert normalized.template_name == "claro_dividida"
    assert normalized.oc == "PO-98765-XYZ"
    assert normalized.requisitante == "BRUNO LUDOLFF"
    assert normalized.numero_ordem == "ORD-321"
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
    assert normalized.numero_ordem == "PED-GSK-99"  # Falls back to pedido_venda
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
    assert normalized.oc == "PED-OUTRO-55"  # Maps to pedido_venda
    assert normalized.requisitante == "CARLOS SILVA"
    assert normalized.numero_ordem == "PED-OUTRO-55"  # Maps to pedido_venda
    assert normalized.quantidade_volumes == 1


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
    assert normalized.protocolo == "0023595237"
    assert normalized.requisitante == "LUCIANO SILVA"


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
    assert normalized.requisitante == "BRUNO LUDOLFF"
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
    assert "cStatus" not in param
    assert "dRegInicial" not in param


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
                "destinatario": {"cNome": "CLI1"}
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
                "destinatario": {"cNome": "CLI2"}
            }
        ]
    }

    mock_post.side_effect = [mock_resp_1, mock_resp_2]

    invoices = omie_client.fetch_all_new_nfes(start_date="20/05/2026")
    
    assert len(invoices) == 2
    assert invoices[0].id_nfe == 2001
    assert invoices[1].id_nfe == 2002
    assert mock_post.call_count == 2
