from app.database.models import NormalizedInvoice
from app.ui.ui_main import _invoice_template_display, _invoice_template_tooltip


def test_template_display_uses_model_name_and_note_marker():
    inv = NormalizedInvoice(
        id_nfe=1,
        numero_nf="000001",
        chave_nfe="",
        cliente_nome="OPERADOR NACIONAL DO SISTEMA ELETRICO ONS",
        cliente_cnpj_cpf="",
        cliente_uf="RJ",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="21/05/2026",
        template_name="default",
        model_name="ONS",
        label_note="OBS ONS",
    )

    assert _invoice_template_display(inv) == "ONS *"
    assert "Layout: default" in _invoice_template_tooltip(inv)
    assert "Observação: OBS ONS" in _invoice_template_tooltip(inv)


def test_template_display_falls_back_to_layout():
    inv = NormalizedInvoice(
        id_nfe=2,
        numero_nf="000002",
        chave_nfe="",
        cliente_nome="CLIENTE",
        cliente_cnpj_cpf="",
        cliente_uf="RJ",
        quantidade_volumes=1,
        status="APROVADA",
        data_emissao="21/05/2026",
        template_name="default",
    )

    assert _invoice_template_display(inv) == "default"
