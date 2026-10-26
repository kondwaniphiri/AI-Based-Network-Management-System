#pragma once
#include <stddef.h>
#include <stdint.h>

#define SNMP_MAX_PACKET 1400
#define SNMP_MAX_BINDINGS 24
#define SNMP_MAX_ARCS 16

typedef struct {
    uint64_t in_octets, out_octets;
    uint32_t in_discards, out_discards, in_errors, out_errors;
    uint32_t discontinuity_ticks, speed_mbps;
    int32_t oper_status;
} snmp_interface_t;

typedef struct {
    uint32_t uptime_ticks;
    snmp_interface_t interfaces[2]; /* stable ifIndex: 1 upstream STA, 2 downstream AP */
} snmp_snapshot_t;

/* Returns encoded response length, or 0 for malformed/unauthenticated input.
 * Supports bounded SNMPv2c GET and GETNEXT. SET is rejected as notWritable.
 * No dynamic allocation; input and output buffers must not overlap. */
#ifdef _WIN32
__declspec(dllexport)
#endif
size_t snmp_respond(const uint8_t *request, size_t length,
                    const char *community, const snmp_snapshot_t *snapshot,
                    uint8_t *response, size_t capacity);
