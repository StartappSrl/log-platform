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

    # Aggiornamenti mancanti: viene dall'inventario (non dai log), un
    # fallimento qui non deve far fallire tutto il resto del report.
    pending_updates_by_host = {}
    try:
        hosts_inventory = gl.get_latest_inventory_per_host(stream_id)
        for inv in hosts_inventory:
            updates = inv.get("pending_updates") or []
            if updates:
                pending_updates_by_host[inv["hostname"]] = updates
    except Exception:
        pass

    return {
        "tenant": tenant_name,
        "display_name": display_name,
        "devices": devices,
        "total_messages": total_messages,
        "pending_updates_by_host": pending_updates_by_host,
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


def build_nightly_report_html(tenant_reports: list, generated_at: datetime | None = None,
                                platform_health: dict | None = None,
                                expiring_certs: list | None = None) -> str:
    """Costruisce l'HTML completo del report - un blocco per cliente, con
    tabella host e un riferimento all'immagine del grafico (incorporata
    separatamente via Content-ID, vedi email_sender.py). Se fornito,
    include anche un riepilogo dello stato della piattaforma stessa e i
    certificati agent in scadenza."""
    generated_at = generated_at or datetime.now(timezone.utc)
    data_str = generated_at.strftime("%d/%m/%Y")

    certs_html = ""
    if expiring_certs:
        cert_rows = "".join(
            f"<tr><td style='padding:3px 8px'>{c['common_name']}</td>"
            f"<td style='padding:3px 8px;text-align:right'>"
            f"{'SCADUTO' if c['days_remaining'] < 0 else str(c['days_remaining']) + ' giorni'}</td></tr>"
            for c in expiring_certs
        )
        certs_html = f"""
        <div style="background:#fee2e2;color:#991b1b;padding:10px 14px;border-radius:6px;margin-bottom:16px">
          <b>Certificati agent in scadenza (o già scaduti) - vanno rigenerati:</b>
          <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:6px">
            {cert_rows}
          </table>
        </div>
        """

    health_html = ""
    if platform_health:
        gl_ok = platform_health["graylog"]["ok"]
        db_ok = platform_health["mariadb"]["ok"]
        if gl_ok and db_ok:
            health_html = (
                '<p style="color:#16a34a;font-size:13px;margin:0 0 16px">'
                '✓ Graylog e MariaDB rispondono correttamente.</p>'
            )
        else:
            problems = []
            if not gl_ok:
                problems.append(f"Graylog: {platform_health['graylog']['error']}")
            if not db_ok:
                problems.append(f"MariaDB: {platform_health['mariadb']['error']}")
            health_html = (
                '<div style="background:#fee2e2;color:#991b1b;padding:10px 14px;'
                'border-radius:6px;margin-bottom:16px;font-size:13px">'
                '<b>Attenzione, problemi rilevati sulla piattaforma:</b><br>'
                + "<br>".join(problems) + "</div>"
            )


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

        updates_html = ""
        if r.get("pending_updates_by_host"):
            update_rows = "".join(
                f"<tr><td style='padding:3px 8px'>{host}</td>"
                f"<td style='padding:3px 8px'>{len(updates)} aggiornamenti in sospeso</td></tr>"
                for host, updates in r["pending_updates_by_host"].items()
            )
            updates_html = f"""
              <table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:8px;background:#fffbeb">
                <tr><td colspan="2" style="padding:4px 8px;font-weight:bold;color:#92400e">Aggiornamenti mancanti</td></tr>
                {update_rows}
              </table>
            """

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
              {updates_html}
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
      {health_html}
      {certs_html}
      {body}
      <p style="color:#94a3b8;font-size:11px;margin-top:20px">Generato automaticamente da Log Platform.</p>
    </body></html>
    """
