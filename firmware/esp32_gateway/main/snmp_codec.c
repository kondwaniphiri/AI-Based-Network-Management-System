/* Small bounded read-only SNMPv2c agent codec, independent of ESP-IDF for tests. */
#include "snmp_codec.h"
#include <stdbool.h>
#include <string.h>
#include <limits.h>

typedef struct { const uint8_t *p; size_t n; } reader_t;
typedef struct { uint8_t *p; size_t n, cap; bool failed; } writer_t;
typedef struct { uint32_t arcs[SNMP_MAX_ARCS]; size_t n; } oid_t;
typedef struct { oid_t oid; } binding_t;
typedef struct { oid_t oid; unsigned kind, interface; } object_t;
enum { K_DESCR, K_UPTIME, K_NAME, K_IFNUMBER, K_INDEX, K_IFDESCR, K_TYPE,
       K_MTU, K_SPEED, K_STATUS, K_IN32, K_INDISCARD, K_INERROR, K_OUT32,
       K_OUTDISCARD, K_OUTERROR, K_IFNAME, K_IN64, K_OUT64, K_HIGHSPEED, K_DISCONT };

static bool tlv(reader_t *r, uint8_t *tag, reader_t *value)
{
    if (r->n < 2) return false;
    *tag = *r->p++; r->n--;
    size_t n = *r->p++; r->n--;
    if (n & 0x80) {
        size_t count = n & 0x7f;
        if (!count || count > 2 || count > r->n) return false;
        n = 0;
        while (count--) { n = (n << 8) | *r->p++; r->n--; }
    }
    if (n > r->n) return false;
    *value = (reader_t){r->p, n};
    r->p += n; r->n -= n;
    return true;
}

static bool take(reader_t *r, uint8_t expected, reader_t *v)
{
    uint8_t tag;
    return tlv(r, &tag, v) && tag == expected;
}

static bool integer(reader_t *r, int32_t *result)
{
    reader_t v;
    if (!take(r, 2, &v) || !v.n || v.n > 4) return false;
    uint32_t value = (v.p[0] & 0x80) ? UINT32_MAX : 0;
    for (size_t i = 0; i < v.n; ++i) value = (value << 8) | v.p[i];
    *result = (int32_t)value;
    return true;
}

static bool decode_oid(reader_t v, oid_t *oid)
{
    oid->n = 0;
    while (v.n) {
        uint32_t arc = 0;
        unsigned count = 0;
        uint8_t byte;
        do {
            if (!v.n || ++count > 5 || arc > (UINT32_MAX >> 7)) return false;
            byte = *v.p++; v.n--;
            arc = (arc << 7) | (byte & 0x7f);
        } while (byte & 0x80);
        if (!oid->n) {
            oid->arcs[0] = arc < 40 ? 0 : arc < 80 ? 1 : 2;
            oid->arcs[1] = arc - oid->arcs[0] * 40;
            oid->n = 2;
        } else {
            if (oid->n == SNMP_MAX_ARCS) return false;
            oid->arcs[oid->n++] = arc;
        }
    }
    return oid->n >= 2;
}

static int compare(const oid_t *a, const oid_t *b)
{
    for (size_t i = 0; i < a->n && i < b->n; ++i)
        if (a->arcs[i] != b->arcs[i]) return a->arcs[i] < b->arcs[i] ? -1 : 1;
    return a->n == b->n ? 0 : a->n < b->n ? -1 : 1;
}

static void prepend(writer_t *w, const uint8_t *bytes, size_t n)
{
    if (w->failed || n > w->cap - w->n) { w->failed = true; return; }
    w->n += n;
    if (n) memcpy(w->p + w->cap - w->n, bytes, n);
}

static void header(writer_t *w, uint8_t tag, size_t length)
{
    uint8_t h[4]; size_t n;
    h[0] = tag;
    if (length < 128) { h[1] = (uint8_t)length; n = 2; }
    else if (length < 256) { h[1] = 0x81; h[2] = (uint8_t)length; n = 3; }
    else { h[1] = 0x82; h[2] = (uint8_t)(length >> 8); h[3] = (uint8_t)length; n = 4; }
    prepend(w, h, n);
}

static void wrap(writer_t *w, uint8_t tag, size_t mark) { header(w, tag, w->n - mark); }
static void unsigned_value(writer_t *w, uint8_t tag, uint64_t value)
{
    uint8_t bytes[9]; size_t n = 0;
    do { bytes[8 - n++] = (uint8_t)value; value >>= 8; } while (value);
    if (bytes[9 - n] & 0x80) bytes[8 - n++] = 0;
    prepend(w, bytes + 9 - n, n); header(w, tag, n);
}

static void signed_value(writer_t *w, int32_t value)
{
    uint32_t u = (uint32_t)value;
    uint8_t bytes[4] = {(uint8_t)(u >> 24), (uint8_t)(u >> 16), (uint8_t)(u >> 8), (uint8_t)u};
    size_t start = 0;
    while (start < 3 && ((bytes[start] == 0 && !(bytes[start+1] & 0x80)) ||
                         (bytes[start] == 255 && (bytes[start+1] & 0x80)))) start++;
    prepend(w, bytes + start, 4 - start); header(w, 2, 4 - start);
}

static void text_value(writer_t *w, const char *text)
{
    size_t n = strlen(text); prepend(w, (const uint8_t *)text, n); header(w, 4, n);
}

static void encode_oid(writer_t *w, const oid_t *oid)
{
    size_t mark = w->n;
    for (size_t index = oid->n; index > 1; --index) {
        uint32_t value = index == 2 ? oid->arcs[0] * 40 + oid->arcs[1] : oid->arcs[index-1];
        uint8_t bytes[5]; size_t n = 0;
        bytes[4 - n++] = (uint8_t)(value & 0x7f); value >>= 7;
        while (value) { bytes[4 - n++] = (uint8_t)((value & 0x7f) | 0x80); value >>= 7; }
        prepend(w, bytes + 5 - n, n);
    }
    wrap(w, 6, mark);
}

static object_t system_object(unsigned field, unsigned kind)
{
    object_t o = {.oid = {{1,3,6,1,2,1,1,0,0},9}, .kind=kind};
    o.oid.arcs[7] = field;
    return o;
}

static object_t interface_object(unsigned column, unsigned interface, unsigned kind, bool extended)
{
    object_t o = {.kind=kind, .interface=interface};
    if (extended) o.oid = (oid_t){{1,3,6,1,2,1,31,1,1,1,column,interface},12};
    else o.oid = (oid_t){{1,3,6,1,2,1,2,2,1,column,interface},11};
    return o;
}

static size_t catalog(object_t *objects)
{
    size_t n = 0;
    objects[n++] = system_object(1,K_DESCR);
    objects[n++] = system_object(3,K_UPTIME);
    objects[n++] = system_object(5,K_NAME);
    objects[n++] = (object_t){.oid={{1,3,6,1,2,1,2,1,0},9},.kind=K_IFNUMBER};
    const unsigned columns[] = {1,2,3,4,5,8,10,13,14,16,19,20};
    const unsigned kinds[] = {K_INDEX,K_IFDESCR,K_TYPE,K_MTU,K_SPEED,K_STATUS,K_IN32,
                              K_INDISCARD,K_INERROR,K_OUT32,K_OUTDISCARD,K_OUTERROR};
    const unsigned extended[] = {1,6,10,15,19};
    const unsigned extended_kinds[] = {K_IFNAME,K_IN64,K_OUT64,K_HIGHSPEED,K_DISCONT};
    for (size_t c=0;c<sizeof(columns)/sizeof(columns[0]);c++)
        for (unsigned i=1;i<=2;i++) objects[n++] = interface_object(columns[c],i,kinds[c],false);
    for (size_t c=0;c<sizeof(extended)/sizeof(extended[0]);c++)
        for (unsigned i=1;i<=2;i++) objects[n++] = interface_object(extended[c],i,extended_kinds[c],true);
    return n;
}

static void object_value(writer_t *w, const object_t *o, const snmp_snapshot_t *s)
{
    const snmp_interface_t *i = o->interface ? &s->interfaces[o->interface-1] : NULL;
    switch (o->kind) {
    case K_DESCR: text_value(w,"IoT Lab ESP32 NAT gateway; software interface counters"); break;
    case K_NAME: text_value(w,"iot-lab-gateway"); break;
    case K_UPTIME: unsigned_value(w,0x43,s->uptime_ticks); break;
    case K_IFNUMBER: signed_value(w,2); break;
    case K_INDEX: signed_value(w,(int32_t)o->interface); break;
    case K_IFDESCR: text_value(w,o->interface==1 ? "Upstream Wi-Fi station" : "Downstream Wi-Fi access point"); break;
    case K_IFNAME: text_value(w,o->interface==1 ? "wifi_sta" : "wifi_ap"); break;
    case K_TYPE: signed_value(w,71); break; /* IANA ieee80211 */
    case K_MTU: signed_value(w,1500); break;
    case K_SPEED: unsigned_value(w,0x42,(uint64_t)i->speed_mbps * 1000000); break;
    case K_HIGHSPEED: unsigned_value(w,0x42,i->speed_mbps); break;
    case K_STATUS: signed_value(w,i->oper_status); break;
    case K_IN32: unsigned_value(w,0x41,(uint32_t)i->in_octets); break;
    case K_OUT32: unsigned_value(w,0x41,(uint32_t)i->out_octets); break;
    case K_IN64: unsigned_value(w,0x46,i->in_octets); break;
    case K_OUT64: unsigned_value(w,0x46,i->out_octets); break;
    case K_INDISCARD: unsigned_value(w,0x41,i->in_discards); break;
    case K_OUTDISCARD: unsigned_value(w,0x41,i->out_discards); break;
    case K_INERROR: unsigned_value(w,0x41,i->in_errors); break;
    case K_OUTERROR: unsigned_value(w,0x41,i->out_errors); break;
    case K_DISCONT: unsigned_value(w,0x43,i->discontinuity_ticks); break;
    }
}

size_t snmp_respond(const uint8_t *request, size_t length, const char *community,
                    const snmp_snapshot_t *snapshot, uint8_t *response, size_t capacity)
{
    if (!request || !snapshot || !response || !community || !*community ||
        length > SNMP_MAX_PACKET || capacity > SNMP_MAX_PACKET) return 0;
    reader_t packet={request,length}, message, text, pdu, list;
    int32_t version, id, error, index;
    uint8_t pdu_tag;
    if (!take(&packet,0x30,&message) || packet.n || !integer(&message,&version) || version!=1 ||
        !take(&message,4,&text) || text.n != strlen(community) || memcmp(text.p,community,text.n) ||
        !tlv(&message,&pdu_tag,&pdu) || message.n ||
        (pdu_tag!=0xa0 && pdu_tag!=0xa1 && pdu_tag!=0xa3) ||
        !integer(&pdu,&id) || !integer(&pdu,&error) || !integer(&pdu,&index) || error || index ||
        !take(&pdu,0x30,&list) || pdu.n) return 0;
    binding_t bindings[SNMP_MAX_BINDINGS]; size_t count=0;
    while (list.n) {
        reader_t entry, oid, value; uint8_t tag;
        if (count == SNMP_MAX_BINDINGS || !take(&list,0x30,&entry) || !take(&entry,6,&oid) ||
            !decode_oid(oid,&bindings[count].oid) || !tlv(&entry,&tag,&value) || entry.n ||
            (pdu_tag!=0xa3 && (tag!=5 || value.n))) return 0;
        count++;
    }
    if (!count) return 0;
    object_t objects[40]; size_t objects_count=catalog(objects);
    writer_t w={response,0,capacity,false};
    size_t message_mark=w.n, pdu_mark=w.n, list_mark=w.n;
    for (size_t b=count;b>0;--b) {
        oid_t *oid=&bindings[b-1].oid;
        const object_t *chosen=NULL;
        for (size_t k=0;k<objects_count;k++) {
            int c=compare(&objects[k].oid,oid);
            if ((pdu_tag==0xa0 && c==0) || (pdu_tag==0xa1 && c>0 &&
                (!chosen || compare(&objects[k].oid,&chosen->oid)<0))) chosen=&objects[k];
        }
        size_t entry_mark=w.n;
        if (pdu_tag==0xa3) header(&w,5,0);
        else if (chosen) object_value(&w,chosen,snapshot);
        else header(&w,pdu_tag==0xa1 ? 0x82 : 0x80,0); /* endOfMibView/noSuchObject */
        encode_oid(&w,chosen ? &chosen->oid : oid);
        wrap(&w,0x30,entry_mark);
    }
    wrap(&w,0x30,list_mark);
    signed_value(&w,pdu_tag==0xa3 ? 1 : 0); /* first binding is not writable */
    signed_value(&w,pdu_tag==0xa3 ? 17 : 0);
    signed_value(&w,id);
    wrap(&w,0xa2,pdu_mark);
    text_value(&w,community); signed_value(&w,1);
    wrap(&w,0x30,message_mark);
    if (w.failed) {
        /* RFC 3416 tooBig response: empty bindings, rather than timing out. */
        w=(writer_t){response,0,capacity,false};
        header(&w,0x30,0); signed_value(&w,0); signed_value(&w,1); signed_value(&w,id);
        wrap(&w,0xa2,0); text_value(&w,community); signed_value(&w,1); wrap(&w,0x30,0);
        if (w.failed) return 0;
    }
    memmove(response,response+capacity-w.n,w.n);
    return w.n;
}
