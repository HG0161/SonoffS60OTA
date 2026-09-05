/*
 * S60 one-shot OTA bridge.
 *
 * The bridge is an ESP32-C3 application image intended for an existing S60
 * OTA slot. It does not contain or replace a bootloader or partition table.
 *
 * Recovery invariant:
 *   1. At startup, confirm this bridge so bootloader rollback cannot discard it.
 *   2. Select the untouched peer app slot for the next boot.
 *   3. Before erasing that peer for an upload, select this bridge again.
 *   4. Only select the uploaded app after esp_ota_end validates it.
 *
 * This cannot protect against failure before app_main starts, but it makes a
 * later Wi-Fi/HTTP failure recoverable by power-cycling back to stock.
 */

#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define BRIDGE_AP_PASSWORD "s60-ota-bridge"
#define BRIDGE_MAX_IMAGE_SIZE 0x1f0000
#define UPLOAD_BUFFER_SIZE 4096

static const char *TAG = "s60_bridge";
static const esp_partition_t *s_running;
static const esp_partition_t *s_peer;
/* Keep the flash buffer out of the HTTP server task's relatively small stack.
 * A large automatic buffer can reset the bridge on handler entry, before the
 * recovery slot is pinned. */
static char s_upload_buffer[UPLOAD_BUFFER_SIZE];

static const char INDEX_HTML[] =
    "<!doctype html><meta name=viewport content='width=device-width'>"
    "<title>S60 OTA Bridge</title>"
    "<style>body{font:16px system-ui;max-width:42rem;margin:3rem auto;padding:0 1rem}"
    "button{padding:.7rem 1rem}code{background:#eee;padding:.1rem .3rem}</style>"
    "<h1>S60 OTA Bridge</h1>"
    "<p>The untouched stock slot is selected for the next power cycle.</p>"
    "<p>Choose a native ESP32-C3 application image no larger than 0x1f0000 "
    "bytes. The peer slot is erased only after you press Upload.</p>"
    "<input id=f type=file accept='.bin,application/octet-stream'> "
    "<button id=u>Upload</button><pre id=o></pre>"
    "<script>u.onclick=async()=>{let x=f.files[0];if(!x){o.textContent='Choose a file';return}"
    "if(!confirm('Overwrite the stock slot with '+x.name+' ('+x.size+' bytes)?'))return;"
    "u.disabled=true;o.textContent='Uploading. Do not remove power...';try{let r=await fetch('/update',"
    "{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:x});"
    "o.textContent=await r.text()}catch(e){o.textContent='Connection ended: '+e+'\nReconnect to the bridge and retry.'}}</script>";

static void delayed_restart(void *unused)
{
    (void)unused;
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

static esp_err_t root_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, INDEX_HTML, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t upload_error(httpd_req_t *req, const char *message)
{
    ESP_LOGE(TAG, "%s", message);
    httpd_resp_set_status(req, "400 Bad Request");
    return httpd_resp_sendstr(req, message);
}

static esp_err_t update_handler(httpd_req_t *req)
{
    if (req->content_len <= 0 || req->content_len > BRIDGE_MAX_IMAGE_SIZE ||
        req->content_len > s_peer->size) {
        return upload_error(req, "Invalid or oversized image. Nothing was erased.\n");
    }

    /* If power is lost after peer erasure begins, reboot this bridge. */
    esp_err_t err = esp_ota_set_boot_partition(s_running);
    if (err != ESP_OK) {
        return upload_error(req, "Could not pin the bridge as recovery image. Nothing was erased.\n");
    }

    esp_ota_handle_t handle = 0;
    err = esp_ota_begin(s_peer, req->content_len, &handle);
    if (err != ESP_OK) {
        return upload_error(req, "Could not begin OTA. The bridge remains selected.\n");
    }

    int remaining = req->content_len;
    while (remaining > 0) {
        int wanted = remaining < (int)sizeof(s_upload_buffer)
                         ? remaining
                         : (int)sizeof(s_upload_buffer);
        int received = httpd_req_recv(req, s_upload_buffer, wanted);
        if (received == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (received <= 0) {
            esp_ota_abort(handle);
            return upload_error(req, "Upload interrupted. Reconnect to the bridge and retry.\n");
        }
        err = esp_ota_write(handle, s_upload_buffer, received);
        if (err != ESP_OK) {
            esp_ota_abort(handle);
            return upload_error(req, "Flash write failed. Reconnect to the bridge and retry.\n");
        }
        remaining -= received;
    }

    err = esp_ota_end(handle);
    if (err != ESP_OK) {
        return upload_error(req, "ESP image validation failed. The bridge remains selected.\n");
    }
    err = esp_ota_set_boot_partition(s_peer);
    if (err != ESP_OK) {
        return upload_error(req, "Image is valid, but selecting it failed. The bridge remains selected.\n");
    }

    httpd_resp_set_type(req, "text/plain");
    httpd_resp_sendstr(req, "Upload verified. Rebooting into the new application...\n");
    xTaskCreate(delayed_restart, "restart", 2048, NULL, 5, NULL);
    return ESP_OK;
}

static esp_err_t start_access_point(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    if (esp_netif_create_default_wifi_ap() == NULL) {
        return ESP_FAIL;
    }

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP));
    wifi_config_t config = {0};
    snprintf((char *)config.ap.ssid, sizeof(config.ap.ssid),
             "S60-OTA-Bridge-%02X%02X", mac[4], mac[5]);
    config.ap.ssid_len = strlen((char *)config.ap.ssid);
    strlcpy((char *)config.ap.password, BRIDGE_AP_PASSWORD,
            sizeof(config.ap.password));
    config.ap.channel = 1;
    config.ap.max_connection = 2;
    config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    config.ap.pmf_cfg.capable = true;
    config.ap.pmf_cfg.required = false;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "AP ready: %s", config.ap.ssid);
    return ESP_OK;
}

static esp_err_t start_web_server(void)
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 8192;
    config.recv_wait_timeout = 20;
    config.send_wait_timeout = 20;

    httpd_handle_t server = NULL;
    ESP_ERROR_CHECK(httpd_start(&server, &config));
    const httpd_uri_t root = {
        .uri = "/", .method = HTTP_GET, .handler = root_handler, .user_ctx = NULL,
    };
    const httpd_uri_t update = {
        .uri = "/update", .method = HTTP_POST, .handler = update_handler, .user_ctx = NULL,
    };
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &root));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &update));
    return ESP_OK;
}

void app_main(void)
{
    /* Vendor OTA may leave a newly booted bridge pending verification.  Confirm
     * it before selecting another slot, otherwise the bootloader can later
     * classify this recovery image as aborted.  A non-pending image can return
     * an error here; that is harmless and must not prevent recovery startup. */
    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "bridge confirmation returned %s", esp_err_to_name(err));
    }

    s_running = esp_ota_get_running_partition();
    s_peer = esp_ota_get_next_update_partition(NULL);
    if (s_running == NULL || s_peer == NULL || s_running == s_peer ||
        s_peer->type != ESP_PARTITION_TYPE_APP) {
        ESP_LOGE(TAG, "No safe peer application partition; refusing to start");
        return;
    }

    /* Make the peer the automatic fallback for the next power cycle. */
    err = esp_ota_set_boot_partition(s_peer);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Peer stock image is not bootable (%s); refusing to start",
                 esp_err_to_name(err));
        return;
    }
    ESP_LOGI(TAG, "power-cycle fallback selected: %s at 0x%08lx",
             s_peer->label, (unsigned long)s_peer->address);

    ESP_ERROR_CHECK(start_access_point());
    ESP_ERROR_CHECK(start_web_server());
    ESP_LOGI(TAG, "open http://192.168.4.1/ (password: %s)", BRIDGE_AP_PASSWORD);
}
