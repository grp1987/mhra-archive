"""
Generate a self-signed TLS cert (cert.pem + key.pem) so the VPS can serve HTTPS
on a bare IP. Browsers show a one-time "not trusted" warning (expected for a
self-signed cert), but the connection — and the passcode — is encrypted.

    python gen_cert.py [<ip-or-hostname>]

Re-run to regenerate. cert.pem/key.pem are gitignored.
"""
import datetime
import ipaddress
import os
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

HERE = os.path.dirname(os.path.abspath(__file__))


def main(host):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    san = []
    try:
        san.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        san.append(x509.DNSName(host))
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(os.path.join(HERE, "key.pem"), "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(os.path.join(HERE, "cert.pem"), "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"wrote cert.pem + key.pem for '{host}' (valid 10 years)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "localhost")
