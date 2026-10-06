from hashlib import sha256
from importlib.resources import files

from ..printing.protocols import CutMode
from ..printing.renderers.image_escpos_renderer import EscPosImageReceiptRenderer


PATTERN_VERSION = "receipt-v1"
PATTERN_DIGEST = "b744c997b1de32da1f812287e476ec4c67eb5402cb851270457ea497c32fc57c"
CODE_VALUE = "INARI-TEST-V1"
REQUIRED_CHECKS = ("text", "accents", "code128", "qr", "feed_and_cut")


def receipt_image() -> bytes:
    image = files(__package__).joinpath("receipt_v1.jpg").read_bytes()
    if sha256(image).hexdigest() != PATTERN_DIGEST:
        raise RuntimeError("The standard Device Test pattern is invalid.")
    return image


def prepared_receipt(renderer: EscPosImageReceiptRenderer) -> bytes:
    config = renderer.config
    if (
        config.max_width != 576
        or config.cut_mode is not CutMode.PARTIAL
        or config.trailing_feed_lines < 1
    ):
        raise ValueError(
            "The receipt Device Test requires 576 dots, feed and partial cut."
        )
    return renderer.render(receipt_image(), mime_type="image/jpeg")
