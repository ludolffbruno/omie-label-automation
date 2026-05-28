import pytest
from app.database.models import NormalizedInvoice
from app.api.dp_generator import DPGenerator


@pytest.fixture
def base_invoice():
    return NormalizedInvoice(
        id_nfe=5555,
        numero_nf="000123",
        chave_nfe="35260500000000000000550010000503391000005555",
        cliente_nome="FÁBRICA DE TESTE S/A",
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


def test_generate_dp_default_template(base_invoice):
    labels = DPGenerator.generate(base_invoice)
    
    # 2 volumes devem gerar 2 etiquetas
    assert len(labels) == 2

    # Verifica comandos Direct Protocol específicos da Honeywell
    assert "OPTIMIZE \"BATCH\" ON" in labels[0]
    assert "CLL" in labels[0]
    assert "PF" in labels[0]

    assert "01/02" in labels[0]
    assert "02/02" in labels[1]

    # Nome do cliente (quebrado em palavras separadas pelo DPGenerator)
    assert "FABRICA" in labels[0]
    assert "TESTE" in labels[0]

    # Campos principais
    assert "NF-e" in labels[0]
    assert "123" in labels[0]
    assert "35260500000000000000550010000503391000005555" in labels[0]
    assert "PP 645,510" in labels[0]
    assert "PP 680,395" in labels[0]
    assert "BARLINE 780,3" in labels[0]
    assert "BARLINE 820" not in labels[0]
    assert 'BARSET "CODE128",1,1,80' in labels[0]
    assert 'PP 190,240' in labels[0]
    assert 'BARPRT "35260500000000000000550010000503391000005555"' in labels[0]
    assert '# BARPRT "35260500000000000000550010000503391000005555"' not in labels[0]


def test_generate_dp_default_prints_model_note_above_barcode(base_invoice):
    invoice = base_invoice.model_copy(update={"label_note": "ENTREGAR NA PORTARIA"})

    label = DPGenerator.generate(invoice)[0]

    assert 'PT "OBS: ENTREGAR NA PORTARIA"' in label
    assert 'BARSET "CODE128",1,1,55' in label
    assert 'PP 190,225' in label


def test_generate_dp_claro_template(base_invoice):
    base_invoice.template_name = "claro"
    base_invoice.oc = "OC-9988-CLARO"
    base_invoice.requisitante = "Carlos Silva"
    base_invoice.numero_ordem = "ORDEM-1122"
    
    labels = DPGenerator.generate(base_invoice)
    
    assert len(labels) == 2
    assert "01/02" in labels[0]

    # Campos especificos do template Claro Direct Protocol
    assert "CLARO" in labels[0]
    assert "PEDIDO" in labels[0]
    assert "PROTOCOLO" in labels[0]
    assert "A/C" in labels[0]
    assert "Carlos Silva" in labels[0]
    assert "ORDEM-1122" in labels[0]
    assert labels[0].count("DIR 4") == 1
    assert 'BARSET "CODE128",1,1,32' in labels[0]
    assert 'PP 8,535' in labels[0]
    assert 'PP 95,455' in labels[0]
    assert 'PP 95,95' in labels[0]
    assert 'BARPRT "35260500000000000000550010000503391000005555"' in labels[0]


def test_generate_dp_claro_ignores_model_note(base_invoice):
    invoice = base_invoice.model_copy(update={
        "template_name": "claro_dividida",
        "label_note": "NAO IMPRIMIR NA CLARO",
    })

    label = DPGenerator.generate(invoice)[0]

    assert "NAO IMPRIMIR NA CLARO" not in label


def test_generate_dp_batch_claro_odd(base_invoice):
    base_invoice.template_name = "claro"
    base_invoice.quantidade_volumes = 3
    base_invoice.numero_nf = "12345"

    labels = DPGenerator.generate_batch([base_invoice])
    # 3 volumes da Claro devem ser agrupados de 2 em 2
    assert len(labels) == 2
    assert "01/03" in labels[0]
    assert "02/03" in labels[0]
    assert "03/03" in labels[1]


def test_generate_dp_claro_pair_prints_two_vertical_nf_key_barcodes(base_invoice):
    left = base_invoice.model_copy(update={
        "id_nfe": 1,
        "template_name": "claro_dividida",
        "cliente_nome": "CLARO SA",
        "quantidade_volumes": 1,
        "numero_ordem": "5500000001",
        "chave_nfe": "11111111111111111111111111111111111111111111",
    })
    right = base_invoice.model_copy(update={
        "id_nfe": 2,
        "template_name": "claro_dividida",
        "cliente_nome": "CLARO SA",
        "quantidade_volumes": 1,
        "numero_ordem": "5500000002",
        "chave_nfe": "22222222222222222222222222222222222222222222",
    })

    labels = DPGenerator.generate_batch([left, right])

    assert len(labels) == 1
    assert labels[0].count("DIR 4") == 2
    assert labels[0].count('BARSET "CODE128",1,1,32') == 2
    assert 'PP 8,535' in labels[0]
    assert 'PP 438,535' in labels[0]
    assert 'PP 505,455' in labels[0]
    assert 'PP 685,260' in labels[0]
    assert 'PP 745,260' in labels[0]
    assert 'BARPRT "11111111111111111111111111111111111111111111"' in labels[0]
    assert 'BARPRT "22222222222222222222222222222222222222222222"' in labels[0]


def test_generate_dp_barcode_test_label():
    label = DPGenerator.generate_barcode_test_label()

    assert 'BARSET "CODE128",1,1,80' in label
    assert "PP 120,300" in label
    assert 'BARPRT "33260501278897000182550010000503771000000000"' in label
    assert "TESTE BARCODE CODE128" in label
