idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_http_server esp_wifi app_update esp_event esp_netif)
