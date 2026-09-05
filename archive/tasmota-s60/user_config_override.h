/* S60TPG OTA-trial configuration for Tasmota 15.6.0. */
#ifndef _USER_CONFIG_OVERRIDE_H_
#define _USER_CONFIG_OVERRIDE_H_

/* Force clean Tasmota defaults in its own NVS namespace. */
#undef  CFG_HOLDER
#define CFG_HOLDER             5601

/* Hardware mapping confirmed by Tasmota discussion #21255. */
#undef  USER_TEMPLATE
#define USER_TEMPLATE "{\"NAME\":\"Sonoff S60TPG\",\"GPIO\":[1,1,1,1,224,544,1,3104,1,32,1,0,0,0,0,0,0,0,1,1,1,1],\"FLAG\":0,\"BASE\":1}"
#undef  MODULE
#define MODULE                 USER_MODULE

/* Start a protected Tasmota setup AP because stock Wi-Fi credentials are not
 * imported into Tasmota's settings namespace. */
#undef  WIFI_CONFIG_TOOL
#define WIFI_CONFIG_TOOL       WIFI_MANAGER
#undef  WIFI_AP_PASSPHRASE
#define WIFI_AP_PASSPHRASE     "s60-tasmota"

/* FIRMWARE_LITE removes sensor drivers; add back only the S60 meter. */
#define USE_ENERGY_SENSOR
#define USE_CSE7766

/* Keep relay/button, web UI, MQTT and OTA, but remove large ESP32 extras and
 * unrelated device families. */
#undef  USE_BERRY
#undef  USE_UFILESYS
#undef  USE_MATTER_DEVICE
#undef  USE_MQTT_TLS
#undef  USE_LIGHT
#undef  USE_ADC_VCC
#undef  USE_DISCOVERY
#undef  USE_TASMOTA_DISCOVERY
#undef  USE_EMULATION
#undef  USE_RULES
#undef  USE_TIMERS
#undef  USE_TIMERS_WEB
#undef  USE_SCRIPT

#endif  // _USER_CONFIG_OVERRIDE_H_

