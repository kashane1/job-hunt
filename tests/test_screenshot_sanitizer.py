from __future__ import annotations

import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.screenshot_sanitizer import (  # noqa: E402
    SANITIZED_TAG,
    BoundingBox,
    ScreenshotSanitizerError,
    is_sanitized_png,
    sanitize,
    sanitized_at_tag,
)
from job_hunt.tracking import check_integrity  # noqa: E402

try:
    from PIL import Image  # noqa: F401

    _PIL = True
except ImportError:
    _PIL = False


def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)


def _fake_png(text_chunks: list[tuple[bytes, bytes]] = ()) -> bytes:
    """Hand-build a minimal valid-enough PNG (stdlib only, no Pillow)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    text = b"".join(_png_chunk(t, d) for t, d in text_chunks)
    idat = _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = _png_chunk(b"IEND", b"")
    return sig + ihdr + text + idat + iend


class DetectionStdlibTest(unittest.TestCase):
    """Detection is stdlib-only — must work with no Pillow installed."""

    def test_tagged_png_detected(self) -> None:
        png = _fake_png([(b"tEXt", b"sanitized_at\x002026-05-18T12:00:00Z")])
        self.assertTrue(is_sanitized_png(png))
        self.assertEqual(sanitized_at_tag(png), "2026-05-18T12:00:00Z")

    def test_untagged_png_not_detected(self) -> None:
        self.assertFalse(is_sanitized_png(_fake_png()))
        self.assertFalse(
            is_sanitized_png(_fake_png([(b"tEXt", b"Software\x00made with X")]))
        )

    def test_non_png_bytes_not_detected(self) -> None:
        self.assertFalse(is_sanitized_png(b"definitely not a png"))
        self.assertFalse(is_sanitized_png(b""))

    def test_itxt_chunk_decoded(self) -> None:
        body = b"sanitized_at\x00\x00\x00\x00\x002026-05-18T01:02:03Z"
        png = _fake_png([(b"iTXt", body)])
        self.assertEqual(sanitized_at_tag(png), "2026-05-18T01:02:03Z")


class BoundingBoxValidationTest(unittest.TestCase):
    def test_valid_box(self) -> None:
        box = BoundingBox(1, 2, 30, 40)
        self.assertEqual((box.left, box.top, box.right, box.bottom), (1, 2, 30, 40))

    def test_inverted_box_rejected(self) -> None:
        with self.assertRaises(ScreenshotSanitizerError) as ctx:
            BoundingBox(10, 0, 5, 20)
        self.assertEqual(ctx.exception.error_code, "invalid_region")

    def test_negative_rejected(self) -> None:
        with self.assertRaises(ScreenshotSanitizerError) as ctx:
            BoundingBox(-1, 0, 5, 5)
        self.assertEqual(ctx.exception.error_code, "invalid_region")

    def test_from_iterable_bad_input(self) -> None:
        with self.assertRaises(ScreenshotSanitizerError) as ctx:
            BoundingBox.from_iterable([1, 2, 3])
        self.assertEqual(ctx.exception.error_code, "invalid_region")


class CheckIntegritySurfacingTest(unittest.TestCase):
    """check-integrity flags checkpoint PNGs missing the sanitized_at tag.
    Stdlib-only — does not require Pillow."""

    def _draft(self, root: Path, name: str) -> Path:
        d = root / "applications" / name
        (d / "checkpoints").mkdir(parents=True)
        (d / "plan.json").write_text(json.dumps({"draft_id": name}), encoding="utf-8")
        return d

    def test_unsanitized_png_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self._draft(root, "draft-a")
            (draft / "checkpoints" / "pre_submit.png").write_bytes(_fake_png())
            report = check_integrity(root)
            flagged = report["unsanitized_checkpoint_screenshots"]
            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["draft_id"], "draft-a")
            self.assertTrue(report["summary"]["has_issues"])
            self.assertEqual(
                report["summary"]["issue_counts"]["unsanitized_checkpoint_screenshots"],
                1,
            )

    def test_sanitized_png_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self._draft(root, "draft-b")
            tagged = _fake_png([(b"tEXt", b"sanitized_at\x002026-05-18T00:00:00Z")])
            (draft / "checkpoints" / "post_submit.png").write_bytes(tagged)
            report = check_integrity(root)
            self.assertEqual(report["unsanitized_checkpoint_screenshots"], [])


@unittest.skipUnless(_PIL, "Pillow not installed (declared dep; pixel test skipped)")
class SanitizePixelTest(unittest.TestCase):
    def _striped(self) -> bytes:
        """120x80 white image with 1px black vertical stripes in a region —
        high horizontal-gradient energy that Gaussian blur collapses."""
        from PIL import Image

        img = Image.new("RGB", (120, 80), (255, 255, 255))
        px = img.load()
        for x in range(10, 80, 2):
            for y in range(10, 60):
                px[x, y] = (0, 0, 0)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _h_energy(img, box) -> int:
        px = img.convert("RGB").load()
        left, top, right, bottom = box
        total = 0
        for y in range(top, bottom):
            for x in range(left, right - 1):
                total += abs(px[x, y][0] - px[x + 1, y][0])
        return total

    def test_region_blurred_and_tagged(self) -> None:
        from PIL import Image

        raw = self._striped()
        box = (10, 10, 80, 60)
        original = Image.open(io.BytesIO(raw))
        pre = self._h_energy(original, box)

        out = sanitize(raw, [BoundingBox(*box)])

        self.assertTrue(is_sanitized_png(out))
        tag = sanitized_at_tag(out)
        self.assertIsNotNone(tag)
        self.assertIn("T", tag)  # ISO-8601 shape

        sanitized_img = Image.open(io.BytesIO(out))
        post = self._h_energy(sanitized_img, box)
        # Blur radius 25 over a 2px stripe pattern collapses the gradient.
        self.assertLess(post, pre * 0.25)

        # A pixel well outside the region is untouched.
        self.assertEqual(
            sanitized_img.convert("RGB").load()[110, 70], (255, 255, 255)
        )

    def test_empty_regions_still_tagged(self) -> None:
        out = sanitize(self._striped(), [])
        self.assertTrue(is_sanitized_png(out))

    def test_invalid_image_raises(self) -> None:
        with self.assertRaises(ScreenshotSanitizerError) as ctx:
            sanitize(b"not an image", [])
        self.assertEqual(ctx.exception.error_code, "invalid_image")

    def test_cli_round_trip(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            tmpd = Path(tmp)
            raw_path = tmpd / "raw.png"
            out_path = tmpd / "checkpoints" / "pre_submit.png"
            raw_path.write_bytes(self._striped())
            proc = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / "job_hunt.py"),
                    "sanitize-screenshot",
                    "--input", str(raw_path),
                    "--output", str(out_path),
                    "--regions", "[[10,10,80,60]]",
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["regions_count"], 1)
            self.assertTrue(out_path.exists())
            self.assertTrue(is_sanitized_png(out_path.read_bytes()))


if __name__ == "__main__":
    unittest.main()
