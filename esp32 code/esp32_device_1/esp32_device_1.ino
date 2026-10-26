/*
 * ============================================================
 * IoT Network Testing System – ESP32 Device 1
 * ============================================================
 * Role     : HTTP + UDP (dual-protocol node)
 * Static IP: 192.168.8.101
 * Server   : 192.168.8.100
 *
 * This device sends data over BOTH HTTP (TCP) and UDP simultaneously,
 * making it useful for comparing how the two protocols behave under
 * the same payload and timing conditions in Wireshark captures.
 *
 * Protocol flags (see defines below):
 *   ENABLE_HTTP true   – POST /data to Flask every 100–2000 ms (randomised)
 *   ENABLE_UDP  true   – UDP datagram to port 12345 every 50–200 ms (randomised)
 *
 * The same fixed dataset is used for both protocols.
 * Only timing and transport layer differ.
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
// Set these three values when flashing a new physical board.
// =======================
#define DEVICE_ID       "esp32_1"   // must match the server's device name
#define STATIC_IP_LAST  101         // unique host address: use 101/102/103

// Enable/Disable protocols independently.
// true  = this protocol is active for this device.
// false = this protocol is skipped (no send, no delay consumed).
#define ENABLE_HTTP true    // disable to remove this device's TCP traffic
#define ENABLE_UDP  true    // disable to remove this device's UDP traffic

// =======================
// <<< WIFI SETTINGS >>>
// Change to match your network.  Only needs editing once – copy the same
// values to device 2 and device 3.
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
// These values are fixed for the project and should not change unless
// the server PC IP or router gateway is different on your network.
// =======================
#ifndef SERVER_IP
#define SERVER_IP   "192.168.8.100"  // Flask server PC static IP
#endif
#ifndef LAB_NET_A
#define LAB_NET_A 192
#define LAB_NET_B 168
#define LAB_NET_C 8
#endif
#define SERVER_PORT 5000             // Flask HTTP port
#ifndef CONTROL_TOKEN
#define CONTROL_TOKEN ""  // Set a lab control token to enable POST /config.
#endif
#define UDP_PORT    12345            // UDP listener port on the Flask server

// =======================
// GLOBALS
// =======================
WebServer server(80);   // built-in HTTP server – answers GET /data from the Flask
                        // traffic_generator thread (download traffic simulation)
WiFiUDP udp;            // UDP socket for outbound datagrams to the Flask server

// Non-blocking send timing. Change the random ranges below to control packet
// rate: shorter intervals generate more traffic and more possible congestion.
// Instead of blocking delay(), the loop checks millis() against these
// timestamps so HTTP and UDP sends can run concurrently in a single thread.
unsigned long lastHttpSend = 0;  // millis() value at the last HTTP send
unsigned long lastUdpSend  = 0;  // millis() value at the last UDP send

// Current randomised intervals (re-rolled after every send).
long httpInterval = 1000;  // initial value; re-randomised after each send
long controlledUdpInterval = 0; // 0 retains the original random schedule
long udpInterval  = 100;   // initial value; re-randomised after each send

// =======================
// DATASET
// Fixed array of JSON strings sent sequentially, looping indefinitely.
// Payload sizes are intentionally varied to produce diverse packet sizes:
//   Entry 0 – small  (~40 bytes) : minimal fields
//   Entry 1 – small+ (~55 bytes) : with short "extra" string
//   Entry 2 – medium (~90 bytes) : longer "extra" string
//   Entry 3 – large  (~150 bytes): long "log" string
//
// Field names use short forms ("temp", "hum") to match the server's
// parsing logic in receive_data().
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

int dataIndex = 0;                                    // current position in dataset[]
int dataSize  = sizeof(dataset) / sizeof(dataset[0]); // total entries (auto-calculated)

String measuredPayload(bool isUdp) {
  String body = dataset[dataIndex];
  body.remove(body.length() - 1);
  uint32_t seq = isUdp ? udpSeq++ : httpSeq++;
  return body + ",\"boot_id\":\"" + bootId + "\",\"seq\":" + String(seq) + "}";
}


// =======================
// HTTP RESPONSE (GET)
// Called by the WebServer when the Flask traffic_generator thread sends
// GET http://192.168.0.101/data
// Responds with a variable-length plain-text body (50–500 'X' chars).
// Variable size simulates a real download and adds traffic variety for
// Wireshark capture without requiring an external file.
// =======================
void handleData() {
  String response = "ESP32 RESPONSE ";

  // Randomise body length to produce different TCP segment sizes
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
// Sends dataset[dataIndex] as a raw UDP datagram to UDP_PORT on the server.
// UDP is connectionless: no handshake, no acknowledgement, no retry.
// The server's udp_listener() thread receives and logs these packets.
//
// Compared to HTTP, UDP:
//   • has no TCP overhead (SYN/ACK/FIN)
//   • sends at a higher rate (50–200 ms vs 100–2000 ms)
//   • may drop packets under congestion (intentional for ML training data)
// =======================
void sendUDP() {
  udp.beginPacket(SERVER_IP, UDP_PORT);  // set destination IP/port

  String payload = measuredPayload(true);        // same dataset as HTTP
  udp.write((const uint8_t*)payload.c_str(), payload.length());   // write bytes to packet buffer

  udp.endPacket();  // transmit the datagram

  Serial.print("[UDP]  Sent \u2192 ");
  Serial.println(payload);
}

// =======================
// SETUP
// Runs once at power-on or after reset.
// Configures WiFi with a static IP, starts the HTTP server, and opens
// the UDP socket.  Static IP avoids DHCP negotiation packets polluting
// the Wireshark capture with DISCOVER/OFFER/REQUEST/ACK traffic.
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

  // Build the static IP from the base + last-octet define.
  // Using IPAddress() constructor directly avoids String parsing.
  IPAddress local_IP(LAB_NET_A, LAB_NET_B, LAB_NET_C, STATIC_IP_LAST);
  IPAddress gateway(LAB_NET_A, LAB_NET_B, LAB_NET_C, 1);
  IPAddress subnet(255, 255, 255, 0);

  // WiFi.config() must be called BEFORE WiFi.begin() for static IP to take effect
  WiFi.config(local_IP, gateway, subnet);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  // Block until connected – serial dots indicate progress to the developer
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnected!");
  Serial.println(WiFi.localIP());   // confirm the assigned static IP

  // Register the /data GET handler and start the HTTP server on port 80
  server.on("/data", handleData);
  const char* controlHeaders[] = {"X-Control-Token"};
  server.collectHeaders(controlHeaders, 1);
  server.on("/config", HTTP_POST, handleConfig);
  server.begin();

  // Begin UDP socket on port 0 (OS assigns an ephemeral source port).
  // Only outbound datagrams are sent – we do not need to listen on UDP.
  udp.begin(0);

  // Seed PRNG with ADC noise for unpredictable interval values.
  // ADC pin 0 is unconnected (floating) – reliably noisy.
  randomSeed(analogRead(0));
}

// =======================
// LOOP
// The main loop uses millis()-based non-blocking timing so that:
//   • server.handleClient() is called every iteration (no missed GET requests)
//   • HTTP and UDP sends can both trigger in the same loop iteration
//   • No delay() is used (which would block the HTTP server)
//
// dataIndex advances only immediately after a send to ensure each dataset
// entry is transmitted exactly once before moving to the next.
// =======================
void loop() {
  // Service any pending HTTP requests from the Flask traffic_generator thread
  server.handleClient();

  // Reconnect if WiFi was lost (e.g. router reboot during a long capture session)
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    return;
  }

  unsigned long now = millis();   // snapshot current time once per iteration

  // --- HTTP TRAFFIC (slow, reliable TCP) ---
  // Fires when ENABLE_HTTP is true and the HTTP interval has elapsed
  if (ENABLE_HTTP && now - lastHttpSend >= (unsigned long)httpInterval) {
    sendHTTP();
    lastHttpSend = now;                   // reset timer
    httpInterval = random(1100, 3000);     // new random interval for next send (+1s base)
  }

  // --- UDP TRAFFIC (fast, unreliable) ---
  // Fires when ENABLE_UDP is true and the UDP interval has elapsed.
  // UDP interval is shorter (50–200 ms) to simulate a higher-rate stream.
  if (ENABLE_UDP && now - lastUdpSend >= (unsigned long)udpInterval) {
    sendUDP();
    lastUdpSend = now;                    // reset timer
    udpInterval = controlledUdpInterval > 0 ? controlledUdpInterval : random(1050, 1200);        // new random interval for next send (+1s base)
  }

  // Advance dataset index immediately after any send in this iteration.
  // The 10 ms window catches both protocols firing in the same loop pass.
  if ((now - lastHttpSend < 10) || (now - lastUdpSend < 10)) {
    dataIndex = (dataIndex + 1) % dataSize;   // wrap back to 0 after last entry
  }
}
