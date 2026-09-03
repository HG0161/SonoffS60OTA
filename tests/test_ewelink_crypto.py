import unittest

from tools.ewelink_crypto import decrypt_data, encrypt_data


class EwelinkCryptoTests(unittest.TestCase):
    def test_round_trip_is_deterministic_with_fixed_iv(self):
        state = {"switch": "off", "power": 0, "nested": {"ok": True}}
        encrypted, iv = encrypt_data(state, "example-device-key", bytes(range(16)))
        self.assertEqual(decrypt_data(encrypted, iv, "example-device-key"), state)

    def test_rejects_bad_iv_length(self):
        with self.assertRaises(ValueError):
            encrypt_data({}, "example-device-key", b"short")


if __name__ == "__main__":
    unittest.main()

