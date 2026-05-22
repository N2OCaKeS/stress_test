"""Docker registry JWT (RS256 предпочтительно, HS256 fallback только для dev).

Настройка для production:
  1. Сгенерить пару ключей:
       openssl genrsa -out docker_signing_key.pem 2048
       openssl rsa -in docker_signing_key.pem -pubout -out docker_signing_key.pub
  2. Выставить ENV `DOCKER_RSA_PRIVATE_KEY` (содержимое .pem, `\\n` escape'нуть).
  3. Настроить Docker registry (config.yml):
       auth:
         token:
           realm:   https://<host>/api/auth/v1/docker/token
           service: <DOCKER_REGISTRY_SERVICE>
           issuer:  <DOCKER_REGISTRY_ISSUER>
           rootcertbundle: /path/to/docker_signing_key.pub
  4. Public key можно забрать с `/api/auth/v1/docker/certs` (PEM) или JWKS.
"""

import base64
import datetime
import hashlib
import logging
from functools import lru_cache

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_rsa_private_key():
    """RSA private key из конфига; для dev — эфемерный (regenerated на каждом restart).

    Production guard в `Settings._validate_production_secrets` падает, если
    `DOCKER_RSA_PRIVATE_KEY` не задан в проде. Здесь fallback срабатывает
    только вне production, но логи кричат операторам, что токены не
    переживут рестарт сервиса.
    """
    settings = get_settings()
    if settings.docker_rsa_private_key:
        pem = settings.docker_rsa_private_key.encode()
        return serialization.load_pem_private_key(pem, password=None)
    logger.warning(
        "Generated ephemeral RSA key for docker_jwt; tokens will not survive restart. "
        "Set DOCKER_RSA_PRIVATE_KEY for production."
    )
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _get_rsa_public_key():
    """Public key пары — берём из приватного, чтобы не дублировать загрузку."""
    return _get_rsa_private_key().public_key()


def _key_id(public_key) -> str:
    """Стабильный `kid` из SHA-256(DER public key), первые 12 hex-символов."""
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()[:12]


def sign_docker_token(payload: dict) -> str:
    """Подписать Docker JWT через RS256 (HS256 fallback в dev — Docker registry его не поймёт)."""
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
    """Self-signed X.509 cert в PEM (cached на весь lifecycle процесса).

    Docker registry rootcertbundle требует certificate, не голый public key.
    Cert строится из той же RSA-пары, что и подпись токенов, чтобы registry
    мог извлечь public key и проверить JWT.
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
    """JWKS-представление public key (для JWT-aware клиентов, валидирующих токены)."""
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
