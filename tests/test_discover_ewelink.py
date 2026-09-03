import socket
import struct
import unittest

from tools.discover_ewelink import encode_name, make_query, parse_packet, read_name


class MdnsTests(unittest.TestCase):
    def test_name_round_trip(self):
        packet = encode_name("_ewelink._tcp.local")
        self.assertEqual(read_name(packet, 0), ("_ewelink._tcp.local", len(packet)))

    def test_query_requests_ptr(self):
        packet = make_query()
        name, cursor = read_name(packet, 12)
        rtype, rclass = struct.unpack_from("!HH", packet, cursor)
        self.assertEqual(name, "_ewelink._tcp.local")
        self.assertEqual(rtype, 12)
        self.assertEqual(rclass, 0x8001)

    def test_parses_ptr_srv_and_a(self):
        service = encode_name("_ewelink._tcp.local")
        instance = encode_name("eWeLink_test._ewelink._tcp.local")
        host = encode_name("eWeLink_test.local")
        ptr = service + struct.pack("!HHIH", 12, 1, 120, len(instance)) + instance
        srv_data = struct.pack("!HHH", 0, 0, 8081) + host
        srv = instance + struct.pack("!HHIH", 33, 1, 120, len(srv_data)) + srv_data
        address = socket.inet_aton("192.168.1.96")
        arecord = host + struct.pack("!HHIH", 1, 1, 120, len(address)) + address
        packet = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 2) + ptr + srv + arecord

        records = parse_packet(packet)

        self.assertEqual(records[0]["value"], "eWeLink_test._ewelink._tcp.local")
        self.assertEqual(records[1]["value"]["port"], 8081)
        self.assertEqual(records[2]["value"], "192.168.1.96")


if __name__ == "__main__":
    unittest.main()

