import unittest

from tools.activate_vendor_ota_probe import validate_manifest_target
from tools.download_ota import DownloadError


class VendorActivationProbeTests(unittest.TestCase):
    def test_accepts_only_exact_tested_target(self):
        files = [
            {
                "name": "user1.bin",
                "downloadUrl": "http://52.57.99.135:8088/ota/rom/opaque/user1.bin",
            }
        ]
        validate_manifest_target(files, "52.57.99.135", 8088)

    def test_rejects_any_unblocked_target(self):
        files = [
            {
                "name": "user1.bin",
                "downloadUrl": "http://52.57.99.136:8088/ota/rom/opaque/user1.bin",
            }
        ]
        with self.assertRaises(DownloadError):
            validate_manifest_target(files, "52.57.99.135", 8088)


if __name__ == "__main__":
    unittest.main()
