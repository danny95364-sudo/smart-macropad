// CYD 小遊戲「六鎖保險箱」韌體。規則見 design/cyd-game-plan.md，邏輯在 game_logic.hpp。
//
// 序列埠 115200，一行一個按鍵：{"k":N} 或單一字元 1～9。
//   1～6 選鎖芯、7 數字 -1（旋鈕左轉）、8 數字 +1（旋鈕右轉）、9 試開（按旋鈕）
// 觸控（XPT2046）：遊戲畫面右上「重開」按鈕開新局；標題／過關／失敗畫面點任何地方 = 按旋鈕。
// 每次觸控觸發印一行 touch x y（看座標準不準）。
// 電腦端轉送程式：python -m macropad.game_bridge。每收到一行回 ok k N / err <原因>。

#include <Arduino.h>

#include "lgfx_cyd.hpp"
#include "game_logic.hpp"

static LGFX lcd;
static LGFX_Sprite spr(&lcd);
static safe::Game game;
static bool dirty = true;

// ── 配色（sprite 是 8 bit RGB332，顏色挑分得開的）──
static const uint32_t C_BG = 0x000000;
static const uint32_t C_TX = 0xFFFFFF;
static const uint32_t C_TX2 = 0x909090;
static const uint32_t C_CELL = 0x242424;
static const uint32_t C_CELL_SEL = 0x484848;
static const uint32_t C_LINE = 0x484848;
static const uint32_t C_GREEN = 0x20C040;
static const uint32_t C_YELLOW = 0xFFC000;
static const uint32_t C_RED = 0xE02020;

static uint32_t markColor(uint8_t m) {
  switch (m) {
    case safe::GREEN: return C_GREEN;
    case safe::YELLOW: return C_YELLOW;
    case safe::RED: return C_RED;
    default: return C_CELL;
  }
}

// ── 文字：中文只用 efontTW_16 一種（大字用放大）；數字用內建 Font7（七段）、Font2、Font0 ──
static void zh(int x, int y, const char* s, uint32_t c, textdatum_t d = textdatum_t::top_left,
               float scale = 1.0f) {
  spr.setFont(&fonts::efontTW_16);
  spr.setTextSize(scale);
  spr.setTextColor(c);
  spr.setTextDatum(d);
  spr.drawString(s, x, y);
  spr.setTextSize(1);
}

static void asc(int x, int y, const char* s, const lgfx::IFont* f, uint32_t c,
                textdatum_t d = textdatum_t::top_left, float scale = 1.0f) {
  spr.setFont(f);
  spr.setTextSize(scale);
  spr.setTextColor(c);
  spr.setTextDatum(d);
  spr.drawString(s, x, y);
  spr.setTextSize(1);
}

static void bigDigit(int cx, int cy, int v, uint32_t c, float scale = 1.0f) {
  char s[2] = {(char)('0' + v), 0};
  asc(cx, cy, s, &fonts::Font7, c, textdatum_t::middle_center, scale);
}

// 鎖頭記號：鎖身＋U 形鎖環
static void padlock(int x, int y, uint32_t c) {
  spr.drawArc(x + 7, y + 7, 5, 3, 180, 360, c);
  spr.fillRect(x + 2, y + 7, 3, 2, c);
  spr.fillRect(x + 10, y + 7, 3, 2, c);
  spr.fillRoundRect(x, y + 8, 15, 11, 2, c);
}

static void arrow(int cx, int y, bool up, uint32_t c) {
  if (up) spr.fillTriangle(cx, y, cx + 8, y + 11, cx - 8, y + 11, c);
  else spr.fillTriangle(cx - 8, y, cx + 8, y, cx, y + 11, c);
}

// ── 遊戲畫面 ──
static const int CELL_W = 100, CELL_H = 58;
static int cellX(int i) { return 6 + (i % 3) * 104; }
static int cellY(int i) { return 44 + (i / 3) * 62; }

// 右上「重開」按鈕；觸控判定區比畫的大一圈，容忍校正誤差
static const int BTN_X = 236, BTN_Y = 3, BTN_W = 78, BTN_H = 36;
static bool inRestart(int x, int y) { return x >= BTN_X - 16 && y <= BTN_Y + BTN_H + 8; }

static void drawTopBar() {
  char s[40];
  snprintf(s, sizeof s, "第 %d 關", game.level);
  zh(6, 3, s, C_TX);
  snprintf(s, sizeof s, "分數 %d", game.score);
  zh(6, 22, s, C_TX2);
  snprintf(s, sizeof s, "剩 %d / %d 次", game.triesLeft, game.triesMax);
  zh(150, 12, s, game.triesLeft <= 2 ? C_RED : C_TX, textdatum_t::top_center);
  spr.fillRoundRect(BTN_X, BTN_Y, BTN_W, BTN_H, 6, C_CELL_SEL);
  spr.drawRoundRect(BTN_X, BTN_Y, BTN_W, BTN_H, 6, C_TX2);
  zh(BTN_X + BTN_W / 2, BTN_Y + BTN_H / 2, "重開", C_TX, textdatum_t::middle_center);
}

static void drawCell(int i) {
  int x = cellX(i), y = cellY(i);
  bool sel = game.cursor == i && !game.locked[i];
  char n[2] = {(char)('1' + i), 0};
  if (game.locked[i]) {
    spr.fillRoundRect(x, y, CELL_W, CELL_H, 6, C_GREEN);
    asc(x + 6, y + 4, n, &fonts::Font2, C_BG);
    padlock(x + CELL_W - 21, y + 5, C_BG);
    bigDigit(x + CELL_W / 2, y + CELL_H / 2 - 1, game.cur[i], C_BG);
    return;
  }
  spr.fillRoundRect(x, y, CELL_W, CELL_H, 6, sel ? C_CELL_SEL : C_CELL);
  if (sel)
    for (int k = 0; k < 3; k++) spr.drawRoundRect(x + k, y + k, CELL_W - 2 * k, CELL_H - 2 * k, 6 - k, C_TX);
  asc(x + 6, y + 4, n, &fonts::Font2, sel ? C_TX : C_TX2);
  bigDigit(x + CELL_W / 2, y + CELL_H / 2 - 1, game.cur[i], C_TX);
  uint8_t m = game.lastMark[i];
  if (m != safe::NONE) spr.fillRect(x + 10, y + CELL_H - 6, CELL_W - 20, 4, markColor(m));
  // 第 1 關：上次試的數字＋往大／往小箭頭
  if (safe::hasArrows(game.level) && game.hint[i] != 0) {
    arrow(x + CELL_W - 13, y + 10, game.hint[i] > 0, markColor(m));
    char t[2] = {(char)('0' + game.lastTry[i]), 0};
    asc(x + CELL_W - 13, y + 24, t, &fonts::Font2, C_TX2, textdatum_t::top_center);
  }
}

static void drawHistory() {
  for (int h = 0; h < game.triesMax && h < safe::MAX_HIST; h++) {
    int sx = 6 + h * 39;
    char lab[4];
    snprintf(lab, sizeof lab, "%d", h + 1);
    asc(sx + 19, 168, lab, &fonts::Font0, C_TX2, textdatum_t::top_center);
    for (int i = 0; i < safe::LOCKS; i++) {
      int cx = sx + (i % 3) * 13, cy = 178 + (i / 3) * 19;
      if (h < game.histN) {
        const safe::Attempt& a = game.hist[h];
        uint32_t c = markColor(a.mark[i]);
        spr.fillRect(cx, cy, 12, 18, c);
        char d[2] = {(char)('0' + a.digit[i]), 0};
        asc(cx + 6, cy + 9, d, &fonts::Font0, a.mark[i] == safe::RED ? C_TX : C_BG,
            textdatum_t::middle_center);
      } else {
        spr.drawRect(cx, cy, 12, 18, C_LINE);
      }
    }
  }
}

static void drawPlay() {
  drawTopBar();
  for (int i = 0; i < safe::LOCKS; i++) drawCell(i);
  drawHistory();
  zh(160, 222, "1～6 選鎖芯 · 旋鈕改數字 · 按下試開", C_TX2, textdatum_t::top_center);
}

static void legend(int x, int y, uint32_t c, const char* s) {
  spr.fillRect(x, y + 2, 12, 12, c);
  zh(x + 17, y, s, C_TX);
}

static void drawTitle() {
  zh(160, 14, "六鎖保險箱", C_TX, textdatum_t::top_center, 2.0f);
  zh(20, 60, "保險箱有 6 個鎖芯，各藏 0～9 一個數字", C_TX2);
  zh(20, 84, "鍵 1～6　選鎖芯", C_TX);
  zh(20, 106, "旋鈕轉　數字 -1 / +1", C_TX);
  zh(20, 128, "按旋鈕　試開", C_TX);
  legend(20, 158, C_GREEN, "對了");
  legend(100, 158, C_YELLOW, "差一點");
  legend(196, 158, C_RED, "差很多");
  zh(160, 206, "按任何鍵開始", C_YELLOW, textdatum_t::top_center);
}

// 一排顯示 6 位密碼（過關／失敗畫面）
static void codeRow(int y, bool showLocked) {
  for (int i = 0; i < safe::LOCKS; i++) {
    int x = 18 + i * 49;
    uint32_t bg = showLocked && !game.locked[i] ? C_CELL : C_GREEN;
    spr.fillRoundRect(x, y, 42, 56, 5, bg);
    bigDigit(x + 21, y + 29, game.code[i], bg == C_GREEN ? C_BG : C_TX, 0.8f);
  }
}

static void drawWin() {
  char s[64];
  snprintf(s, sizeof s, "第 %d 關 破解！", game.level);
  zh(160, 12, s, C_GREEN, textdatum_t::top_center, 2.0f);
  codeRow(58, false);
  snprintf(s, sizeof s, "用了 %d 次，剩 %d 次", game.used, game.triesLeft);
  zh(160, 128, s, C_TX, textdatum_t::top_center);
  snprintf(s, sizeof s, "累計分數 %d", game.score);
  zh(160, 152, s, C_TX, textdatum_t::top_center);
  int nl = game.level + 1;
  snprintf(s, sizeof s, "下一關：%d 次機會%s", safe::triesFor(nl),
           safe::hasArrows(nl) ? "" : "，沒有箭頭");
  zh(160, 180, s, C_TX2, textdatum_t::top_center);
  zh(160, 210, "按旋鈕 進下一關", C_YELLOW, textdatum_t::top_center);
}

static void drawLose() {
  char s[64];
  zh(160, 12, "機會用完", C_RED, textdatum_t::top_center, 2.0f);
  zh(160, 50, "密碼是", C_TX2, textdatum_t::top_center);
  codeRow(72, true);
  snprintf(s, sizeof s, "到第 %d 關 · 分數 %d", game.level, game.score);
  zh(160, 148, s, C_TX, textdatum_t::top_center);
  zh(160, 210, "按旋鈕 重新開始", C_YELLOW, textdatum_t::top_center);
}

static void render() {
  spr.fillScreen(C_BG);
  switch (game.phase) {
    case safe::TITLE: drawTitle(); break;
    case safe::PLAY: drawPlay(); break;
    case safe::WIN: drawWin(); break;
    case safe::LOSE: drawLose(); break;
  }
  lcd.startWrite();
  spr.pushSprite(0, 0);
  lcd.endWrite();
}

// ── 收按鍵：{"k":N} 或單一字元 1～9 ──
static char buf[128];
static size_t blen = 0;
static bool overflow = false;

static int parseKey(const char* s) {
  while (*s == ' ') s++;
  if (s[0] >= '1' && s[0] <= '9' && (s[1] == 0 || s[1] == ' ')) return s[0] - '0';
  const char* p = strstr(s, "\"k\"");
  if (!p) return 0;
  p = strchr(p + 3, ':');
  if (!p) return 0;
  return atoi(p + 1);
}

static void processLine() {
  buf[blen] = 0;
  int k = parseKey(buf);
  if (k < 1 || k > 9) {
    Serial.println("err bad key");
    return;
  }
  if (game.press(k)) dirty = true;
  Serial.printf("ok k %d\n", k);
}

static void readSerial() {
  while (Serial.available()) {
    int ch = Serial.read();
    if (ch == '\n') {
      if (overflow) Serial.println("err line too long");
      else if (blen) processLine();
      blen = 0;
      overflow = false;
    } else if (ch != '\r') {
      if (blen < sizeof(buf) - 1) buf[blen++] = (char)ch;
      else overflow = true;
    }
  }
}

// esp_random 沒開 WiFi 時亂度較弱，混入按鍵當下的 micros()（按鍵時間點本身就隨機）
// ── 觸控：放開前不重複觸發，且兩次觸發至少隔 300ms ──
static const uint32_t TOUCH_COOLDOWN_MS = 300;
static bool touchDown = false;
static uint32_t lastTouchMs = 0;

static void readTouch() {
  int32_t x, y;
  bool down = lcd.getTouch(&x, &y);
  if (!down) {
    touchDown = false;
    return;
  }
  if (touchDown) return;
  touchDown = true;
  uint32_t now = millis();
  if (now - lastTouchMs < TOUCH_COOLDOWN_MS) return;
  lastTouchMs = now;
  Serial.printf("touch %d %d\n", (int)x, (int)y);
  if (game.phase == safe::PLAY) {
    if (inRestart(x, y)) {
      game.newGame();
      dirty = true;
    }
  } else if (game.press(9)) {
    dirty = true;
  }
}

static uint32_t hwRandom() { return esp_random() ^ (micros() * 2654435761u); }

void setup() {
  Serial.begin(115200);
  lcd.init();
  lcd.setRotation(PANEL_ROTATION);
  lcd.setBrightness(255);
  lcd.fillScreen(C_BG);
  // 同看板：16 bit 整張放不下，用 8 bit（RGB332）整張 sprite
  spr.setColorDepth(8);
  bool ok = spr.createSprite(320, 240) != nullptr;
  Serial.printf("boot game %s sprite %s free %u\n", PANEL_NAME, ok ? "ok" : "FAIL",
                (unsigned)ESP.getFreeHeap());
  game.rng = hwRandom;
  render();
  dirty = false;
}

void loop() {
  readSerial();
  readTouch();
  if (dirty) {
    dirty = false;
    render();
  }
  delay(5);
}
