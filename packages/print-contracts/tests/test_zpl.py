from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from inari_print_contracts import (
    LabelContractError,
    ZplLayout,
    escape_field,
    split_labels,
)

LAYOUT = ZplLayout("test_4x6", 812, 1218)


def label(value="Inari"):
    return f"^XA^CI28^FO10,10^A0N,20,10^FH_^FD{value}^FS^XZ".encode()


def test_split_keeps_exact_envelopes_and_source_order():
    first, second = label("First"), label("Second")
    assert split_labels(b"\n" + first + b"\r\n" + second + b"\t", LAYOUT) == (
        first,
        second,
    )


@given(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cc", "Cf", "Cs")),
        min_size=1,
        max_size=30,
    )
)
def test_escaping_cannot_create_an_extra_label(value):
    content = label(escape_field(value))
    assert split_labels(content, LAYOUT) == (content,)


def test_command_prefixes_and_hex_indicator_are_escaped_canonically():
    assert escape_field("^XZ~JA_41") == "_5EXZ_7EJA_5F41"
    assert split_labels(label(escape_field("^XZ~JA_41")), LAYOUT)


@pytest.mark.parametrize(
    "field", ["_41", "_5e", "_5", "_ZZ", "~JA", "^XZ^XA", "line\nbreak", "\u202e"]
)
def test_noncanonical_field_data_fails_without_repair(field):
    with pytest.raises(LabelContractError):
        split_labels(label(field), LAYOUT)


@pytest.mark.parametrize(
    "command",
    [
        "^PQ2",
        "~JA",
        "^CC!",
        "^PW800",
        "^LL1000",
        "^DFR:label",
        "^RF",
        "^JUS",
        "^GB10,10,2",
        "^BQN,2,3",
    ],
)
def test_commands_outside_contract_major_one_fail(command):
    with pytest.raises(LabelContractError):
        split_labels(label().replace(b"^FO", command.encode() + b"^FO"), LAYOUT)


def test_utf8_does_not_allow_truncation_or_control_replacement():
    assert split_labels(label(escape_field("Café")), LAYOUT)
    with pytest.raises(LabelContractError):
        split_labels(label() + b"\xff", LAYOUT)
    with pytest.raises(LabelContractError):
        escape_field("SKU\x00A")


def test_geometry_and_quantity_are_checked_before_any_envelope_is_returned():
    with pytest.raises(LabelContractError):
        split_labels(label() + label().replace(b"^FO10,10", b"^FO800,10"), LAYOUT)
    with pytest.raises(LabelContractError):
        split_labels(label() * 501, LAYOUT)


def test_data_matrix_escape_preserves_literal_backslashes():
    escaped = escape_field(r"SKU\01", data_matrix=True)
    content = f"^XA^CI28^FO10,10^BXN,2,200,16,16,6,\\^FH_^FD{escaped}^FS^XZ".encode()
    assert split_labels(content, LAYOUT)
    with pytest.raises(LabelContractError):
        split_labels(content.replace(b"SKU\\\\01", b"SKU\\01"), LAYOUT)


def test_data_matrix_capacity_accounts_for_error_correction():
    content = b"^XA^CI28^FO10,10^BXN,2,200,10,10,6,\\^FH_^FDAB^FS^XZ"
    with pytest.raises(LabelContractError, match="geometry"):
        split_labels(content, LAYOUT)


@pytest.mark.parametrize(
    "change",
    [
        {"dpi": True},
        {"width_dots": 813},
        {"height_dots": 1219},
        {"profile_id": 123},
        {"max_fields": 129},
    ],
)
def test_layout_rejects_values_outside_the_contract(change):
    values = {"profile_id": "test", "width_dots": 812, "height_dots": 1218, **change}
    with pytest.raises(LabelContractError):
        ZplLayout(**values)
