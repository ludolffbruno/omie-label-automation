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
        claro_buffer: List[NormalizedInvoice] = []

        def flush_claro() -> None:
            while claro_buffer:
                left = claro_buffer.pop(0)
                right = claro_buffer.pop(0) if claro_buffer else None
                labels.append(cls._generate_claro_pair(left, right))

        for invoice in invoices:
            if invoice.template_name in {"claro", "claro_dividida"}:
                claro_buffer.append(invoice)
                if len(claro_buffer) == 2:
                    flush_claro()
            else:
                flush_claro()
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
            "^FO65,150^A0N,32,30^FDNº ORDEM^FS",
            "^FO390,150^A0N,32,30^FDNF-e^FS",
            "^FO590,150^A0N,32,30^FDVOL^FS",
            "^FO740,150^A0N,32,30^FDUF^FS",
            f"^FO65,194^A0N,31,28^FD{ordem}^FS",
            f"^FO390,190^A0N,38,34^FD{nf}^FS",
            f"^FO590,190^A0N,38,34^FD{volume}^FS",
            f"^FO740,190^A0N,38,34^FD{uf}^FS",
            "^FO65,285^A0N,32,30^FDREQUISITANTE^FS",
            f"^FO85,330^A0N,28,24^FD{req}^FS",
            "^FO65,405^GB750,38,1^FS",
            f"^FO75,414^A0N,18,16^FD{barcode}^FS",
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
        left_total = left_total or left.quantidade_volumes or 1
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
            zpl += cls._claro_half(right, x=464, vol=1, total_vols=right.quantidade_volumes or 1)
        zpl += cls._footer()
        return "\n".join(zpl)

    @classmethod
    def _claro_half(cls, inv: NormalizedInvoice, x: int, vol: int, total_vols: int) -> List[str]:
        ordem = cls._order_number(inv)
        nf = cls._format_nf(inv.numero_nf)
        protocolo = cls._sanitize(inv.protocolo or inv.oc or "")
        volume = f"{vol:02d}" if total_vols <= 1 else cls._format_volume(vol, total_vols)
        req = cls._requester(inv)

        return [
            f"^FO{x + 78},25^A0N,56,52^FDP^FS",
            f"^FO{x + 16},92^A0N,32,30^FDPLATINUM^FS",
            f"^FO{x + 245},45^A0N,38,34^FDCLARO^FS",
            f"^FO{x},160^A0N,31,29^FDNº ORDEM^FS",
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
        return f"""
        <div style="width:880px;height:560px;background:#fff;color:#000;
                    font-family:Arial,sans-serif;position:relative;border:1px solid #999;">
          <div style="position:absolute;left:50px;top:38px;font-size:32px;">PLATINUM</div>
          <div style="position:absolute;left:470px;top:25px;font-size:24px;">Cliente</div>
          <div style="position:absolute;left:420px;top:60px;font-size:30px;font-weight:700;">{cliente}</div>
          <div style="position:absolute;left:700px;top:25px;font-size:26px;">Entrega</div>
          <div style="position:absolute;left:805px;top:58px;width:42px;height:62px;border:2px solid #000;
                      font-size:34px;text-align:center;line-height:62px;">0</div>
          <div style="position:absolute;left:65px;top:150px;font-size:30px;">Nº ORDEM</div>
          <div style="position:absolute;left:390px;top:150px;font-size:30px;">NF-e</div>
          <div style="position:absolute;left:590px;top:150px;font-size:30px;">VOL</div>
          <div style="position:absolute;left:740px;top:150px;font-size:30px;">UF</div>
          <div style="position:absolute;left:65px;top:190px;font-size:32px;font-weight:700;">{ordem}</div>
          <div style="position:absolute;left:390px;top:188px;font-size:36px;font-weight:700;">{nf}</div>
          <div style="position:absolute;left:590px;top:188px;font-size:36px;font-weight:700;">{volume}</div>
          <div style="position:absolute;left:740px;top:188px;font-size:36px;font-weight:700;">{uf}</div>
          <div style="position:absolute;left:65px;top:285px;font-size:30px;">REQUISITANTE</div>
          <div style="position:absolute;left:85px;top:330px;font-size:26px;font-weight:700;">{req}</div>
          <div style="position:absolute;left:65px;top:405px;width:750px;height:38px;border:1px solid #000;text-align:center;">.</div>
          <div style="position:absolute;left:185px;top:468px;font-size:14px;">FAVOR CONFERIR O MATERIAL NO ATO DO RECEBIMENTO,</div>
          <div style="position:absolute;left:165px;top:487px;font-size:14px;">NAO ACEITAREMOS RECLAMACOES OU DEVOLUCOES POSTERIORES.</div>
          <div style="position:absolute;left:60px;top:522px;font-size:20px;font-weight:700;">
            R. FRANCISCO EUGENIO 268 SALA 636, SAO CRISTOVAO RJ - TEL:21 3878-8855
          </div>
        </div>
        """

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
        return cls._sanitize(
            inv.numero_ordem
            or inv.oc
            or inv.pedido_cliente
            or inv.pedido_venda
            or inv.numero_nf
        )[:24]

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
