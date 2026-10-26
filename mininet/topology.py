"""
Phase 7 – Mininet Network Topology
====================================
Simulates the IoT network topology in Mininet:
  - 1 standalone Open vSwitch (s1, star topology)
  - server  → 10.0.0.1
  - esp32_1 → 10.0.0.11   (HTTP + UDP sender)
  - esp32_2 → 10.0.0.12   (HTTP sender)
  - esp32_3 → 10.0.0.13   (UDP sender)
  - phone1  → 10.0.0.21   (light browser)
  - phone2  → 10.0.0.22   (heavy streamer)

This module is imported by:
    - run_baseline.py
    - run_optimized.py

It can also run standalone for testing.

Usage
-----
    sudo python3 mininet/topology.py

    sudo python3 mininet/topology.py \
        --bw 10 \
        --delay 5ms \
        --loss 1.0

Requirements
------------
    sudo apt install mininet openvswitch-switch
"""

import argparse
import os
import sys


# ============================================================================
# Verify Mininet installation
# ============================================================================
def _require_mininet():
    try:
        from mininet.net import Mininet  # noqa: F401
    except ImportError:
        sys.exit(
            "[ERROR] Mininet not installed.\n"
            "Install with:\n"
            "    sudo apt update\n"
            "    sudo apt install mininet openvswitch-switch"
        )


# ============================================================================
# Build topology
# ============================================================================
def build_network(
    bw: float = 10.0,
    delay: str = "2ms",
    loss: float = 0.5,
    server_ip: str = "10.0.0.1"
):
    """
    Build and return Mininet network.

    Parameters
    ----------
    bw        : bandwidth in Mbps, applied to each host link
    delay     : delay string, e.g. "2ms"; larger values increase RTT
    loss      : packet loss %, from 0 to 100
    server_ip : server IP; must stay in the same /24 as the fixed host IPs

    Returns
    -------
    net : Mininet object
    """

    from mininet.net import Mininet
    from mininet.node import OVSSwitch
    from mininet.link import TCLink
    from mininet.topo import Topo
    from mininet.log import setLogLevel

    setLogLevel("info")

    # ------------------------------------------------------------------------
    # Custom topology
    # ------------------------------------------------------------------------
    class IoTTopo(Topo):

        def build(self):

            # ---------------------------------------------------------------
            # Central switch
            # ---------------------------------------------------------------
            s1 = self.addSwitch("s1")

            # ---------------------------------------------------------------
            # Hosts
            # ---------------------------------------------------------------
            server = self.addHost(
                "server",
                ip=f"{server_ip}/24"
            )

            esp32_1 = self.addHost(
                "esp32_1",
                ip="10.0.0.11/24"
            )

            esp32_2 = self.addHost(
                "esp32_2",
                ip="10.0.0.12/24"
            )

            esp32_3 = self.addHost(
                "esp32_3",
                ip="10.0.0.13/24"
            )

            phone1 = self.addHost(
                "phone1",
                ip="10.0.0.21/24"
            )

            phone2 = self.addHost(
                "phone2",
                ip="10.0.0.22/24"
            )

            # ---------------------------------------------------------------
            # Default link properties
            # ---------------------------------------------------------------
            # These values emulate the common link condition. They are
            # independent per-link limits, not one shared switch bottleneck.
            # use_htb enables Linux traffic shaping for the bandwidth setting.
            link_opts = dict(
                bw=bw,
                delay=delay,
                loss=loss,
                use_htb=True
            )

            # ---------------------------------------------------------------
            # Server
            # ---------------------------------------------------------------
            self.addLink(server, s1, **link_opts)

            # ---------------------------------------------------------------
            # ESP32 devices
            # ---------------------------------------------------------------
            self.addLink(esp32_1, s1, **link_opts)

            self.addLink(esp32_2, s1, **link_opts)

            # UDP stress node intentionally gets twice the configured loss so
            # it behaves as the noisy source in the comparison experiment.
            self.addLink(
                esp32_3,
                s1,
                bw=bw,
                delay=delay,
                loss=min(loss * 2, 100),
                use_htb=True
            )

            # ---------------------------------------------------------------
            # Phones
            # ---------------------------------------------------------------
            self.addLink(phone1, s1, **link_opts)

            # phone2 represents a heavy stream; its 2x capacity is a deliberate
            # workload assumption. Change the multiplier to model another load.
            self.addLink(
                phone2,
                s1,
                bw=bw * 2,
                delay=delay,
                loss=loss,
                use_htb=True
            )

    topo = IoTTopo()

    # ------------------------------------------------------------------------
    # Create Mininet WITHOUT controller
    # ------------------------------------------------------------------------
    net = Mininet(
        topo=topo,
        switch=OVSSwitch,
        link=TCLink,
        controller=None,
        autoSetMacs=True
    )

    return net


# ============================================================================
# Interactive mode
# ============================================================================
def run_interactive(
    bw: float,
    delay: str,
    loss: float,
    server_ip: str
):
    """
    Start topology and open Mininet CLI.
    """

    from mininet.cli import CLI

    # Cleanup old Mininet state
    os.system("sudo mn -c > /dev/null 2>&1")

    print("[+] Building topology...")

    net = build_network(
        bw=bw,
        delay=delay,
        loss=loss,
        server_ip=server_ip
    )

    # ------------------------------------------------------------------------
    # Start network
    # ------------------------------------------------------------------------
    net.start()

    # ------------------------------------------------------------------------
    # Configure switch as standalone
    # ------------------------------------------------------------------------
    for sw in net.switches:
        sw.cmd(
            f"ovs-vsctl set-fail-mode {sw.name} standalone"
        )

    print("\n[+] Mininet IoT topology started.")
    print("------------------------------------------------")
    print("Hosts:")
    print("  server   → 10.0.0.1")
    print("  esp32_1  → 10.0.0.11")
    print("  esp32_2  → 10.0.0.12")
    print("  esp32_3  → 10.0.0.13")
    print("  phone1   → 10.0.0.21")
    print("  phone2   → 10.0.0.22")
    print("------------------------------------------------")
    print(f"Bandwidth : {bw} Mbps")
    print(f"Delay     : {delay}")
    print(f"Loss      : {loss}%")
    print("------------------------------------------------")
    print("Useful commands:")
    print("  pingall")
    print("  nodes")
    print("  net")
    print("  exit")
    print("------------------------------------------------")

    try:
        CLI(net)

    finally:
        print("\n[+] Stopping Mininet...")
        net.stop()

        os.system("sudo mn -c > /dev/null 2>&1")

        print("[+] Cleanup complete.")


# ============================================================================
# Main
# ============================================================================
if __name__ == "__main__":

    _require_mininet()

    parser = argparse.ArgumentParser(
        description="Phase 7 – IoT Mininet Topology"
    )

    parser.add_argument(
        "--bw",
        type=float,
        default=10.0,
        help="Bandwidth per host link in Mbps"
    )

    parser.add_argument(
        "--delay",
        type=str,
        default="2ms",
        help="Delay (e.g. 2ms)"
    )

    parser.add_argument(
        "--loss",
        type=float,
        default=0.5,
        help="Packet loss percentage (0-100)"
    )

    parser.add_argument(
        "--server-ip",
        type=str,
        default="10.0.0.1",
        help="Server IP"
    )

    args = parser.parse_args()

    run_interactive(
        bw=args.bw,
        delay=args.delay,
        loss=args.loss,
        server_ip=args.server_ip
    )
