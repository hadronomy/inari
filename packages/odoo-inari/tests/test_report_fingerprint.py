import base64
from datetime import datetime
import json
from pathlib import Path

from inari_print_contracts.fingerprint import (
    DeviceWorkFingerprintInput,
    fingerprint_device_work,
)


def test_report_fingerprint_matches_controller_fixture():
    fixture_path = (
        Path(__file__).parents[3]
        / "crates/inari-gateway/tests/fixtures/device-work-fingerprint.json"
    )
    fixture = json.loads(fixture_path.read_text())
    work = fixture["work"]
    fingerprint = fingerprint_device_work(
        DeviceWorkFingerprintInput(
            contract_major=work["contract_major"],
            operation=work["document"]["operation"],
            device_id=work["device_id"],
            media_type="application/pdf",
            document=base64.b64decode(work["document"]["content_base64"], validate=True),
            options=work["normalized_device_options"],
            expires_at=datetime.fromisoformat(fixture["expires_at"]),
        )
    )
    assert fingerprint.hex() == fixture["payload_fingerprint"]
