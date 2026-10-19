"""
Invia il riepilogo settimanale ai clienti per cui e' stato attivato.

Uso (dentro il container auth, di norma da cron il lunedi' mattina):
    python3 -m app.send_customer_reports                      # invio vero ai clienti attivati
    python3 -m app.send_customer_reports --dry-run            # genera i file HTML, NON invia
    python3 -m app.send_customer_reports --to me@x.it --include-disabled
                                                              # PROVA: tutto solo a me, nessuna mail ai clienti

Sicurezze:
  - Senza --to scrive SOLO ai clienti con weekly_report_enabled e un'email valida.
  - Con --to scrive SOLO a quell'indirizzo (oggetto "[PROVA]"), mai ai clienti, mai in copia.
  - --include-disabled e' ammesso solo insieme a --to o --dry-run.
  - Se Graylog non risponde per un cliente, quel cliente NON riceve nulla (niente falsi allarmi).
  - Ogni invio vero lascia una riga nel log di controllo (utente 'sistema') e una copia
    ai destinatari del report notturno.

Codici di uscita: 0 tutto ok, 1 almeno un errore, 2 uso scorretto.
"""
import argparse
import re
import sys
from datetime import datetime, timezone

from . import create_app
from .models import db, Tenant, SmtpSettings
from .customer_report import collect_customer_data, render_customer_html, banner
from .email_sender import send_html_email_with_images
from .send_html_stdin import prepare_html

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_addresses(raw):
    parts = [p.strip() for p in re.split(r"[;,]", raw or "") if p.strip()]
    return [p for p in parts if EMAIL_RE.match(p)], [p for p in parts if not EMAIL_RE.match(p)]


def _audit(action, details):
    """Traccia nel log di controllo (best effort: un problema qui non deve bloccare l'invio)."""
    try:
        from .models import AuditLog
        db.session.add(AuditLog(username="sistema", action=action, details=details, ip=None,
                                created_at=datetime.utcnow()))
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        print(f"  (avviso: log di controllo non scritto: {exc})")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="non invia: scrive l'HTML in /tmp")
    ap.add_argument("--to", help="invia SOLO a questo indirizzo (modalita' prova)")
    ap.add_argument("--tenant", help="solo questo cliente (nome identificativo)")
    ap.add_argument("--include-disabled", action="store_true",
                    help="include anche i clienti non attivati (solo con --to o --dry-run)")
    args = ap.parse_args(argv)

    if args.include_disabled and not (args.to or args.dry_run):
        print("ERRORE: --include-disabled e' ammesso solo con --to o --dry-run "
              "(altrimenti scriverebbe a clienti non attivati).", file=sys.stderr)
        return 2
    if args.to and not EMAIL_RE.match(args.to):
        print(f"ERRORE: indirizzo di prova non valido: {args.to}", file=sys.stderr)
        return 2

    app = create_app()
    with app.app_context():
        settings = SmtpSettings.query.get(1)
        if not args.dry_run and (not settings or not settings.smtp_host):
            print("ERRORE: SMTP non configurato (Impostazioni nel pannello). Nessun invio.", file=sys.stderr)
            return 1

        query = Tenant.query.order_by(Tenant.name)
        if args.tenant:
            query = query.filter_by(name=args.tenant)
        tenants = query.all()
        if not args.include_disabled:
            tenants = [t for t in tenants if t.weekly_report_enabled]
        if not tenants:
            print("Nessun cliente selezionato (attivane almeno uno, oppure usa --include-disabled con --to).")
            return 0

        def send(subject, body, recipients):
            body2, images = prepare_html(body)
            send_html_email_with_images(
                subject, body2, recipients, smtp_host=settings.smtp_host, smtp_port=settings.smtp_port or 587,
                smtp_user=settings.smtp_user or "", smtp_password=settings.smtp_password or "",
                smtp_from=settings.smtp_from or "", smtp_use_tls=settings.smtp_use_tls, inline_images=images)

        internal = parse_addresses(settings.report_recipients)[0] if settings else []
        sent = skipped = failed = 0
        for t in tenants:
            nome = t.display_name or t.name
            if not t.graylog_stream_id:
                print(f"SALTATO {t.name}: cliente senza stream Graylog")
                skipped += 1
                continue
            customer_addrs, bad = parse_addresses(t.email)
            if not args.to and not args.dry_run and not customer_addrs:
                motivo = f"indirizzo non valido ({', '.join(bad)})" if bad else "nessuna email del referente"
                print(f"SALTATO {t.name}: {motivo}")
                skipped += 1
                continue

            try:
                data = collect_customer_data(nome, t.graylog_stream_id)
            except Exception as exc:  # noqa: BLE001
                print(f"ERRORE {t.name}: dati non disponibili ({str(exc)[:120]}): il cliente NON riceve nulla", file=sys.stderr)
                failed += 1
                continue
            page = render_customer_html(data)
            stato, testo = banner(data)
            oggi = datetime.now(timezone.utc).strftime("%d/%m/%Y")
            subject = f"Riepilogo settimanale del servizio log — {nome} — {oggi}"

            if args.dry_run:
                path = f"/tmp/riepilogo-cliente-{t.name}.html"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(page)
                print(f"DRY-RUN {t.name}: {stato.upper()} - {testo} ({len(data['dispositivi'])} {'dispositivo' if len(data['dispositivi']) == 1 else 'dispositivi'}) -> {path}")
                continue

            try:
                if args.to:
                    send(f"[PROVA] {subject}", page, [args.to])
                    print(f"PROVA   {t.name}: inviato a {args.to} ({stato.upper()} - {testo})")
                else:
                    send(subject, page, customer_addrs)
                    print(f"INVIATO {t.name}: {', '.join(customer_addrs)} ({stato.upper()} - {testo})")
                    _audit("invio_report_cliente", f"cliente={t.name}, destinatari={', '.join(customer_addrs)}")
                    if internal:
                        try:
                            send(f"[Copia inviata a {', '.join(customer_addrs)}] {subject}", page, internal)
                        except Exception as exc:  # noqa: BLE001
                            print(f"  (avviso: copia interna non inviata: {str(exc)[:100]})")
                sent += 1
            except Exception as exc:  # noqa: BLE001
                print(f"ERRORE {t.name}: invio fallito ({str(exc)[:120]})", file=sys.stderr)
                failed += 1

        print(f"Riepilogo: {sent} inviati, {skipped} saltati, {failed} errori.")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
