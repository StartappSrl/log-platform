"""
Genera una scheda PDF con l'inventario completo (hardware, sistema
operativo, rete, software installato) di un singolo endpoint - pensata
per essere condivisa con il cliente, non solo consultata a video.
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


def _fmt_gb(value) -> str:
    if value is None:
        return "-"
    return f"{value:.1f} GB"


def build_inventory_pdf(inventory: dict, tenant: str, hostname: str) -> bytes:
    """inventory: il dizionario completo restituito da
    get_full_inventory_for_host(). Ritorna i byte del PDF."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitoloScheda", parent=styles["Title"], fontSize=18, spaceAfter=6)
    subtitle_style = ParagraphStyle("Sottotitolo", parent=styles["Normal"], fontSize=10,
                                     textColor=colors.grey, spaceAfter=16)
    heading_style = ParagraphStyle("Sezione", parent=styles["Heading2"], fontSize=13,
                                    spaceBefore=16, spaceAfter=8)

    story = []

    story.append(Paragraph(f"Scheda inventario — {hostname}", title_style))
    generato_il = datetime.now().strftime("%d/%m/%Y %H:%M")
    rilevato_il = inventory.get("collected_at")
    rilevato_str = ""
    if rilevato_il:
        try:
            rilevato_str = f" — dati rilevati il {datetime.fromtimestamp(rilevato_il).strftime('%d/%m/%Y %H:%M')}"
        except (TypeError, ValueError, OSError):
            pass
    story.append(Paragraph(f"Cliente: {tenant} — scheda generata il {generato_il}{rilevato_str}", subtitle_style))

    if not inventory:
        story.append(Paragraph(
            "Nessun dato di inventario trovato per questo host nel periodo considerato.",
            styles["Normal"]))
        doc.build(story)
        return buf.getvalue()

    # --- Sistema ---
    os_info = inventory.get("os", {}) or {}
    cpu_info = inventory.get("cpu", {}) or {}
    mem_info = inventory.get("memory", {}) or {}

    story.append(Paragraph("Sistema", heading_style))
    sistema_data = [
        ["Sistema operativo", os_info.get("name", "-")],
        ["CPU", f"{cpu_info.get('model', '-')} ({cpu_info.get('cores', '-')} core)"],
        ["Memoria RAM", f"{mem_info.get('total_mb', 0) / 1024:.1f} GB" if mem_info.get("total_mb") else "-"],
    ]
    t = Table(sistema_data, colWidths=[5 * cm, 11 * cm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)

    # --- Dischi ---
    disks = [d for d in (inventory.get("disks") or []) if d.get("total_gb", 0) > 0]
    if disks:
        story.append(Paragraph("Dischi", heading_style))
        disk_rows = [["Punto di montaggio", "Dispositivo", "Totale", "Usato"]]
        for d in disks:
            disk_rows.append([d.get("mount", "-"), d.get("device", "-"),
                               _fmt_gb(d.get("total_gb")), _fmt_gb(d.get("used_gb"))])
        t = Table(disk_rows, colWidths=[5 * cm, 5 * cm, 3 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ]))
        story.append(t)

    # --- Rete ---
    network = inventory.get("network") or []
    if network:
        story.append(Paragraph("Interfacce di rete", heading_style))
        net_rows = [["Nome", "Indirizzo MAC / IP"]]
        for n in network:
            addr = n.get("mac") or n.get("ip") or "-"
            net_rows.append([n.get("name", "-"), addr])
        t = Table(net_rows, colWidths=[5 * cm, 11 * cm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ]))
        story.append(t)

    # --- Software ---
    software = inventory.get("software") or []
    story.append(PageBreak())
    story.append(Paragraph(f"Software installato ({len(software)} pacchetti)", heading_style))
    if software:
        sw_rows = [["Pacchetto", "Versione"]]
        for pkg in sorted(software, key=lambda p: p.get("name", "").lower()):
            sw_rows.append([pkg.get("name", "-"), pkg.get("version", "-")])
        t = Table(sw_rows, colWidths=[10 * cm, 6 * cm], repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("Nessun elenco software disponibile.", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()
