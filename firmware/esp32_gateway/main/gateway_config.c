#include "gateway_config.h"
#include <string.h>
#include "lwip/inet.h"

/* Separate translation unit keeps the full firmware linked even when local
 * credentials have not been generated. An unconfigured image safely stops. */
bool gateway_configuration_ready(void)
{
    return strlen(GW_UPSTREAM_SSID)>0 && strlen(GW_UPSTREAM_SSID)<=32 &&
           strlen(GW_UPSTREAM_PASSWORD)>=8 && strlen(GW_UPSTREAM_PASSWORD)<=63 &&
           strlen(GW_AP_SSID)>0 && strlen(GW_AP_SSID)<=32 &&
           strlen(GW_AP_PASSWORD)>=8 && strlen(GW_AP_PASSWORD)<=63 &&
           strlen(GW_SNMP_COMMUNITY)>=16 && strlen(GW_SNMP_COMMUNITY)<=64 &&
           inet_addr(GW_COLLECTOR_IP)!=INADDR_NONE;
}
