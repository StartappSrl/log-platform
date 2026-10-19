#!/usr/bin/env python3
"""
Esito dei controlli notturni, in una mail HTML impaginata - una ogni
mattina, anche quando va tutto bene (cosi' l'assenza della mail e'
essa stessa un segnale: vuol dire che il cron non gira).

Gira sul NODO (non dentro un container): da li' si vedono disco,
backup e container degli altri moduli. L'invio passa dal container
`auth` (stesso SMTP configurato in Impostazioni, stessi destinatari del
report notturno) tramite il modulo app.send_html_stdin.

Controlla:
  - backup: esito di verifica_backup.sh, eta', dimensione
  - disco del nodo
  - esito dell'ultimo report notturno (dal suo log)
  - esito dell'ultimo controllo dispositivi offline (dal suo log)
  - moduli NS8 in esecuzione
  - ultimo riavvio del nodo

Solo libreria standard. Uso:
    esito_notturno.py              controlla e invia la mail
    esito_notturno.py --dry-run    controlla, scrive l'HTML su file, NON invia

Il codice di uscita dice se la MAIL e' stata inviata (0) o no (1), non
l'esito dei controlli: i problemi trovati sono il contenuto della mail.
"""
import argparse
import html
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime

BACKUP_ROOT = os.environ.get("BACKUP_DIR", "/var/lib/logplatform-backups")
VERIFY_SCRIPT = os.environ.get(
    "VERIFY_SCRIPT", "/root/logmanager/final-package/scripts/verifica_backup.sh")
REPORT_LOG = "/var/log/logplatform-report.log"
OFFLINE_LOG = "/var/log/logplatform-offline-check.log"
MODULES = [
    ("mariadb", "logplatform-mariadb"), ("mongodb", "logplatform-mongodb"),
    ("opensearch", "logplatform-opensearch"), ("graylog", "logplatform-graylog"),
    ("auth", "logplatform-auth"), ("gate", "logplatform-gate"),
]
DISK_WARN, DISK_FAIL = 75, 90          # stesse soglie della Dashboard
ERROR_PATTERN = re.compile(
    r"\bError\b|\bERRORE\b|Traceback|fallit|SMTP non configurato|no such container|non configurat")
ORDER = {"ok": 0, "warn": 1, "fail": 2}


# ----------------------------------------------------------------------
# Esecuzione comandi (iniettabile nei test)
# ----------------------------------------------------------------------
def run_cmd(cmd, input_text=None, timeout=300):
    """cwd=/tmp: 'sudo -u' fallisce se lanciato da una cartella di root."""
    try:
        p = subprocess.run(cmd, input=input_text, capture_output=True, text=True,
                           cwd="/tmp", timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"comando non trovato: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout dopo {timeout}s: {' '.join(cmd[:3])}"


def fmt_bytes(n):
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{int(v)} B" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def check(nome, stato, righe):
    return {"nome": nome, "stato": stato, "righe": righe}


# ----------------------------------------------------------------------
# Raccolta dati
# ----------------------------------------------------------------------
def collect_backup(run, now, backup_root=BACKUP_ROOT, verify_script=VERIFY_SCRIPT):
    try:
        dirs = [os.path.join(backup_root, d) for d in os.listdir(backup_root)
                if os.path.isdir(os.path.join(backup_root, d))]
    except OSError:
        dirs = []
    if not dirs:
        return check("Backup", "fail", [f"Nessun backup trovato in {backup_root}"])

    latest = max(dirs, key=os.path.getmtime)
    when = datetime.fromtimestamp(os.path.getmtime(latest))
    age_h = max(0, int((now - when).total_seconds() // 3600))  # mai negativo (scarti d'orologio)

    rc, out, _err = run([verify_script, latest])
    fails = [l.strip()[len("[FALLITO]"):].strip() for l in out.splitlines()
             if l.strip().startswith("[FALLITO]")]
    warns = [l.strip()[len("[ATTENZIONE]"):].strip() for l in out.splitlines()
             if l.strip().startswith("[ATTENZIONE]")]

    righe = [f"Ultimo backup: {when:%d/%m/%Y %H:%M} ({age_h} ore fa)",
             f"Dimensione: {fmt_bytes(dir_size(latest))}"]
    if rc != 0 or fails:
        stato = "fail"
        righe.append("Verifica di integrità: NON superata")
        righe += [f"Problema: {f}" for f in fails] or ["Lo script di verifica ha restituito un errore"]
    elif warns:
        stato = "warn"
        righe.append("Verifica di integrità: superata, con avvisi")
        righe += [f"Avviso: {w}" for w in warns]
    else:
        stato = "ok"
        righe.append("Verifica di integrità: superata")
    return check("Backup", stato, righe)


def collect_disk(path="/", usage_fn=shutil.disk_usage):
    total, used, free = usage_fn(path)
    pct = round(used / total * 100)
    stato = "fail" if pct >= DISK_FAIL else "warn" if pct >= DISK_WARN else "ok"
    return check("Spazio disco", stato,
                 [f"Utilizzo: {pct}% di {fmt_bytes(total)}", f"Libero: {fmt_bytes(free)}"])


def collect_log_job(nome, path, now, max_age_h, ok_marker, ok_label):
    """Stato dell'ultima esecuzione di un job pianificato, letto dal suo log:
    il log deve essere recente e la sua ultima riga significativa deve essere
    il messaggio di successo (non un errore)."""
    if not os.path.exists(path):
        return check(nome, "fail", [f"Log {path} assente: il job non ha mai girato"])
    age_h = (now - datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() / 3600
    if age_h > max_age_h:
        return check(nome, "fail", [
            f"Ultima esecuzione: {age_h:.0f} ore fa (atteso entro {max_age_h:g} ore)",
            "Il cron potrebbe non girare più"])
    state, last_line = None, ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-60:]:
            if ok_marker in line:
                state, last_line = "ok", line.strip()
            elif ERROR_PATTERN.search(line):
                state, last_line = "fail", line.strip()
    if state == "ok":
        return check(nome, "ok", [ok_label, f"Ultima esecuzione: {age_h * 60:.0f} minuti fa"
                                  if age_h < 3 else f"Ultima esecuzione: {age_h:.0f} ore fa"])
    if state == "fail":
        return check(nome, "fail", ["Ultima esecuzione fallita", last_line[:160]])
    return check(nome, "warn", ["Il log è recente ma non contiene un esito riconoscibile"])


_UNITS_IT = {"second": ("secondo", "secondi"), "minute": ("minuto", "minuti"), "hour": ("ora", "ore"),
             "day": ("giorno", "giorni"), "week": ("settimana", "settimane"),
             "month": ("mese", "mesi"), "year": ("anno", "anni")}
_ABOUT_IT = {"About a second": "circa un secondo", "About a minute": "circa un minuto",
             "About an hour": "circa un'ora", "Less than a second": "meno di un secondo"}


def it_uptime(status):
    """Traduce la durata che podman scrive in inglese ('Up 14 hours',
    'Up About an hour', 'Up 3 weeks (healthy)') - se non la riconosce
    restituisce il testo originale, mai un errore."""
    text = re.sub(r"\s*\(.*?\)\s*$", "", status.strip())
    text = text[3:].strip() if text.startswith("Up ") else text
    if text in _ABOUT_IT:
        return _ABOUT_IT[text]
    m = re.fullmatch(r"(\d+) (second|minute|hour|day|week|month|year)s?", text)
    if m:
        n = int(m.group(1))
        singolare, plurale = _UNITS_IT[m.group(2)]
        return f"{n} {singolare if n == 1 else plurale}"
    return text


def collect_modules(run, home="/home"):
    righe, down = [], 0
    try:
        names = os.listdir(home)
    except OSError:
        names = []
    for mod, container in MODULES:
        inst = next((n for n in sorted(names) if re.fullmatch(rf"logplatform-{mod}\d+", n)), None)
        if inst is None:
            righe.append(f"{mod}: istanza non trovata")
            down += 1
            continue
        rc, uid_out, _ = run(["id", "-u", inst])
        uid = uid_out.strip()
        rc, out, _ = run(["sudo", "-u", inst, f"XDG_RUNTIME_DIR=/run/user/{uid}", "podman", "ps",
                          "--filter", f"name={container}", "--format", "{{.Status}}"])
        status = out.strip()
        if rc == 0 and status.startswith("Up"):
            righe.append(f"{mod}: in esecuzione da {it_uptime(status)}")
        else:
            righe.append(f"{mod}: NON in esecuzione")
            down += 1
    return check("Moduli della piattaforma", "fail" if down else "ok", righe)


def collect_host(uptime_seconds):
    hours = uptime_seconds / 3600
    if hours < 24:
        return check("Nodo", "warn", [f"Riavviato {hours:.0f} ore fa",
                                      "Verifica che tutti i servizi siano ripartiti"])
    return check("Nodo", "ok", [f"Attivo da {hours / 24:.0f} giorni senza riavvii"])


def read_uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


# ----------------------------------------------------------------------
# Impaginazione HTML (tabelle e stili inline: compatibile coi client di posta)
# ----------------------------------------------------------------------
NAVY, BLU = "#0F1B33", "#2E5A9C"
PALETTE = {  # sfondo, testo, etichetta
    "ok":   ("#DCFCE7", "#166534", "OK"),
    "warn": ("#FEF3C7", "#92400E", "Attenzione"),
    "fail": ("#FEE2E2", "#991B1B", "Problema"),
}


def overall_status(checks):
    return max((c["stato"] for c in checks), key=lambda s: ORDER[s], default="ok")


def summary_text(checks):
    n_fail = sum(c["stato"] == "fail" for c in checks)
    n_warn = sum(c["stato"] == "warn" for c in checks)
    if n_fail:
        return f"{n_fail} {'problema' if n_fail == 1 else 'problemi'} da risolvere"
    if n_warn:
        return f"{n_warn} {'avviso' if n_warn == 1 else 'avvisi'} da controllare"
    return "Tutto regolare"


def render_html(checks, now, host):
    e = html.escape
    overall = overall_status(checks)
    bg, fg, _ = PALETTE[overall]
    rows = []
    for c in checks:
        cbg, cfg, label = PALETTE[c["stato"]]
        dettagli = "".join(
            f'<div style="font-size:13px;line-height:19px;color:#475569;">{e(r)}</div>'
            for r in c["righe"])
        rows.append(f"""
      <tr><td style="padding:14px 24px;border-top:1px solid #E2E8F0;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
          <td style="font-size:15px;font-weight:bold;color:{NAVY};padding-bottom:4px;">{e(c['nome'])}</td>
          <td align="right" style="padding-bottom:4px;">
            <span style="display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:bold;background:{cbg};color:{cfg};">{label}</span>
          </td></tr></table>
        {dettagli}
      </td></tr>""")

    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8"><title>Esito controlli notturni</title></head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F1F5F9;"><tr><td align="center" style="padding:24px 12px;">
  <table width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;background:#FFFFFF;border-top:4px solid {NAVY};">
    <tr><td style="padding:22px 24px 8px 24px;">
      <img src="cid:logo" alt="Defendia" height="38" style="display:block;height:38px;border:0;margin-bottom:14px;">
      <div style="font-size:21px;font-weight:bold;color:{NAVY};">Esito dei controlli notturni</div>
      <div style="font-size:13px;color:#64748B;padding-top:4px;">{e(host)} &nbsp;–&nbsp; {now:%d/%m/%Y alle %H:%M}</div>
    </td></tr>
    <tr><td style="padding:14px 24px 16px 24px;">
      <div style="background:{bg};color:{fg};padding:12px 16px;font-size:16px;font-weight:bold;">{e(summary_text(checks))}</div>
    </td></tr>{''.join(rows)}
    <tr><td style="padding:16px 24px 20px 24px;border-top:1px solid #E2E8F0;font-size:12px;line-height:18px;color:#94A3B8;">
      Messaggio automatico di Log Platform. Se una mattina questa mail non arriva, il controllo notturno
      non è stato eseguito: verifica il crontab del nodo.
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


# ----------------------------------------------------------------------
# Invio tramite il container auth
# ----------------------------------------------------------------------
def send_via_auth(html_text, subject, run, home="/home"):
    names = os.listdir(home)
    inst = next((n for n in sorted(names) if re.fullmatch(r"logplatform-auth\d+", n)), None)
    if inst is None:
        return False, "istanza auth non trovata in /home"
    uid = run(["id", "-u", inst])[1].strip()
    rc, out, err = run(["sudo", "-u", inst, f"XDG_RUNTIME_DIR=/run/user/{uid}", "podman", "exec", "-i",
                        "logplatform-auth", "python3", "-m", "app.send_html_stdin", subject],
                       input_text=html_text)
    if rc != 0:
        return False, (err or out).strip()[-300:]
    return True, ""


def safe(nome, fn, *args):
    """Un controllo che si rompe non deve impedire l'invio della mail (che e'
    proprio il posto dove il problema va segnalato): diventa una riga rossa."""
    try:
        return fn(*args)
    except Exception as exc:
        return check(nome, "fail", [f"Controllo non eseguibile: {type(exc).__name__}: {exc}"])


def gather(run, now):
    return [
        safe("Backup", collect_backup, run, now),
        safe("Spazio disco", collect_disk, "/"),
        safe("Report notturno ai clienti", collect_log_job, "Report notturno ai clienti",
             REPORT_LOG, now, 26, "Report inviato a", "Ultimo invio riuscito"),
        safe("Controllo dispositivi offline", collect_log_job, "Controllo dispositivi offline",
             OFFLINE_LOG, now, 1, "Controllo dispositivi offline completato", "Ultimo controllo riuscito"),
        safe("Moduli della piattaforma", collect_modules, run),
        safe("Nodo", lambda: collect_host(read_uptime())),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="non invia: scrive l'HTML su file")
    ap.add_argument("--out", default="/tmp/esito-notturno.html")
    args = ap.parse_args()

    now = datetime.now()
    host = socket.gethostname()
    checks = gather(run_cmd, now)
    page = render_html(checks, now, host)
    subject = f"[{host}] Esito controlli notturni {now:%d/%m/%Y}: {summary_text(checks).lower()}"

    for c in checks:
        print(f"{c['stato'].upper():5} {c['nome']}")

    if args.dry_run:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(page)
        print(f"Dry-run: HTML scritto in {args.out} (nessuna mail inviata)")
        return 0

    ok, err = send_via_auth(page, subject, run_cmd)
    if not ok:
        print(f"INVIO FALLITO: {err}", file=sys.stderr)
        return 1
    print(f"Mail inviata: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
