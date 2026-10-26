"""Small SNMPv2c GET client for the lab gateway; no external SNMP utility needed.

SNMPv2c communities are unencrypted. Use the gateway's collector IP ACL and a
dedicated lab network. This module does not implement SNMPv3.
"""
import secrets
import socket


def tlv(tag, body):
    length = len(body)
    size = bytes([length]) if length < 128 else (
        bytes([0x81, length]) if length < 256 else bytes([0x82, length >> 8, length & 255]))
    return bytes([tag]) + size + body


def integer(value):
    bits = value.bit_length() if value >= 0 else (~value).bit_length()
    size = max(1, (bits + 8) // 8)
    return tlv(2, value.to_bytes(size, "big", signed=True))


def oid_bytes(oid):
    arcs = [int(v) for v in oid.strip(".").split(".")]
    if len(arcs) < 2 or not 0 <= arcs[0] <= 2 or arcs[1] < 0 or (arcs[0] < 2 and arcs[1] >= 40):
        raise ValueError("Invalid object identifier")
    result = bytearray()
    for arc in [arcs[0] * 40 + arcs[1]] + arcs[2:]:
        if not 0 <= arc <= 0xffffffff:
            raise ValueError("OID arc out of range")
        encoded = [arc & 127]
        arc >>= 7
        while arc:
            encoded.insert(0, (arc & 127) | 128)
            arc >>= 7
        result.extend(encoded)
    return bytes(result)


def read_tlv(data, offset=0):
    if offset + 2 > len(data):
        raise ValueError("Truncated BER")
    tag, length = data[offset:offset + 2]
    offset += 2
    if length & 128:
        n = length & 127
        if not 1 <= n <= 2 or offset + n > len(data):
            raise ValueError("Invalid BER length")
        length = int.from_bytes(data[offset:offset+n], "big")
        offset += n
    end = offset + length
    if end > len(data):
        raise ValueError("Truncated BER value")
    return tag, data[offset:end], end


def children(data):
    result, position = [], 0
    while position < len(data):
        tag, value, position = read_tlv(data, position)
        result.append((tag, value))
    return result


def request(oids, community, request_id, pdu_tag=0xa0):
    bindings = b"".join(tlv(0x30, tlv(6, oid_bytes(oid)) + tlv(5, b"")) for oid in oids)
    pdu = tlv(pdu_tag, integer(request_id) + integer(0) + integer(0) + tlv(0x30, bindings))
    return tlv(0x30, integer(1) + tlv(4, community.encode()) + pdu)


def parse_response(packet, oids, community, request_id):
    tag, body, end = read_tlv(packet)
    if tag != 0x30 or end != len(packet):
        raise ValueError("Invalid SNMP message")
    message = children(body)
    if len(message) != 3 or message[0] != (2, b"\x01") or message[1] != (4, community.encode()) or message[2][0] != 0xa2:
        raise ValueError("Unexpected SNMP version/community/PDU")
    pdu = children(message[2][1])
    if len(pdu) != 4 or any(t != 2 for t, _ in pdu[:3]) or pdu[3][0] != 0x30:
        raise ValueError("Invalid response PDU")
    numbers = [int.from_bytes(v, "big", signed=True) for _, v in pdu[:3]]
    if numbers[0] != request_id or numbers[1] or numbers[2]:
        raise ValueError("SNMP request ID mismatch or agent error")
    bindings = children(pdu[3][1])
    if len(bindings) != len(oids):
        raise ValueError("SNMP varbind count mismatch")
    result = []
    for oid, (tag, value) in zip(oids, bindings):
        if tag != 0x30:
            raise ValueError("Invalid varbind")
        pair = children(value)
        if len(pair) != 2 or pair[0] != (6, oid_bytes(oid)):
            raise ValueError("Unexpected response OID")
        tag, value = pair[1]
        if tag not in (2, 0x41, 0x42, 0x43, 0x46) or not 1 <= len(value) <= 9:
            raise ValueError("Unsupported or missing numeric SNMP object")
        number = int.from_bytes(value, "big", signed=tag == 2)
        if number < 0:
            raise ValueError("Negative SNMP counter")
        result.append(number)
    return result


def get(agent, oids, community, timeout=2, port=161):
    if not community:
        raise ValueError("SNMP community is required")
    request_id = secrets.randbelow(0x7fffffff)
    packet = request(oids, community, request_id)
    if len(packet) > 1400:
        raise ValueError("SNMP request too large")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        # Connected UDP validates the response endpoint as well as the request ID.
        sock.connect((agent, port))
        sock.send(packet)
        response = sock.recv(1401)
    if len(response) > 1400:
        raise ValueError("Oversized SNMP response")
    return parse_response(response, oids, community, request_id)
