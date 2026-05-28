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


def test_generate_default_template_prints_model_note(base_invoice):
    invoice = base_invoice.model_copy(update={"label_note": "ENTREGAR NA PORTARIA"})

    label = ZPLGenerator.generate(invoice)[0]

    assert "OBS: ENTREGAR NA PORTARIA" in label
    assert "^FO65,425^GB750,30,1^FS" in label


def test_preview_default_template_shows_model_note(base_invoice):
    invoice = base_invoice.model_copy(update={"label_note": "ENTREGAR NA PORTARIA"})

    html = ZPLGenerator.preview_html(invoice)

    assert "OBS: ENTREGAR NA PORTARIA" in html


def test_preview_claro_template_ignores_model_note(base_invoice):
    invoice = base_invoice.model_copy(update={
        "cliente_nome": "CLARO SA",
        "template_name": "claro_dividida",
        "label_note": "NAO IMPRIMIR NA CLARO",
    })

    html = ZPLGenerator.preview_html(invoice)

    assert "NAO IMPRIMIR NA CLARO" not in html


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
    assert "PEDIDO" in labels[0]
    assert "PROTOCOLO" in labels[0]
    assert "AC/ Carlos Silva" in labels[0]
    assert "ORDEM-1122" in labels[0]
    assert "OBS:" not in labels[0]


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


def test_order_number_never_falls_back_to_omie_order(base_invoice):
    base_invoice.numero_ordem = None
    base_invoice.oc = None
    base_invoice.pedido_cliente = None
    base_invoice.pedido_venda = "12862"

    labels = ZPLGenerator.generate(base_invoice)

    assert "XXXXX" in labels[0]
    assert "12862" not in labels[0]


def test_batch_pairs_claro_even_with_standard_between(base_invoice):
    standard = base_invoice.model_copy(update={
        "id_nfe": 1,
        "numero_nf": "050001",
        "template_name": "default",
        "quantidade_volumes": 1,
        "numero_ordem": "STD-1",
    })
    claros = [
        base_invoice.model_copy(update={
            "id_nfe": 10 + idx,
            "numero_nf": f"05000{idx + 2}",
            "cliente_nome": "CLARO SA",
            "template_name": "claro_dividida",
            "quantidade_volumes": 1,
            "numero_ordem": f"55000000{idx}",
            "protocolo": f"00236000{idx}",
            "requisitante": "MUCIO 2121-3885",
        })
        for idx in range(3)
    ]

    labels = ZPLGenerator.generate_batch([claros[0], standard, claros[1], claros[2]])

    assert len(labels) == 3
    assert "STD-1" in labels[0]
    assert "550000000" in labels[1]
    assert "550000001" in labels[1]
    assert "550000002" in labels[2]
