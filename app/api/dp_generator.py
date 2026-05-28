import re
from typing import List, Optional

from app.database.models import NormalizedInvoice
from app.api.zpl_generator import ZPLGenerator


class DPGenerator:
    """Gera Direct Protocol (DP) para etiquetas logisticas na Honeywell PC42t 203 DPI."""

    @classmethod
    def generate(cls, invoice: NormalizedInvoice) -> List[str]:
        """Gera uma string DP para cada volume da nota fiscal."""
        total_vols = invoice.quantidade_volumes or 1
        dp_labels = []

        for vol in range(1, total_vols + 1):
            if invoice.template_name in {"claro", "claro_dividida"}:
                dp = cls._generate_claro_dividida(invoice, vol, total_vols)
            else:
                dp = cls._generate_standard(invoice, vol, total_vols)
            dp_labels.append(dp)

        return dp_labels

    @classmethod
    def generate_batch(cls, invoices: List[NormalizedInvoice]) -> List[str]:
        """Gera DP para uma lista de NF-es, agrupando Claro em etiqueta dividida."""
        labels: List[str] = []
        claro_buffer: List[tuple[NormalizedInvoice, int, int]] = []

        def flush_claro() -> None:
            while claro_buffer:
                left_item = claro_buffer.pop(0)
                right_item = claro_buffer.pop(0) if claro_buffer else None
                
                left_inv, left_vol, left_tot = left_item
                if right_item:
                    right_inv, right_vol, right_tot = right_item
                else:
                    right_inv, right_vol, right_tot = None, 1, 1
                
                labels.append(cls._generate_claro_pair_from_items(left_inv, left_vol, left_tot, right_inv, right_vol, right_tot))

        for invoice in invoices:
            if invoice.template_name in {"claro", "claro_dividida"}:
                total_vols = invoice.quantidade_volumes or 1
                for vol in range(1, total_vols + 1):
                    claro_buffer.append((invoice, vol, total_vols))
                while len(claro_buffer) >= 2:
                    left_item = claro_buffer.pop(0)
                    right_item = claro_buffer.pop(0)
                    left_inv, left_vol, left_tot = left_item
                    right_inv, right_vol, right_tot = right_item
                    labels.append(cls._generate_claro_pair_from_items(left_inv, left_vol, left_tot, right_inv, right_vol, right_tot))
            else:
                flush_claro()
                labels.extend(cls.generate(invoice))

        flush_claro()
        return labels

    @staticmethod
    def _header() -> List[str]:
        return [
            "",
            "CLL",
            "OPTIMIZE \"BATCH\" ON",
            "ALIGN 1",
            "DIR 1",
        ]

    @staticmethod
    def _footer() -> List[str]:
        return [
            "PF",
            "",
        ]

    @classmethod
    def generate_barcode_test_label(cls, barcode: Optional[str] = None) -> str:
        barcode_value = ZPLGenerator._sanitize(
            barcode or "33260501278897000182550010000503771000000000"
        )[:44]
        if not barcode_value:
            barcode_value = "000000000000"

        dp = cls._header() + [
            "FONT \"Swiss 721 BT\",12,0",
            "PP 30,520",
            "PT \"TESTE BARCODE CODE128\"",
            "BARSET \"CODE128\",1,1,80",
            "PP 120,300",
            f"BARPRT \"{barcode_value}\"",
        ]
        dp += cls._footer()
        return "\n".join(dp)

    @classmethod
    def _generate_standard(cls, inv: NormalizedInvoice, vol: int, total_vols: int) -> str:
        # Reutiliza lógica de tratamento de dados sanitizados do ZPLGenerator
        cliente = ZPLGenerator._client_display(inv)
        ordem = ZPLGenerator._order_number(inv)
        nf = ZPLGenerator._format_nf(inv.numero_nf)
        volume = ZPLGenerator._format_volume(vol, total_vols)
        uf = ZPLGenerator._sanitize(inv.cliente_uf)[:2]
        if not uf:
            uf = "XX"
        req = ZPLGenerator._requester(inv)
        label_note = ZPLGenerator._sanitize(inv.label_note or "")[:80]
        barcode = ZPLGenerator._sanitize(inv.chave_nfe or inv.numero_nf or "")
        if not barcode:
            barcode = "XXXXXXXX"
        else:
            barcode = barcode[:44]

        dp = cls._header() + [
            # ===== LOGO PLATINUM TI =====
            "FONT \"Swiss 721 BT\",18,0",
            "PP 30,490",
            "PT \"PLATINUM TI\"",
            "",
            # ===== CLIENTE =====
            "FONT \"Swiss 721 BT\",9,0",
            "PP 360,510",
            "PT \"Cliente:\"",
            "FONT \"Swiss 721 BT\",12,0",
        ]
        cliente_palavras = [p for p in cliente.split() if p]
        if len(cliente_palavras) >= 2:
            dp += [
                "PP 360,480",
                f"PT \"{cliente_palavras[0]}\"",
                "PP 360,450",
                f"PT \"{cliente_palavras[1]}\"",
            ]
        elif cliente_palavras:
            dp += [
                "PP 360,480",
                f"PT \"{cliente_palavras[0]}\"",
            ]
        else:
            dp += [
                "PP 360,480",
                "PT \"\"",
            ]
        dp += [
            "",
            # ===== ENTREGA =====
            "FONT \"Swiss 721 BT\",9,0",
            "PP 645,510",
            "PT \"Entrega\"",
            "PP 645,480",
            "PT \"0- EMIT\"",
            "PP 645,455",
            "PT \"1- DEST\"",
            # Quadrado da entrega
            "PP 755,495",
            "BARLINE 55,2",
            "PP 755,435",
            "BARLINE 55,2",
            "PP 755,435",
            "BARLINE 2,60",
            "PP 810,435",
            "BARLINE 2,60",
            # Numero 0 do emitente
            "FONT \"Swiss 721 BT\",16,0",
            "PP 773,482",
            "PT \"0\"",
            "",
            # ===== LINHA SEPARADORA 1 =====
            "PP 30,415",
            "BARLINE 780,3",
            "",
            # ===== CABECALHO DADOS =====
            "FONT \"Swiss 721 BT\",11,0",
            "PP 30,395",
            "PT \"PEDIDO\"",
            "FONT \"Swiss 721 BT\",16,0",
            "PP 30,350",
            f"PT \"{ordem}\"",
            "",
            "FONT \"Swiss 721 BT\",11,0",
            "PP 320,395",
            "PT \"NF-e\"",
            "FONT \"Swiss 721 BT\",18,0",
            "PP 320,350",
            f"PT \"{nf}\"",
            "",
            "FONT \"Swiss 721 BT\",11,0",
            "PP 535,395",
            "PT \"VOL\"",
            "FONT \"Swiss 721 BT\",18,0",
            "PP 535,350",
            f"PT \"{volume}\"",
            "",
            "FONT \"Swiss 721 BT\",11,0",
            "PP 680,395",
            "PT \"UF\"",
            "FONT \"Swiss 721 BT\",18,0",
            "PP 680,350",
            f"PT \"{uf}\"",
            "",
            # ===== LINHA SEPARADORA 2 =====
            "PP 30,310",
            "BARLINE 780,2",
            "",
            # ===== REQUISITANTE =====
            "FONT \"Swiss 721 BT\",10,0",
            "PP 30,285",
            "PT \"REQUISITANTE:\"",
            "FONT \"Swiss 721 BT\",14,0",
            "PP 270,285",
            f"PT \"{req}\"",
            "",
        ]
        if label_note:
            dp += [
                # ===== OBSERVACAO DO MODELO =====
                "FONT \"Swiss 721 BT\",8,0",
                "PP 30,255",
                f"PT \"OBS: {label_note}\"",
                "",
                # ===== CODIGO DE BARRAS =====
                "BARSET \"CODE128\",1,1,55",
                "PP 190,225",
                f"BARPRT \"{barcode}\"",
                "",
            ]
        else:
            dp += [
                # ===== CODIGO DE BARRAS =====
                "BARSET \"CODE128\",1,1,80",
                "PP 190,240",
                f"BARPRT \"{barcode}\"",
                "",
            ]
        dp += [
            # ===== LINHA SEPARADORA 3 =====
            "PP 30,200",
            "BARLINE 780,2",
            "",
            # ===== OBS CENTRALIZADA =====
            "ALIGN 2",
            "FONT \"Swiss 721 BT\",7,0",
            "PP 440,95",
            "PT \"FAVOR CONFERIR O MATERIAL NO ATO DO RECEBIMENTO.\"",
            "PP 440,70",
            "PT \"NAO ACEITAREMOS RECLAMACOES OU DEVOLUCOES POSTERIORES.\"",
            "",
            # ===== RODAPE CENTRALIZADO =====
            "FONT \"Swiss 721 BT\",7,0",
            "PP 425,45",
            "PT \"R. FRANCISCO EUGENIO 268 SALA 636, SAO CRISTOVAO RJ - TEL:21 3878-8855\"",
            "ALIGN 1",
        ]
        dp += cls._footer()
        return "\n".join(dp)

    @classmethod
    def _generate_claro_dividida(cls, inv: NormalizedInvoice, vol: int, total_vols: int) -> str:
        return cls._generate_claro_pair_from_items(inv, vol, total_vols, None, 1, 1)

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
        dp = cls._header()
        dp += cls._claro_half(left, x=0, vol=left_vol, total_vols=left_total)
        
        # Divisoria tracejada central em DP
        dp += ["", "# ===== LINHA DIVISORIA CENTRAL ====="]
        for y_offset in range(50, 490, 30):
            dp += [
                f"PP 438,{560 - y_offset}",
                "BARLINE 1,4",
            ]
            
        if right:
            dp += cls._claro_half(right, x=430, vol=right_vol, total_vols=right_total)
        dp += cls._footer()
        return "\n".join(dp)

    @classmethod
    def _claro_half(cls, inv: NormalizedInvoice, x: int, vol: int, total_vols: int) -> List[str]:
        ordem = ZPLGenerator._order_number(inv)
        nf = ZPLGenerator._format_nf(inv.numero_nf)
        protocolo = ZPLGenerator._sanitize(inv.protocolo or "")
        if not protocolo:
            protocolo = "XXXXXXXX"
        volume = f"{vol:02d}" if total_vols <= 1 else ZPLGenerator._format_volume(vol, total_vols)
        req = ZPLGenerator._requester(inv)
        barcode = ZPLGenerator._sanitize(inv.chave_nfe or inv.numero_nf or "")
        if not barcode:
            barcode = "XXXXXXXX"
        else:
            barcode = barcode[:44]

        is_right = x >= 430
        text_x = x + (75 if is_right else 95)
        nf_value_x = x + (155 if is_right else 175)
        vol_label_x = x + (255 if is_right else 290)
        vol_value_x = x + (315 if is_right else 355)

        return [
            "",
            # ===== BARCODE VERTICAL =====
            "DIR 4",
            "BARSET \"CODE128\",1,1,32",
            f"PP {x + 8},535",
            f"BARPRT \"{barcode}\"",
            "DIR 1",
            "",
            # ===== LOGO E CLIENTE =====
            "FONT \"Swiss 721 BT\",16,0",
            f"PP {text_x},455",
            "PT \"PLATINUM TI\"",
            "FONT \"Swiss 721 BT\",9,0",
            f"PP {text_x},420",
            "PT \"Cliente:\"",
            "FONT \"Swiss 721 BT\",14,0",
            f"PP {text_x},395",
            "PT \"CLARO\"",
            "",
            # ===== DADOS PRINCIPAIS =====
            "FONT \"Swiss 721 BT\",10,0",
            f"PP {text_x},340",
            "PT \"PEDIDO:\"",
            "FONT \"Swiss 721 BT\",14,0",
            f"PP {text_x},310",
            f"PT \"{ordem}\"",
            "",
            "FONT \"Swiss 721 BT\",10,0",
            f"PP {text_x},260",
            "PT \"NF-e:\"",
            "FONT \"Swiss 721 BT\",12,0",
            f"PP {nf_value_x},260",
            f"PT \"{nf}\"",
            "",
            "FONT \"Swiss 721 BT\",10,0",
            f"PP {vol_label_x},260",
            "PT \"VOL:\"",
            "FONT \"Swiss 721 BT\",12,0",
            f"PP {vol_value_x},260",
            f"PT \"{volume}\"",
            "",
            # ===== PROTOCOLO E A/C =====
            "FONT \"Swiss 721 BT\",10,0",
            f"PP {text_x},205",
            "PT \"PROTOCOLO:\"",
            "FONT \"Swiss 721 BT\",12,0",
            f"PP {text_x},175",
            f"PT \"{protocolo}\"",
            "",
            "FONT \"Swiss 721 BT\",10,0",
            f"PP {text_x},125",
            "PT \"A/C:\"",
            "FONT \"Swiss 721 BT\",12,0",
            f"PP {text_x},95",
            f"PT \"{req}\"",
        ]
