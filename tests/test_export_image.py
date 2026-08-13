from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path


class ExportImageTest(unittest.TestCase):
    def test_rejects_an_input_larger_than_the_private_bucket_limit(self):
        from scripts.export_approved import ExportError, MAX_IMAGE_BYTES, convert_cover_to_webp

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ExportError, "2 MiB"):
                convert_cover_to_webp(b"x" * (MAX_IMAGE_BYTES + 1), Path(directory) / "cover.webp")

    def test_converts_to_bounded_webp_without_metadata(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is installed by the exporter workflow")

        from scripts.export_approved import convert_cover_to_webp

        source = io.BytesIO()
        exif = Image.Exif()
        exif[0x010E] = "PRIVATE_SENTINEL"
        Image.new("RGB", (2400, 1600), (143, 105, 86)).save(
            source,
            format="JPEG",
            exif=exif,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cover.webp"
            convert_cover_to_webp(source.getvalue(), output)
            with Image.open(output) as converted:
                self.assertLessEqual(max(converted.size), 1200)
                self.assertEqual("WEBP", converted.format)
                self.assertEqual({}, dict(converted.getexif()))


if __name__ == "__main__":
    unittest.main()
