#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_net_stack.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "lwip/inet.h"
#include "lwip/lwip_napt.h"
#include "lwip/tcpip.h"
#include "lwip/priv/tcpip_priv.h"
#include "lwip/prot/ip.h"
#include "dhcpserver/dhcpserver.h"
#include "gateway_config.h"
#include "gateway_metrics.h"
#include "snmp_service.h"

static esp_netif_t *station,*access_point;
static unsigned retry_delay=1000;
static const char *TAG="gateway";

static void retry(void *arg)
{
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
        vTaskDelay(pdMS_TO_TICKS(retry_delay));
        retry_delay=retry_delay<16000 ? retry_delay*2 : 30000;
        esp_wifi_connect();
    }
}
static TaskHandle_t retry_task;

struct portmap_call { struct tcpip_api_call_data call; uint32_t upstream; };
static err_t install_portmaps(struct tcpip_api_call_data *arg)
{
    struct portmap_call *call=(struct portmap_call *)arg;
    const char *destinations[]={GW_SENSOR_1,GW_SENSOR_2,GW_SENSOR_3};
    for (unsigned i=0;i<3;i++) {
        if (!ip_portmap_add(IP_PROTO_TCP,call->upstream,8101+i,inet_addr(destinations[i]),80)) return ERR_MEM;
    }
    return ERR_OK;
}

static void configure_upstream(ip_event_got_ip_t *event)
{
    /* Reject overlapping networks: NAT between identical subnets is ambiguous. */
    esp_netif_ip_info_t ap;
    ESP_ERROR_CHECK(esp_netif_get_ip_info(access_point,&ap));
    if ((event->ip_info.ip.addr & ap.netmask.addr)==(ap.ip.addr & ap.netmask.addr)) {
        ESP_LOGE(TAG,"Upstream subnet overlaps the sensor subnet; routing is disabled");
        esp_netif_napt_disable(access_point);
        return;
    }
    ESP_ERROR_CHECK(esp_netif_set_default_netif(station));
    esp_netif_dns_info_t dns;
    if (esp_netif_get_dns_info(station,ESP_NETIF_DNS_MAIN,&dns)==ESP_OK) {
        uint8_t offer=2; /* DHCPS_OFFER_DNS */
        esp_netif_dhcps_stop(access_point);
        ESP_ERROR_CHECK(esp_netif_dhcps_option(access_point,ESP_NETIF_OP_SET,
                          ESP_NETIF_DOMAIN_NAME_SERVER,&offer,sizeof(offer)));
        ESP_ERROR_CHECK(esp_netif_set_dns_info(access_point,ESP_NETIF_DNS_MAIN,&dns));
        ESP_ERROR_CHECK(esp_netif_dhcps_start(access_point));
    }
    ESP_ERROR_CHECK(esp_netif_napt_enable(access_point));
    /* Mutate the NAT table on the lwIP thread. Ports are in host byte order. */
    struct portmap_call call={.upstream=event->ip_info.ip.addr};
    if (tcpip_api_call(install_portmaps,&call.call)!=ERR_OK) ESP_LOGE(TAG,"Failed to install probe portmaps");
    retry_delay=1000;
    gateway_metrics_oper(GW_IF_STATION,1);
    ESP_LOGI(TAG,"Upstream address " IPSTR "; configure this as IOT_GATEWAY_UPLINK_IP",IP2STR(&event->ip_info.ip));
    ESP_LOGI(TAG,"HTTP probe ports: 8101 -> %s:80; 8102 -> %s:80; 8103 -> %s:80",
             GW_SENSOR_1,GW_SENSOR_2,GW_SENSOR_3);
}

static void event_handler(void *arg,esp_event_base_t base,int32_t id,void *data)
{
    (void)arg;
    if (base==WIFI_EVENT) {
        switch (id) {
        case WIFI_EVENT_STA_START:
            gateway_metrics_attach(station,GW_IF_STATION);
            esp_wifi_connect(); break;
        case WIFI_EVENT_AP_START:
            gateway_metrics_attach(access_point,GW_IF_SOFTAP);
            gateway_metrics_oper(GW_IF_SOFTAP,1); break;
        case WIFI_EVENT_AP_STOP: gateway_metrics_oper(GW_IF_SOFTAP,0); break;
        case WIFI_EVENT_STA_DISCONNECTED:
            gateway_metrics_oper(GW_IF_STATION,0);
            esp_netif_napt_disable(access_point);
            if (retry_task) xTaskNotifyGive(retry_task);
            break;
        default: break;
        }
    } else if (base==IP_EVENT && id==IP_EVENT_STA_GOT_IP) configure_upstream(data);
}

void app_main(void)
{
    if (!gateway_configuration_ready()) {
        ESP_LOGE(TAG,"Generate gateway_config.local.h with tools/configure_gateway.py before flashing");
        return;
    }
    esp_err_t err=nvs_flash_init();
    if (err==ESP_ERR_NVS_NO_FREE_PAGES || err==ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase()); err=nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    station=esp_netif_create_default_wifi_sta();
    access_point=esp_netif_create_default_wifi_ap();
    ESP_ERROR_CHECK(esp_netif_dhcps_stop(access_point));
    esp_netif_ip_info_t ip={0};
    ip.ip.addr=inet_addr(GW_AP_IP); ip.gw.addr=ip.ip.addr; ip.netmask.addr=inet_addr(GW_NETMASK);
    ESP_ERROR_CHECK(esp_netif_set_ip_info(access_point,&ip));
    /* Static sensors use .101-.103; DHCP for phones is restricted to .10-.30. */
    dhcps_lease_t lease={.enable=true};
    lease.start_ip.addr=htonl((ntohl(ip.ip.addr)&0xffffff00U)|10);
    lease.end_ip.addr=htonl((ntohl(ip.ip.addr)&0xffffff00U)|30);
    ESP_ERROR_CHECK(esp_netif_dhcps_option(access_point,ESP_NETIF_OP_SET,
                      ESP_NETIF_REQUESTED_IP_ADDRESS,&lease,sizeof(lease)));
    ESP_ERROR_CHECK(esp_netif_dhcps_start(access_point));
    wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    wifi_config_t sta={0},ap={0};
    strlcpy((char *)sta.sta.ssid,GW_UPSTREAM_SSID,sizeof(sta.sta.ssid));
    strlcpy((char *)sta.sta.password,GW_UPSTREAM_PASSWORD,sizeof(sta.sta.password));
    sta.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK;
    strlcpy((char *)ap.ap.ssid,GW_AP_SSID,sizeof(ap.ap.ssid));
    ap.ap.ssid_len=strlen(GW_AP_SSID);
    strlcpy((char *)ap.ap.password,GW_AP_PASSWORD,sizeof(ap.ap.password));
    ap.ap.channel=1; ap.ap.max_connection=GW_MAX_CLIENTS; ap.ap.authmode=WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&sta));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP,&ap));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,event_handler,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,event_handler,NULL));
    if (xTaskCreate(retry,"wifi_reconnect",3072,NULL,4,&retry_task)!=pdPASS) abort();
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    snmp_service_start();
    ESP_LOGI(TAG,"Downstream SSID %s, gateway %s; ifIndex 1=STA, 2=AP",GW_AP_SSID,GW_AP_IP);
}
