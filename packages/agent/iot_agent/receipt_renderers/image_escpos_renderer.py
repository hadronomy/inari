from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, ImageOps

from ..exceptions import PrinterServiceError
from ..printers import CutMode, EscPosCommands


@dataclass(slots=True, frozen=True)
class EscPosImageReceiptRendererConfig:
    max_width: int = 576
    threshold: int = 180
    dither: Literal["threshold", "floyd-steinberg"] = "threshold"
    trailing_feed_lines: int = 3
    cut_mode: CutMode | None = CutMode.PARTIAL


class EscPosImageReceiptRenderer:
    def __init__(self, config: EscPosImageReceiptRendererConfig | None = None) -> None:
        self.config = config or EscPosImageReceiptRendererConfig()

    def render(self, image_bytes: bytes, *, mime_type: str | None = None) -> bytes:
        image = self._load_image(image_bytes, mime_type=mime_type)
        image = self._prepare_image(image)
        raster = self._encode_raster(self._to_monochrome(image))

        chunks = [EscPosCommands.INITIALIZE, raster]
        if self.config.trailing_feed_lines:
            chunks.append(EscPosCommands.feed_lines(self.config.trailing_feed_lines))
        if self.config.cut_mode is not None:
            chunks.append(EscPosCommands.cut(self.config.cut_mode))
        return b"".join(chunks)

    def _load_image(self, image_bytes: bytes, *, mime_type: str | None) -> Image.Image:
        try:
            with Image.open(BytesIO(image_bytes)) as source:
                image = ImageOps.exif_transpose(source)
                image.load()
                return image.copy()
        except Exception as exc:
            message = "Could not decode receipt image."
            if mime_type:
                message = f"Could not decode receipt image with mime type {mime_type!r}."
            raise PrinterServiceError("INVALID_RECEIPT_IMAGE", message) from exc

    def _prepare_image(self, image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            image = Image.alpha_composite(background, rgba)

        rgb = image.convert("RGB")
        if rgb.width <= self.config.max_width:
            return rgb

        ratio = self.config.max_width / rgb.width
        resized_height = max(1, int(rgb.height * ratio))
        return rgb.resize((self.config.max_width, resized_height), Image.Resampling.LANCZOS)

    def _to_monochrome(self, image: Image.Image) -> Image.Image:
        grayscale = image.convert("L")
        if self.config.dither == "floyd-steinberg":
            return grayscale.convert("1")

        threshold = self.config.threshold
        return grayscale.point(lambda pixel: 0 if pixel < threshold else 255, mode="1")

    @staticmethod
    def _encode_raster(image: Image.Image) -> bytes:
        if image.mode != "1":
            raise PrinterServiceError("INVALID_RECEIPT_IMAGE", "Receipt image must be converted to 1-bit mode.")

        width, height = image.size
        padded_width = ((width + 7) // 8) * 8
        if padded_width != width:
            padded = Image.new("1", (padded_width, height), 255)
            padded.paste(image, (0, 0))
            image = padded
            width = padded_width

        pixels = image.load()
        bytes_per_row = width // 8
        raster_bytes = bytearray()

        for y in range(height):
            for chunk_index in range(bytes_per_row):
                value = 0
                for bit in range(8):
                    x = chunk_index * 8 + bit
                    if pixels[x, y] == 0:
                        value |= 0x80 >> bit
                raster_bytes.append(value)

        header = b"\x1d\x76\x30\x00" + bytes(
            (
                bytes_per_row & 0xFF,
                (bytes_per_row >> 8) & 0xFF,
                height & 0xFF,
                (height >> 8) & 0xFF,
            )
        )
        return header + bytes(raster_bytes)
