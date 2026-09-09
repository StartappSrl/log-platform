"""
Tracciamento dei certificati emessi (per tenant/server) e generazione di
una Certificate Revocation List (CRL) X.509 standard.

LIMITE ONESTO — leggi prima di considerare la revoca "completa":
l'input GELF TCP+TLS nativo di Graylog non supporta la verifica di una
CRL (il suo schema di configurazione ha solo tls_client_auth_cert_file,
nessun campo per una CRL). Questo modulo produce una CRL corretta e
standard, e la tiene aggiornata - ma perche' Graylog la usi davvero per
RIFIUTARE le connessioni di un certificato revocato, serve un proxy TLS
davanti alla porta GELF che supporti la verifica CRL (es. nginx con lo
stream module e la direttiva ssl_crl, o stunnel) - vedi
NGINX-CRL-PROXY.md per un disegno di questa parte, non ancora applicato
in produzione per mancanza di tempo/test dal vivo in questa sessione.

Senza quel proxy, "revocare" un certificato qui lo segna come non valido
nei nostri registri (utile per audit, per rigenerare pacchetti agent, e
come base per il proxy quando lo si aggiunge), ma quel client potrebbe
ancora connettersi a Graylog finche' non gli si toglie anche l'accesso di
rete o non si ruota la CA.
"""
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from . import ca_manager

LEDGER_PATH = ca_manager.CA_DIR / "certs.db"


def _connect():
    ca_manager.CA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LEDGER_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS issued_certs (
            serial_number TEXT PRIMARY KEY,
            common_name TEXT NOT NULL,
            is_server INTEGER NOT NULL DEFAULT 0,
            issued_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            revoked_at TEXT
        )
    """)
    conn.commit()
    return conn


def record_issued_certificate(serial_number: int, common_name: str, is_server: bool = False):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO issued_certs (serial_number, common_name, is_server, issued_at) "
            "VALUES (?, ?, ?, ?)",
            (str(serial_number), common_name, int(is_server), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def list_issued_certificates() -> list:
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT serial_number, common_name, is_server, issued_at, revoked, revoked_at "
            "FROM issued_certs ORDER BY issued_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_certificate(serial_number: str) -> bool:
    """Ritorna True se un certificato con questo seriale esisteva ed e'
    stato segnato come revocato, False se il seriale non era noto."""
    with closing(_connect()) as conn:
        cur = conn.execute(
            "UPDATE issued_certs SET revoked = 1, revoked_at = ? "
            "WHERE serial_number = ? AND revoked = 0",
            (datetime.now(timezone.utc).isoformat(), serial_number),
        )
        conn.commit()
        return cur.rowcount > 0


def get_revoked_serials() -> list:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT serial_number FROM issued_certs WHERE revoked = 1").fetchall()
        return [r[0] for r in rows]


def issue_and_record_certificate(common_name: str, is_server: bool = False,
                                  dns_names: list | None = None) -> tuple:
    """Come ca_manager.issue_certificate, ma registra anche il seriale nel
    registro locale - usare SEMPRE questa funzione invece di chiamare
    ca_manager.issue_certificate direttamente, altrimenti il certificato
    emesso non sara' mai revocabile (non comparira' nel registro)."""
    cert_pem, key_pem = ca_manager.issue_certificate(common_name, is_server=is_server, dns_names=dns_names)
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    record_issued_certificate(cert.serial_number, common_name, is_server=is_server)
    return cert_pem, key_pem


def generate_crl_pem(validity_days: int = 15) -> str:
    """Genera una CRL X.509 standard con tutti i seriali revocati,
    firmata dalla CA interna. Valida per 'validity_days' giorni (default
    15) - va rigenerata/ripubblicata periodicamente entro questa finestra
    (vedi regenerate_crl_file.py per uno script cron-abile): un CRL
    scaduto viene trattato da molti client TLS come 'nessuna
    informazione', quindi non basta generarla una volta sola."""
    ca_key, ca_cert = ca_manager._load_ca()

    builder = x509.CertificateRevocationListBuilder()
    builder = builder.issuer_name(ca_cert.subject)
    now = datetime.now(timezone.utc)
    builder = builder.last_update(now)
    builder = builder.next_update(now + timedelta(days=validity_days))

    for serial_str in get_revoked_serials():
        revoked_cert = (
            x509.RevokedCertificateBuilder()
            .serial_number(int(serial_str))
            .revocation_date(now)
            .build()
        )
        builder = builder.add_revoked_certificate(revoked_cert)

    crl = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    return crl.public_bytes(serialization.Encoding.PEM).decode()
