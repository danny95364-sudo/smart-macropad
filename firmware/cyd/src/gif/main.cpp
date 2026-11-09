// CYD 循環 GIF 播放器（env cyd_gif，計畫見 design/cyd-gif-plan.md）。
// 開機列出 LittleFS 根目錄的 *.gif，播第一張並無限循環；點螢幕換下一張。
// GIF 置中，小於 320x240 的部分是黑邊；幀間隔照 GIF 檔裡寫的。
// 轉檔：python tools/make_gif.py 輸入檔 名字 → firmware/cyd/data/，再 pio run -e cyd_gif -t uploadfs。
#include <Arduino.h>
#include <FS.h>
#include <LittleFS.h>
#include <AnimatedGIF.h>
#include <algorithm>
#include <vector>

#include "lgfx_cyd.hpp"

static LGFX lcd;
static AnimatedGIF gif;

static std::vector<String> files;
static size_t current = 0;
static int offX = 0, offY = 0;  // GIF 畫布在螢幕上的左上角
static File gifFile;

// ---- AnimatedGIF 檔案 callback（讀 LittleFS） ----
static void *gifOpen(const char *name, int32_t *size) {
  gifFile = LittleFS.open(name, "r");
  if (!gifFile) return nullptr;
  *size = gifFile.size();
  return &gifFile;
}

static void gifClose(void *handle) {
  File *f = static_cast<File *>(handle);
  if (f) f->close();
}

static int32_t gifRead(GIFFILE *pFile, uint8_t *buf, int32_t len) {
  File *f = static_cast<File *>(pFile->fHandle);
  int32_t remain = pFile->iSize - pFile->iPos;
  if (len > remain) len = remain;
  if (len <= 0) return 0;
  int32_t n = f->read(buf, len);
  pFile->iPos = f->position();
  return n;
}

static int32_t gifSeek(GIFFILE *pFile, int32_t pos) {
  File *f = static_cast<File *>(pFile->fHandle);
  f->seek(pos);
  pFile->iPos = f->position();
  return pFile->iPos;
}

// COOKED 模式：函式庫已處理透明與 disposal，pPixels 是這一行的 RGB565（LE）
static void gifDraw(GIFDRAW *d) {
  lcd.pushImage(offX + d->iX, offY + d->iY + d->y, d->iWidth, 1,
                reinterpret_cast<const lgfx::rgb565_t *>(d->pPixels));
}

// ---- 觸控：按下的那一刻算一次 ----
static bool touchDown = false;
static uint32_t lastTapMs = 0;

static bool tapped() {
  int32_t x, y;
  bool down = lcd.getTouch(&x, &y);
  if (!down) {
    touchDown = false;
    return false;
  }
  if (touchDown) return false;
  touchDown = true;
  uint32_t now = millis();
  if (now - lastTapMs < 300) return false;
  lastTapMs = now;
  return true;
}

static void showMessage(const char *line1, const char *line2) {
  lcd.fillScreen(TFT_BLACK);
  lcd.setTextColor(TFT_WHITE, TFT_BLACK);
  lcd.setTextDatum(middle_center);
  lcd.setTextSize(2);
  lcd.drawString(line1, lcd.width() / 2, lcd.height() / 2 - 14);
  lcd.setTextSize(1);
  lcd.drawString(line2, lcd.width() / 2, lcd.height() / 2 + 14);
}

static void listGifs() {
  files.clear();
  File root = LittleFS.open("/");
  if (!root) return;
  for (File f = root.openNextFile(); f; f = root.openNextFile()) {
    if (f.isDirectory()) continue;
    String name = f.name();
    if (!name.startsWith("/")) name = "/" + name;
    String lower = name;
    lower.toLowerCase();
    if (lower.endsWith(".gif")) {
      files.push_back(name);
      Serial.printf("gif %s %u bytes\n", name.c_str(), (unsigned)f.size());
    }
  }
  // 依檔名排序，播放順序固定
  std::sort(files.begin(), files.end(), [](const String &a, const String &b) { return a < b; });
}

// 播 files[idx] 直到被點擊（回傳 true）或解碼失敗（回傳 false）
static bool playUntilTap(size_t idx) {
  const char *name = files[idx].c_str();
  if (!gif.open(name, gifOpen, gifClose, gifRead, gifSeek, gifDraw)) {
    Serial.printf("err open %s %d\n", name, gif.getLastError());
    return false;
  }
  int cw = gif.getCanvasWidth(), ch = gif.getCanvasHeight();
  offX = (lcd.width() - cw) / 2;
  offY = (lcd.height() - ch) / 2;
  gif.setDrawType(GIF_DRAW_COOKED);
  if (gif.allocFrameBuf() != GIF_SUCCESS) {
    Serial.printf("err framebuf %dx%d free %u\n", cw, ch, (unsigned)ESP.getFreeHeap());
    gif.close();
    return false;
  }
  Serial.printf("play %s %dx%d free %u\n", name, cw, ch, (unsigned)ESP.getFreeHeap());
  lcd.fillScreen(TFT_BLACK);

  bool tap = false;
  int frames = 0;
  while (!tap) {
    uint32_t start = millis();
    int delayMs = 0;
    lcd.startWrite();
    int rc = gif.playFrame(false, &delayMs);
    lcd.endWrite();
    frames++;
    if (rc < 0) {
      Serial.printf("err decode %s %d\n", name, gif.getLastError());
      break;
    }
    // 照 GIF 的幀間隔等，等的時候看觸控
    if (delayMs < 10) delayMs = 10;
    while (millis() - start < (uint32_t)delayMs) {
      if (tapped()) {
        tap = true;
        break;
      }
      delay(2);
    }
    if (rc == 0) {  // 最後一幀：回到開頭
      if (frames <= 1) {  // 單幀或無法前進：靜止顯示，只等觸控
        while (!tapped()) delay(10);
        tap = true;
      }
      gif.reset();
      frames = 0;
    }
  }
  gif.freeFrameBuf(free);
  gif.close();
  return tap;
}

void setup() {
  Serial.begin(115200);
  lcd.init();
  lcd.setRotation(PANEL_ROTATION);
  lcd.setBrightness(255);
  lcd.fillScreen(TFT_BLACK);

  if (!LittleFS.begin(false)) {
    Serial.println("err littlefs mount");
    showMessage("LittleFS error", "run: pio run -e cyd_gif -t uploadfs");
    return;
  }
  Serial.printf("boot gif %s fs %u/%u bytes\n", PANEL_NAME, (unsigned)LittleFS.usedBytes(),
                (unsigned)LittleFS.totalBytes());
  listGifs();
  Serial.printf("found %u gif\n", (unsigned)files.size());
  if (files.empty()) {
    showMessage("No GIF found", "put .gif in data/ then uploadfs");
    return;
  }
  gif.begin(GIF_PALETTE_RGB565_LE);
}

void loop() {
  if (files.empty()) {
    delay(200);
    return;
  }
  bool ok = playUntilTap(current);
  if (!ok && files.size() == 1) {
    showMessage("GIF decode error", files[0].c_str());
    while (true) delay(1000);
  }
  current = (current + 1) % files.size();
}
