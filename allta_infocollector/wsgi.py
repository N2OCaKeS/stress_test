import certifi
import requests
import os
import ssl
import socket

from pathlib import Path

from src.main import app



def trust_api_cert_for_requests(host: str = "allta.devos.astralinux.ru", port: int = 21500) -> None:
    certs_dir = Path.home() / ".config" / "allta" / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)
    cert_file = certs_dir / "allta-api.crt"
    bundle_file = certs_dir / "certifi-allta-bundle.pem"

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert_der = tls.getpeercert(binary_form=True)
        if not cert_der:
            raise RuntimeError("empty peer cert")
        cert_pem = ssl.DER_cert_to_PEM_cert(cert_der).strip() + "\n"
        cert_file.write_text(cert_pem, encoding="utf-8")
    except Exception:
        if not cert_file.exists():
            raise
        cert_pem = cert_file.read_text(encoding="utf-8")

    base = Path(certifi.where()).read_text(encoding="utf-8")
    if not base.endswith("\n"):
        base += "\n"
    bundle_file.write_text(base + cert_pem, encoding="utf-8")

    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle_file)
    os.environ["SSL_CERT_FILE"] = str(bundle_file)




if __name__ == "__main__":
    trust_api_cert_for_requests()
    print(requests.get("https://allta.devos.astralinux.ru:21501/api/server/health").status_code)
    app.run()