"""Exercise the actual firmware C codec through ctypes and a loopback UDP agent.

Build .tools/snmp_codec.dll with tools/test_gateway.ps1 to enable native tests.
"""
import ctypes as C
import importlib.util
import json
import os
from pathlib import Path
import random
import socket
import sys
import threading
import unittest
import sqlite3
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in ["monitoring", "server", "tools"]:
    sys.path.insert(0, str(ROOT / directory))
import snmp_wire as wire
from snmp_collect import OIDS
import gateway_topology as topology
from configure_gateway import outputs
from probe import target
import telemetry

COMMUNITY = "test-only-community"
LIBRARY = Path(os.environ.get("IOT_SNMP_TEST_LIBRARY", str(ROOT / ".tools/snmp_codec.dll")))


class Interface(C.Structure):
    _fields_ = [("in_octets", C.c_uint64), ("out_octets", C.c_uint64)] + [
        (key, C.c_uint32) for key in ["in_discards", "out_discards", "in_errors", "out_errors",
                                     "discontinuity_ticks", "speed_mbps"]] + [("oper_status", C.c_int32)]


class Snapshot(C.Structure):
    _fields_ = [("uptime_ticks", C.c_uint32), ("interfaces", Interface * 2)]


def pdu(packet):
    _, body, _ = wire.read_tlv(packet)
    return wire.children(wire.children(body)[2][1])


@unittest.skipUnless(LIBRARY.exists(), "Build native firmware codec with tools/test_gateway.ps1")
class NativeAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = C.CDLL(str(LIBRARY))
        cls.lib.snmp_respond.argtypes = [C.c_void_p, C.c_size_t, C.c_char_p,
                                        C.POINTER(Snapshot), C.c_void_p, C.c_size_t]
        cls.lib.snmp_respond.restype = C.c_size_t

    def setUp(self):
        self.snapshot = Snapshot(uptime_ticks=12345)
        for i, interface in enumerate(self.snapshot.interfaces):
            interface.in_octets = 2**40 + 123 + i
            interface.out_octets = 2**63 + 456 + i
            interface.oper_status = 1
        self.oids = [oid.format(i=2) for oid in OIDS.values()]

    def respond(self, request, capacity=1400):
        buffer = C.create_string_buffer(capacity)
        length = self.lib.snmp_respond(request, len(request), COMMUNITY.encode(),
                                       C.byref(self.snapshot), buffer, capacity)
        return buffer.raw[:length]

    def test_collector_objects_and_counter64_preserve_full_precision(self):
        response = self.respond(wire.request(self.oids, COMMUNITY, 123))
        values = dict(zip(OIDS, wire.parse_response(response,self.oids,COMMUNITY,123)))
        self.assertEqual(values["uptime"], 12345)
        self.assertEqual(values["in_octets"], 2**40 + 124)
        self.assertEqual(values["out_octets"], 2**63 + 457)
        self.assertEqual(values["speed_mbps"], 0)

    def test_all_19_required_instances_fit_one_request(self):
        oids = [OIDS["uptime"]] + [oid.format(i=i) for i in [1,2]
                for name, oid in OIDS.items() if name != "uptime"]
        self.assertEqual(len(oids), 19)
        response = self.respond(wire.request(oids, COMMUNITY, -123))
        self.assertEqual(len(wire.parse_response(response,oids,COMMUNITY,-123)), 19)
        response = self.respond(wire.request(oids, COMMUNITY, -2147483648))
        self.assertEqual(len(wire.parse_response(response,oids,COMMUNITY,-2147483648)), 19)

    def test_wrong_community_truncation_and_unsupported_versions_are_silent(self):
        packet = wire.request(self.oids,COMMUNITY,123)
        for end in range(len(packet)):
            self.assertEqual(self.respond(packet[:end]), b"")
        self.assertEqual(self.respond(wire.request(self.oids,"wrong",123)),b"")
        _, body, _ = wire.read_tlv(packet)
        v1 = wire.tlv(0x30, body.replace(b"\x02\x01\x01",b"\x02\x01\x00",1))
        self.assertEqual(self.respond(v1),b"")

    def test_getnext_walk_is_ordered_and_includes_both_interfaces(self):
        oid = ".1.3.6.1.2.1"
        seen = []
        def decode(raw):
            arcs, arc = [], 0
            for byte in raw:
                arc = (arc << 7) | (byte & 127)
                if not byte & 128:
                    if not arcs:
                        first = min(arc // 40,2)
                        arcs = [first,arc-first*40]
                    else:
                        arcs.append(arc)
                    arc = 0
            return tuple(arcs)
        for _ in range(50):
            bindings = wire.children(pdu(self.respond(wire.request([oid],COMMUNITY,123,0xa1)))[3][1])
            pair = wire.children(bindings[0][1])
            if pair[1][0] == 0x82:
                break
            next_oid = decode(pair[0][1])
            self.assertGreater(next_oid,tuple(int(v) for v in oid.strip('.').split('.')))
            seen.append(next_oid)
            oid = '.' + '.'.join(map(str,next_oid))
        self.assertEqual(len(seen),38)
        self.assertIn(tuple(map(int, OIDS["in_octets"].format(i=1).strip('.').split('.'))),seen)
        self.assertIn(tuple(map(int, OIDS["in_octets"].format(i=2).strip('.').split('.'))),seen)

    def test_unknown_object_and_set_rejection(self):
        unknown = wire.request([".1.3.6.1.2.1.999.0"], COMMUNITY, 123)
        binding = wire.children(pdu(self.respond(unknown))[3][1])[0][1]
        self.assertEqual(wire.children(binding)[1],(0x80,b""))
        response = self.respond(wire.request(self.oids,COMMUNITY,123,0xa3))
        fields = pdu(response)
        self.assertEqual(int.from_bytes(fields[1][1],"big"),17)
        self.assertEqual(int.from_bytes(fields[2][1],"big"),1)

    def test_small_response_buffer_returns_toobig_without_overrun(self):
        response = self.respond(wire.request(self.oids,COMMUNITY,123),capacity=70)
        fields = pdu(response)
        self.assertEqual(int.from_bytes(fields[1][1],"big"),1)
        self.assertEqual(fields[3],(0x30,b""))

    def test_bounded_parser_handles_5000_mutations(self):
        rng = random.Random(42)
        packet = wire.request(self.oids,COMMUNITY,123)
        for _ in range(5000):
            data = bytearray(packet)
            for _ in range(rng.randrange(1,8)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            if rng.randrange(2):
                data = data[:rng.randrange(len(data)+1)]
            self.assertLessEqual(len(self.respond(bytes(data))),1400)

    def test_python_collector_interoperates_with_native_agent_over_udp(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
            server.bind(("127.0.0.1",0))
            server.settimeout(3)
            errors = []
            def serve():
                try:
                    packet, peer = server.recvfrom(1401)
                    server.sendto(self.respond(packet),peer)
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=serve)
            worker.start()
            values = wire.get("127.0.0.1",self.oids,COMMUNITY,port=server.getsockname()[1])
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)
            self.assertEqual(values[1],2**40+124)


class GatewayIntegrationTests(unittest.TestCase):
    def test_old_telemetry_database_migrates_and_keeps_real_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp)/"events.db")
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE events (timestamp REAL,run_id TEXT,source TEXT,device TEXT,"
                           "protocol TEXT,bytes INTEGER,boot_id TEXT,seq INTEGER,received INTEGER)")
                db.execute("INSERT INTO events VALUES (1,'old','host','esp32_1','UDP',80,'boot',0,1)")
            db.close()
            with patch.object(telemetry,"PATH",database):
                telemetry.record({"device":"esp32_1","seq":1},"192.168.50.101","UDP",80,
                                 peer_ip="192.168.8.110")
                with telemetry.connect() as connection:
                    rows = connection.execute("SELECT source,peer_ip FROM events ORDER BY timestamp").fetchall()
                self.assertEqual(rows,[("host",None),("192.168.50.101","192.168.8.110")])

    def test_nat_identity_only_maps_known_devices_from_configured_peer(self):
        with patch.dict(os.environ,{"IOT_USE_GATEWAY":"1", "IOT_GATEWAY_UPLINK_IP":"192.168.8.110",
                                    "IOT_SENSOR_SUBNET":"192.168.50.0/24"}):
            self.assertEqual(topology.logical_source({"device":"esp32_1"},"192.168.8.110"),"192.168.50.101")
            self.assertEqual(topology.logical_source({"device":"esp32_2"},"192.168.8.110"),"192.168.50.102")
            self.assertEqual(topology.logical_source({"device":"esp32_1"},"192.168.8.200"),"192.168.8.200")
            self.assertEqual(topology.logical_source({"device":"unknown"},"192.168.8.110"),"192.168.8.110")
            self.assertEqual(topology.probe_url("esp32_3","192.168.50.103"),"http://192.168.8.110:8103/data")

    def test_forwarded_probes_keep_downstream_source_identity(self):
        self.assertEqual(target("192.168.50.101=http://192.168.8.110:8101/data"),
                         ("192.168.50.101","http://192.168.8.110:8101/data"))
        self.assertEqual(target("http://192.168.8.101/data")[0],"192.168.8.101")
        with self.assertRaises(ValueError):
            target("broken")

    def test_shared_configuration_generates_matching_sensor_network(self):
        data = json.loads((ROOT/"firmware/esp32_gateway/gateway_config.example.json").read_text())
        gateway, sensor = outputs(data)
        self.assertIn('#define GW_SENSOR_3 "192.168.50.103"',gateway)
        self.assertIn('#define LAB_NET_C 50',sensor)
        self.assertIn('#define WIFI_SSID "IoT-Lab-Gateway"',sensor)
        self.assertIn('#define SERVER_IP "192.168.8.100"',sensor)
        with self.assertRaises(ValueError):
            outputs(dict(data,server_ip="192.168.50.100"))
        with self.assertRaises(ValueError):
            outputs(dict(data,snmp_community="public"))

    def test_python_client_rejects_response_id_mismatch(self):
        packet = wire.request([OIDS["uptime"]],COMMUNITY,123)
        with self.assertRaises(ValueError):
            wire.parse_response(packet,[OIDS["uptime"]],COMMUNITY,321)


if __name__ == "__main__":
    unittest.main()
