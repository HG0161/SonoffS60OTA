import unittest

from tools.safeboot_migration import (
    PINNED_ARTIFACTS,
    PUBLISHED_BLUETOOTH_FILE,
    PUBLISHED_BLUETOOTH_SOURCE_COMMIT,
    sha256_bytes,
    validate_native_image,
)


class PublishedArtifactTests(unittest.TestCase):
    def test_bluetooth_image_is_the_reviewed_upstream_build(self):
        image = PUBLISHED_BLUETOOTH_FILE.read_bytes()
        pinned = PINNED_ARTIFACTS["bluetooth"]

        self.assertEqual(len(image), pinned["size"])
        self.assertEqual(sha256_bytes(image), pinned["sha256"])
        report = validate_native_image(image, 0x1F0000, "published Bluetooth")
        self.assertTrue(report["chip_is_esp32_c3"])
        self.assertIn(b"bluetooth", image)
        self.assertIn(
            PUBLISHED_BLUETOOTH_SOURCE_COMMIT[:7].encode("ascii"),
            image,
        )


if __name__ == "__main__":
    unittest.main()
