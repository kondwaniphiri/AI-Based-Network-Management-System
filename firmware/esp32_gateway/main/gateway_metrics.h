#pragma once
#include "esp_netif.h"
#include "snmp_codec.h"

/* Install once the default lwIP netif exists (on the interface START event). */
void gateway_metrics_attach(esp_netif_t *interface, unsigned ifindex);
void gateway_metrics_oper(unsigned ifindex, int up);
void gateway_metrics_snapshot(snmp_snapshot_t *snapshot);
