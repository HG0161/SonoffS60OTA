import unittest

from tools.get_device_key import EwelinkError
from tools.query_ota import ota_endpoints, ota_identity


class QueryOtaTests(unittest.TestCase):
    def test_uses_module_model_and_reported_firmware(self):
        device = {
            "deviceid": "abc",
            "productModel": "S60TPG",
            "extra": {"model": "SN-ESP32C3-S60-01"},
            "params": {"fwVersion": "1.1.1"},
        }
        self.assertEqual(
            ota_identity(device),
            {"deviceid": "abc", "model": "SN-ESP32C3-S60-01", "version": "1.1.1"},
        )

    def test_requires_version(self):
        with self.assertRaises(EwelinkError):
            ota_identity({"deviceid": "abc", "productModel": "S60TPG"})

    def test_extracts_safe_unique_router_endpoints(self):
        ota_list = [
            {
                "binList": [
                    {"downloadUrl": "http://192.0.2.10:8088/private/one?token=secret"},
                    {"downloadUrl": "http://192.0.2.10:8088/private/two?token=secret"},
                    {"downloadUrl": "https://updates.example/private/three"},
                ]
            }
        ]

        self.assertEqual(
            ota_endpoints(ota_list),
            [("192.0.2.10", 8088), ("updates.example", 443)],
        )


if __name__ == "__main__":
    unittest.main()
