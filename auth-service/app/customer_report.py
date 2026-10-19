"""
Riepilogo settimanale destinato al CLIENTE (non all'amministratore): stato dei
suoi dispositivi, attivita' della settimana, aggiornamenti in sospeso.

Principi:
  - Isolamento: ogni interrogazione a Graylog porta 'filter=streams:<stream del
    cliente>'. Una mail contiene SOLO i dati di quel cliente.
  - Niente falsi allarmi per colpa nostra: se una interrogazione a Graylog fallisce
    l'eccezione risale e il cliente NON riceve nulla (meglio saltare una settimana
    che scrivere a un cliente "nessun dispositivo attivo" per un nostro errore).
  - Linguaggio per il cliente: niente dettagli interni (backup, disco del nodo,
    certificati, nomi di altri clienti).

Limite noto: l'elenco dispositivi si ricava dai messaggi piu' recenti della settimana
(fino a 5000); con volumi molto alti un dispositivo rimasto muto da giorni potrebbe
non comparire. Il riepilogo lo segnala ('troncato').
"""
import html
from datetime import datetime, timedelta, timezone

from . import graylog_client as gl

GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
NAVY, BLU = "#0F1B33", "#2E5A9C"
PALETTE = {  # sfondo, testo
    "ok": ("#DCFCE7", "#166534"),
    "warn": ("#FEF3C7", "#92400E"),
    "fail": ("#FEE2E2", "#991B1B"),
}
ORDINE = {"ok": 0, "warn": 1, "fail": 2}
MAX_DISPOSITIVI = 40


def local_tz():
    """Fuso orario italiano per decidere dove inizia 'oggi'; se il container non ha
    i dati dei fusi, si usa UTC (i giorni cambiano 1-2 ore prima/dopo: trascurabile)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Rome")
    except Exception:
        return timezone.utc


def _search(stream_id, seconds, limit=1):
    return gl._request("GET", "/api/search/universal/relative", params={
        "query": "*", "range": int(seconds), "limit": int(limit), "sort": "timestamp:desc",
        "fields": "timestamp,source", "filter": f"streams:{stream_id}",
    })


def _parse_ts(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def stato_dispositivo(eta_s):
    if eta_s <= 86400:
        return "ok"
    return "warn" if eta_s <= 3 * 86400 else "fail"


def fmt_int(n):
    return f"{n:,}".replace(",", ".")


def fmt_eta(seconds):
    if seconds < 3600:
        m = max(1, round(seconds / 60))
        return "1 minuto fa" if m == 1 else f"{m} minuti fa"
    if seconds < 172800:
        h = round(seconds / 3600)
        return "1 ora fa" if h == 1 else f"{h} ore fa"
    return f"{round(seconds / 86400)} giorni fa"


def collect_customer_data(display_name, stream_id, now=None, tz=None):
    """Raccoglie i dati di UN cliente. Solleva eccezione se Graylog non risponde."""
    now = now or datetime.now(timezone.utc)
    tz = tz or local_tz()
    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    # Messaggi per giorno di calendario: conteggi cumulativi dall'inizio di ciascun giorno
    cum = [int(_search(stream_id, max(1, (now - (midnight - timedelta(days=i))).total_seconds()))
               .get("total_results") or 0) for i in range(7)]
    giorni = []
    for i in range(6, -1, -1):
        n = cum[i] - (cum[i - 1] if i > 0 else 0)
        giorni.append(((midnight - timedelta(days=i)).date(), max(n, 0)))
    totale = cum[6]

    # Dispositivi visti nella settimana, dai messaggi piu' recenti
    secs7 = max(1, (now - (midnight - timedelta(days=6))).total_seconds())
    sample, errore = None, None
    for lim in (5000, 1000):
        try:
            sample = _search(stream_id, secs7, lim)
            break
        except Exception as e:  # noqa: BLE001
            errore = e
    if sample is None:
        raise errore
    msgs = [m["message"] for m in (sample.get("messages") or [])]
    troncato = len(msgs) < int(sample.get("total_results") or 0)
    ultimo = {}
    for m in msgs:                      # ordinati dal piu' recente: la prima occorrenza e' l'ultimo contatto
        src = m.get("source")
        if src and src not in ultimo:
            ultimo[src] = m.get("timestamp")

    pending = {}
    try:
        for inv in gl.get_latest_inventory_per_host(stream_id):
            ups = inv.get("pending_updates") or []
            if ups:
                pending[inv["hostname"]] = len(ups)
    except Exception:  # noqa: BLE001  - gli aggiornamenti sono un extra: non devono bloccare il resto
        pending = {}

    dispositivi = []
    for nome, ts in ultimo.items():
        eta = (now - _parse_ts(ts)).total_seconds()
        dispositivi.append({"nome": nome, "eta_s": eta, "stato": stato_dispositivo(eta),
                            "aggiornamenti": pending.get(nome, 0)})
    dispositivi.sort(key=lambda d: (-ORDINE[d["stato"]], d["nome"].lower()))
    return {"nome": display_name, "primo_giorno": giorni[0][0], "ultimo_giorno": giorni[-1][0],
            "giorni": giorni, "totale": totale, "dispositivi": dispositivi, "troncato": troncato}


def banner(d):
    """(stato, testo) del riquadro di sintesi."""
    if not d["dispositivi"]:
        return "fail", "Nessun dispositivo ha inviato dati negli ultimi 7 giorni"
    silenti = sum(1 for x in d["dispositivi"] if x["stato"] != "ok")
    if silenti:
        return "warn", (f"{silenti} dispositivo non invia" if silenti == 1 else f"{silenti} dispositivi non inviano") \
            + " dati da più di 24 ore"
    return "ok", "Il servizio funziona regolarmente"


def render_customer_html(d):
    e = html.escape
    stato, testo = banner(d)
    bg, fg = PALETTE[stato]

    mx = max([n for _, n in d["giorni"]] + [1])
    barre = []
    for i, (giorno, n) in enumerate(d["giorni"]):
        etichetta = f"{GIORNI[giorno.weekday()]} {giorno:%d/%m}" + (" (oggi)" if i == len(d["giorni"]) - 1 else "")
        w = 0 if n == 0 else max(2, round(100 * n / mx))
        barra = (f'<table width="{w}%" cellpadding="0" cellspacing="0" border="0"><tr><td style="background:{BLU};'
                 f'height:10px;font-size:1px;line-height:1px;">&nbsp;</td></tr></table>') if w else ""
        barre.append(f'<tr><td width="110" style="font-size:13px;color:#475569;padding:3px 0;">{etichetta}</td>'
                     f'<td style="padding:3px 8px;">{barra}</td>'
                     f'<td width="70" align="right" style="font-size:13px;color:#1E293B;padding:3px 0;">{fmt_int(n)}</td></tr>')

    righe = []
    for x in d["dispositivi"][:MAX_DISPOSITIVI]:
        cbg, cfg = PALETTE[x["stato"]]
        etichetta = "Attivo" if x["stato"] == "ok" else "Non invia"
        agg = str(x["aggiornamenti"]) if x["aggiornamenti"] else "–"
        righe.append(
            f'<tr><td style="padding:8px 6px;border-top:1px solid #E2E8F0;font-size:13px;color:{NAVY};">{e(x["nome"])}</td>'
            f'<td style="padding:8px 6px;border-top:1px solid #E2E8F0;"><span style="display:inline-block;padding:2px 10px;'
            f'border-radius:10px;font-size:12px;font-weight:bold;background:{cbg};color:{cfg};">{etichetta}</span></td>'
            f'<td style="padding:8px 6px;border-top:1px solid #E2E8F0;font-size:13px;color:#475569;">{fmt_eta(x["eta_s"])}</td>'
            f'<td align="right" style="padding:8px 6px;border-top:1px solid #E2E8F0;font-size:13px;color:#475569;">{agg}</td></tr>')
    extra = len(d["dispositivi"]) - MAX_DISPOSITIVI
    if extra > 0:
        righe.append(f'<tr><td colspan="4" style="padding:8px 6px;font-size:12px;color:#64748B;">… e altri {extra} dispositivi</td></tr>')
    if not righe:
        righe.append('<tr><td colspan="4" style="padding:10px 6px;font-size:13px;color:#64748B;">Nessun dispositivo ha inviato dati in questo periodo.</td></tr>')
    nota_troncato = ('<div style="font-size:12px;color:#64748B;padding-top:8px;">L\'elenco si basa sui messaggi più recenti '
                     'della settimana.</div>') if d["troncato"] else ""

    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8"><title>Riepilogo settimanale</title></head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F1F5F9;"><tr><td align="center" style="padding:24px 12px;">
  <table width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;background:#FFFFFF;border-top:4px solid {NAVY};">
    <tr><td style="padding:22px 24px 8px 24px;">
      <img src="cid:logo" alt="" height="38" style="display:block;height:38px;border:0;margin-bottom:14px;">
      <div style="font-size:21px;font-weight:bold;color:{NAVY};">Riepilogo settimanale del servizio</div>
      <div style="font-size:14px;color:#334155;padding-top:6px;">{e(d['nome'])}</div>
      <div style="font-size:13px;color:#64748B;padding-top:2px;">dal {d['primo_giorno']:%d/%m/%Y} al {d['ultimo_giorno']:%d/%m/%Y}</div>
    </td></tr>
    <tr><td style="padding:14px 24px 8px 24px;">
      <div style="background:{bg};color:{fg};padding:12px 16px;font-size:16px;font-weight:bold;">{e(testo)}</div>
    </td></tr>
    <tr><td style="padding:14px 24px 4px 24px;">
      <div style="font-size:15px;font-weight:bold;color:{NAVY};">Attività della settimana</div>
      <div style="font-size:13px;color:#475569;padding:4px 0 8px 0;">Messaggi raccolti: <b>{fmt_int(d['totale'])}</b></div>
      <table width="100%" cellpadding="0" cellspacing="0" border="0">{''.join(barre)}</table>
    </td></tr>
    <tr><td style="padding:18px 24px 6px 24px;">
      <div style="font-size:15px;font-weight:bold;color:{NAVY};padding-bottom:6px;">Dispositivi ({len(d['dispositivi'])})</div>
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr style="color:#64748B;font-size:12px;text-align:left;"><th style="padding:4px 6px;font-weight:normal;">Dispositivo</th>
        <th style="padding:4px 6px;font-weight:normal;">Stato</th><th style="padding:4px 6px;font-weight:normal;">Ultimo invio</th>
        <th align="right" style="padding:4px 6px;font-weight:normal;">Aggiornamenti in sospeso</th></tr>
        {''.join(righe)}
      </table>{nota_troncato}
    </td></tr>
    <tr><td style="padding:18px 24px 20px 24px;border-top:1px solid #E2E8F0;font-size:12px;line-height:18px;color:#94A3B8;">
      Messaggio automatico del servizio di raccolta dei log. Per qualsiasi domanda puoi rispondere a questa email.
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""
