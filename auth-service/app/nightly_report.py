"""
Report notturno per l'AMMINISTRATORE: una mail HTML con la sintesi della piattaforma e, per
ciascun cliente, i suoi dispositivi (stato, ultimo log, errori, avvisi, aggiornamenti in
sospeso, disco). Stesso stile delle altre mail di Log Platform.

Cosa e' cambiato rispetto alla versione precedente:
  - sintesi in cima (tutto regolare / punti da controllare / problemi) e riquadri con i totali
  - UNA tabella dispositivi per cliente: gli aggiornamenti mancanti stanno accanto al
    dispositivo, e i dispositivi noti dall'inventario ma senza log oggi compaiono (prima
    comparivano solo sotto "aggiornamenti mancanti", come se fossero normali)
  - il grafico a ciambella solo quando ha senso (almeno due dispositivi con log), senza
    etichette sovrapposte; i colori coincidono con i puntini nella tabella
  - logo, carattere uniforme, plurali corretti, dichiarazione della codifica nell'HTML

Compatibilita': le funzioni build_tenant_report_data, render_donut_chart_png e
build_nightly_report_html mantengono nome e parametri; si aggiunge chart_images().
"""
import html as _html
import io
import os
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")  # nessun display, solo generazione file - necessario in un server senza schermo
import matplotlib.pyplot as plt

from . import graylog_client as gl

NAVY, BLU = "#0F1B33", "#2E5A9C"
PALETTE = ["#2E5A9C", "#0F1B33", "#C41E3A", "#5B8DEF", "#E08A3C", "#2F9E8F", "#8B5CF6", "#64748B"]
GREY = "#CBD5E1"
TONI = {  # sfondo, testo
    "ok": ("#DCFCE7", "#166534"), "warn": ("#FEF3C7", "#92400E"),
    "fail": ("#FEE2E2", "#991B1B"), "neutral": ("#F1F5F9", "#475569"),
}
FONT = "Arial,Helvetica,sans-serif"
MAX_RIGHE = 25
# False (prudente): oltre ai dispositivi con log compaiono solo quelli dell'inventario CON aggiornamenti in
# sospeso (come nel report precedente, ma ora nella posizione giusta). True: compaiono TUTTI i dispositivi
# dell'inventario che oggi non hanno inviato log; da usare solo dopo aver verificato che l'inventario non
# ricordi per sempre i PC dismessi (altrimenti restano nel report e tengono giallo il riquadro in cima).
INCLUDI_HOST_SENZA_AGGIORNAMENTI = False


# ----------------------------------------------------------------------
# Dati
# ----------------------------------------------------------------------
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

    # Aggiornamenti mancanti e dispositivi noti: vengono dall'inventario (non dai log), un
    # fallimento qui non deve far fallire tutto il resto del report.
    pending_updates_by_host = {}
    inventory_hosts = []
    try:
        for inv in gl.get_latest_inventory_per_host(stream_id):
            if inv.get("hostname"):
                inventory_hosts.append(inv["hostname"])
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
        "inventory_hosts": inventory_hosts,
    }


# ----------------------------------------------------------------------
# Grafico
# ----------------------------------------------------------------------
def _ranked(devices):
    return sorted((d for d in devices if (d.get("total") or 0) > 0), key=lambda d: d["total"], reverse=True)


def _device_colors(devices):
    """Un colore per i dispositivi con piu' log: lo stesso nel grafico e nel puntino della tabella."""
    return {d["hostname"]: (PALETTE[i] if i < len(PALETTE) else GREY) for i, d in enumerate(_ranked(devices))}


def needs_chart(devices) -> bool:
    """Una ciambella con un solo dispositivo e' un cerchio pieno senza informazione: serve da 2 in su."""
    return len(_ranked(devices)) >= 2


def render_donut_chart_png(devices: list) -> bytes:
    """Ciambella con la quota di log per dispositivo (senza etichette: la legenda e' la tabella,
    con i puntini colorati) e il totale al centro. Ritorna i byte PNG."""
    ranked = _ranked(devices)
    fig, ax = plt.subplots(figsize=(2.4, 2.4))
    if not ranked:
        ax.text(0.5, 0.5, "Nessun dato", ha="center", va="center", fontsize=12, color="#94A3B8")
        ax.axis("off")
    else:
        top, resto = ranked[:len(PALETTE)], ranked[len(PALETTE):]
        values = [d["total"] for d in top] + ([sum(d["total"] for d in resto)] if resto else [])
        colors = PALETTE[:len(top)] + ([GREY] if resto else [])
        ax.pie(values, colors=colors, startangle=90, counterclock=False,
               wedgeprops={"width": 0.38, "edgecolor": "white", "linewidth": 2})
        ax.text(0, 0.08, f"{sum(values):,}".replace(",", "."), ha="center", va="center",
                fontsize=17, fontweight="bold", color=NAVY)
        ax.text(0, -0.2, "messaggi", ha="center", va="center", fontsize=9, color="#64748B")
        ax.axis("equal")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def chart_images(tenant_reports: list) -> dict:
    """Immagini da incorporare nella mail: solo per i clienti il cui grafico e' davvero mostrato."""
    return {f"chart{i}": render_donut_chart_png(r["devices"])
            for i, r in enumerate(tenant_reports) if needs_chart(r["devices"])}


# ----------------------------------------------------------------------
# Utilita' di formato
# ----------------------------------------------------------------------
def _e(v):
    return _html.escape(str(v))


def _int(n):
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "–"


def _plural(n, uno, molti):
    return f"{n} {uno if n == 1 else molti}"


def _eta_seconds(last_seen, now):
    """Secondi trascorsi da last_seen (ISO oppure epoch); None se assente o illeggibile."""
    if last_seen in (None, ""):
        return None
    try:
        if isinstance(last_seen, (int, float)):
            ts = last_seen / 1000 if last_seen > 1e11 else last_seen
            dt = datetime.fromtimestamp(ts, timezone.utc)
        else:
            dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return max((now - dt).total_seconds(), 0)
    except (ValueError, OSError, OverflowError):
        return None


def _fmt_eta(seconds):
    if seconds is None:
        return "–"
    if seconds < 3600:
        m = max(1, round(seconds / 60))
        return "1 minuto fa" if m == 1 else f"{m} minuti fa"
    if seconds < 172800:
        h = round(seconds / 3600)
        return "1 ora fa" if h == 1 else f"{h} ore fa"
    d = round(seconds / 86400)
    return "1 giorno fa" if d == 1 else f"{d} giorni fa"


def _local_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Rome")
    except Exception:
        return timezone.utc


def _pill(testo, tono):
    bg, fg = TONI[tono]
    return (f'<span style="display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;'
            f'font-weight:bold;background:{bg};color:{fg};white-space:nowrap;">{_e(testo)}</span>')


def _num(v, colore):
    return f'<span style="color:{colore};font-weight:bold;">{_int(v)}</span>' if v else '<span style="color:#94A3B8;">0</span>'


# ----------------------------------------------------------------------
# Righe della tabella dispositivi di un cliente
# ----------------------------------------------------------------------
def _device_rows(r, now):
    """Dispositivi con log + dispositivi noti dall'inventario ma senza log. Ritorna lista di dict."""
    pending = r.get("pending_updates_by_host") or {}
    righe, visti = [], set()
    for d in r["devices"]:
        host = d.get("hostname") or "?"
        visti.add(host)
        online = d.get("online")
        righe.append({"host": host, "senza_log": False, "online": online,
                      "eta": _eta_seconds(d.get("last_seen"), now),
                      "total": d.get("total") or 0, "errors": d.get("errors") or 0,
                      "warnings": d.get("warnings") or 0,
                      "updates": len(pending.get(host) or []), "disk": d.get("disk_percent")})
    if INCLUDI_HOST_SENZA_AGGIORNAMENTI:
        for host in r.get("inventory_hosts") or []:
            if host not in visti:
                visti.add(host)
                righe.append({"host": host, "senza_log": True, "online": None, "eta": None, "total": None,
                              "errors": None, "warnings": None, "updates": len(pending.get(host) or []), "disk": None})
    for host, ups in pending.items():          # dispositivi con aggiornamenti in sospeso che oggi non hanno inviato log
        if host not in visti:
            righe.append({"host": host, "senza_log": True, "online": None, "eta": None, "total": None,
                          "errors": None, "warnings": None, "updates": len(ups), "disk": None})
    # prima chi ha un problema: senza log / offline, poi chi ha piu' errori, poi per volume
    righe.sort(key=lambda x: (0 if (x["senza_log"] or x["online"] is False) else 1,
                              -(x["errors"] or 0), -(x["total"] or 0), x["host"].lower()))
    return righe


def _tenant_card(i, r, now):
    righe = _device_rows(r, now)
    con_log = [d for d in r["devices"]]
    grafico = needs_chart(r["devices"])
    colori = _device_colors(r["devices"]) if grafico else {}
    ha_disco = any(x["disk"] is not None for x in righe)
    n_dev = len(con_log)
    senza = sum(1 for x in righe if x["senza_log"])
    offline = sum(1 for x in righe if x["online"] is False)
    err = sum((d.get("errors") or 0) for d in con_log)
    avv = sum((d.get("warnings") or 0) for d in con_log)

    if not righe:
        tono_bordo = "neutral"
    elif senza or offline:
        tono_bordo = "warn"
    else:
        tono_bordo = "ok"
    bordo = {"ok": "#16A34A", "warn": "#D97706", "neutral": "#CBD5E1"}[tono_bordo]

    testo_chips = _plural(n_dev, "dispositivo con log", "dispositivi con log")
    if senza:
        testo_chips += f" – {senza} senza log"
    chips = f'<span style="font-size:13px;color:#475569;">{_e(testo_chips)}</span>'

    kpi = (
        '<table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="padding-right:22px;"><div style="font-size:22px;font-weight:bold;color:{NAVY};">{_int(r["total_messages"])}</div>'
        f'<div style="font-size:12px;color:#64748B;">messaggi oggi</div></td>'
        f'<td style="padding-right:22px;"><div style="font-size:22px;font-weight:bold;color:{"#B91C1C" if err else "#94A3B8"};">{_int(err)}</div>'
        f'<div style="font-size:12px;color:#64748B;">errori</div></td>'
        f'<td style="padding-right:22px;"><div style="font-size:22px;font-weight:bold;color:{"#B45309" if avv else "#94A3B8"};">{_int(avv)}</div>'
        f'<div style="font-size:12px;color:#64748B;">avvisi</div></td>'
        '</tr></table>'
    )
    if grafico:
        corpo_alto = (f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                      f'<td valign="middle">{kpi}</td>'
                      f'<td width="150" align="right" valign="middle"><img src="cid:chart{i}" width="136" height="136" '
                      f'alt="Quota dei messaggi per dispositivo" style="display:block;border:0;"></td></tr></table>')
    else:
        corpo_alto = kpi

    if not righe:
        tabella = ('<div style="padding:14px 0 4px 0;font-size:13px;color:#64748B;">'
                   'Nessun dispositivo ha inviato log oggi.</div>')
    else:
        intest = ['Dispositivo', 'Stato', 'Ultimo log', 'Log', 'Errori', 'Avvisi', 'Aggiorn.']
        if ha_disco:
            intest.append('Disco')
        th = "".join(
            f'<th align="{"left" if j < 3 else "right"}" style="padding:6px 6px;font-size:11px;font-weight:normal;color:#64748B;'
            f'border-bottom:1px solid #E2E8F0;white-space:nowrap;">{t}</th>' for j, t in enumerate(intest))
        corpo = []
        for x in righe[:MAX_RIGHE]:
            dot = (f'<span style="display:inline-block;width:9px;height:9px;background:{colori.get(x["host"], GREY)};'
                   f'margin-right:7px;"></span>') if grafico and not x["senza_log"] else ""
            if x["senza_log"]:
                stato = _pill("Nessun log oggi", "warn")
            elif x["online"] is True:
                stato = _pill("Online", "ok")
            elif x["online"] is False:
                stato = _pill("Offline", "warn")
            else:
                stato = '<span style="color:#94A3B8;">–</span>'
            agg = (f'<span style="color:#B45309;font-weight:bold;">{x["updates"]}</span>'
                   if x["updates"] else '<span style="color:#94A3B8;">0</span>')
            if x["disk"] is None:
                disco = '<span style="color:#94A3B8;">–</span>'
            else:
                col = "#B91C1C" if x["disk"] >= 90 else "#B45309" if x["disk"] >= 75 else "#334155"
                disco = f'<span style="color:{col};font-weight:{"bold" if x["disk"] >= 75 else "normal"};">{_e(x["disk"])}%</span>'
            cel = lambda contenuto, al="right": (f'<td align="{al}" style="padding:8px 6px;border-bottom:1px solid #F1F5F9;'
                                                 f'font-size:12px;color:#334155;white-space:nowrap;">{contenuto}</td>')
            corpo.append(
                "<tr>"
                f'<td style="padding:8px 6px;border-bottom:1px solid #F1F5F9;font-size:13px;color:{NAVY};{"white-space:nowrap;" if len(x["host"]) <= 20 else ""}">{dot}{_e(x["host"])}</td>'
                + cel(stato, "left") + cel(_fmt_eta(x["eta"]), "left")
                + cel("–" if x["total"] is None else _int(x["total"]))
                + cel("–" if x["errors"] is None else _num(x["errors"], "#B91C1C"))
                + cel("–" if x["warnings"] is None else _num(x["warnings"], "#B45309"))
                + cel(agg) + (cel(disco) if ha_disco else "") + "</tr>")
        extra = len(righe) - MAX_RIGHE
        if extra > 0:
            corpo.append(f'<tr><td colspan="{len(intest)}" style="padding:8px 6px;font-size:12px;color:#64748B;">'
                         f'… e altri {extra} dispositivi</td></tr>')
        nota = ""
        if senza:
            testo_nota = _plural(senza, "dispositivo noto dall'inventario non ha inviato log",
                                 "dispositivi noti dall'inventario non hanno inviato log")
            nota = f'<div style="padding-top:8px;font-size:12px;color:#92400E;">{_e(testo_nota)} oggi.</div>'
        
        tabella = (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">'
                   f'<tr>{th}</tr>{"".join(corpo)}</table>{nota}')

    return f"""
    <tr><td style="padding:0 24px 14px 24px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #E2E8F0;border-left:4px solid {bordo};">
        <tr><td style="padding:14px 18px 4px 18px;">
          <div style="font-size:17px;font-weight:bold;color:{NAVY};">{_e(r['display_name'])}</div>
          <div style="padding-top:2px;">{chips}</div>
        </td></tr>
        <tr><td style="padding:10px 18px 16px 18px;">{corpo_alto}{tabella}</td></tr>
      </table>
    </td></tr>"""


# ----------------------------------------------------------------------
# Mail completa
# ----------------------------------------------------------------------
def build_nightly_report_html(tenant_reports: list, generated_at: datetime | None = None,
                                platform_health: dict | None = None,
                                expiring_certs: list | None = None) -> str:
    """Costruisce l'HTML completo del report: sintesi, stato della piattaforma, certificati in
    scadenza e un blocco per cliente. Se il logo (cid:logo) non e' disponibile va tolto dal
    chiamante (vedi send_html_stdin.prepare_html)."""
    generated_at = generated_at or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    local = generated_at.astimezone(_local_tz())
    now = generated_at

    # --- sintesi ---
    fail, warn = [], []
    if platform_health:
        if not platform_health["graylog"]["ok"]:
            fail.append("Graylog non risponde")
        if not platform_health["mariadb"]["ok"]:
            fail.append("MariaDB non risponde")
    scaduti = sum(1 for c in (expiring_certs or []) if c["days_remaining"] < 0)
    in_scad = len(expiring_certs or []) - scaduti
    if scaduti:
        fail.append(_plural(scaduti, "certificato agent scaduto", "certificati agent scaduti"))
    if in_scad:
        warn.append(_plural(in_scad, "certificato agent in scadenza", "certificati agent in scadenza"))
    tutte = [row for r in tenant_reports for row in _device_rows(r, now)]
    non_attivi = sum(1 for x in tutte if x["senza_log"] or x["online"] is False)
    attivi = sum(len(r["devices"]) for r in tenant_reports)
    if non_attivi:
        warn.append(_plural(non_attivi, "dispositivo non invia log", "dispositivi non inviano log"))
    if tenant_reports and attivi == 0 and not non_attivi:
        warn.append("nessun dispositivo ha inviato log oggi")
    if fail:
        tono, titolo = "fail", "Ci sono problemi da risolvere"
    elif warn:
        tono, titolo = "warn", "Alcuni punti da controllare"
    else:
        tono, titolo = "ok", "Tutto regolare"
    bg, fg = TONI[tono]
    testo_punti = "; ".join(fail + warn)
    dettaglio = (testo_punti[0].upper() + testo_punti[1:] + ".") if testo_punti else \
        "La piattaforma risponde e tutti i dispositivi stanno inviando log."

    tot_msg = sum(int(r.get("total_messages") or 0) for r in tenant_reports)
    tot_err = sum((d.get("errors") or 0) for r in tenant_reports for d in r["devices"])
    tot_avv = sum((d.get("warnings") or 0) for r in tenant_reports for d in r["devices"])

    def tile(valore, etichetta, colore=NAVY):
        return (f'<td width="25%" align="center" style="padding:12px 4px;background:#F8FAFC;border:1px solid #E2E8F0;">'
                f'<div style="font-size:22px;font-weight:bold;color:{colore};">{_int(valore)}</div>'
                f'<div style="font-size:12px;color:#64748B;padding-top:2px;">{etichetta}</div></td>')
    tiles = ('<table width="100%" cellpadding="0" cellspacing="6" border="0" style="margin:0 -6px;"><tr>'
             + tile(len(tenant_reports), "clienti") + tile(tot_msg, "messaggi oggi")
             + tile(tot_err, "errori", "#B91C1C" if tot_err else "#94A3B8")
             + tile(tot_avv, "avvisi", "#B45309" if tot_avv else "#94A3B8") + "</tr></table>")

    # --- piattaforma e certificati ---
    avvisi_html = ""
    if platform_health:
        gl_ok, db_ok = platform_health["graylog"]["ok"], platform_health["mariadb"]["ok"]
        if gl_ok and db_ok:
            piattaforma = (f'<div style="font-size:13px;color:#166534;">&#10003;&nbsp; Graylog e MariaDB rispondono correttamente.</div>')
        else:
            problemi = []
            if not gl_ok:
                problemi.append(f"Graylog: {_e(platform_health['graylog']['error'])}")
            if not db_ok:
                problemi.append(f"MariaDB: {_e(platform_health['mariadb']['error'])}")
            piattaforma = (f'<div style="background:{TONI["fail"][0]};color:{TONI["fail"][1]};padding:10px 14px;font-size:13px;">'
                           f'<b>Problemi rilevati sulla piattaforma:</b><br>{"<br>".join(problemi)}</div>')
        avvisi_html += f'<tr><td style="padding:0 24px 10px 24px;">{piattaforma}</td></tr>'
    if expiring_certs:
        righe_c = "".join(
            f'<tr><td style="padding:3px 0;font-size:13px;">{_e(c["common_name"])}</td>'
            f'<td align="right" style="padding:3px 0;font-size:13px;font-weight:bold;">'
            f'{"SCADUTO" if c["days_remaining"] < 0 else _plural(c["days_remaining"], "giorno", "giorni")}</td></tr>'
            for c in expiring_certs)
        t = "fail" if scaduti else "warn"
        avvisi_html += (f'<tr><td style="padding:0 24px 10px 24px;"><div style="background:{TONI[t][0]};color:{TONI[t][1]};'
                        f'padding:10px 14px;"><div style="font-size:13px;font-weight:bold;">Certificati agent in scadenza '
                        f'(o già scaduti): vanno rigenerati</div><table width="100%" cellpadding="0" cellspacing="0" border="0" '
                        f'style="margin-top:4px;">{righe_c}</table></div></td></tr>')

    schede = "".join(_tenant_card(i, r, now) for i, r in enumerate(tenant_reports)) or (
        '<tr><td style="padding:0 24px 14px 24px;font-size:13px;color:#64748B;">Nessun cliente configurato.</td></tr>')

    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    pulsante = (f'<a href="{_e(base)}" style="display:inline-block;background:{NAVY};color:#FFFFFF;text-decoration:none;'
                f'font-size:13px;font-weight:bold;padding:9px 18px;">Apri Log Platform</a>'
                if base.startswith(("http://", "https://")) else "")

    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Report log notturno</title></head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:{FONT};">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F1F5F9;"><tr><td align="center" style="padding:24px 12px;">
  <table width="680" cellpadding="0" cellspacing="0" border="0" style="width:680px;max-width:100%;background:#FFFFFF;border-top:4px solid {NAVY};font-family:{FONT};">
    <tr><td style="padding:22px 24px 8px 24px;">
      <img src="cid:logo" alt="" height="38" style="display:block;height:38px;border:0;margin-bottom:14px;">
      <div style="font-size:22px;font-weight:bold;color:{NAVY};">Report log notturno</div>
      <div style="font-size:13px;color:#64748B;padding-top:4px;">{local:%d/%m/%Y} &nbsp;–&nbsp; generato alle {local:%H:%M}</div>
    </td></tr>
    <tr><td style="padding:14px 24px 8px 24px;">
      <div style="background:{bg};color:{fg};padding:12px 16px;">
        <div style="font-size:16px;font-weight:bold;">{_e(titolo)}</div>
        <div style="font-size:13px;padding-top:3px;">{_e(dettaglio)}</div>
      </div>
    </td></tr>
    <tr><td style="padding:6px 24px 14px 24px;">{tiles}</td></tr>
    {avvisi_html}
    {schede}
    <tr><td style="padding:6px 24px 22px 24px;">{pulsante}</td></tr>
    <tr><td style="padding:16px 24px 20px 24px;border-top:1px solid #E2E8F0;font-size:12px;line-height:18px;color:#94A3B8;">
      Generato automaticamente da Log Platform.
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""
