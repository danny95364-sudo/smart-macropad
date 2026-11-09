// 單字卡顯示端（env cyd_vocab）：字卡、排程、鍵盤全由 PC（macropad.vocab）處理，板子只負責畫面、
// learn 卡的自動翻面計時與觸控。不存檔、不開 Wi-Fi/藍牙、沒有聲音。
//
// 序列埠 115200，UTF-8，一行一則，欄位用 Tab 分隔（design/cyd-vocab-impl-plan.md 段 C）：
//   PC → 板子
//     card  <id> <learn|due> <word> <pos> <zh> <example> <statusL> <statusR>
//           顯示正面；learn 正面 6 s → 背面 8 s 自動翻面；due 不自動翻面
//     flip                    翻面
//     toast <文字> <ok|bad|info>   顯示 900 ms（期間 learn 計時暫停）
//     status <statusL> <statusR>   只更新狀態列
//     pause / resume          暫停／繼續計時，狀態列顯示「暫停」色塊
//     shot                    截圖：回 "shot 320 240"＋RGB565 原始像素＋"shot end"（tools/cyd_shot.py）
//   板子 → PC（同樣 Tab 分隔）
//     hello cyd_vocab <版本>   開機
//     back <id>               翻到背面（自動或 flip）
//     done <id>               learn 卡背面時間到（之後停在背面等下一張 card）
//     tap / hold              觸控短按／長按 ≥ 1 秒（PC 回 flip／切換暫停）
//     err <說明>              指令格式錯誤
//
// 畫面：16 bit 整張 320x240 sprite 要 150KB，沒 PSRAM 放不下；
// 改用 320x120 的 16 bit sprite 分上下兩段畫，換卡時整張重畫不閃，同一面期間只更新進度條。
#include <Arduino.h>

#include "lgfx_cyd.hpp"

static const char* VERSION = "2";

// ── 時間 ──
static const uint32_t FRONT_MS = 6000;       // learn 正面停留
static const uint32_t BACK_MS = 8000;        // learn 背面停留
static const uint32_t TOAST_MS = 900;
static const uint32_t LONG_PRESS_MS = 1000;  // 觸控長按 = hold

static LGFX lcd;
static LGFX_Sprite band(&lcd);
static const int W = 320, H = 240, BAND_H = 120;
static int bandY = 0;  // 目前這段 sprite 對應到螢幕的 y

// ── 配色 ──
static const uint32_t C_BG = 0x0E1116;
static const uint32_t C_STATUS = 0x8A9099;
static const uint32_t C_WORD = 0xFFFFFF;
static const uint32_t C_DIM = 0x5C626B;
static const uint32_t C_BLUE = 0x378ADD;
static const uint32_t C_AMBER = 0xFAC775;
static const uint32_t C_EXAMPLE = 0xB4B2A9;
static const uint32_t C_TEAL_TX = 0x5DCAA5;
static const uint32_t C_OK_BG = 0x085041, C_OK_TX = 0x9FE1CB;
static const uint32_t C_BAD_BG = 0x3A2024, C_BAD_TX = 0xF0A0A0;
static const uint32_t C_INFO_BG = 0x15304D, C_INFO_TX = 0x9CC8F0;
static const uint32_t C_PAUSE_BG = 0x3A2024, C_PAUSE_TX = 0xF0A0A0;
static const uint32_t C_DUE_BAR = 0x7F77DD;                        // 到期卡左側紫色直條
static const uint32_t C_DUE_BG = 0x3C3489, C_DUE_TX = 0xCECBF6;    // 「複習」色塊

static const lgfx::IFont* F_ZH_S = &fonts::efontTW_14;   // 狀態列、詞性
static const lgfx::IFont* F_ZH_XS = &fonts::efontTW_12;  // 底部按鍵說明、暫停色塊
static const lgfx::IFont* F_ZH_L = &fonts::efontTW_24;   // 中文意思、toast、等待畫面
// 單字：太長就換小一號
static const lgfx::IFont* F_FRONT[] = {&fonts::FreeSansBold24pt7b, &fonts::FreeSansBold18pt7b,
                                       &fonts::FreeSansBold12pt7b};
static const lgfx::IFont* F_BACK[] = {&fonts::FreeSansBold18pt7b, &fonts::FreeSansBold12pt7b,
                                      &fonts::FreeSansBold9pt7b};
static const lgfx::IFont* F_EX = &fonts::FreeSans9pt7b;  // 例句（只有 ASCII）

// ── 版面 ──
static const int DUE_BAR_W = 6;
static const int STATUS_CY = 14;
static const int FRONT_WORD_Y = 106;
static const int BACK_WORD_Y = 48, MEANING_Y = 94;
static const int STACK_POS_Y = 74, STACK_MEANING_Y = 100;  // 詞性＋中文放不下一行時改上下排
static const int EX_TOP = 120, EX_BOTTOM = 204;            // 例句可用區
static const int TOAST_CY = 100;
static const int BAR_Y = 212;     // 4px 進度條
static const int LEGEND_Y = 228;  // 按鍵說明（文字中線）
static const int MAX_TEXT_W = 296;

// ── 目前這張卡（PC 送來）──
static bool hasCard = false;
static bool isDue = false;
static char cId[64], cWord[48], cPos[24], cZh[96], cEx[192];
static char stL[64], stR[64];

// ── 狀態 ──
enum Phase { FRONT, BACK };
static Phase phase = FRONT;
static bool doneSent = false;  // learn 背面計時已到（停著等下一張）
static bool paused = false;
static uint32_t phaseStart = 0;
static int lastBarW = -1;
static bool toastOn = false;
static uint32_t toastStart = 0;
static char toastText[96];
static uint32_t toastBg = 0, toastFg = 0;
// 計時凍結（暫停或有 toast 時）：解凍時把凍結的時間補回 phaseStart
static bool frozen = false;
static uint32_t frozenAt = 0;

static bool timed() { return hasCard && !isDue && !doneSent; }
static uint32_t phaseDur() { return phase == FRONT ? FRONT_MS : BACK_MS; }
static uint32_t phaseElapsed(uint32_t now) { return (frozen ? frozenAt : now) - phaseStart; }
static void updateFreeze() {
  bool want = paused || toastOn;
  uint32_t now = millis();
  if (want && !frozen) {
    frozen = true;
    frozenAt = now;
  } else if (!want && frozen) {
    frozen = false;
    phaseStart += now - frozenAt;
  }
}

static void copyField(char* dst, size_t n, const char* src) {
  strncpy(dst, src ? src : "", n - 1);
  dst[n - 1] = 0;
}

// ── 畫圖（座標都是螢幕座標，自動扣掉目前段的 bandY）──
static void text(const char* s, int x, int y, const lgfx::IFont* f, uint32_t c,
                 textdatum_t d = textdatum_t::top_left) {
  band.setFont(f);
  band.setTextColor(c);
  band.setTextDatum(d);
  band.drawString(s, x, y - bandY);
}
static int textW(const char* s, const lgfx::IFont* f) {
  band.setFont(f);
  return band.textWidth(s);
}
static const lgfx::IFont* fitFont(const char* s, const lgfx::IFont* const* fs, int n, int maxW) {
  for (int i = 0; i < n - 1; i++)
    if (textW(s, fs[i]) <= maxW) return fs[i];
  return fs[n - 1];
}
static int contentCx() { return isDue ? (W + DUE_BAR_W) / 2 : W / 2; }

// 色塊：左緣 x、文字中線 STATUS_CY，回傳右緣
static int chip(int x, const char* s, const lgfx::IFont* f, uint32_t bg, uint32_t fg) {
  int bw = textW(s, f) + 12;
  band.fillRoundRect(x, STATUS_CY - 10 - bandY, bw, 20, 5, bg);
  text(s, x + bw / 2, STATUS_CY, f, fg, textdatum_t::middle_center);
  return x + bw;
}

// 例句換行：寬度 MAX_TEXT_W 內，最多 3 行（再多截斷加 ...），在例句區垂直置中
static void drawExample(const char* s) {
  static const int MAX_LINES = 3, LINE_H = 20;
  char lines[MAX_LINES][96];
  int nLines = 0;
  char cur[96] = {0};
  const char* p = s;
  bool cut = false;
  while (*p) {
    const char* e = p;
    while (*e && *e != ' ') e++;
    char word[64];
    int len = min((int)(e - p), 63);
    memcpy(word, p, len);
    word[len] = 0;
    char trial[160];
    snprintf(trial, sizeof(trial), "%s%s%s", cur, cur[0] ? " " : "", word);
    if (cur[0] && textW(trial, F_EX) > MAX_TEXT_W) {
      if (nLines == MAX_LINES - 1) {
        cut = true;
        break;
      }
      copyField(lines[nLines++], sizeof(lines[0]), cur);
      copyField(cur, sizeof(cur), word);
    } else {
      copyField(cur, sizeof(cur), trial);
    }
    p = *e ? e + 1 : e;
  }
  if (cut) {  // 最後一行塞不下：截到放得下 "..." 為止
    char t[100];
    int n = strlen(cur);
    while (n > 0) {
      snprintf(t, sizeof(t), "%.*s...", n, cur);
      if (textW(t, F_EX) <= MAX_TEXT_W) break;
      n--;
    }
    copyField(cur, sizeof(cur), t);
  }
  if (cur[0] && nLines < MAX_LINES) copyField(lines[nLines++], sizeof(lines[0]), cur);
  int cy = (EX_TOP + EX_BOTTOM) / 2;
  int y0 = cy - (nLines * LINE_H) / 2 + LINE_H / 2;
  for (int i = 0; i < nLines; i++)
    text(lines[i], contentCx(), y0 + i * LINE_H, F_EX, C_EXAMPLE, textdatum_t::middle_center);
}

static uint32_t barColor() { return phase == FRONT ? C_BLUE : C_AMBER; }

// 進度條寬度（只有 learn 卡）：正面由 0 長到滿，背面由滿縮到 0
static int barWidth(uint32_t now) {
  if (!hasCard || isDue) return 0;
  if (doneSent) return 0;
  uint32_t dur = phaseDur();
  uint32_t t = min(phaseElapsed(now), dur);
  int w = (int)((uint64_t)W * t / dur);
  return phase == FRONT ? w : W - w;
}

// 底部一行說明：「2 會 · 1 不會 · 旋鈕 換字/翻面」
static void drawLegend() {
  const char* seg[3] = {"2 會", "1 不會", "旋鈕 換字/翻面"};
  uint32_t col[3] = {C_TEAL_TX, C_BAD_TX, C_DIM};
  const char* sep = " · ";
  int sw = textW(sep, F_ZH_XS), total = 2 * sw;
  int w[3];
  for (int i = 0; i < 3; i++) total += (w[i] = textW(seg[i], F_ZH_XS));
  int x = (W - total) / 2;
  for (int i = 0; i < 3; i++) {
    text(seg[i], x, LEGEND_Y, F_ZH_XS, col[i], textdatum_t::middle_left);
    x += w[i];
    if (i < 2) {
      text(sep, x, LEGEND_Y, F_ZH_XS, C_DIM, textdatum_t::middle_left);
      x += sw;
    }
  }
}

// 狀態列：左「D12·新3」或「複習」色塊（＋「暫停」色塊），右「答8·剩5」
static void drawStatus() {
  int x = isDue ? DUE_BAR_W + 8 : 10;
  if (isDue) {
    x = chip(x, "複習", F_ZH_S, C_DUE_BG, C_DUE_TX);
  } else {
    text(stL, x, STATUS_CY, F_ZH_S, C_STATUS, textdatum_t::middle_left);
    x += textW(stL, F_ZH_S);
  }
  if (paused) chip(x + 8, "暫停", F_ZH_XS, C_PAUSE_BG, C_PAUSE_TX);
  text(stR, W - 10, STATUS_CY, F_ZH_S, C_STATUS, textdatum_t::middle_right);
}

static void drawToast() {
  int tw = constrain(textW(toastText, F_ZH_L) + 48, 120, W - 8), th = 48;
  int x = (W - tw) / 2, y = TOAST_CY - th / 2 - bandY;
  band.fillRoundRect(x, y, tw, th, 10, toastBg);
  band.drawRoundRect(x, y, tw, th, 10, toastFg);
  text(toastText, W / 2, TOAST_CY + 1, F_ZH_L, toastFg, textdatum_t::middle_center);
}

static void drawScene(uint32_t now) {
  band.fillScreen(C_BG);
  if (!hasCard) {
    text("等待電腦連線", W / 2, H / 2 - 8, F_ZH_L, C_STATUS, textdatum_t::middle_center);
    if (paused) {
      int bw = textW("暫停", F_ZH_XS) + 12;
      chip((W - bw) / 2, "暫停", F_ZH_XS, C_PAUSE_BG, C_PAUSE_TX);
    }
    if (toastOn) drawToast();
    return;
  }
  if (isDue) band.fillRect(0, 0 - bandY, DUE_BAR_W, H, C_DUE_BAR);
  drawStatus();

  int cx = contentCx();
  if (phase == FRONT) {
    text(cWord, cx, FRONT_WORD_Y, fitFont(cWord, F_FRONT, 3, MAX_TEXT_W), C_WORD,
         textdatum_t::middle_center);
  } else {
    text(cWord, cx, BACK_WORD_Y, fitFont(cWord, F_BACK, 3, MAX_TEXT_W), C_WORD,
         textdatum_t::middle_center);
    // 詞性（小、灰）+ 中文意思（琥珀），整組置中；一行放不下就上下排
    int pw = textW(cPos, F_ZH_S), mw = textW(cZh, F_ZH_L), gap = 8;
    if (pw + gap + mw <= MAX_TEXT_W) {
      int x0 = cx - (pw + gap + mw) / 2;
      text(cPos, x0, MEANING_Y, F_ZH_S, C_STATUS, textdatum_t::middle_left);
      text(cZh, x0 + pw + gap, MEANING_Y, F_ZH_L, C_AMBER, textdatum_t::middle_left);
    } else {
      text(cPos, cx, STACK_POS_Y, F_ZH_S, C_STATUS, textdatum_t::middle_center);
      text(cZh, cx, STACK_MEANING_Y, F_ZH_L, C_AMBER, textdatum_t::middle_center);
    }
    drawExample(cEx);
  }

  int bw = barWidth(now);
  if (bw > 0) band.fillRect(0, BAR_Y - bandY, bw, 4, barColor());
  drawLegend();
  if (toastOn) drawToast();
}

static void render() {
  uint32_t now = millis();
  lcd.startWrite();
  for (bandY = 0; bandY < H; bandY += BAND_H) {
    drawScene(now);
    band.pushSprite(0, bandY);
    lcd.waitDMA();  // 16 bit 同色深會走 DMA 非同步送，等送完才能畫下一段（不然上半段會變成下半段內容）
  }
  lcd.endWrite();
  bandY = 0;
  lastBarW = barWidth(now);
}

static uint16_t rgb565(uint32_t c) { return lcd.color888(c >> 16, (c >> 8) & 0xFF, c & 0xFF); }

// 同一面期間只更新進度條（直接畫到螢幕，只畫有變的那一段）
static void updateBar() {
  int w = barWidth(millis());
  if (w == lastBarW) return;
  lcd.startWrite();
  if (w > lastBarW) lcd.fillRect(lastBarW, BAR_Y, w - lastBarW, 4, rgb565(barColor()));
  else lcd.fillRect(w, BAR_Y, lastBarW - w, 4, rgb565(C_BG));
  lcd.endWrite();
  lastBarW = w;
}

// ── 翻面 ──
static void enter(Phase p) {
  phase = p;
  phaseStart = millis();
  if (frozen) frozenAt = phaseStart;
  if (p == BACK) Serial.printf("back\t%s\n", cId);
  render();
}

// ── 序列埠指令 ──
static const int MAX_FIELDS = 10;

static void cmdCard(char** f, int n) {
  if (n < 9) {
    Serial.printf("err\tcard needs 8 fields, got %d\n", n - 1);
    return;
  }
  if (strcmp(f[2], "learn") != 0 && strcmp(f[2], "due") != 0) {
    Serial.printf("err\tbad kind %s\n", f[2]);
    return;
  }
  copyField(cId, sizeof(cId), f[1]);
  isDue = strcmp(f[2], "due") == 0;
  copyField(cWord, sizeof(cWord), f[3]);
  copyField(cPos, sizeof(cPos), f[4]);
  copyField(cZh, sizeof(cZh), f[5]);
  copyField(cEx, sizeof(cEx), f[6]);
  copyField(stL, sizeof(stL), f[7]);
  copyField(stR, sizeof(stR), f[8]);
  hasCard = true;
  doneSent = false;
  enter(FRONT);
}

static void cmdToast(char** f, int n) {
  if (n < 2) {
    Serial.print("err\ttoast needs text\n");
    return;
  }
  const char* kind = n >= 3 ? f[2] : "info";
  copyField(toastText, sizeof(toastText), f[1]);
  if (strcmp(kind, "ok") == 0) toastBg = C_OK_BG, toastFg = C_OK_TX;
  else if (strcmp(kind, "bad") == 0) toastBg = C_BAD_BG, toastFg = C_BAD_TX;
  else toastBg = C_INFO_BG, toastFg = C_INFO_TX;
  toastOn = true;
  toastStart = millis();
  updateFreeze();
  render();
}

// 截圖：重畫一次，把 RGB565（big-endian）原始像素整張送回 PC（tools/cyd_shot.py）
static void sendShot() {
  uint32_t now = millis();
  Serial.printf("shot %d %d\n", W, H);
  for (bandY = 0; bandY < H; bandY += BAND_H) {
    drawScene(now);
    Serial.write((const uint8_t*)band.getBuffer(), W * BAND_H * 2);
  }
  bandY = 0;
  Serial.print("\nshot end\n");
}

static void handleLine(char* line) {
  char* f[MAX_FIELDS];
  int n = 0;
  f[n++] = line;
  for (char* p = line; *p && n < MAX_FIELDS; p++) {
    if (*p == '\t') {
      *p = 0;
      f[n++] = p + 1;
    }
  }
  const char* c = f[0];
  if (strcmp(c, "card") == 0) {
    cmdCard(f, n);
  } else if (strcmp(c, "flip") == 0) {
    if (!hasCard) return;
    doneSent = false;
    enter(phase == FRONT ? BACK : FRONT);
  } else if (strcmp(c, "toast") == 0) {
    cmdToast(f, n);
  } else if (strcmp(c, "status") == 0) {
    if (n < 3) {
      Serial.print("err\tstatus needs 2 fields\n");
      return;
    }
    copyField(stL, sizeof(stL), f[1]);
    copyField(stR, sizeof(stR), f[2]);
    render();
  } else if (strcmp(c, "pause") == 0) {
    paused = true;
    updateFreeze();
    render();
  } else if (strcmp(c, "resume") == 0) {
    paused = false;
    updateFreeze();
    render();
  } else if (strcmp(c, "shot") == 0) {
    sendShot();
  } else {
    Serial.printf("err\tunknown command %s\n", c);
  }
}

static void readSerial() {
  static char buf[512];
  static int n = 0;
  static bool overflow = false;
  while (Serial.available()) {
    int ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      buf[n] = 0;
      if (overflow) Serial.print("err\tline too long\n");
      else if (n) handleLine(buf);
      n = 0;
      overflow = false;
    } else if (n < (int)sizeof(buf) - 1) {
      buf[n++] = (char)ch;
    } else {
      overflow = true;
    }
  }
}

// ── 觸控：按住 ≥ 1 秒 = hold（按住時就送）；短按在放開時送 tap ──
static bool touching = false, longFired = false;
static uint32_t touchStart = 0;

static void readTouch() {
  int32_t x, y;
  bool down = lcd.getTouch(&x, &y);
  uint32_t now = millis();
  if (down && !touching) {
    touching = true;
    longFired = false;
    touchStart = now;
  } else if (down && touching) {
    if (!longFired && now - touchStart >= LONG_PRESS_MS) {
      longFired = true;
      Serial.print("hold\n");
    }
  } else if (!down && touching) {
    touching = false;
    if (longFired || now - touchStart < 30) return;  // 太短當雜訊
    Serial.print("tap\n");
  }
}

void setup() {
  Serial.setRxBufferSize(1024);  // 一行最多 512 bytes，畫圖時也不會掉字
  Serial.begin(115200);
  btStop();  // 不開藍牙；Wi-Fi 從沒初始化
  lcd.init();
  lcd.setRotation(PANEL_ROTATION);
  lcd.setBrightness(255);
  lcd.fillScreen(rgb565(C_BG));
  band.setColorDepth(16);
  band.createSprite(W, BAND_H);
  Serial.printf("hello\tcyd_vocab\t%s\n", VERSION);
  render();
}

void loop() {
  readSerial();
  readTouch();
  uint32_t now = millis();
  if (toastOn && now - toastStart >= TOAST_MS) {
    toastOn = false;
    updateFreeze();
    render();
  } else if (!toastOn && timed() && !paused && phaseElapsed(now) >= phaseDur()) {
    if (phase == FRONT) {
      enter(BACK);
    } else {
      doneSent = true;
      Serial.printf("done\t%s\n", cId);
      render();
    }
  } else {
    updateBar();
  }
  delay(5);
}
