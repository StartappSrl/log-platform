"""
Genera il report notturno riepilogativo (statistiche log per cliente,
grafico a ciambella per host, invio email) - vedi email_sender.py per la
parte di invio SMTP vera e propria.
"""
import io
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")  # nessun display, solo generazione file - necessario in un server senza schermo
import matplotlib.pyplot as plt

from . import graylog_client as gl


def build_tenant_report_data(tenant_name: str, display_name: str, stream_id: str) -> dict:
    """Raccoglie le statistiche di un tenant per il report - riusa la
    stessa aggregazione gia' costruita per la Dashboard dispositivi,
    cosi' i numeri restano coerenti tra pannello e report."""
    try:
        data = gl.get_device_dashboard_for_tenant(stream_id)
    except gl.GraylogError:
        data = {"devices": [], "total_messages_today": 0}

    devices = data["devices"]
    total_messages = data["total_messages_today"]

    return {
        "tenant": tenant_name,
        "display_name": display_name,
        "devices": devices,
        "total_messages": total_messages,
    }


def render_donut_chart_png(devices: list) -> bytes:
    """Grafico a ciambella con la % di log per host - ritorna i byte PNG
    pronti per essere allegati/incorporati nell'email."""
    if not devices:
        fig, ax = plt.subplots(figsize=(3, 3))
        ax.text(0.5, 0.5, "Nessun dato", ha="center", va="center", fontsize=11, color="#94a3b8")
        ax.axis("off")
    else:
        labels = [d["hostname"] for d in devices]
        values = [max(d["total"], 0) for d in devices]
        if sum(values) == 0:
            values = [1] * len(values)  # evita un grafico completamente vuoto se tutti i conteggi sono 0

        fig, ax = plt.subplots(figsize=(4, 4))
        colors = plt.cm.tab20.colors
        ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90,
               colors=colors[:len(values)], wedgeprops={"width": 0.4})
        ax.axis("equal")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", transparent=False,
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def build_nightly_report_html(tenant_reports: list, generated_at: datetime | None = None) -> str:
    """Costruisce l'HTML completo del report - un blocco per cliente, con
    tabella host e un riferimento all'immagine del grafico (incorporata
    separatamente via Content-ID, vedi email_sender.py)."""
    generated_at = generated_at or datetime.now(timezone.utc)
    data_str = generated_at.strftime("%d/%m/%Y")

    blocks = []
    for i, r in enumerate(tenant_reports):
        rows = "".join(
            f"<tr><td style='padding:4px 8px;border-bottom:1px solid #e2e8f0'>{d['hostname']}</td>"
            f"<td style='padding:4px 8px;border-bottom:1px solid #e2e8f0;text-align:right'>{d['total']}</td>"
            f"<td style='padding:4px 8px;border-bottom:1px solid #e2e8f0;text-align:right;color:#dc2626'>{d['errors']}</td>"
            f"<td style='padding:4px 8px;border-bottom:1px solid #e2e8f0;text-align:right;color:#d97706'>{d['warnings']}</td></tr>"
            for d in r["devices"]
        )
        if not rows:
            rows = "<tr><td colspan='4' style='padding:8px;color:#94a3b8'>Nessun dispositivo ha inviato log</td></tr>"

        blocks.append(f"""
        <table style="width:100%;border-collapse:collapse;margin-bottom:8px">
          <tr>
            <td style="width:140px;vertical-align:top">
              <img src="cid:chart{i}" width="130" height="130" alt="grafico log per host">
            </td>
            <td style="vertical-align:top;padding-left:16px">
              <h2 style="margin:0 0 8px;font-size:16px;color:#1e293b">{r['display_name']}</h2>
              <p style="margin:0 0 8px;color:#64748b;font-size:13px">
                Log ricevuti oggi: <b>{r['total_messages']}</b> — {len(r['devices'])} dispositivi
              </p>
              <table style="width:100%;border-collapse:collapse;font-size:13px">
                <tr style="color:#64748b;text-align:left">
                  <th style="padding:4px 8px">Host</th><th style="text-align:right;padding:4px 8px">Totale</th>
                  <th style="text-align:right;padding:4px 8px">Errori</th><th style="text-align:right;padding:4px 8px">Avvisi</th>
                </tr>
                {rows}
              </table>
            </td>
          </tr>
        </table>
        """)

    body = "".join(
        f'<div style="border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:16px;background:#ffffff">{b}</div>'
        for b in blocks
    )

    return f"""
    <html><body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;background:#f8fafc;margin:0;padding:20px">
      <h1 style="font-size:18px;color:#1e293b">Report log — {data_str}</h1>
      {body}
      <p style="color:#94a3b8;font-size:11px;margin-top:20px">Generato automaticamente da Log Platform.</p>
    </body></html>
    """
