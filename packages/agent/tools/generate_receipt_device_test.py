# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==12.2.0", "qrcode==8.2", "python-barcode==0.16.1"]
# ///
"""Generate the fixed 576-dot receipt pattern. Run with `uv run --script`."""

from pathlib import Path

from barcode import Code128
from PIL import Image, ImageDraw, ImageFont
from qrcode import QRCode
from qrcode.constants import ERROR_CORRECT_M


ROOT = Path(__file__).resolve().parents[3]
FONTS = ROOT / "packages/brand/inari_brand/assets/fonts"
DESTINATION = ROOT / "packages/agent/inari/device_tests/receipt_v1.jpg"
CODE = "INARI-TEST-V1"


def main() -> None:
    image = Image.new("L", (576, 1056), 255)
    draw = ImageDraw.Draw(image)
    regular = FONTS / "atkinson-hyperlegible-next-regular.otf"
    semibold = FONTS / "atkinson-hyperlegible-next-semibold.otf"

    def text(value: str, y: int, size: int = 26, *, bold: bool = False) -> None:
        font = ImageFont.truetype(semibold if bold else regular, size)
        box = draw.textbbox((0, 0), value, font=font)
        if box[2] - box[0] > 512:
            raise ValueError(f"The pattern text exceeds its margin: {value}")
        draw.text((288, y), value, font=font, anchor="mt", fill=0)

    text("INARI · DEVICE TEST", 32, 38, bold=True)
    text("Prueba de dispositivo", 92, 30)
    text("Standard pattern · receipt-v1", 144)
    draw.line((32, 199, 543, 199), fill=0, width=2)
    text("Text and accents", 225, 28, bold=True)
    text("Á É Í Ó Ú · á é í ó ú · Ñ ñ · Ü ü", 272)
    text("0123456789 · € 12,34 · % + - /", 316)
    text("Code 128", 378, 28, bold=True)

    bits = Code128(CODE).build()[0]
    left = (576 - len(bits) * 2) // 2
    if left < 32:
        raise ValueError("The barcode exceeds its quiet zone.")
    for position, bit in enumerate(bits):
        if bit == "1":
            draw.rectangle(
                (left + position * 2, 428, left + position * 2 + 1, 507), fill=0
            )
    text(CODE, 530, 24)
    text("QR", 591, 28, bold=True)
    qr = QRCode(box_size=6, border=4, error_correction=ERROR_CORRECT_M)
    qr.add_data(CODE)
    qr.make(fit=True)
    qr_image = qr.make_image().convert("L")
    image.paste(qr_image, ((576 - qr_image.width) // 2, 632))
    text(CODE, 856, 24)
    draw.line((32, 905, 543, 905), fill=0, width=2)
    text("Check text, both codes, feed and cut.", 934, 24)
    text("No sale · Sin venta", 989, 26, bold=True)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    image.save(DESTINATION, format="JPEG", quality=95, subsampling=0, optimize=False)
    print(DESTINATION)


if __name__ == "__main__":
    main()
