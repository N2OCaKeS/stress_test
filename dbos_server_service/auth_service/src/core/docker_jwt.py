"""Docker registry JWT helpers (RS256 preferred, HS256 fallback for dev).

Setup for production:
  1. Generate key pair:
       openssl genrsa -out docker_signing_key.pem 2048
       openssl rsa -in docker_signing_key.pem -pubout -out docker_signing_key.pub
  2. Set DOCKER_RSA_PRIVATE_KEY env var (contents of .pem file, with \\n escaped).
  3. Configure Docker registry (config.yml):
       auth:
         token:
           realm:   https://<host>/api/auth/v1/docker/token
           service: <DOCKER_REGISTRY_SERVICE>
           issuer:  <DOCKER_REGISTRY_ISSUER>
           rootcertbundle: /path/to/docker_signing_key.pub
  4. Fetch the public key from /api/auth/v1/docker/certs (PEM) or JWKS endpoint.
"""

import base64
import datetime
import hashlib
from functools import lru_cache

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.core.config import get_settings


@lru_cache(maxsize=1)
def _get_rsa_private_key():
    """Load RSA private key from config, or generate a temporary one for dev."""
    settings = get_settings()
    if settings.docker_rsa_private_key:
        pem = settings.docker_rsa_private_key.encode()
        return serialization.load_pem_private_key(pem, password=None)
    # Dev fallback: ephemeral key (regenerated on each restart — not for production)
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _get_rsa_public_key():
    return _get_rsa_private_key().public_key()


def _key_id(public_key) -> str:
    """Compute a stable kid from the public key DER hash (first 12 hex chars)."""
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()[:12]


def sign_docker_token(payload: dict) -> str:
    """Sign a Docker JWT with RS256 (or HS256 in dev without a configured key)."""
    settings = get_settings()
    private_key = _get_rsa_private_key()
    kid = _key_id(_get_rsa_public_key())

    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


@lru_cache(maxsize=1)
def get_public_key_pem() -> str:
    """Return a self-signed X.509 certificate in PEM format.

    Docker registry rootcertbundle requires a certificate, not a bare public key.
    The cert is derived from the same RSA key used to sign tokens, so the registry
    can extract and use the public key for JWT verification.
    """
    private_key = _get_rsa_private_key()
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "auth_service")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def get_jwks() -> dict:
    """Return JWKS representation of the public key for token verification."""
    pub = _get_rsa_public_key()
    pub_numbers = pub.public_numbers()
    n_bytes = pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, "big")
    e_bytes = pub_numbers.e.to_bytes((pub_numbers.e.bit_length() + 7) // 8, "big")

    kid = _key_id(pub)
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode(),
                "e": base64.urlsafe_b64encode(e_bytes).rstrip(b"=").decode(),
            }
        ]
    }
