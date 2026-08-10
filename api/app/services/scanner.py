"""Malware scanning. Files stay quarantined until a scan says clean.

- ClamdScanner streams bytes to a ClamAV daemon over TCP (INSTREAM, stdlib
  socket only). Production installations must use this backend; the compose
  file ships a clamav service.
- StubScanner is for development and tests: it flags the standard EICAR test
  signature and passes everything else. Production settings validation
  refuses it.

A scanner outage fails CLOSED: the file stays in quarantine with scan_state
"failed" and can be re-scanned; it is never served.
"""

import socket
import struct

from app.config import Settings

# The industry-standard antivirus test string (harmless by definition).
EICAR_SIGNATURE = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

_CHUNK = 64 * 1024


class ScanResult:
    def __init__(self, clean: bool, detail: str = "") -> None:
        self.clean = clean
        # Bounded, log-safe classification only — never file content.
        self.detail = detail[:200]


class ScannerUnavailable(Exception):
    pass


class StubScanner:
    backend_name = "stub"

    def scan_bytes(self, data: bytes) -> ScanResult:
        if EICAR_SIGNATURE in data:
            return ScanResult(False, "EICAR-Test-Signature")
        return ScanResult(True)

    def health(self) -> dict[str, str]:
        return {"backend": self.backend_name, "status": "ok (dev/test only)"}


class ClamdScanner:
    backend_name = "clamd"

    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def _command(self, payload: bytes | None, command: bytes) -> str:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as conn:
                conn.sendall(command)
                if payload is not None:
                    view = memoryview(payload)
                    for start in range(0, len(view), _CHUNK):
                        chunk = view[start : start + _CHUNK]
                        conn.sendall(struct.pack("!I", len(chunk)))
                        conn.sendall(chunk)
                    conn.sendall(struct.pack("!I", 0))
                response = b""
                while True:
                    part = conn.recv(4096)
                    if not part:
                        break
                    response += part
                return response.decode("utf-8", "replace").strip("\x00").strip()
        except OSError as error:
            raise ScannerUnavailable(type(error).__name__) from error

    def scan_bytes(self, data: bytes) -> ScanResult:
        response = self._command(data, b"zINSTREAM\x00")
        if response.endswith("OK"):
            return ScanResult(True)
        if "FOUND" in response:
            # e.g. "stream: Eicar-Signature FOUND" — keep the signature name only.
            name = response.split(":", 1)[-1].replace("FOUND", "").strip()
            return ScanResult(False, name or "malware")
        raise ScannerUnavailable(response[:100] or "unexpected clamd response")

    def health(self) -> dict[str, str]:
        try:
            response = self._command(None, b"zPING\x00")
            status = "ok" if "PONG" in response else f"unexpected: {response[:50]}"
        except ScannerUnavailable as error:
            status = f"error: {error}"
        return {"backend": self.backend_name, "status": status}


def build_scanner(settings: Settings) -> StubScanner | ClamdScanner:
    if settings.scanner_backend == "clamd":
        return ClamdScanner(settings.clamd_host, settings.clamd_port)
    return StubScanner()
