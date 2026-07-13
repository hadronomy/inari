from __future__ import annotations

import pytest

from inari_tray.main import _parse_args
from inari_tray.single_instance import ActivationRequest


def test_direct_invitation_argument_is_preserved() -> None:
    invitation = "inari://enroll?invite_id=inv_example#code=secret"

    arguments = _parse_args([invitation])

    assert arguments.invitation == invitation


def test_activation_request_rejects_non_inari_links() -> None:
    with pytest.raises(ValueError, match="inari://enroll"):
        ActivationRequest(invitation="https://controller.example/setup")
