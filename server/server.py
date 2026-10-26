"""
IoT Network Testing System – Flask Server
==========================================
Host machine: 192.168.8.100
HTTP (Flask): port 5000
UDP listener : port 12345

Device roles
------------
  ESP32_1 (192.168.8.101) – sends HTTP POST  AND  UDP datagrams
  ESP32_2 (192.168.8.102) – sends HTTP POST only
  ESP32_3 (192.168.8.103) – sends UDP datagrams only
  Phone-1 (real device)    – light user: browses /browse, taps likes, watches /stream
  Phone-2 (real device)    – heavy user: continuous video streaming via /stream

Endpoints
---------
  GET  /               – redirects to /dashboard
  POST /data           – receive ESP32 sensor JSON
  GET  /packets        – diagnostic: all stored HTTP packets
  GET  /udp-log        – diagnostic: all stored UDP datagrams
  GET  /video/<id>     – stream video 1-6 using protocol-specific method (id 1-6)
  GET  /video          – legacy: same as /video/1
  GET  /stream         – video streaming page
  GET  /browse         – blog browsing page
  POST /api/like/<id>  – like button handler
  POST /api/comment/<id> – comment handler
  GET  /dashboard      – real-time device monitoring dashboard (root)
  GET  /api/dashboard  – JSON stats snapshot (polled by dashboard.html)
  GET  /api/metrics    – historical metrics for graphs
  GET  /api/metrics/export – CSV download of all metrics

Video stream protocol mapping
------------------------------
  1  IoT Traffic Sim   – HTTP chunked transfer (TCP, Content-Transfer)
  2  UDP Burst Test    – parallel UDP socket flood  + HTTP confirmation
  3  Mixed Protocol    – TCP HTTP + background UDP  + ICMP ping
  4  Congestion Test   – rate-limited TCP (sustained 512 KB/s throttle)
  5  ML Training Set   – HTTP byte-range (206 Partial Content)
  6  Stress Test       – maximum-rate chunked TCP (no throttle)

Usage
-----
    pip install flask requests
    python server.py
"""

import io
import os
import re
import csv
import sqlite3
import threading
import time
import subprocess
import platform
import random
import socket
import json as _json
from collections import deque
import requests
import telemetry
import monitoring_status
import gateway_topology
from flask import Flask, request, jsonify, send_file, Response, render_template, redirect, url_for
from datetime import datetime

# ---------------------------------------------------------------------------
# Network configuration
# Change these if your router or PC IP differs from the project defaults.
# ---------------------------------------------------------------------------
SERVER_HOST = "0.0.0.0"   # bind Flask to all interfaces so LAN traffic is accepted
SERVER_PORT = 5000         # Change only if every client URL is changed too.

# UDP socket settings
UDP_HOST        = "0.0.0.0"  # bind to all interfaces
UDP_PORT        = 12345       # must match #define UDP_PORT in every ESP32 sketch
UDP_BUFFER_SIZE = 2048        # bytes; increase for larger UDP payloads.

# Static IP addresses for each ESP32 device.
# These must match the STATIC_IP_LAST defines and the server subnet in the
# corresponding .ino files. Keep every address unique.
ESP32_DEVICES = {
    "esp32_1": "192.168.8.101",   # HTTP + UDP  (ENABLE_HTTP true,  ENABLE_UDP true)
    "esp32_2": "192.168.8.102",   # HTTP only   (ENABLE_HTTP true,  ENABLE_UDP false)
    "esp32_3": "192.168.8.103",   # UDP  only   (ENABLE_HTTP false, ENABLE_UDP true)
}

if gateway_topology.enabled():
    gateway_topology.upstream_ip()  # Gateway mode requires a valid uplink address.
    ESP32_DEVICES = gateway_topology.sensors()

# ---------------------------------------------------------------------------
# Video streaming configuration
# ---------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))

# Folder that holds the 6 named video files
VIDEOS_DIR = os.path.join(_BASE, "templates", "videos")

# Each stream id maps to (filename, protocol_label, description)
STREAM_CONFIG = {
    1: ("Davido - Fall (Official Video).mp4",                                                                                          "HTTP-Chunked",  "IoT Traffic Sim",   "TCP Chunked Transfer"),
    2: ("DRAKE - WHAT DID I MISS.mp4",                                                                                                  "UDP-Burst",     "UDP Burst Test",     "High-rate UDP Datagrams"),
    3: ("MIKA - Grace Kelly.mp4",                                                                                                        "Mixed",         "Mixed Protocol",     "TCP + UDP + ICMP"),
    4: ("Ne-Yo - One In A Million [Official Video].mp4",                                                                                 "TCP-Throttled", "Congestion Test",    "Sustained 512 KB/s TCP"),
    5: ("Tems - Free Mind (Music Video).mp4",                                                                                            "HTTP-Range",    "ML Training Set",    "HTTP 206 Byte-Range"),
    6: ("Tyler ICU - Ebasini Wav.files ft. Leemckrazy, Tman Xpress, Ceeka RSA,Visca, AL Xapo, Sjavas DaDeejay.mp4",                     "TCP-Stress",    "Stress Test",        "Max-Rate TCP Chunked"),
}

# ---------------------------------------------------------------------------
# Optional application discard simulation, disabled for normal measurements.
# ---------------------------------------------------------------------------
# Application impairment is explicit and disabled for real measurements.
DROP_RATE = float(os.environ.get("IOT_DROP_RATE", "0"))
if not 0 <= DROP_RATE <= 1:
    raise ValueError("IOT_DROP_RATE must be between 0 and 1")

def _current_drop_rate():
    """Configured application discard probability, distinct from network loss."""
    return DROP_RATE


VIDEO_LOG_PATH     = os.environ.get("IOT_VIDEO_LOG", os.path.join(_BASE, "video_log.csv"))
VIDEO_CHUNK_SIZE   = 65536   # Larger chunks improve throughput but use more RAM.
VIDEO_SYNTHETIC_MB = 50      # Fallback size when a requested file is absent.
# Shorter windows make the dashboard more responsive but produce noisier
# throughput values; longer windows are more stable.
MIN_THROUGHPUT_WINDOW_MS = 1000
# These caps shape the experiment workload, not the physical network speed.
STREAM_RATE_LIMIT_MBPS = {
    1: 50,   # TCP Chunked cap
    6: 180,  # TCP Stress cap
}

# SQLite metrics database
METRICS_DB_PATH = os.environ.get("IOT_METRICS_DB", os.path.join(_BASE, "metrics.db"))

# Create the CSV log file with a header row if it does not already exist
if not os.path.exists(VIDEO_LOG_PATH):
    with open(VIDEO_LOG_PATH, "w", newline="") as _f:
        csv.writer(_f).writerow(["timestamp", "client_ip", "stream_id", "protocol",
                                  "bytes_served", "duration_ms", "throughput_kbps"])

# ---------------------------------------------------------------------------
# SQLite metrics database
# ---------------------------------------------------------------------------
def _init_db():
    con = sqlite3.connect(METRICS_DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS stream_metrics (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT,
            client_ip     TEXT,
            stream_id     INTEGER,
            protocol      TEXT,
            bytes_served  INTEGER,
            duration_ms   REAL,
            throughput_kbps REAL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS esp_metrics (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT,
            device     TEXT,
            proto      TEXT,
            latency_ms REAL,
            bytes      INTEGER
        )
    """)
    con.commit()
    con.close()

_init_db()
_metrics_lock = threading.Lock()

def _log_metric_db(stream_id, client_ip, protocol, bytes_served, duration_ms):
    duration_ms = max(float(duration_ms), MIN_THROUGHPUT_WINDOW_MS)
    rate_mbps = STREAM_RATE_LIMIT_MBPS.get(stream_id)
    if rate_mbps and bytes_served:
        min_duration_ms = (bytes_served * 8 * 1000) / (rate_mbps * 1_000_000)
        duration_ms = max(duration_ms, min_duration_ms)
    throughput_kbps = round((bytes_served * 8 / 1024) / max(duration_ms / 1000, 0.001), 2)
    ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    with _metrics_lock:
        con = sqlite3.connect(METRICS_DB_PATH)
        con.execute(
            "INSERT INTO stream_metrics (timestamp,client_ip,stream_id,protocol,"
            "bytes_served,duration_ms,throughput_kbps) VALUES (?,?,?,?,?,?,?)",
            (ts, client_ip, stream_id, protocol, bytes_served, duration_ms, throughput_kbps)
        )
        con.commit()
        con.close()
    # also append to CSV
    with open(VIDEO_LOG_PATH, "a", newline="") as _f:
        csv.writer(_f).writerow([ts, client_ip, stream_id, protocol,
                                  bytes_served, round(duration_ms, 2), throughput_kbps])

# ---------------------------------------------------------------------------
# Blog data (in-memory – resets on server restart)
# Pre-populated with networking / IoT topics relevant to this project.
# Posts, likes, and comments are protected by blog_lock for thread safety.
# ---------------------------------------------------------------------------
BLOG_POSTS = [
    {
        "id": 1,
        "title": "How Wireshark Captures Network Traffic",
        "date": "2026-04-01",
        "body": (
            "Wireshark is a packet analyser that captures every frame "
            "passing through a network interface. It stores each frame with "
            "microsecond timestamps, source/destination addresses, protocol "
            "details, and raw payload bytes. This makes it invaluable for "
            "debugging IoT devices and training ML models on real traffic."
        ),
        "likes": 12,
        "comments": [
            {"author": "Alice", "text": "Great intro to Wireshark!"},
            {"author": "Bob",   "text": "Used this for my ESP32 project too."},
        ],
    },
    {
        "id": 2,
        "title": "TCP vs UDP: Choosing the Right Protocol for IoT",
        "date": "2026-04-05",
        "body": (
            "TCP guarantees delivery and ordering through its three-way "
            "handshake and ACK mechanism, making it ideal for sensor data "
            "that must not be lost. UDP, on the other hand, skips the "
            "handshake entirely, allowing higher throughput at the cost of "
            "reliability – perfect for high-rate sampling where occasional "
            "packet loss is acceptable."
        ),
        "likes": 7,
        "comments": [
            {"author": "Carol", "text": "Exactly what I needed for my project."},
        ],
    },
    {
        "id": 3,
        "title": "Using AI to Optimise Network Traffic in Mixed Environments",
        "date": "2026-04-10",
        "body": (
            "Machine learning models trained on labelled Wireshark captures "
            "can classify traffic types, detect anomalies, and suggest routing "
            "optimisations. The key challenge is generating representative "
            "training data that includes both stable TCP flows and bursty UDP "
            "streams – exactly what this ESP32 testbed is designed to produce."
        ),
        "likes": 20,
        "comments": [],
    },
    {
        "id": 4,
        "title": "Static IP Addressing in ESP32 Projects",
        "date": "2026-04-12",
        "body": (
            "Using static IPs on microcontrollers eliminates DHCP noise from "
            "your captures. With WiFi.config() called before WiFi.begin() in "
            "arduino-esp32, the device skips the DISCOVER/OFFER/REQUEST/ACK "
            "handshake entirely, keeping your Wireshark pcap clean and focused "
            "on the traffic you actually want to analyse."
        ),
        "likes": 5,
        "comments": [
            {"author": "Dave", "text": "This saved me hours of debugging!"},
            {"author": "Eve",  "text": "Works great on ESP32 Dev Module."},
        ],
    },
]

# Thread lock for blog state – likes/comments can arrive from multiple threads
blog_lock = threading.Lock()

# ---------------------------------------------------------------------------
# In-memory storage
# Both lists grow indefinitely during a capture session.  For long sessions
# consider flushing to disk; for Wireshark analysis the pcap file is the
# primary record and these lists serve only as a quick diagnostic tool.
# ---------------------------------------------------------------------------
received_packets = []   # HTTP POST entries: {timestamp, source_ip, payload}
udp_log          = []   # UDP datagrams:     {timestamp, source_ip, source_port, raw, size}
storage_lock     = threading.Lock()   # protects both lists from concurrent writes

# ---------------------------------------------------------------------------
# Dashboard live statistics
# All writes are protected by stats_lock; reads happen in /api/dashboard.
# ---------------------------------------------------------------------------
_SERVER_START = time.time()

device_stats = {
    "esp32_1": {"ip": ESP32_DEVICES["esp32_1"], "label": "ESP32-1", "protocols": "HTTP+UDP",
                "http_count": 0, "udp_count": 0,
                "http_bytes": 0, "udp_bytes": 0, "total_bytes": 0,
                "last_seen": None, "last_data": {},
                "latency_ms": None, "dropped_udp": 0},
    "esp32_2": {"ip": ESP32_DEVICES["esp32_2"], "label": "ESP32-2", "protocols": "HTTP",
                "http_count": 0, "udp_count": 0,
                "http_bytes": 0, "udp_bytes": 0, "total_bytes": 0,
                "last_seen": None, "last_data": {},
                "latency_ms": None, "dropped_udp": 0},
    "esp32_3": {"ip": ESP32_DEVICES["esp32_3"], "label": "ESP32-3", "protocols": "UDP",
                "http_count": 0, "udp_count": 0,
                "http_bytes": 0, "udp_bytes": 0, "total_bytes": 0,
                "last_seen": None, "last_data": {},
                "latency_ms": None, "dropped_udp": 0},
}

phone_clients = {}                    # {ip: {label, request_count, bytes_served, last_seen, last_url}}
ts_events     = deque(maxlen=3600)    # (epoch_float, source_key, proto, byte_count)
recent_events = deque(maxlen=20)      # {time, source, proto, detail}  for activity feed
video_count   = 0                     # /video streams served to completion
stats_lock    = threading.Lock()      # guards all dashboard stats above

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder=os.path.join(_BASE, "templates"),
            static_folder=os.path.join(_BASE, "static"))


@app.route("/data", methods=["POST"])
def receive_data():
    """
    POST /data – inbound HTTP endpoint for ESP32 devices.

    ESP32 sketches send JSON with these fields:
      device  – string identifier  e.g. "esp32_1"
      temp    – integer temperature value
      hum     – integer humidity value
      extra   – optional string padding (medium payloads)
      log     – optional string padding (large payloads)

    Returns 200 {"status": "ok", "received": <byte_count>} on success.
    """
    # Reject requests that forgot to set Content-Type: application/json
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    entry = {
        "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
        "source_ip": request.remote_addr,
        "payload": payload,
    }

    # Update dashboard stats — acquire stats_lock first so the packet is
    # visible to /api/dashboard at the same moment it enters received_packets.
    device_name = payload.get("device", "")
    byte_count  = len(request.get_data())
    telemetry.record(payload, gateway_topology.logical_source(payload, request.remote_addr),
                     "HTTP", byte_count, peer_ip=request.remote_addr)
    with stats_lock:
        with storage_lock:
            received_packets.append(entry)
        if device_name in device_stats:
            ds = device_stats[device_name]
            ds["http_count"]  += 1
            ds["http_bytes"]  += byte_count
            ds["total_bytes"] += byte_count
            ds["last_seen"]    = time.time()
            ds["last_data"]    = {k: v for k, v in payload.items()
                                  if k not in ("extra", "log")}
            recent_events.append({
                "time":   datetime.utcnow().strftime("%H:%M:%S"),
                "source": ds["label"],
                "proto":  "HTTP",
                "detail": (f"temp={payload.get('temp','?')} "
                           f"hum={payload.get('hum','?')}  {byte_count}B"),
            })
        ts_events.append((time.time(), device_name or entry["source_ip"], "http", byte_count))

    # Log to console.  Field names match the ESP32 dataset ("temp", "hum").
    print(
        f"[HTTP-RX] {entry['timestamp']}  from {entry['source_ip']}  "
        f"device={payload.get('device', '?')}  "
        f"temp={payload.get('temp', '?')}  "
        f"hum={payload.get('hum', '?')}  "
        f"size={byte_count} bytes"
    )

    return jsonify({"status": "ok", "received": byte_count}), 200


@app.route("/api/telemetry/export")
def export_telemetry():
    try:
        since = float(request.args.get("since", 0))
        if not 0 <= since < float("inf"):
            raise ValueError()
    except ValueError:
        return jsonify({"error": "since must be finite nonnegative epoch seconds"}), 400
    return Response(telemetry.export_csv(request.args.get("run_id"), since), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=telemetry.csv"})


@app.route("/api/delivery")
def delivery_summary():
    return jsonify(telemetry.delivery_summary(request.args.get("run_id")))


@app.route("/packets", methods=["GET"])
def list_packets():
    """
    GET /packets – diagnostic endpoint.
    Returns all HTTP POST entries received so far as a JSON array.
    Use this to verify HTTP traffic without opening Wireshark.
    Example: curl http://192.168.0.100:5000/packets
    """
    with storage_lock:
        snapshot = list(received_packets)
    return jsonify({"count": len(snapshot), "packets": snapshot}), 200


@app.route("/udp-log", methods=["GET"])
def list_udp_log():
    """
    GET /udp-log – diagnostic endpoint.
    Returns all UDP datagrams received so far as a JSON array.
    Use this to verify UDP traffic without opening Wireshark.
    Example: curl http://192.168.8.100:5000/udp-log
    """
    with storage_lock:
        snapshot = list(udp_log)
    return jsonify({"count": len(snapshot), "datagrams": snapshot}), 200


# ===========================================================================
# Video streaming – 6 protocol-specific endpoints
# ===========================================================================

def _parse_range(range_header, file_size):
    """Parse an HTTP Range header.  Returns (byte1, byte2, length) or None."""
    if not range_header or not range_header.startswith("bytes="):
        return None
    try:
        parts = range_header[6:].split("-")
        byte1 = int(parts[0]) if parts[0] else 0
        byte2 = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        byte2 = min(byte2, file_size - 1)
        return byte1, byte2, byte2 - byte1 + 1
    except (ValueError, IndexError):
        return None


def _range_response(generator_fn, byte1, byte2, file_size, length, extra_headers=None):
    """Build a 206 Partial Content Response from a chunk generator."""
    headers = {
        "Content-Range":  f"bytes {byte1}-{byte2}/{file_size}",
        "Accept-Ranges":  "bytes",
        "Content-Length": str(length),
        "Content-Type":   "video/mp4",
        "Cache-Control":  "no-cache",
    }
    if extra_headers:
        headers.update(extra_headers)
    return Response(generator_fn(), status=206, mimetype="video/mp4", headers=headers)


def _file_range_gen(path, byte1, length, chunk_size=None):
    """Generator that yields `length` bytes from `path` starting at `byte1`."""
    cs = chunk_size or VIDEO_CHUNK_SIZE
    with open(path, "rb") as f:
        f.seek(byte1)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(cs, remaining))
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)


def _synthetic_generator(size_bytes):
    """Yields size_bytes of patterned data in VIDEO_CHUNK_SIZE chunks."""
    pattern = bytes(range(256))
    chunk   = (pattern * (VIDEO_CHUNK_SIZE // 256 + 1))[:VIDEO_CHUNK_SIZE]
    sent = 0
    while sent < size_bytes:
        to_send = min(VIDEO_CHUNK_SIZE, size_bytes - sent)
        yield chunk[:to_send]
        sent += to_send


def _throttled_generator(path_or_size, rate_bytes_per_sec):
    """Yields file (or synthetic) data throttled to rate_bytes_per_sec."""
    if isinstance(path_or_size, str) and os.path.exists(path_or_size):
        with open(path_or_size, "rb") as f:
            while True:
                chunk = f.read(VIDEO_CHUNK_SIZE)
                if not chunk:
                    break
                t0 = time.time()
                yield chunk
                elapsed = time.time() - t0
                expected = len(chunk) / rate_bytes_per_sec
                if expected > elapsed:
                    time.sleep(expected - elapsed)
    else:
        size = path_or_size if isinstance(path_or_size, int) else VIDEO_SYNTHETIC_MB * 1024 * 1024
        pattern = bytes(range(256))
        chunk_data = (pattern * (VIDEO_CHUNK_SIZE // 256 + 1))[:VIDEO_CHUNK_SIZE]
        sent = 0
        while sent < size:
            to_send = min(VIDEO_CHUNK_SIZE, size - sent)
            c = chunk_data[:to_send]
            t0 = time.time()
            yield c
            sent += to_send
            elapsed = time.time() - t0
            expected = to_send / rate_bytes_per_sec
            if expected > elapsed:
                time.sleep(expected - elapsed)


# Maps STREAM_CONFIG protocol names → short badge shown in the activity feed
_PROTO_BADGE = {
    "HTTP-Chunked":  "TCP",
    "UDP-Burst":     "UDP",
    "Mixed":         "Mixed",
    "TCP-Throttled": "TCP",
    "HTTP-Range":    "HTTP",
    "TCP-Stress":    "TCP",
}


def _touch_phone(client_ip, url="/video", protocol=None, bytes_chunk=0):
    """Update phone last_seen; fire an activity-feed event when a new stream starts."""
    with stats_lock:
        is_new  = client_ip not in phone_clients
        old_url = None if is_new else phone_clients[client_ip].get("last_url")
        if is_new:
            phone_clients[client_ip] = {
                "label": f"Phone @{client_ip}", "request_count": 0,
                "bytes_served": 0, "last_seen": None, "last_url": url,
                "protocols": set(),
            }
        pc = phone_clients[client_ip]
        pc["last_seen"] = time.time()
        # Add bytes during streaming to keep stats live
        if bytes_chunk > 0:
            pc["bytes_served"] += bytes_chunk
        # Always record a ts_event so the 30-s traffic chart stays live during streaming
        ts_events.append((time.time(), client_ip, "http", bytes_chunk))
        # Fire a feed entry only once per new stream URL (not on every chunk heartbeat)
        if url != old_url:
            pc["last_url"] = url
            if protocol:
                pc["protocols"].add(protocol)
            badge = _PROTO_BADGE.get(protocol, "HTTP") if protocol else "HTTP"
            recent_events.append({
                "time":   datetime.utcnow().strftime("%H:%M:%S"),
                "source": pc["label"],
                "proto":  badge,
                "detail": f"started {url}  [{protocol or 'HTTP'}]",
            })


def _update_phone_stats(client_ip, bytes_served, url="/video", protocol=None):
    global video_count
    with stats_lock:
        if client_ip not in phone_clients:
            phone_clients[client_ip] = {
                "label": f"Phone @{client_ip}", "request_count": 0,
                "bytes_served": 0, "last_seen": None, "last_url": url,
                "protocols": set(),
            }
        pc = phone_clients[client_ip]
        pc["request_count"] += 1
        pc["bytes_served"]  += bytes_served
        pc["last_seen"]      = time.time()
        pc["last_url"]       = url
        if protocol:
            pc["protocols"].add(protocol)
        video_count += 1
        mb = bytes_served / (1024 * 1024)
        badge = _PROTO_BADGE.get(protocol, "HTTP") if protocol else "HTTP"
        recent_events.append({
            "time":   datetime.utcnow().strftime("%H:%M:%S"),
            "source": pc["label"],
            "proto":  badge,
            "detail": f"stream {url}  {mb:.1f} MB  [{protocol or 'HTTP'}]",
        })
        ts_events.append((time.time(), client_ip, "http", bytes_served))


@app.route("/")
def root():
    """Root entry point – redirects to dashboard."""
    return redirect(url_for("dashboard_page"))


@app.route("/video")
def stream_video_legacy():
    """Legacy /video – redirects to stream 1."""
    return redirect(url_for("stream_video", stream_id=1))


@app.route("/video/<int:stream_id>")
def stream_video(stream_id):
    """
    GET /video/<stream_id> – streams a video using the protocol matching the stream type.

    Stream 1: HTTP chunked transfer (TCP)
    Stream 2: UDP burst alongside HTTP response
    Stream 3: Mixed – TCP + background UDP bursts
    Stream 4: Throttled TCP at 512 KB/s
    Stream 5: HTTP 206 Partial Content (byte-range)
    Stream 6: Max-rate TCP stress

    Query Parameters:
    - protocol: Override stream protocol. Use 'random' for random selection or specific stream ID (1-6).
    """
    # Dynamic protocol selection via query parameter
    protocol_override = request.args.get('protocol')
    if protocol_override:
        if protocol_override.lower() == 'random':
            stream_id = random.randint(1, 6)
        elif protocol_override.isdigit() and 1 <= int(protocol_override) <= 6:
            stream_id = int(protocol_override)

    if stream_id not in STREAM_CONFIG:
        return jsonify({"error": "Invalid stream id. Use 1-6."}), 404

    fname, protocol, _, _ = STREAM_CONFIG[stream_id]
    video_path = os.path.join(VIDEOS_DIR, fname)
    client_ip  = request.remote_addr
    start_time = time.time()

    # Mark phone online immediately; fire a feed entry when a new stream URL is opened.
    _touch_phone(client_ip, f"/video/{stream_id}", protocol)

    # ── Stream 1: Standard HTTP chunked TCP ─────────────────────────────────
    if stream_id == 1:
        if not os.path.exists(video_path):
            return jsonify({"error": f"Video file not found: {fname}"}), 404
        file_size = os.path.getsize(video_path)
        rng1 = _parse_range(request.headers.get("Range"), file_size)
        if rng1:
            byte1, byte2, length = rng1
            def gen1_range():
                yielded = 0
                chunk_n = 0
                rate_mbps = STREAM_RATE_LIMIT_MBPS.get(1)
                rate_bytes_per_sec = rate_mbps * 1_000_000 / 8 if rate_mbps else None
                for chunk in _file_range_gen(video_path, byte1, length):
                    t0 = time.time()
                    yield chunk
                    yielded += len(chunk)
                    chunk_n += 1
                    if rate_bytes_per_sec:
                        elapsed = time.time() - t0
                        expected = len(chunk) / rate_bytes_per_sec
                        if expected > elapsed:
                            time.sleep(expected - elapsed)
                    if chunk_n % 30 == 0:
                        _touch_phone(client_ip, "/video/1", protocol, len(chunk))
                _update_phone_stats(client_ip, yielded, "/video/1", protocol)
                _log_metric_db(1, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return _range_response(gen1_range, byte1, byte2, file_size, length)
        else:
            def gen1():
                yielded = 0
                chunk_n = 0
                rate_mbps = STREAM_RATE_LIMIT_MBPS.get(1)
                rate_bytes_per_sec = rate_mbps * 1_000_000 / 8 if rate_mbps else None
                with open(video_path, "rb") as f1:
                    for c in iter(lambda: f1.read(VIDEO_CHUNK_SIZE), b""):
                        t0 = time.time()
                        yield c
                        yielded += len(c)
                        chunk_n += 1
                        if rate_bytes_per_sec:
                            elapsed = time.time() - t0
                            expected = len(c) / rate_bytes_per_sec
                            if expected > elapsed:
                                time.sleep(expected - elapsed)
                        if chunk_n % 30 == 0:
                            _touch_phone(client_ip, "/video/1", protocol, len(c))
                _update_phone_stats(client_ip, yielded, "/video/1", protocol)
                _log_metric_db(1, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return Response(gen1(), status=200, mimetype="video/mp4",
                            headers={
                                "Content-Length": str(file_size),
                                "Accept-Ranges": "bytes",
                                "Content-Type": "video/mp4",
                                "Cache-Control": "no-cache"
                            })

    # ── Stream 2: UDP burst – fires UDP packets alongside the HTTP response ──
    elif stream_id == 2:
        if not os.path.exists(video_path):
            return jsonify({"error": f"Video file not found: {fname}"}), 404
        file_size = os.path.getsize(video_path)
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        def udp_flood():
            """Send UDP datagrams at ~100 ms intervals while HTTP stream runs."""
            seq = 0
            while getattr(udp_flood, "running", True):
                pkt = f'{{"stream":"udp_burst","seq":{seq},"ts":{time.time():.3f}}}'.encode()
                try:
                    udp_sock.sendto(pkt, (client_ip, 12346))
                except Exception:
                    pass
                seq += 1
                time.sleep(0.1)
            udp_sock.close()
        udp_flood.running = True
        threading.Thread(target=udp_flood, daemon=True).start()

        rng = _parse_range(request.headers.get("Range"), file_size)
        if rng:
            byte1, byte2, length = rng
            def gen2_range():
                yielded = 0
                chunk_n = 0
                for chunk in _file_range_gen(video_path, byte1, length):
                    yield chunk
                    yielded += len(chunk)
                    chunk_n += 1
                    if chunk_n % 30 == 0:
                        _touch_phone(client_ip, "/video/2", protocol, len(chunk))
                udp_flood.running = False
                _update_phone_stats(client_ip, yielded, "/video/2", protocol)
                _log_metric_db(2, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return _range_response(gen2_range, byte1, byte2, file_size, length)
        else:
            def gen2():
                yielded = 0
                chunk_n = 0
                with open(video_path, "rb") as f:
                    for c in iter(lambda: f.read(VIDEO_CHUNK_SIZE), b""):
                        yield c
                        yielded += len(c)
                        chunk_n += 1
                        if chunk_n % 30 == 0:
                            _touch_phone(client_ip, "/video/2", protocol, len(c))
                udp_flood.running = False
                _update_phone_stats(client_ip, yielded, "/video/2", protocol)
                _log_metric_db(2, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return Response(gen2(), status=200, mimetype="video/mp4",
                            headers={
                                "Content-Length": str(file_size),
                                "Accept-Ranges": "bytes",
                                "Content-Type": "video/mp4",
                                "Cache-Control": "no-cache"
                            })

    # ── Stream 3: Mixed – TCP HTTP + background UDP + ICMP ping ─────────────
    elif stream_id == 3:
        udp_sock3 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        def mixed_udp():
            seq = 0
            while getattr(mixed_udp, "running", True):
                pkt = f'{{"stream":"mixed","proto":"udp","seq":{seq}}}'.encode()
                try:
                    udp_sock3.sendto(pkt, (client_ip, 12346))
                except Exception:
                    pass
                seq += 1
                time.sleep(random.uniform(0.05, 0.2))
            udp_sock3.close()
        def mixed_icmp():
            is_win = platform.system().lower() == "windows"
            while getattr(mixed_icmp, "running", True):
                cmd = ["ping", "-n", "1", "-w", "500", client_ip] if is_win else ["ping", "-c", "1", "-W", "1", client_ip]
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                except Exception:
                    pass
                time.sleep(random.uniform(1.0, 3.0))
        mixed_udp.running = True
        mixed_icmp.running = True
        threading.Thread(target=mixed_udp,  daemon=True).start()
        threading.Thread(target=mixed_icmp, daemon=True).start()

        if not os.path.exists(video_path):
            mixed_udp.running = False
            mixed_icmp.running = False
            return jsonify({"error": f"Video file not found: {fname}"}), 404
        file_size3 = os.path.getsize(video_path)

        def _stop_mixed():
            mixed_udp.running  = False
            mixed_icmp.running = False

        rng3 = _parse_range(request.headers.get("Range"), file_size3)
        if rng3:
            byte1, byte2, length = rng3
            def gen3_range():
                yielded = 0
                chunk_n = 0
                for chunk in _file_range_gen(video_path, byte1, length):
                    yield chunk
                    yielded += len(chunk)
                    chunk_n += 1
                    if chunk_n % 30 == 0:
                        _touch_phone(client_ip, "/video/3", protocol, len(chunk))
                _stop_mixed()
                _update_phone_stats(client_ip, yielded, "/video/3", protocol)
                _log_metric_db(3, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return _range_response(gen3_range, byte1, byte2, file_size3, length)
        else:
            def gen3():
                yielded = 0
                chunk_n = 0
                with open(video_path, "rb") as fh3:
                    for c in iter(lambda: fh3.read(VIDEO_CHUNK_SIZE), b""):
                        yield c
                        yielded += len(c)
                        chunk_n += 1
                        if chunk_n % 30 == 0:
                            _touch_phone(client_ip, "/video/3", protocol, len(c))
                _stop_mixed()
                _update_phone_stats(client_ip, yielded, "/video/3", protocol)
                _log_metric_db(3, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return Response(gen3(), status=200, mimetype="video/mp4",
                            headers={
                                "Content-Length": str(file_size3),
                                "Accept-Ranges": "bytes",
                                "Content-Type": "video/mp4",
                                "Cache-Control": "no-cache"
                            })

    # ── Stream 4: Throttled TCP – sustained 512 KB/s ─────────────────────────
    elif stream_id == 4:
        if not os.path.exists(video_path):
            return jsonify({"error": f"Video file not found: {fname}"}), 404
        RATE = 512 * 1024  # 512 KB/s
        file_size4 = os.path.getsize(video_path)

        rng4 = _parse_range(request.headers.get("Range"), file_size4)
        if rng4:
            byte1, byte2, length = rng4
            def gen4_range():
                yielded = 0
                chunk_n = 0
                with open(video_path, "rb") as f4:
                    f4.seek(byte1)
                    remaining = length
                    while remaining > 0:
                        chunk = f4.read(min(VIDEO_CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        t0 = time.time()
                        yield chunk
                        remaining -= len(chunk)
                        yielded   += len(chunk)
                        chunk_n   += 1
                        elapsed   = time.time() - t0
                        expected  = len(chunk) / RATE
                        if expected > elapsed:
                            time.sleep(expected - elapsed)
                        if chunk_n % 30 == 0:
                            _touch_phone(client_ip, "/video/4", protocol, len(chunk))
                _update_phone_stats(client_ip, yielded, "/video/4", protocol)
                _log_metric_db(4, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return _range_response(gen4_range, byte1, byte2, file_size4, length)
        else:
            def gen4():
                yielded = 0
                chunk_n = 0
                for c in _throttled_generator(video_path, RATE):
                    yield c
                    yielded += len(c)
                    chunk_n += 1
                    if chunk_n % 30 == 0:
                        _touch_phone(client_ip, "/video/4", protocol, len(c))
                _update_phone_stats(client_ip, yielded, "/video/4", protocol)
                _log_metric_db(4, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return Response(gen4(), status=200, mimetype="video/mp4",
                            headers={
                                "Content-Length": str(file_size4),
                                "Accept-Ranges": "bytes",
                                "Content-Type": "video/mp4",
                                "Cache-Control": "no-cache"
                            })

    # ── Stream 5: HTTP 206 Partial Content (byte-range) ─────────────────────
    elif stream_id == 5:
        if not os.path.exists(video_path):
            return jsonify({"error": f"Video file not found: {fname}"}), 404

        file_size = os.path.getsize(video_path)
        range_header = request.headers.get("Range", None)
        if not range_header:
            # No range requested – send full file with Accept-Ranges advertised
            dur = round((time.time() - start_time) * 1000, 2)
            _update_phone_stats(client_ip, file_size, "/video/5", protocol)
            _log_metric_db(5, client_ip, protocol, file_size, dur)
            resp = send_file(video_path, mimetype="video/mp4", conditional=True)
            resp.headers["Accept-Ranges"] = "bytes"
            resp.headers["Content-Type"] = "video/mp4"
            resp.headers["Cache-Control"] = "no-cache"
            return resp

        # Parse byte range
        byte1, byte2 = 0, file_size - 1
        m = range_header.replace("bytes=", "").split("-")
        byte1 = int(m[0]) if m[0] else 0
        byte2 = int(m[1]) if len(m) > 1 and m[1] else file_size - 1
        byte2 = min(byte2, file_size - 1)
        length = byte2 - byte1 + 1

        def gen5_range():
            chunk_n = 0
            with open(video_path, "rb") as f:
                f.seek(byte1)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(VIDEO_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)
                    chunk_n += 1
                    if chunk_n % 30 == 0:
                        _touch_phone(client_ip, "/video/5", protocol, len(chunk))
            dur = round((time.time() - start_time) * 1000, 2)
            _update_phone_stats(client_ip, length, "/video/5", protocol)
            _log_metric_db(5, client_ip, protocol, length, dur)

        return Response(
            gen5_range(), status=206, mimetype="video/mp4",
            headers={
                "Content-Range":  f"bytes {byte1}-{byte2}/{file_size}",
                "Accept-Ranges":  "bytes",
                "Content-Length": str(length),
                "Content-Type":   "video/mp4",
                "Cache-Control":  "no-cache",
            },
        )

    # ── Stream 6: Maximum-rate TCP stress (no throttle, no delay) ────────────
    elif stream_id == 6:
        if not os.path.exists(video_path):
            return jsonify({"error": f"Video file not found: {fname}"}), 404
        file_size6 = os.path.getsize(video_path)
        LARGE_CHUNK = 262144  # 256 KB chunks for max throughput

        rng6 = _parse_range(request.headers.get("Range"), file_size6)
        if rng6:
            byte1, byte2, length = rng6
            def gen6_range():
                yielded = 0
                chunk_n = 0
                rate_mbps = STREAM_RATE_LIMIT_MBPS.get(6)
                rate_bytes_per_sec = rate_mbps * 1_000_000 / 8 if rate_mbps else None
                for chunk in _file_range_gen(video_path, byte1, length, LARGE_CHUNK):
                    t0 = time.time()
                    yield chunk
                    yielded += len(chunk)
                    chunk_n += 1
                    if rate_bytes_per_sec:
                        elapsed = time.time() - t0
                        expected = len(chunk) / rate_bytes_per_sec
                        if expected > elapsed:
                            time.sleep(expected - elapsed)
                    if chunk_n % 10 == 0:
                        _touch_phone(client_ip, "/video/6", protocol, len(chunk))
                _update_phone_stats(client_ip, yielded, "/video/6", protocol)
                _log_metric_db(6, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return _range_response(gen6_range, byte1, byte2, file_size6, length)
        else:
            def gen6():
                yielded = 0
                chunk_n = 0
                rate_mbps = STREAM_RATE_LIMIT_MBPS.get(6)
                rate_bytes_per_sec = rate_mbps * 1_000_000 / 8 if rate_mbps else None
                with open(video_path, "rb") as f6:
                    while True:
                        c = f6.read(LARGE_CHUNK)
                        if not c:
                            break
                        t0 = time.time()
                        yield c
                        yielded += len(c)
                        chunk_n += 1
                        if rate_bytes_per_sec:
                            elapsed = time.time() - t0
                            expected = len(c) / rate_bytes_per_sec
                            if expected > elapsed:
                                time.sleep(expected - elapsed)
                        if chunk_n % 10 == 0:
                            _touch_phone(client_ip, "/video/6", protocol, len(c))
                _update_phone_stats(client_ip, yielded, "/video/6", protocol)
                _log_metric_db(6, client_ip, protocol, yielded,
                               round((time.time() - start_time) * 1000, 2))
            return Response(gen6(), status=200, mimetype="video/mp4",
                            headers={
                                "Content-Length": str(file_size6),
                                "Accept-Ranges": "bytes",
                                "Content-Type": "video/mp4",
                                "Cache-Control": "no-cache"
                            })


# ===========================================================================
# Web pages (phone simulation targets)
# ===========================================================================

@app.route("/stream")
def stream_page():
    """
    GET /stream – styled video streaming web page.

    Simulates a phone or laptop accessing a local streaming service.
    The page uses JavaScript fetch() to download /video in the background
    when the user clicks Play, generating high-bandwidth TCP traffic
    Wireshark captures as a sustained large-payload flow.
    """
    ip = request.remote_addr
    with stats_lock:
        if ip not in phone_clients:
            phone_clients[ip] = {"label": f"Phone @{ip}", "request_count": 0,
                                  "bytes_served": 0, "last_seen": None, "last_url": "/stream"}
        phone_clients[ip]["request_count"] += 1
        phone_clients[ip]["last_seen"]      = time.time()
        phone_clients[ip]["last_url"]       = "/stream"
        ts_events.append((time.time(), ip, "http", 500))
    return render_template("stream.html")


@app.route("/browse")
def browse_page():
    """
    GET /browse – styled blog browsing web page.

    Simulates a phone doing light interactive browsing: reading articles,
    liking posts, and posting comments.  Each interaction generates a small
    HTTP POST to /api/like or /api/comment, adding micro-transaction traffic
    alongside the heavier ESP32 and video streams.
    """
    with blog_lock:
        posts = list(BLOG_POSTS)   # read-only snapshot; inner dicts not mutated here
    ip = request.remote_addr
    with stats_lock:
        if ip not in phone_clients:
            phone_clients[ip] = {"label": f"Phone @{ip}", "request_count": 0,
                                  "bytes_served": 0, "last_seen": None, "last_url": "/browse"}
        phone_clients[ip]["request_count"] += 1
        phone_clients[ip]["last_seen"]      = time.time()
        phone_clients[ip]["last_url"]       = "/browse"
        ts_events.append((time.time(), ip, "http", 300))
    return render_template("browse.html", posts=posts)


@app.route("/api/like/<int:post_id>", methods=["POST"])
def like_post(post_id: int):
    """
    POST /api/like/<post_id> – increments the like counter for a blog post.

    Called via JavaScript fetch() from browse.html and by phone_sim.py.
    Returns {"likes": <new_count>} on success, or 404 if post_id is not found.
    Each call is a small HTTP round-trip that adds realistic micro-transaction
    traffic to the Wireshark capture.
    """
    with blog_lock:
        for post in BLOG_POSTS:
            if post["id"] == post_id:
                post["likes"] += 1
                new_count = post["likes"]
                print(f"[LIKE]    post_id={post_id}  total={new_count}  from={request.remote_addr}")
                _touch_phone(request.remote_addr, "/browse")
                return jsonify({"likes": new_count}), 200
    return jsonify({"error": "post not found"}), 404


@app.route("/api/comment/<int:post_id>", methods=["POST"])
def add_comment(post_id: int):
    """
    POST /api/comment/<post_id> – appends a comment to a blog post.

    Expects JSON body:  {"author": "Name", "text": "Comment text"}
    Returns {"comments": [...all comments on this post...]} on success.
    Input lengths are capped server-side (author 50 chars, text 500 chars)
    to prevent oversized payloads from skewing the traffic dataset.
    """
    data   = request.get_json(silent=True) or {}
    author = str(data.get("author", "Anonymous"))[:50]
    text   = str(data.get("text",   ""))[:500]

    if not text.strip():
        return jsonify({"error": "comment text is empty"}), 400

    comment = {"author": author, "text": text}
    with blog_lock:
        for post in BLOG_POSTS:
            if post["id"] == post_id:
                post["comments"].append(comment)
                all_comments = list(post["comments"])
                print(f"[COMMENT] post_id={post_id}  author={author}  from={request.remote_addr}")
                _touch_phone(request.remote_addr, "/browse")
                return jsonify({"comments": all_comments}), 200
    return jsonify({"error": "post not found"}), 404


# ===========================================================================
# Dashboard
# ===========================================================================

@app.route("/dashboard")
def dashboard_page():
    """GET /dashboard – real-time device monitoring dashboard."""
    return render_template("dashboard.html")


@app.route("/api/dashboard")
def api_dashboard():
    """GET /api/dashboard – JSON stats snapshot polled by dashboard.html."""
    now = time.time()

    with storage_lock:
        http_total = len(received_packets)
        udp_total  = len(udp_log)

    with stats_lock:
        ds_snap  = {k: dict(v) for k, v in device_stats.items()}
        pc_snap  = {k: dict(v) for k, v in phone_clients.items()}
        ts_snap  = list(ts_events)
        rev_snap = list(recent_events)
        vc       = video_count

    def esp_status(last_seen):
        if last_seen is None:       return "offline"
        age = now - last_seen
        if age < 5:                 return "online"
        if age < 30:                return "idle"
        return "offline"

    def phone_status(last_seen):
        if last_seen is None:       return "offline"
        age = now - last_seen
        if age < 30:                return "online"
        if age < 300:               return "idle"
        return "offline"

    def fmt_t(epoch):
        return datetime.utcfromtimestamp(epoch).strftime("%H:%M:%S") if epoch else "—"

    delivery = telemetry.delivery_summary(telemetry.RUN_ID)

    def _pkt_loss_pct(name):
        rows = [r for r in delivery if r["device"] == name and r["protocol"] == "UDP"]
        expected = sum(r["last_seq"] - r["first_seq"] + 1 for r in rows)
        return round(100 * sum(r["observed_missing"] for r in rows) / expected, 2) if expected else None

    devices = {
        name: {
            "ip":               ds["ip"],
            "label":            ds["label"],
            "protocols":        ds["protocols"],
            "status":           esp_status(ds["last_seen"]),
            "http_count":       ds["http_count"],
            "udp_count":        ds["udp_count"],
            "http_bytes":       ds["http_bytes"],
            "udp_bytes":        ds["udp_bytes"],
            "total_bytes":      ds["total_bytes"],
            "last_seen":        fmt_t(ds["last_seen"]),
            "last_data":        ds["last_data"],
            "latency_ms":       ds.get("latency_ms"),
            "dropped_udp":      ds.get("dropped_udp", 0),
            "packet_loss_pct":  _pkt_loss_pct(name),
            "loss_measurement": "cumulative_observed_udp_sequence_gaps",
        }
        for name, ds in ds_snap.items()
    }

    phones = {
        ip: {
            "label":         pc["label"],
            "status":        phone_status(pc["last_seen"]),
            "request_count": pc["request_count"],
            "bytes_served":  pc["bytes_served"],
            "last_seen":     fmt_t(pc["last_seen"]),
            "last_url":      pc["last_url"],
            "protocols":     "+".join(sorted(pc.get("protocols") or set())) or "HTTP",
        }
        for ip, pc in pc_snap.items()
    }

    phone_bytes  = sum(pc["bytes_served"] for pc in pc_snap.values())
    device_bytes = sum(ds["total_bytes"] for ds in ds_snap.values())
    total_dropped = sum(ds.get("dropped_udp", 0) for ds in ds_snap.values())
    totals = {
        "http_packets":  http_total,
        "udp_packets":   udp_total,
        "total_bytes":   device_bytes + phone_bytes,
        "video_streams": vc,
        "dropped_packets": total_dropped,
        "current_drop_rate_pct": round(_current_drop_rate() * 100, 2),
    }

    # 30-second sliding window, 1-second buckets
    start_sec = int(now) - 29
    buckets   = 30
    labels_ts = [
        datetime.utcfromtimestamp(start_sec + i).strftime("%H:%M:%S")
        for i in range(buckets)
    ]
    series = {f"{n}_http": [0] * buckets for n in ds_snap}
    series.update({f"{n}_udp": [0] * buckets for n in ds_snap})
    series["phones"] = [0] * buckets

    for ep, src, proto, _ in ts_snap:
        bucket = int(ep) - start_sec
        if 0 <= bucket < buckets:
            key = f"{src}_{proto}" if src in ds_snap else "phones"
            if key in series:
                series[key][bucket] += 1

    return jsonify({
        "run_id":        telemetry.RUN_ID,
        "server_time":   now,
        "latency_kind": "http_rtt" if gateway_topology.enabled() else "icmp_rtt",
        "gateway_mode": gateway_topology.enabled(),
        "gateway":       monitoring_status.snapshot(telemetry.RUN_ID),
        "uptime_s":      int(now - _SERVER_START),
        "devices":       devices,
        "phones":        phones,
        "totals":        totals,
        "timeseries":    {"labels": labels_ts, "series": series},
        "recent_events": list(reversed(rev_snap)),
    }), 200


@app.route("/api/metrics")
def api_metrics():
    """
    GET /api/metrics – returns historical stream metrics from SQLite for dashboard graphs.
    Returns last 100 stream records sorted by timestamp ascending.
    """
    try:
        con = sqlite3.connect(METRICS_DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM stream_metrics ORDER BY id DESC LIMIT 100"
        ).fetchall()
        con.close()
        data = [dict(r) for r in reversed(rows)]
        return jsonify({"metrics": data}), 200
    except Exception as e:
        return jsonify({"error": str(e), "metrics": []}), 200


@app.route("/api/metrics/export")
def api_metrics_export():
    """GET /api/metrics/export – downloads video_log.csv."""
    if os.path.exists(VIDEO_LOG_PATH):
        return send_file(VIDEO_LOG_PATH, mimetype="text/csv",
                         as_attachment=True, download_name="metrics_export.csv")
    return jsonify({"error": "No metrics data yet"}), 404


@app.route("/api/export/sqlite")
def api_export_sqlite():
    """GET /api/export/sqlite – dumps all SQLite rows as JSON for the data pipeline."""
    try:
        con = sqlite3.connect(METRICS_DB_PATH)
        con.row_factory = sqlite3.Row
        stream_rows = [dict(r) for r in con.execute(
            "SELECT * FROM stream_metrics ORDER BY id ASC"
        ).fetchall()]
        esp_rows = [dict(r) for r in con.execute(
            "SELECT * FROM esp_metrics ORDER BY id ASC"
        ).fetchall()]
        con.close()
        return jsonify({"stream_metrics": stream_rows, "esp_metrics": esp_rows}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analysis")
def api_analysis():
    """GET /api/analysis – returns saved analysis PNGs as base64 for the dashboard."""
    import base64
    graphs_dir = os.path.join(_BASE, "..", "graphs")
    graph_names = ["latency", "packet_loss", "throughput",
                   "protocol_comparison", "comparison_table"]
    result = {}
    for name in graph_names:
        path = os.path.join(graphs_dir, f"{name}.png")
        if os.path.exists(path):
            with open(path, "rb") as f:
                result[name] = base64.b64encode(f.read()).decode("utf-8")
    if not result:
        return jsonify({"error": "No analysis graphs found. Run datasets/analyze_results.py first."}), 404
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Background thread: UDP listener (port 12345)
# ---------------------------------------------------------------------------
def udp_listener():
    """
    Dedicated thread that receives UDP datagrams from ESP32 devices.

    Architecture note
    -----------------
    UDP is connectionless, so there is no handshake – the socket just
    blocks on recvfrom() until a datagram arrives.  The loop runs without
    any sleep to keep up with the high-rate bursts from ESP32_1 and ESP32_3
    (50 – 200 ms intervals → up to 20 packets/second per device).

    Each datagram is expected to be a raw UTF-8 JSON string matching the
    same dataset entries that HTTP POST uses, e.g.:
        {"device":"esp32_3","temp":21,"hum":55}

    Stored entry format
    -------------------
    {
        "timestamp"   : ISO-8601 UTC string,
        "source_ip"   : sender IP address,
        "source_port" : ephemeral UDP port assigned by sender OS,
        "raw"         : decoded UTF-8 string,
        "size"        : datagram size in bytes
    }
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # SO_REUSEADDR lets us restart the server quickly without "address in use" errors
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_HOST, UDP_PORT))

    print(f"[THREAD:UDP] Listening on UDP {UDP_HOST}:{UDP_PORT}")

    while True:
        try:
            # Block until a datagram arrives; returns (bytes, (ip, port))
            data, addr = sock.recvfrom(UDP_BUFFER_SIZE)
            raw_text   = data.decode("utf-8", errors="replace")
            timestamp  = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"

            # Pre-parse to identify device before drop decision
            try:
                _pre_parsed = _json.loads(raw_text)
                if not isinstance(_pre_parsed, dict):
                    _pre_parsed = {}
                _pre_dev    = _pre_parsed.get("device", "")
            except Exception:
                _pre_parsed, _pre_dev = {}, ""

            # Simulate packet loss: drop with current probability
            if random.random() < _current_drop_rate():
                telemetry.record(_pre_parsed, gateway_topology.logical_source(_pre_parsed, addr[0]),
                                 "UDP", len(data), received=False, peer_ip=addr[0])
                with stats_lock:
                    if _pre_dev in device_stats:
                        device_stats[_pre_dev]["dropped_udp"] += 1
                print(f"[DROP]    {timestamp}  from {addr[0]}  device={_pre_dev}  (simulated drop)")
                continue

            entry = {
                "timestamp"  : timestamp,
                "source_ip"  : addr[0],
                "source_port": addr[1],
                "raw"        : raw_text,
                "size"       : len(data),
            }

            with storage_lock:
                udp_log.append(entry)

            telemetry.record(_pre_parsed, gateway_topology.logical_source(_pre_parsed, addr[0]),
                             "UDP", len(data), peer_ip=addr[0])

            # Reuse pre-parsed values
            _parsed = _pre_parsed
            _dev    = _pre_dev
            with stats_lock:
                if _dev in device_stats:
                    ds = device_stats[_dev]
                    ds["udp_count"]   += 1
                    ds["udp_bytes"]   += len(data)
                    ds["total_bytes"] += len(data)
                    ds["last_seen"]    = time.time()
                    ds["last_data"]    = {k: v for k, v in _parsed.items()
                                         if k not in ("extra", "log")}
                    recent_events.append({
                        "time":   datetime.utcnow().strftime("%H:%M:%S"),
                        "source": ds["label"],
                        "proto":  "UDP",
                        "detail": (f"temp={_parsed.get('temp','?')} "
                                   f"hum={_parsed.get('hum','?')}  {len(data)}B"),
                    })
                else:
                    # Track UDP traffic from phone clients (non-ESP32 devices)
                    if addr[0] in phone_clients:
                        pc = phone_clients[addr[0]]
                        pc["last_seen"] = time.time()
                        pc["bytes_served"] += len(data)
                        recent_events.append({
                            "time":   datetime.utcnow().strftime("%H:%M:%S"),
                            "source": pc["label"],
                            "proto":  "UDP",
                            "detail": f"UDP packet {len(data)}B",
                        })
                ts_events.append((time.time(), _dev or addr[0], "udp", len(data)))

            # Truncate the printed payload to 80 chars so the console stays readable
            print(
                f"[UDP-RX]  {timestamp}  from {addr[0]}:{addr[1]}  "
                f"size={len(data)} bytes  data={raw_text[:80]}"
                + ("..." if len(raw_text) > 80 else "")
            )

        except Exception as exc:
            print(f"[THREAD:UDP] ERROR: {exc}")


# ---------------------------------------------------------------------------
# Background thread: server-initiated HTTP GET traffic
# ---------------------------------------------------------------------------
def traffic_generator():
    """
    Sends HTTP GET requests to every ESP32's /data endpoint in round-robin
    order, creating server→device TCP traffic to complement the device→server
    POST traffic.

    The ESP32 responds with a variable-length plain-text body (50 – 500 bytes
    of 'X' characters, generated on the fly in handleData()).  This simulates
    a lightweight download and ensures Wireshark sees traffic in both
    directions on the HTTP sessions.

    Timing: random interval 0.2 – 2.0 s between successive GET requests.
    """
    device_names = list(ESP32_DEVICES.keys())
    idx = 0

    print("[THREAD:GET] Traffic generator started.")

    while True:
        name = device_names[idx % len(device_names)]
        ip   = ESP32_DEVICES[name]
        url  = gateway_topology.probe_url(name, ip)

        try:
            probe_started = time.perf_counter()
            resp = requests.get(url, timeout=3)
            if gateway_topology.enabled():
                with stats_lock:
                    device_stats[name]["latency_ms"] = (time.perf_counter() - probe_started) * 1000 if resp.ok else None
            print(
                f"[TX-GET]  -> {name} ({ip})  "
                f"status={resp.status_code}  "
                f"response_size={len(resp.content)} bytes"
            )
        except requests.exceptions.ConnectionError:
            if gateway_topology.enabled():
                with stats_lock:
                    device_stats[name]["latency_ms"] = None
            print(f"[TX-GET]  -> {name} ({ip})  ERROR: connection refused / unreachable")
        except requests.exceptions.Timeout:
            if gateway_topology.enabled():
                with stats_lock:
                    device_stats[name]["latency_ms"] = None
            print(f"[TX-GET]  -> {name} ({ip})  ERROR: timeout")
        except Exception as exc:
            print(f"[TX-GET]  -> {name} ({ip})  ERROR: {exc}")

        idx += 1

        # Random interval keeps the traffic pattern non-periodic for realism
        delay = random.uniform(0.2, 2.0)
        time.sleep(delay)


# ---------------------------------------------------------------------------
# Background thread: ICMP ping sweeps
# ---------------------------------------------------------------------------
def ping_worker():
    """
    Periodically pings all ESP32 devices to inject ICMP Echo Request /
    Echo Reply pairs into the capture.  This adds a third protocol layer
    (alongside HTTP/TCP and UDP) and is useful for latency analysis.

    Timing
    ------
    - 0.5 – 1.5 s gap between each individual ping within a sweep
    - 2.0 – 5.0 s pause after a full sweep of all devices

    The command is built to be OS-agnostic:
    - Windows : ping -n 1 -w 1000 <ip>
    - Linux / macOS: ping -c 1 -W 1 <ip>
    """
    if gateway_topology.enabled():
        print("[PING] NAT nodes use forwarded HTTP probes; ICMP probing disabled.")
        return
    is_windows = platform.system().lower() == "windows"

    print("[THREAD:PING] Ping worker started.")

    while True:
        for name, ip in ESP32_DEVICES.items():
            # Single echo request; stdout/stderr discarded – we only care about return code
            if is_windows:
                cmd = ["ping", "-n", "1", "-w", "1000", ip]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", ip]

            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3,
                )
                status = "OK" if result.returncode == 0 else "UNREACHABLE"
                # Parse RTT from ping output
                rtt = None
                if result.returncode == 0:
                    # Windows: "Average = 12ms"  |  Linux/mac: "min/avg/max = 1.2/3.4/5.6 ms"
                    m = re.search(r"[Aa]verage\s*=\s*(\d+)\s*ms", result.stdout)
                    if not m:
                        m = re.search(r"min/avg/max[^=]*=\s*[\d.]+/([\d.]+)/", result.stdout)
                    if m:
                        rtt = float(m.group(1))
                if rtt is None and result.returncode == 0:
                    # Fallback: extract any number followed by ms
                    m2 = re.search(r"(\d+\.?\d*)\s*ms", result.stdout)
                    if m2:
                        rtt = float(m2.group(1))
                with stats_lock:
                    device_stats[name]["latency_ms"] = rtt
                print(f"[PING]    {name} ({ip})  {status}"
                      + (f"  rtt={rtt:.1f}ms" if rtt is not None else ""))
            except subprocess.TimeoutExpired:
                print(f"[PING]    {name} ({ip})  TIMEOUT")
            except Exception as exc:
                print(f"[PING]    {name} ({ip})  ERROR: {exc}")

            # Short gap so pings to different devices are distinguishable in Wireshark
            time.sleep(random.uniform(0.5, 1.5))

        # Pause between full sweeps to avoid flooding at the ICMP layer
        time.sleep(random.uniform(2.0, 5.0))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  IoT Network Testing System – Flask Server")
    print(f"  HTTP (Flask) : {SERVER_HOST}:{SERVER_PORT}")
    print(f"  UDP listener : {UDP_HOST}:{UDP_PORT}")
    print(f"  Devices      : {list(ESP32_DEVICES.keys())}")
    print("=" * 60)

    # All background threads are daemon threads: they stop automatically
    # when the main process exits (Ctrl-C or process kill).
    t_udp  = threading.Thread(target=udp_listener,      daemon=True, name="UDP-Thread")
    t_get  = threading.Thread(target=traffic_generator, daemon=True, name="GET-Thread")
    t_ping = threading.Thread(target=ping_worker,       daemon=True, name="PING-Thread")

    t_udp.start()   # UDP datagram receiver
    t_get.start()   # HTTP GET generator  (server → ESP32)
    t_ping.start()  # ICMP ping sweeper   (server → ESP32)

    # threaded=True: Flask spawns a new thread per incoming HTTP request,
    # which allows simultaneous POSTs from multiple ESP32 devices.
    # debug=False:   disables the reloader, which would interfere with threads.
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True, debug=False)
