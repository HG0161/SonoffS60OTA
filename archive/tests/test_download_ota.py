import unittest

from archive.tools.download_ota import DownloadError, ota_files


class DownloadOtaTests(unittest.TestCase):
    def test_selects_and_validates_manifest_files(self):
        digest = "a" * 64
        metadata = {
            "response": {
                "data": {
                    "otaInfoList": [
                        {
                            "version": "1.2.0",
                            "binList": [
                                {"name": "user1.bin", "digest": digest, "downloadUrl": "http://example.test/user1.bin"}
                            ],
                        }
                    ]
                }
            }
        }
        version, files = ota_files(metadata, "1.2.0")
        self.assertEqual(version, "1.2.0")
        self.assertEqual(files[0]["name"], "user1.bin")

    def test_rejects_path_traversal(self):
        metadata = {
            "response": {
                "data": {
                    "otaInfoList": [
                        {
                            "version": "1.2.0",
                            "binList": [
                                {"name": "../user1.bin", "digest": "a" * 64, "downloadUrl": "http://example.test/x"}
                            ],
                        }
                    ]
                }
            }
        }
        with self.assertRaises(DownloadError):
            ota_files(metadata)


if __name__ == "__main__":
    unittest.main()

