#include "snmp_service.h"
#include "snmp_codec.h"
#include "gateway_metrics.h"
#include "gateway_config.h"
#include <string.h>
#include <errno.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"

static void serve(void *unused)
{
    (void)unused;
    int fd=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);
    if (fd<0) { ESP_LOGE("snmp","Socket creation failed"); vTaskDelete(NULL); return; }
    struct sockaddr_in bind_addr={.sin_family=AF_INET,.sin_port=htons(GW_SNMP_PORT),
                                  .sin_addr.s_addr=htonl(INADDR_ANY)};
    if (bind(fd,(struct sockaddr *)&bind_addr,sizeof(bind_addr))<0) {
        ESP_LOGE("snmp","UDP bind failed: %d",errno); close(fd); vTaskDelete(NULL); return;
    }
    in_addr_t permitted=inet_addr(GW_COLLECTOR_IP);
    uint8_t request[SNMP_MAX_PACKET+1],response[SNMP_MAX_PACKET];
    ESP_LOGI("snmp","Read-only SNMPv2c UDP/161; collector ACL %s",GW_COLLECTOR_IP);
    for (;;) {
        struct sockaddr_in peer; socklen_t peer_len=sizeof(peer);
        int length=recvfrom(fd,request,sizeof(request),0,(struct sockaddr *)&peer,&peer_len);
        if (length<0) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }
        if (length>SNMP_MAX_PACKET || peer.sin_addr.s_addr!=permitted) continue;
        snmp_snapshot_t snapshot;
        gateway_metrics_snapshot(&snapshot);
        size_t size=snmp_respond(request,(size_t)length,GW_SNMP_COMMUNITY,
                                 &snapshot,response,sizeof(response));
        if (size) sendto(fd,response,size,0,(struct sockaddr *)&peer,peer_len);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void snmp_service_start(void)
{
    if (xTaskCreate(serve,"snmp_agent",16384,NULL,3,NULL)!=pdPASS)
        ESP_LOGE("snmp","Cannot create SNMP task");
}
