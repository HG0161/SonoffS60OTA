import struct
import unittest

from tools.websocket_minimal import encode_client_frame


class WebSocketTests(unittest.TestCase):
    def test_client_frame_is_masked_and_reversible(self):
        payload = b'{"action":"test"}'
        mask = b"abcd"
        frame = encode_client_frame(payload, mask=mask)
        self.assertEqual(frame[0], 0x81)
        self.assertTrue(frame[1] & 0x80)
        length = frame[1] & 0x7F
        self.assertEqual(length, len(payload))
        self.assertEqual(frame[2:6], mask)
        decoded = bytes(value ^ mask[index % 4] for index, value in enumerate(frame[6:]))
        self.assertEqual(decoded, payload)

    def test_extended_16_bit_length(self):
        frame = encode_client_frame(b"x" * 200, mask=b"1234")
        self.assertEqual(frame[1] & 0x7F, 126)
        self.assertEqual(struct.unpack("!H", frame[2:4])[0], 200)


if __name__ == "__main__":
    unittest.main()

