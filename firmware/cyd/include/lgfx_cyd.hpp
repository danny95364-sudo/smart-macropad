// CYD 2.8 吋的 LovyanGFX 設定。面板驅動由 build flag 決定（PANEL_ST7789 / PANEL_ILI9341）。
// SPI：MOSI 13、SCLK 14、MISO 12、CS 15、DC 2、RST 無；背光 21。
#pragma once
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#ifndef PANEL_INVERT
#define PANEL_INVERT 0
#endif
#ifndef PANEL_RGB_ORDER
#define PANEL_RGB_ORDER 0
#endif
#ifndef PANEL_ROTATION
#define PANEL_ROTATION 1
#endif

#ifndef TOUCH_X_MIN
#define TOUCH_X_MIN 200
#endif
#ifndef TOUCH_X_MAX
#define TOUCH_X_MAX 3900
#endif
#ifndef TOUCH_Y_MIN
#define TOUCH_Y_MIN 200
#endif
#ifndef TOUCH_Y_MAX
#define TOUCH_Y_MAX 3900
#endif
#ifndef TOUCH_OFFSET_ROTATION
#define TOUCH_OFFSET_ROTATION 0
#endif

#if defined(PANEL_ST7789)
#define PANEL_NAME "ST7789"
#else
#define PANEL_NAME "ILI9341"
#endif

class LGFX : public lgfx::LGFX_Device {
#if defined(PANEL_ST7789)
  lgfx::Panel_ST7789 _panel;
#else
  lgfx::Panel_ILI9341 _panel;
#endif
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;
#if defined(CYD_TOUCH)
  lgfx::Touch_XPT2046 _touch;  // 只有 cyd_game 開（-DCYD_TOUCH），看板不變
#endif

 public:
  LGFX() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI2_HOST;  // HSPI
      cfg.spi_mode = 0;
      cfg.freq_write = 40000000;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = false;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = 14;
      cfg.pin_mosi = 13;
      cfg.pin_miso = 12;
      cfg.pin_dc = 2;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = 15;
      cfg.pin_rst = -1;
      cfg.pin_busy = -1;
      cfg.memory_width = 240;
      cfg.memory_height = 320;
      cfg.panel_width = 240;
      cfg.panel_height = 320;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.dummy_read_pixel = 8;
      cfg.dummy_read_bits = 1;
      cfg.readable = true;
      cfg.invert = PANEL_INVERT;
      cfg.rgb_order = PANEL_RGB_ORDER;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;  // 觸控在另一組 SPI 腳位
      _panel.config(cfg);
    }
    {
      auto cfg = _light.config();
      cfg.pin_bl = 21;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      _panel.setLight(&_light);
    }
#if defined(CYD_TOUCH)
    {
      // XPT2046 在另一組 SPI 腳位：CLK 25、DIN 32、DO 39、CS 33、IRQ 36
      auto cfg = _touch.config();
      cfg.spi_host = SPI3_HOST;  // VSPI
      cfg.freq = 1000000;
      cfg.pin_sclk = 25;
      cfg.pin_mosi = 32;
      cfg.pin_miso = 39;
      cfg.pin_cs = 33;
      cfg.pin_int = 36;
      cfg.bus_shared = false;
      // 常見 CYD 校正值；不準時看序列埠的 touch x y 再調
      cfg.x_min = TOUCH_X_MIN;
      cfg.x_max = TOUCH_X_MAX;
      cfg.y_min = TOUCH_Y_MIN;
      cfg.y_max = TOUCH_Y_MAX;
      cfg.offset_rotation = TOUCH_OFFSET_ROTATION;
      _touch.config(cfg);
      _panel.setTouch(&_touch);
    }
#endif
    setPanel(&_panel);
  }
};
