#include "gateway_metrics.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "esp_netif_net_stack.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "lwip/netif.h"
#include "lwip/pbuf.h"
#include "lwip/tcpip.h"

typedef struct {
    esp_netif_t *esp;
    struct netif *netif;
    netif_input_fn input;
    netif_linkoutput_fn linkoutput;
    snmp_interface_t counters;
} slot_t;

static slot_t slots[2];
static portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;

static slot_t *find(struct netif *netif)
{
    for (unsigned i=0;i<2;i++) if (slots[i].netif==netif) return &slots[i];
    return NULL;
}

static err_t measured_input(struct pbuf *p, struct netif *netif)
{
    slot_t *s=find(netif);
    if (!s || !s->input) return ERR_IF;
    uint16_t bytes=p->tot_len;
    err_t result=s->input(p,netif); /* Ownership remains with original lwIP caller. */
    portENTER_CRITICAL(&lock);
    s->counters.in_octets+=bytes;
    if (result==ERR_MEM || result==ERR_BUF) s->counters.in_discards++;
    else if (result!=ERR_OK) s->counters.in_errors++;
    portEXIT_CRITICAL(&lock);
    return result;
}

static err_t measured_output(struct netif *netif, struct pbuf *p)
{
    slot_t *s=find(netif);
    if (!s || !s->linkoutput) return ERR_IF;
    uint16_t bytes=p->tot_len;
    err_t result=s->linkoutput(netif,p);
    portENTER_CRITICAL(&lock);
    if (result==ERR_OK) s->counters.out_octets+=bytes;
    else if (result==ERR_MEM || result==ERR_BUF) s->counters.out_discards++;
    else s->counters.out_errors++;
    portEXIT_CRITICAL(&lock);
    return result;
}

static void attach_in_tcpip_thread(void *arg)
{
    slot_t *s=arg;
    struct netif *netif=esp_netif_get_netif_impl(s->esp);
    if (!netif || !netif->input || !netif->linkoutput) {
        ESP_LOGE("metrics","Interface is not ready for counter installation");
        return;
    }
    if (s->netif==netif && netif->input==measured_input) return;
    s->input=netif->input;
    s->linkoutput=netif->linkoutput;
    portENTER_CRITICAL(&lock);
    memset(&s->counters,0,sizeof(s->counters));
    s->counters.discontinuity_ticks=(uint32_t)(esp_timer_get_time()/10000);
    s->counters.oper_status=netif_is_up(netif) ? 1 : 2;
    s->netif=netif;
    portEXIT_CRITICAL(&lock);
    netif->input=measured_input;
    netif->linkoutput=measured_output;
    ESP_LOGI("metrics","Counters installed for ifIndex %u", (unsigned)(s-slots)+1);
}

void gateway_metrics_attach(esp_netif_t *interface,unsigned ifindex)
{
    if (ifindex<1 || ifindex>2) return;
    slots[ifindex-1].esp=interface;
    if (tcpip_callback(attach_in_tcpip_thread,&slots[ifindex-1])!=ERR_OK)
        ESP_LOGE("metrics","Cannot schedule interface counter installation");
}

void gateway_metrics_oper(unsigned ifindex,int up)
{
    if (ifindex<1 || ifindex>2) return;
    portENTER_CRITICAL(&lock);
    slots[ifindex-1].counters.oper_status=up ? 1 : 2;
    portEXIT_CRITICAL(&lock);
}

void gateway_metrics_snapshot(snmp_snapshot_t *snapshot)
{
    snapshot->uptime_ticks=(uint32_t)(esp_timer_get_time()/10000);
    portENTER_CRITICAL(&lock);
    for (unsigned i=0;i<2;i++) {
        snapshot->interfaces[i]=slots[i].counters;
        if (!snapshot->interfaces[i].oper_status) snapshot->interfaces[i].oper_status=2;
    }
    portEXIT_CRITICAL(&lock);
    /* speed_mbps=0: a shared Wi-Fi radio has no fixed measured link capacity.
       The collector leaves utilization missing instead of assuming a PHY rate. */
}
