from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass


class ReportRequestError(ValueError):
    pass


@dataclass(frozen=True)
class ReportRequest:
    binding_id: int
    binding_revision_id: int
    report_action_id: int
    source_model: str
    source_ids: tuple[int, ...]
    site_id: int
    report_type: str
    copies: int


_FIELDS = frozenset(
    {
        "version",
        "binding_id",
        "binding_revision_id",
        "report_action_id",
        "source_model",
        "source_ids",
        "site_id",
        "report_type",
        "copies",
        "wizard_data",
    }
)


def _integer(value: object, maximum: int = 2**31 - 1) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ReportRequestError("The report request contains an invalid integer.")
    return value


def parse_report_request(value: object) -> ReportRequest:
    if not isinstance(value, dict) or value.keys() != _FIELDS:
        raise ReportRequestError("The report request fields do not match version 1.")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ReportRequestError("The report request version is not supported.")
    source_model = value["source_model"]
    if (
        not isinstance(source_model, str)
        or not source_model.isascii()
        or len(source_model) > 128
        or not source_model
        or any(
            not (part.isidentifier() and not part.startswith("_"))
            for part in source_model.split(".")
        )
    ):
        raise ReportRequestError("The report source model is invalid.")
    source_ids = value["source_ids"]
    if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 500:
        raise ReportRequestError("A report requires between 1 and 500 source records.")
    ordered_ids = tuple(_integer(record_id) for record_id in source_ids)
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ReportRequestError("The report source records contain duplicates.")
    report_type = value["report_type"]
    if report_type not in ("qweb-pdf", "qweb-text"):
        raise ReportRequestError("The report type is not supported.")
    # Report-specific wizard contracts enter through the trusted producer.
    if type(value["wizard_data"]) is not dict or value["wizard_data"]:
        raise ReportRequestError("This report request does not accept wizard data.")
    return ReportRequest(
        binding_id=_integer(value["binding_id"]),
        binding_revision_id=_integer(value["binding_revision_id"]),
        report_action_id=_integer(value["report_action_id"]),
        source_model=source_model,
        source_ids=ordered_ids,
        site_id=_integer(value["site_id"]),
        report_type=report_type,
        copies=_integer(value["copies"], 10),
    )


def check_report_action(request: ReportRequest, action: Mapping[str, object]) -> None:
    context = action.get("context")
    if isinstance(context, str):
        try:
            context = ast.literal_eval(context)
        except (ValueError, TypeError, SyntaxError, RecursionError):
            context = None
    if (
        action.get("type") != "ir.actions.client"
        or action.get("tag") != "inari_report_print"
        or not isinstance(context, dict)
        or context.get("inari_managed_report") is not True
        or type(context.get("inari_report_binding_id")) is not int
        or context["inari_report_binding_id"] != request.binding_id
        or type(context.get("inari_binding_revision_id")) is not int
        or context["inari_binding_revision_id"] != request.binding_revision_id
    ):
        raise ReportRequestError("The report action does not match its Report Binding.")
