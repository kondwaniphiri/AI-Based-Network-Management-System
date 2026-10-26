idf_component_register(SRCS "gateway.c" "gateway_config.c" "gateway_metrics.c" "snmp_codec.c" "snmp_service.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_wifi esp_netif esp_event esp_timer nvs_flash lwip)
