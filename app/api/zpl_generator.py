import html
import re
from typing import List, Optional

from app.database.models import NormalizedInvoice


# ======================================================================
#  Honeywell PC42t - 203 DPI
#  Etiqueta: 110 mm x 70 mm
#  203 DPI ~= 8 dots/mm -> 880 x 560 dots
# ======================================================================
LABEL_WIDTH = 880
LABEL_HEIGHT = 560
MARGIN = 24
INNER_W = LABEL_WIDTH - (MARGIN * 2)
INNER_H = LABEL_HEIGHT - (MARGIN * 2)


class ZPLGenerator:
    """Gera ZPL para etiquetas logisticas na Honeywell PC42t 203 DPI."""

    @classmethod
    def generate(cls, invoice: NormalizedInvoice) -> List[str]:
        """Gera uma string ZPL para cada volume da nota fiscal."""
        total_vols = invoice.quantidade_volumes or 1
        zpl_labels = []

        for vol in range(1, total_vols + 1):
            if invoice.template_name in {"claro", "claro_dividida"}:
                zpl = cls._generate_claro_dividida(invoice, vol, total_vols)
            else:
                zpl = cls._generate_standard(invoice, vol, total_vols)
            zpl_labels.append(zpl)

        return zpl_labels

    @classmethod
    def generate_batch(cls, invoices: List[NormalizedInvoice]) -> List[str]:
        """Gera ZPL para uma lista de NF-es, agrupando Claro em etiqueta dividida."""
        labels: List[str] = []
        claro_buffer: List[tuple[NormalizedInvoice, int, int]] = []

        def emit_claro_pair(allow_single: bool = False) -> None:
            if len(claro_buffer) < 2 and not allow_single:
                return
            left_item = claro_buffer.pop(0)
            right_item = claro_buffer.pop(0) if claro_buffer else None
            left, left_vol, left_total = left_item
            if right_item:
                right, right_vol, right_total = right_item
            else:
                right, right_vol, right_total = None, 1, 1
            labels.append(cls._generate_claro_pair_from_items(left, left_vol, left_total, right, right_vol, right_total))

        def flush_claro() -> None:
            while claro_buffer:
                emit_claro_pair(allow_single=True)

        for invoice in invoices:
            if invoice.template_name in {"claro", "claro_dividida"} or cls._is_claro_invoice(invoice):
                total_vols = invoice.quantidade_volumes or 1
                for vol in range(1, total_vols + 1):
                    claro_buffer.append((invoice, vol, total_vols))
                while len(claro_buffer) >= 2:
                    emit_claro_pair()
            else:
                labels.extend(cls.generate(invoice))

        flush_claro()
        return labels

    @staticmethod
    def _sanitize(text: Optional[str]) -> str:
        """Remove acentos e caracteres que quebram comandos ZPL."""
        if not text:
            return ""
        s = re.sub(r"[\r\n\t]", " ", str(text))
        s = s.replace("^", "").replace("~", "")
        src = "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
        dst = "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC"
        return s.translate(str.maketrans(src, dst)).strip()

    @staticmethod
    def _header() -> List[str]:
        return [
            "^XA",
            f"^PW{LABEL_WIDTH}",
            f"^LL{LABEL_HEIGHT}",
            "^LH0,0",
            "^CI28",
            "^MMT",
            "^MNY",
        ]

    @staticmethod
    def _footer() -> List[str]:
        return ["^PQ1,0,1,Y", "^XZ"]

    @classmethod
    def _generate_standard(cls, inv: NormalizedInvoice, vol: int, total_vols: int) -> str:
        cliente = cls._client_display(inv)
        ordem = cls._order_number(inv)
        nf = cls._format_nf(inv.numero_nf)
        volume = cls._format_volume(vol, total_vols)
        uf = cls._sanitize(inv.cliente_uf)[:2]
        req = cls._requester(inv)
        barcode = cls._barcode_value(inv)
        label_note = cls._sanitize(inv.label_note or "")[:90]

        zpl = cls._header() + [
            "^FO70,35^A0N,56,52^FDP^FS",
            "^FO48,95^A0N,32,30^FDPLATINUM^FS",
            "^FO470,28^A0N,28,26^FDCliente^FS",
            f"^FO420,66^A0N,34,30^FD{cliente}^FS",
            "^FO700,24^A0N,30,28^FDEntrega^FS",
            "^FO700,68^A0N,16,14^FD0- EMIT^FS",
            "^FO700,88^A0N,16,14^FD1- DEST^FS",
            "^FO806,58^GB42,62,2^FS",
            "^FO819,70^A0N,38,34^FD0^FS",
            "^FO65,150^A0N,32,30^FDPEDIDO^FS",
            "^FO390,150^A0N,32,30^FDNF-e^FS",
            "^FO590,150^A0N,32,30^FDVOL^FS",
            "^FO740,150^A0N,32,30^FDUF^FS",
            f"^FO65,194^A0N,31,28^FD{ordem}^FS",
            f"^FO390,190^A0N,38,34^FD{nf}^FS",
            f"^FO590,190^A0N,38,34^FD{volume}^FS",
            f"^FO740,190^A0N,38,34^FD{uf}^FS",
            "^FO65,285^A0N,32,30^FDREQUISITANTE^FS",
            f"^FO85,330^A0N,28,24^FD{req}^FS",
        ]
        if label_note:
            zpl += [
                f"^FO65,382^A0N,18,16^FDOBS: {label_note}^FS",
                "^FO65,425^GB750,30,1^FS",
                f"^FO75,432^A0N,15,13^FD{barcode}^FS",
            ]
        else:
            zpl += [
                "^FO65,405^GB750,38,1^FS",
                f"^FO75,414^A0N,18,16^FD{barcode}^FS",
            ]
        zpl += [
            "^FO190,468^A0N,15,13^FDFAVOR CONFERIR O MATERIAL NO ATO DO RECEBIMENTO,^FS",
            "^FO168,487^A0N,15,13^FDNAO ACEITAREMOS RECLAMACOES OU DEVOLUCOES POSTERIORES.^FS",
            "^FO64,522^A0N,22,18^FDR. FRANCISCO EUGENIO 268 SALA 636, SAO CRISTOVAO RJ - TEL:21 3878-8855^FS",
        ]
        zpl += cls._footer()
        return "\n".join(zpl)

    @classmethod
    def _generate_claro_dividida(cls, inv: NormalizedInvoice, vol: int, total_vols: int) -> str:
        return cls._generate_claro_pair(inv, None, vol, total_vols)

    @classmethod
    def _generate_claro_pair(
        cls,
        left: NormalizedInvoice,
        right: Optional[NormalizedInvoice],
        left_vol: int = 1,
        left_total: Optional[int] = None,
    ) -> str:
        return cls._generate_claro_pair_from_items(
            left,
            left_vol,
            left_total or left.quantidade_volumes or 1,
            right,
            1,
            right.quantidade_volumes or 1 if right else 1,
        )

    @classmethod
    def _generate_claro_pair_from_items(
        cls,
        left: NormalizedInvoice,
        left_vol: int,
        left_total: int,
        right: Optional[NormalizedInvoice],
        right_vol: int = 1,
        right_total: int = 1,
    ) -> str:
        zpl = cls._header()
        zpl += cls._claro_half(left, x=18, vol=left_vol, total_vols=left_total)
        zpl += [
            "^FO438,0^GB1,560,1^FS",
            "^FO438,25^GB1,4,1^FS",
            "^FO438,55^GB1,4,1^FS",
            "^FO438,85^GB1,4,1^FS",
            "^FO438,115^GB1,4,1^FS",
            "^FO438,145^GB1,4,1^FS",
            "^FO438,175^GB1,4,1^FS",
            "^FO438,205^GB1,4,1^FS",
            "^FO438,235^GB1,4,1^FS",
            "^FO438,265^GB1,4,1^FS",
            "^FO438,295^GB1,4,1^FS",
            "^FO438,325^GB1,4,1^FS",
            "^FO438,355^GB1,4,1^FS",
            "^FO438,385^GB1,4,1^FS",
            "^FO438,415^GB1,4,1^FS",
            "^FO438,445^GB1,4,1^FS",
            "^FO438,475^GB1,4,1^FS",
            "^FO438,505^GB1,4,1^FS",
        ]
        if right:
            zpl += cls._claro_half(right, x=464, vol=right_vol, total_vols=right_total)
        zpl += cls._footer()
        return "\n".join(zpl)

    @classmethod
    def _claro_half(cls, inv: NormalizedInvoice, x: int, vol: int, total_vols: int) -> List[str]:
        ordem = cls._order_number(inv)
        nf = cls._format_nf(inv.numero_nf)
        protocolo = cls._sanitize(inv.protocolo or "")
        volume = f"{vol:02d}" if total_vols <= 1 else cls._format_volume(vol, total_vols)
        req = cls._requester(inv)

        return [
            f"^FO{x + 78},25^A0N,56,52^FDP^FS",
            f"^FO{x + 16},92^A0N,32,30^FDPLATINUM^FS",
            f"^FO{x + 245},45^A0N,38,34^FDCLARO^FS",
            f"^FO{x},160^A0N,31,29^FDPEDIDO^FS",
            f"^FO{x + 250},160^A0N,31,29^FDNF-e^FS",
            f"^FO{x},205^A0N,30,27^FD{ordem}^FS",
            f"^FO{x + 250},205^A0N,33,30^FD{nf}^FS",
            f"^FO{x},285^A0N,31,29^FDPROTOCOLO^FS",
            f"^FO{x + 260},285^A0N,31,29^FDVOL^FS",
            f"^FO{x},330^A0N,29,25^FD{protocolo}^FS",
            f"^FO{x + 260},330^A0N,31,28^FD{volume}^FS",
            f"^FO{x},410^A0N,31,29^FDA/C^FS",
            f"^FO{x + 8},455^A0N,28,24^FD{req}^FS",
        ]

    @classmethod
    def preview_html(cls, inv: NormalizedInvoice, vol: int = 1, total_vols: Optional[int] = None) -> str:
        """Renderiza uma previa aproximada em HTML para a UI."""
        total = total_vols or inv.quantidade_volumes or 1
        cliente = html.escape(cls._client_display(inv))
        ordem = html.escape(cls._order_number(inv))
        nf = html.escape(cls._format_nf(inv.numero_nf))
        volume = html.escape(cls._format_volume(vol, total))
        uf = html.escape(cls._sanitize(inv.cliente_uf)[:2])
        req = html.escape(cls._requester(inv))
        is_claro = inv.template_name in {"claro", "claro_dividida"} or cls._is_claro_invoice(inv)
        if is_claro:
            return cls.preview_html_claro_pair(inv, None, vol, total)
        barcode = html.escape(cls._barcode_value(inv))
        label_note = html.escape(cls._sanitize(inv.label_note or "")[:90])
        note_html = ""
        barcode_height = 42
        spacer_after_barcode = 18
        if label_note:
            note_html = f"""
          <tr>
            <td colspan="12" height="22" valign="middle" style="border-bottom:1px solid #b7b7b7;padding-left:8px;font-size:11px;font-weight:700;">
              OBS: {label_note}
            </td>
          </tr>
            """
            barcode_height = 36
            spacer_after_barcode = 10
        return f"""
        <table width="768" cellspacing="0" cellpadding="0" style="background:#fff;color:#000;
               font-family:Arial,sans-serif;border:2px solid #8a8a8a;margin:100px auto 70px auto;border-collapse:collapse;table-layout:fixed;">
          <col width="64"><col width="64"><col width="64"><col width="64"><col width="64"><col width="64">
          <col width="64"><col width="64"><col width="64"><col width="64"><col width="64"><col width="64">
          <tr>
            <td colspan="4" height="50" align="center" valign="middle"
                style="border-right:2px solid #8a8a8a;font-size:24px;font-weight:700;">PLATINUM TI</td>
            <td colspan="7" height="50" align="center" valign="middle"
                style="border-right:2px solid #8a8a8a;overflow:hidden;">
              <div style="font-size:10px;line-height:12px;">Cliente:</div>
              <div style="font-size:20px;line-height:22px;font-weight:700;white-space:nowrap;overflow:hidden;">{cliente}</div>
            </td>
            <td colspan="1" height="50" align="center" valign="middle">
              <span style="display:inline-block;border:1px solid #000;font-size:8px;line-height:9px;font-weight:700;">Entrega<br>0- EMIT<br>1- DEST</span>
              <span style="display:inline-block;border:2px solid #000;font-size:24px;line-height:30px;font-weight:700;">0</span>
            </td>
          </tr>
          <tr><td colspan="12" height="10" style="border-top:2px solid #8a8a8a;border-bottom:1px solid #b7b7b7;"></td></tr>
          <tr>
            <td colspan="3" height="50" valign="middle" style="padding-left:8px;border-bottom:2px solid #8a8a8a;">
              <div style="font-size:11px;line-height:13px;">Nº ORDEM</div>
              <div style="font-size:16px;line-height:18px;font-weight:700;">{ordem}</div>
            </td>
            <td colspan="4" height="50" valign="middle" style="border-bottom:2px solid #8a8a8a;">
              <div style="font-size:11px;line-height:13px;">NF-e</div>
              <div style="font-size:16px;line-height:18px;font-weight:700;">{nf}</div>
            </td>
            <td colspan="3" height="50" valign="middle" style="border-bottom:2px solid #8a8a8a;">
              <div style="font-size:11px;line-height:13px;">VOL</div>
              <div style="font-size:16px;line-height:18px;font-weight:700;">{volume}</div>
            </td>
            <td colspan="2" height="50" valign="middle" style="border-bottom:2px solid #8a8a8a;">
              <div style="font-size:11px;line-height:13px;">UF</div>
              <div style="font-size:16px;line-height:18px;font-weight:700;">{uf}</div>
            </td>
          </tr>
          <tr><td colspan="12" height="14" style="border-bottom:1px solid #b7b7b7;"></td></tr>
          <tr>
            <td colspan="12" height="27" valign="middle" style="border-bottom:2px solid #8a8a8a;padding-left:8px;font-size:12px;font-weight:700;">
              REQUISITANTE: {req or 'XXXXXXXX'}
            </td>
          </tr>
          {note_html}
          <tr>
            <td colspan="12" height="{barcode_height}" valign="top" style="border-bottom:1px solid #b7b7b7;padding:6px 8px 0 8px;">
              <div style="height:26px;border:1px solid #000;font-size:22px;line-height:25px;overflow:hidden;letter-spacing:1px;">
                ||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||
              </div>
            </td>
          </tr>
          <tr><td colspan="12" height="{spacer_after_barcode}" style="border-bottom:2px solid #8a8a8a;"></td></tr>
          <tr>
            <td colspan="12" height="29" align="center" valign="middle" style="font-size:10px;line-height:12px;">
              FAVOR CONFERIR O MATERIAL NO ATO DO RECEBIMENTO.<br>
              NAO ACEITAREMOS RECLAMACOES OU DEVOLUCOES POSTERIORES.
            </td>
          </tr>
          <tr>
            <td colspan="12" height="24" align="center" valign="middle" style="border-top:2px solid #8a8a8a;font-size:10px;font-weight:700;">
              R. FRANCISCO EUGENIO 268 SALA 636, SAO CRISTOVAO RJ - TEL:21 3878-8855
            </td>
          </tr>
        </table>
        """

    @classmethod
    def preview_html_claro_pair(
        cls,
        left: NormalizedInvoice,
        right: Optional[NormalizedInvoice],
        left_vol: int = 1,
        left_total: Optional[int] = None,
        right_vol: int = 1,
        right_total: Optional[int] = None,
    ) -> str:
        left_total = left_total or left.quantidade_volumes or 1
        right_total = right_total or (right.quantidade_volumes if right else 1) or 1
        left_html = cls._preview_claro_half(left, "PLATINUM TI", left_vol, left_total)
        right_html = cls._preview_claro_half(right, "PLATINUM TI", right_vol, right_total) if right else ""
        return f"""
        <table width="820" height="520" cellspacing="0" cellpadding="0" style="background:#fff;color:#000;
               font-family:Arial,sans-serif;margin:34px auto;border-collapse:collapse;">
          <tr>
            <td width="409" valign="top">{left_html}</td>
            <td width="2" bgcolor="#777"></td>
            <td width="409" valign="top">{right_html}</td>
          </tr>
        </table>
        """

    @classmethod
    def _preview_claro_half(cls, inv: Optional[NormalizedInvoice], title: str, vol: int, total: int) -> str:
        if not inv:
            return ""
        ordem = html.escape(cls._order_number(inv))
        nf = html.escape(cls._format_nf(inv.numero_nf))
        volume = html.escape(f"{vol:02d}" if total <= 1 else cls._format_volume(vol, total))
        protocolo = html.escape(cls._sanitize(inv.protocolo or "XXXXXXXXXX"))
        req = html.escape(cls._sanitize(inv.requisitante or "MUCIO 2121-3885"))
        title = html.escape(title)
        return f"""
        <table width="409" height="520" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
          <tr>
            <td width="409" valign="top" style="padding:28px 20px 0 40px;">
              <div style="font-size:34px;line-height:38px;font-weight:700;">{title}</div>
              <div style="font-size:18px;line-height:19px;margin-top:12px;">Cliente:</div>
              <div style="font-size:30px;line-height:33px;font-weight:700;">CLARO</div>
              <div style="font-size:18px;line-height:19px;margin-top:32px;">Pedido:</div>
              <div style="font-size:30px;line-height:33px;font-weight:700;">{ordem}</div>
              <table width="320" cellspacing="0" cellpadding="0" style="margin-top:28px;border-collapse:collapse;">
                <tr>
                  <td width="72" style="font-size:19px;">NF-e:</td>
                  <td width="104" style="font-size:25px;font-weight:700;">{nf}</td>
                  <td width="60" style="font-size:19px;">VOL:</td>
                  <td width="84" style="font-size:25px;font-weight:700;">{volume}</td>
                </tr>
              </table>
              <div style="font-size:18px;line-height:19px;margin-top:32px;">Protocolo:</div>
              <div style="font-size:25px;line-height:27px;font-weight:700;">{protocolo}</div>
              <div style="font-size:18px;line-height:19px;margin-top:32px;">A/C:</div>
              <div style="font-size:25px;line-height:27px;font-weight:700;">{req}</div>
            </td>
          </tr>
        </table>
        """

    @staticmethod
    def _is_claro_invoice(inv: NormalizedInvoice) -> bool:
        text = f"{inv.cliente_nome or ''} {inv.cliente_cnpj_cpf or ''}".upper()
        digits = re.sub(r"\D", "", inv.cliente_cnpj_cpf or "")
        return "CLARO" in text or "TELMEX" in text or digits.startswith("40432548")

    @classmethod
    def _client_display(cls, inv: NormalizedInvoice) -> str:
        name = cls._sanitize(inv.cliente_nome).upper()
        if "CLARO" in name:
            return "CLARO"
        if "GSK" in name or "GLAXOSMITHKLINE" in name:
            return "GSK"
        remove_words = {
            "LTDA", "S/A", "SA", "S.A.", "EIRELI", "ME", "EPP", "DO", "DA", "DE", "DOS", "DAS",
            "INDUSTRIA", "COMERCIO", "BRASIL", "TECNICAS", "TECNICA",
        }
        words = [w for w in re.split(r"\s+", name) if w and w not in remove_words]
        return " ".join(words[:2])[:24] or name[:24]

    @classmethod
    def _order_number(cls, inv: NormalizedInvoice) -> str:
        val = inv.numero_ordem or inv.oc
        if not val:
            return "XXXXX"
        return cls._sanitize(val)[:24]

    @classmethod
    def _requester(cls, inv: NormalizedInvoice) -> str:
        req = cls._sanitize(inv.requisitante or "")
        if not req:
            return ""
        upper = req.upper()
        if upper.startswith("A/C") or upper.startswith("AC/"):
            return req[:32]
        return f"AC/ {req}"[:32]

    @staticmethod
    def _format_volume(vol: int, total_vols: int) -> str:
        return f"{vol:02d}/{total_vols:02d}"

    @staticmethod
    def _format_nf(numero_nf: str) -> str:
        raw = str(numero_nf or "").strip()
        if not re.fullmatch(r"\d+", raw):
            return raw
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return raw
        return f"{int(digits):,}".replace(",", ".")

    @classmethod
    def _barcode_value(cls, inv: NormalizedInvoice) -> str:
        return cls._sanitize(inv.chave_nfe or inv.numero_nf or ".")[:70] or "."
