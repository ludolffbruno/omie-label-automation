import pytest
from app.database.models import NormalizedInvoice
from app.api.zpl_generator import ZPLGenerator


@pytest.fixture
def base_invoice():
    return NormalizedInvoice(
        id_nfe=5555,
        numero_nf="000123",
        chave_nfe="35260500000000000000550010000503391000005555",
        cliente_nome="FÁBRICA DE TESTE S/A",  # Accented name to test sanitization
        cliente_cnpj_cpf="11.222.333/0001-44",
        cliente_uf="SP",
        pedido_venda="PED-987",
        quantidade_volumes=2,
        protocolo="135260000099",
        status="APROVADA",
        data_emissao="20/05/2026",
        template_name="default",
        observacoes="A/C: João dos Santos"
    )


def test_generate_default_template(base_invoice):
    labels = ZPLGenerator.generate(base_invoice)
    
    # 2 volumes devem gerar 2 etiquetas
    assert len(labels) == 2

    # Verifica dimensoes corretas para Honeywell PC42t 110mm x 70mm
    assert "^PW880" in labels[0]
    assert "^LL560" in labels[0]

    assert "01/02" in labels[0]
    assert "02/02" in labels[1]

    # Acentos sanitizados e nome reduzido para o layout do modelo
    assert "FABRICA TESTE" in labels[0]

    # Campos principais
    assert "NF-e" in labels[0]
    assert "123" in labels[0]
    assert "35260500000000000000550010000503391000005555" in labels[0]


def test_generate_claro_template(base_invoice):
    base_invoice.template_name = "claro"
    base_invoice.oc = "OC-9988-CLARO"
    base_invoice.requisitante = "Carlos Silva"
    base_invoice.numero_ordem = "ORDEM-1122"
    
    labels = ZPLGenerator.generate(base_invoice)
    
    assert len(labels) == 2
    assert "01/02" in labels[0]

    # Campos especificos do template Claro
    assert "CLARO" in labels[0]
    assert "Nº ORDEM" in labels[0]
    assert "PROTOCOLO" in labels[0]
    assert "AC/ Carlos Silva" in labels[0]
    assert "ORDEM-1122" in labels[0]


def test_generate_gsk_template(base_invoice):
    base_invoice.template_name = "gsk"
    base_invoice.oc = "GSK-OC-8877"
    base_invoice.requisitante = "Ana Paula"
    base_invoice.quantidade_volumes = 1
    
    labels = ZPLGenerator.generate(base_invoice)
    
    assert len(labels) == 1
    assert "01/01" in labels[0]

    # GSK usa o modelo padrao dos demais clientes
    assert "Cliente" in labels[0]
    assert "GSK-OC-8877" in labels[0]
    assert "AC/ Ana Paula" in labels[0]
