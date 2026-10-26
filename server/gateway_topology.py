"""Explicit NAT topology; preserve logical sensor identity without trusting any peer."""
import ipaddress
import os


def enabled():
    return os.environ.get("IOT_USE_GATEWAY", "0") == "1"


def upstream_ip():
    return str(ipaddress.IPv4Address(os.environ["IOT_GATEWAY_UPLINK_IP"]))


def sensors():
    network = ipaddress.IPv4Network(os.environ.get("IOT_SENSOR_SUBNET", "192.168.50.0/24"))
    if network.prefixlen != 24:
        raise ValueError("IOT_SENSOR_SUBNET must be a /24")
    return {f"esp32_{i}": str(network.network_address + 100 + i) for i in [1,2,3]}


def logical_source(payload, peer_ip):
    if enabled() and peer_ip == upstream_ip():
        return sensors().get(payload.get("device"), peer_ip)
    return peer_ip


def probe_url(device, direct_ip):
    if enabled():
        # Validate the device name rather than accepting a user-provided port.
        index = list(sensors()).index(device)
        return f"http://{upstream_ip()}:{8101 + index}/data"
    return f"http://{direct_ip}/data"
