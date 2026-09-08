"""
Gestione della CA interna usata per l'mTLS tra gli agent client e l'input
GELF di Graylog. auth-service possiede questa CA (la genera al primo avvio
se non esiste, e la conserva su un percorso persistente): è il punto
naturale perché è già il servizio che genera i pacchetti agent dal
pannello, e i suoi dati (come la password MariaDB) sono già trattati con
la stessa cura.

Persistenza: CA_DIR (di default /data/ca) deve essere un volume/percorso
che sopravvive ai riavvii del container - altrimenti ogni riavvio genera
una CA diversa e tutti i certificati già distribuiti ai client smettono
di funzionare.
"""
import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CA_DIR = Path(os.environ.get("CA_DIR", "/data/ca"))
CA_CERT_PATH = CA_DIR / "ca.pem"
CA_KEY_PATH = CA_DIR / "ca-key.pem"


def _generate_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_pem_private_key(path: Path, key):
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    os.chmod(path, 0o600)


def _write_pem_cert(path: Path, cert):
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def ensure_ca_exists() -> None:
    """Genera la CA se non esiste già. Idempotente: se esiste, non tocca nulla."""
    if CA_CERT_PATH.exists() and CA_KEY_PATH.exists():
        return

    CA_DIR.mkdir(parents=True, exist_ok=True)
    key = _generate_key()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LogPlatform"),
        x509.NameAttribute(NameOID.COMMON_NAME, "LogPlatform Internal CA"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(key, hashes.SHA256())
    )
    _write_pem_private_key(CA_KEY_PATH, key)
    _write_pem_cert(CA_CERT_PATH, cert)


def get_ca_cert_pem() -> str:
    ensure_ca_exists()
    return CA_CERT_PATH.read_text()


def _load_ca():
    ensure_ca_exists()
    ca_key = serialization.load_pem_private_key(CA_KEY_PATH.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(CA_CERT_PATH.read_bytes())
    return ca_key, ca_cert


def issue_certificate(common_name: str, is_server: bool = False,
                       dns_names: list | None = None) -> tuple[str, str]:
    """Emette un certificato (client o server) firmato dalla CA interna.
    Ritorna (cert_pem, key_pem). Non salva nulla su disco: chi chiama
    decide cosa farne (es. includerlo in un pacchetto agent, o scriverlo
    su un percorso da montare in un altro modulo)."""
    ca_key, ca_cert = _load_ca()

    key = _generate_key()
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LogPlatform"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=825))
    )

    if is_server:
        san_entries = [x509.DNSName(name) for name in (dns_names or [common_name])]
        builder = builder.add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    else:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)

    cert = builder.sign(ca_key, hashes.SHA256())

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem
