"""Generate ignored local gateway/sensor headers from one private JSON config."""
import argparse
import ipaddress
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(data):
    for key in ["upstream_ssid", "upstream_password", "ap_ssid", "ap_password", "snmp_community"]:
        if not isinstance(data.get(key), str) or any(ord(c) < 32 or ord(c) > 126 for c in data[key]):
            raise ValueError(f"{key} must contain printable ASCII characters")
    for key in ["upstream_ssid", "ap_ssid"]:
        if not 1 <= len(data[key]) <= 32:
            raise ValueError(f"{key} must be 1..32 bytes")
    for key in ["upstream_password", "ap_password"]:
        if not 8 <= len(data[key]) <= 63:
            raise ValueError(f"{key} must be a WPA2 password of 8..63 bytes")
    if not 16 <= len(data["snmp_community"]) <= 64:
        raise ValueError("snmp_community must be 16..64 bytes")
    token = data.get("control_token", "")
    if not isinstance(token,str) or (token and (len(token)<16 or any(ord(c)<32 or ord(c)>126 for c in token))):
        raise ValueError("An optional control_token must be at least 16 printable ASCII characters")
    data = dict(data)
    for key in ["server_ip", "collector_ip", "ap_ip"]:
        data[key] = str(ipaddress.IPv4Address(data[key]))
    ap = ipaddress.IPv4Address(data["ap_ip"])
    if int(ap) & 255 != 1:
        raise ValueError("ap_ip must be the .1 address of a dedicated /24 subnet")
    subnet = ipaddress.IPv4Network(f"{ap}/24", strict=False)
    if any(ipaddress.IPv4Address(data[k]) in subnet for k in ["server_ip", "collector_ip"]):
        raise ValueError("Server and collector must be on the UPSTREAM network")
    return data


def outputs(data):
    data = validate(data)
    prefix = data["ap_ip"].rsplit(".", 1)[0]
    macros = {"GW_UPSTREAM_SSID":data["upstream_ssid"], "GW_UPSTREAM_PASSWORD":data["upstream_password"],
              "GW_AP_SSID":data["ap_ssid"], "GW_AP_PASSWORD":data["ap_password"],
              "GW_SNMP_COMMUNITY":data["snmp_community"], "GW_COLLECTOR_IP":data["collector_ip"],
              "GW_AP_IP":data["ap_ip"], "GW_NETMASK":"255.255.255.0", "GW_SERVER_IP":data["server_ip"]}
    macros.update({f"GW_SENSOR_{i}":f"{prefix}.{100+i}" for i in [1,2,3]})
    gateway = "#pragma once\n// Generated local secrets. Do not commit.\n" + "".join(
        f"#define {key} {json.dumps(value)}\n" for key, value in macros.items())
    octets = prefix.split(".")
    sensor = "#pragma once\n// Generated local Wi-Fi configuration. Do not commit.\n"
    for key, value in [("WIFI_SSID",data["ap_ssid"]), ("WIFI_PASS",data["ap_password"]),
                       ("SERVER_IP",data["server_ip"]), ("CONTROL_TOKEN",data.get("control_token", ""))]:
        sensor += f"#define {key} {json.dumps(value)}\n"
    for key, value in zip(["LAB_NET_A", "LAB_NET_B", "LAB_NET_C"],octets):
        sensor += f"#define {key} {value}\n"
    return gateway, sensor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Private JSON file; see gateway_config.example.json")
    args = parser.parse_args()
    try:
        gateway, sensor = outputs(json.loads(Path(args.config).read_text(encoding="utf-8")))
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    paths = [(ROOT / "firmware/esp32_gateway/main/gateway_config.local.h", gateway)]
    paths += [(ROOT / f"esp32 code/esp32_device_{i}/gateway_network.local.h",sensor) for i in [1,2,3]]
    for path, text in paths:
        path.write_text(text, encoding="utf-8")
    print("Generated local gateway header and three sensor network headers. Credentials were not printed.")


if __name__ == "__main__":
    main()
