"""Screenshot PII sanitizer — blur sensitive regions before checkpoint write.

Decided in todo 045 (2026-04-17): per-application checkpoint screenshots at
``data/applications/{draft_id}/checkpoints/*.png`` contain the candidate's
legal name, address, phone, and email. ``.gitignore`` keeps them out of git,
but they sit on disk in cleartext. Every checkpoint screenshot must pass
through :func:`sanitize` before it is written; ``check-integrity`` flags any
checkpoint PNG missing the ``sanitized_at`` provenance tag.

Detection (:func:`is_sanitized_png` / :func:`sanitized_at_tag`) is
stdlib-only so ``check-integrity`` carries no image-library dependency.
Only the blur itself (:func:`sanitize`) needs Pillow, which is a declared
install-time dependency in ``pyproject.toml``.
"""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass
from typing import Final

from .utils import StructuredError, now_iso

SANITIZED_TAG: Final = "sanitized_at"
DEFAULT_BLUR_RADIUS: Final = 25

SCREENSHOT_SANITIZER_ERROR_CODES: Final = frozenset({
    "pillow_missing",
    "invalid_image",
    "invalid_region",
})


class ScreenshotSanitizerError(StructuredError):
    """Structured error for screenshot-sanitization failures."""

    ALLOWED_ERROR_CODES = SCREENSHOT_SANITIZER_ERROR_CODES


@dataclass(frozen=True)
class BoundingBox:
    """Pixel box in PIL crop convention: ``(left, top, right, bottom)`` with
    right/bottom exclusive. ``left < right``, ``top < bottom``, all ``>= 0``.
    """

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        for name, value in (
            ("left", self.left), ("top", self.top),
            ("right", self.right), ("bottom", self.bottom),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ScreenshotSanitizerError(
                    f"BoundingBox.{name} must be a non-negative int, got {value!r}",
                    error_code="invalid_region",
                )
        if self.right <= self.left or self.bottom <= self.top:
            raise ScreenshotSanitizerError(
                f"BoundingBox needs left<right and top<bottom, got {self}",
                error_code="invalid_region",
            )

    @classmethod
    def from_iterable(cls, seq) -> "BoundingBox":
        try:
            left, top, right, bottom = (int(x) for x in seq)
        except (TypeError, ValueError) as exc:
            raise ScreenshotSanitizerError(
                f"Region must be 4 ints [left,top,right,bottom], got {seq!r}",
                error_code="invalid_region",
            ) from exc
        return cls(left, top, right, bottom)


def _pil_or_raise():
    try:
        from PIL import Image, ImageFilter
        from PIL.PngImagePlugin import PngInfo
    except ImportError as exc:
        raise ScreenshotSanitizerError(
            "Pillow is required for screenshot sanitization but is not "
            "installed (declared in pyproject.toml as Pillow>=10.0).",
            error_code="pillow_missing",
            remediation="pip install 'Pillow>=10.0'",
        ) from exc
    return Image, ImageFilter, PngInfo


def sanitize(image_bytes: bytes, regions: list[BoundingBox]) -> bytes:
    """Gaussian-blur each region and stamp a ``sanitized_at`` PNG text chunk.

    Regions are clamped to the image bounds — an out-of-bounds box from a
    flaky selector blurs the in-bounds part rather than raising. The returned
    PNG always carries the ``sanitized_at`` tag, even when ``regions`` is
    empty: the tag asserts "this screenshot went through the sanitizer",
    which is exactly the invariant ``check-integrity`` verifies. A screenshot
    with no detected PII still must be tagged, or the integrity check cannot
    distinguish "no PII" from "sanitizer skipped".
    """
    Image, ImageFilter, PngInfo = _pil_or_raise()
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as exc:  # PIL raises many error types on bad input
        raise ScreenshotSanitizerError(
            f"Could not decode screenshot image: {exc}",
            error_code="invalid_image",
        ) from exc
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    width, height = img.size
    for region in regions:
        left = max(0, min(region.left, width))
        top = max(0, min(region.top, height))
        right = max(0, min(region.right, width))
        bottom = max(0, min(region.bottom, height))
        if right <= left or bottom <= top:
            continue
        box = (left, top, right, bottom)
        blurred = img.crop(box).filter(
            ImageFilter.GaussianBlur(radius=DEFAULT_BLUR_RADIUS)
        )
        img.paste(blurred, box)
    meta = PngInfo()
    meta.add_text(SANITIZED_TAG, now_iso())
    out = io.BytesIO()
    img.save(out, format="PNG", pnginfo=meta)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Stdlib-only PNG text-chunk detection (no Pillow needed)
# ---------------------------------------------------------------------------

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"


def _iter_png_text_chunks(data: bytes):
    """Yield ``(chunk_type, body)`` for every tEXt/zTXt/iTXt chunk.

    Tolerant of truncation and does not validate CRCs — detection only needs
    to read keywords, and a corrupt-CRC tag is still a tag we wrote.
    """
    if not data.startswith(_PNG_SIGNATURE):
        return
    pos = len(_PNG_SIGNATURE)
    total = len(data)
    while pos + 8 <= total:
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        start = pos + 8
        end = start + length
        if end > total:
            return
        if ctype in (b"tEXt", b"zTXt", b"iTXt"):
            yield ctype, data[start:end]
        if ctype == b"IEND":
            return
        pos = end + 4  # skip the 4-byte CRC


def _decode_text_chunk(ctype: bytes, body: bytes) -> tuple[str, str] | None:
    try:
        if ctype == b"tEXt":
            keyword, _, text = body.partition(b"\x00")
            return keyword.decode("latin-1"), text.decode("latin-1", "replace")
        if ctype == b"zTXt":
            keyword, _, rest = body.partition(b"\x00")
            if not rest:
                return None
            if rest[0] != 0:  # unknown compression method
                return keyword.decode("latin-1"), ""
            return (
                keyword.decode("latin-1"),
                zlib.decompress(rest[1:]).decode("latin-1", "replace"),
            )
        if ctype == b"iTXt":
            keyword, _, rest = body.partition(b"\x00")
            if len(rest) < 2:
                return None
            comp_flag, comp_method = rest[0], rest[1]
            rest = rest[2:]
            _lang, _, rest = rest.partition(b"\x00")
            _translated, _, text_bytes = rest.partition(b"\x00")
            if comp_flag == 1 and comp_method == 0:
                text_bytes = zlib.decompress(text_bytes)
            return keyword.decode("latin-1"), text_bytes.decode("utf-8", "replace")
    except (zlib.error, UnicodeDecodeError, IndexError):
        return None
    return None


def sanitized_at_tag(image_bytes: bytes) -> str | None:
    """Return the ``sanitized_at`` timestamp embedded in the PNG, or None."""
    for ctype, body in _iter_png_text_chunks(image_bytes):
        decoded = _decode_text_chunk(ctype, body)
        if decoded is not None and decoded[0] == SANITIZED_TAG:
            return decoded[1]
    return None


def is_sanitized_png(image_bytes: bytes) -> bool:
    """True iff the PNG carries the ``sanitized_at`` provenance tag."""
    return sanitized_at_tag(image_bytes) is not None
