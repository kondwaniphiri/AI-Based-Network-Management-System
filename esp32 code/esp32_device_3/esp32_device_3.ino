/*
 * ============================================================
 * IoT Network Testing System – ESP32 Device 3
 * ============================================================
 * Role     : UDP only (high-speed congestion source)
 * Static IP: 192.168.8.103
 * Server   : 192.168.8.100
 *
 * This device sends data exclusively over UDP at a high rate
 * (50–200 ms between datagrams).  It acts as a congestion
 * source on the network, allowing comparison of how HTTP
 * traffic from Devices 1 and 2 is affected by concurrent
 * high-rate UDP traffic in Wireshark captures and ML analysis.
 *
 * Protocol flags:
 *   ENABLE_HTTP false  – HTTP disabled; no TCP connections made
 *   ENABLE_UDP  true   – UDP datagram to port 12345 every 50–200 ms
 *
 * Board   : ESP32 Dev Module (or any ESP32 board)
 * IDE     : Arduino IDE 2.x with arduino-esp32 board package
 * Libraries: WiFi, HTTPClient, WebServer, WiFiUDP (all built-in)
 * ============================================================
 */

#include <WiFi.h>
#include <esp_system.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <WiFiUDP.h>

// =======================
// <<< CHANGE PER DEVICE >>>
// =======================
#define DEVICE_ID       "esp32_3"   // must match the server's device name
#define STATIC_IP_LAST  103         // unique host address; do not duplicate it

// ENABLE_HTTP false = no TCP connections from this device
// ENABLE_UDP  true  = high-rate UDP datagrams to the Flask server
#define ENABLE_HTTP false
#define ENABLE_UDP  true

// =======================
// <<< WIFI SETTINGS >>>
// Change to match your network.
// =======================
#if __has_include("gateway_network.local.h")
#include "gateway_network.local.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID  ""  // Configure locally; never commit real Wi-Fi credentials.
#define WIFI_PASS  ""
#endif

// =======================
// NETWORK CONFIG
// =======================
#ifndef SERVER_IP
#define SERVER_IP   "192.168.8.100"  // Flask server PC
#endif
#ifndef LAB_NET_A
#define LAB_NET_A 192
#define LAB_NET_B 168
#define LAB_NET_C 8
#endif
#define SERVER_PORT 5000             // Flask HTTP port (used only by HTTP sends)
#ifndef CONTROL_TOKEN
#define CONTROL_TOKEN ""  // Set a lab control token to enable POST /config.
#endif
#define UDP_PORT    12345            // UDP listener port on the Flask server

// =======================
// GLOBALS
// =======================
WebServer server(80);   // HTTP server – still answers GET /data from the Flask
                        // even though this device doesn't POST over HTTP
WiFiUDP udp;            // UDP socket for high-rate outbound datagrams

// millis()-based non-blocking timing
unsigned long lastHttpSend = 0;  // unused (ENABLE_HTTP false)
unsigned long lastUdpSend  = 0;

long httpInterval = 1000;  // unused
// Shorter UDP intervals create a higher-rate stress source; longer intervals
// reduce congestion. The value is re-randomised after every send.
long controlledUdpInterval = 0; // 0 retains the original random schedule
long udpInterval  = 100;

// =======================
// DATASET
// Same entries as Devices 1 and 2 – dataset is shared across the project
// to keep content deterministic.  Only protocol and timing differ.
//   Entry 0 – small  (~40 bytes)
//   Entry 1 – small+ (~55 bytes)
//   Entry 2 – medium (~90 bytes)
//   Entry 3 – large  (~150 bytes)
// =======================
const char* dataset[] = {
  "{\"device\":\"" DEVICE_ID "\",\"temp\":21,\"hum\":55}",
  "{\"device\":\"" DEVICE_ID "\",\"temp\":25,\"hum\":60,\"extra\":\"AAAAAAAAAAAAAA\"}",
  "{\"device\":\"" DEVICE_ID "\",\"temp\":30,\"hum\":65,\"extra\":\"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\"}",
  "{\"device\":\"" DEVICE_ID "\",\"temp\":28,\"hum\":70,\"log\":\"CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\"}"
};

// Independent sequence spaces prevent HTTP traffic appearing as UDP loss.
uint32_t httpSeq = 0, udpSeq = 0;
String bootId;

int dataIndex = 0;
int dataSize  = sizeof(dataset) / sizeof(dataset[0]);

String measuredPayload(bool isUdp) {
  String body = dataset[dataIndex];
  body.remove(body.length() - 1);
  uint32_t seq = isUdp ? udpSeq++ : httpSeq++;
  return body + ",\"boot_id\":\"" + bootId + "\",\"seq\":" + String(seq) + "}";
}


// =======================
// HTTP RESPONSE (GET)
// Even though this device sends no HTTP POST traffic, it still hosts a
// WebServer so the Flask traffic_generator can send GET /data to it.
// Variable body size (50–500 bytes) ensures response sizes are diverse.
// =======================
void handleData() {
  String response = "ESP32 RESPONSE ";

  int size = random(50, 500);
  for (int i = 0; i < size; i++) {
    response += "X";
  }

  server.send(200, "text/plain", response);
}

// =======================
// SEND HTTP
// =======================
void sendHTTP() {
  HTTPClient http;

  String url = "http://" + String(SERVER_IP) + ":" + String(SERVER_PORT) + "/data";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  String payload = measuredPayload(false);

  int code = http.POST(payload);

  Serial.print("[HTTP] Sent → ");
  Serial.print(payload);
  Serial.print(" | Code: ");
  Serial.println(code);

  http.end();
}

// =======================
// SEND UDP
// Core function for this device.  Sends dataset[dataIndex] as a raw
// UDP datagram at a high rate (50–200 ms) to act as a congestion source.
// The datagram payload is identical to what the HTTP devices send,
// making it easy to compare packet loss and delay in Wireshark.
// =======================
void sendUDP() {
  udp.beginPacket(SERVER_IP, UDP_PORT);

  String payload = measuredPayload(true);
  udp.write((const uint8_t*)payload.c_str(), payload.length());

  udp.endPacket();

  Serial.print("[UDP]  Sent \u2192 ");
  Serial.println(payload);
}

// =======================
// SETUP
// =======================
void handleConfig() {
  if (String(CONTROL_TOKEN).length() == 0 ||
      server.header("X-Control-Token") != String(CONTROL_TOKEN)) {
    server.send(403, "text/plain", "Control disabled or invalid token");
    return;
  }
  String value = server.arg("udp_interval_ms");
  for (unsigned int i = 0; i < value.length(); i++) {
    if (!isDigit(value[i])) { server.send(400, "text/plain", "Integer required"); return; }
  }
  long interval = value.toInt();
  if (interval < 50 || interval > 10000) {
    server.send(400, "text/plain", "Interval must be 50..10000 ms");
    return;
  }
  controlledUdpInterval = interval;
  udpInterval = interval;
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void setup() {
  Serial.begin(115200);
  bootId = String(esp_random(), HEX) + String(esp_random(), HEX);

  IPAddress local_IP(LAB_NET_A, LAB_NET_B, LAB_NET_C, STATIC_IP_LAST);
  IPAddress gateway(LAB_NET_A, LAB_NET_B, LAB_NET_C, 1);
  IPAddress subnet(255, 255, 255, 0);

  WiFi.config(local_IP, gateway, subnet);  // must be before WiFi.begin()
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnected!");
  Serial.println(WiFi.localIP());

  server.on("/data", handleData);  // serve GET /data for the Flask GET thread
  const char* controlHeaders[] = {"X-Control-Token"};
  server.collectHeaders(controlHeaders, 1);
  server.on("/config", HTTP_POST, handleConfig);
  server.begin();

  udp.begin(0);  // open UDP socket with OS-assigned source port

  randomSeed(analogRead(0));  // noise-based seed for random intervals
}

// =======================
// LOOP
// High-rate UDP-only loop.
// server.handleClient() is still called so GET /data requests from the
// Flask traffic_generator are handled even though this device never POSTs.
// =======================
void loop() {
  server.handleClient();

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    return;
  }

  unsigned long now = millis();

  // HTTP TRAFFIC – disabled (ENABLE_HTTP false); block never executes
  if (ENABLE_HTTP && now - lastHttpSend >= (unsigned long)httpInterval) {
    sendHTTP();
    lastHttpSend = now;
    httpInterval = random(1100, 3000);
  }

  // UDP TRAFFIC – high-speed bursts (50–200 ms interval)
  if (ENABLE_UDP && now - lastUdpSend >= (unsigned long)udpInterval) {
    sendUDP();
    lastUdpSend = now;
    udpInterval = controlledUdpInterval > 0 ? controlledUdpInterval : random(1050, 1200);   // re-randomise for next send (+1s base)
  }

  // Advance dataset index after any send in this iteration
  if ((now - lastHttpSend < 10) || (now - lastUdpSend < 10)) {
    dataIndex = (dataIndex + 1) % dataSize;
  }
}
