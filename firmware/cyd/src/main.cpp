// CYD 看板韌體：收序列埠（115200）一行一個 JSON，照方向 B 色塊畫。
// 資料格式見 macropad/display.py 檔頭；畫法對照 macropad/display_sim.py。
//
// 額外的除錯指令（電腦端不會送，手動測試用）：
//   {"t":"inv","v":1}   顏色反相開/關（白屏、黑白顛倒時試）
//   {"t":"rot","v":3}   旋轉方向 0～3（上下顛倒時試）
// 每收到一行回一行：ok <t> / err <原因>，開機時送 boot <面板>。

#include <Arduino.h>
#include <ArduinoJson.h>
#include <vector>

#include "lgfx_cyd.hpp"

static LGFX lcd;
static LGFX_Sprite spr(&lcd);
static int bandH = 240;  // sprite 高度；記憶體不夠時分段畫
static int OY = 0;       // 目前這段的頂端 y

// ── 方向 B 配色 ──
static const uint32_t K_BG = 0x000000;
static const uint32_t K_TX = 0xFFFFFF;
static const uint32_t K_TX2 = 0xA0A0A0;
static const uint32_t K_CARD = 0xF2F2F2;
static const uint32_t K_CTX = 0x111111;
static const uint32_t K_CTX2 = 0x555555;
static const uint32_t K_LINE = 0x333333;
static const uint32_t K_ERRC = 0xD92D20;
static const uint32_t K_OFFC = 0x333333;
static const uint32_t K_WHITE = 0xFFFFFF;
static const uint32_t K_BLACK = 0x000000;

static const int XS[3] = {56, 144, 232};
static const int YS[2] = {4, 122};
static const int TILE_W = 84, TILE_H = 114;

static const uint32_t PRESS_MS = 150;
static const uint32_t CARD_MS = 4000;
static const uint32_t ERR_MS = 6000;
static const uint32_t SWITCH_MS = 500;
static const uint32_t KNOB_MS = 300;
static const uint32_t OFFLINE_MS = 10000;

// ── 狀態 ──
struct State {
  bool valid = false;
  String m, n, rn;
  uint32_t c = 0x44546A;
  String k[6];
  String r[3];
} st;

struct Card {
  bool on = false;
  int i = 0;
  String l, x;
  bool e = false;
  uint32_t until = 0;
} card;

static bool everConnected = false;  // 收過資料以後才顯示「離線」畫面，之前是開機畫面
static bool offline = true;
static uint32_t lastRx = 0;
static uint32_t offSince = 0;
static int pressKey = 0;
static uint32_t pressUntil = 0;
static bool switchOn = false;
static String switchFrom;
static uint32_t switchUntil = 0;
static bool knobOn = false;
static uint32_t knobUntil = 0;
static bool dirty = true;
static int drawnMinute = -1;
static bool invertNow = PANEL_INVERT;

// ── 字型：efontTW 只有固定大小，22 px 用 24、18 px 用 16、32 px 用 24 放大 ──
enum Sz { S14, S14B, S16B, S24B, S32B };

static void setF(Sz z) {
  spr.setTextSize(1);
  switch (z) {
    case S14: spr.setFont(&fonts::efontTW_14); break;
    case S14B: spr.setFont(&fonts::efontTW_14_b); break;
    case S16B: spr.setFont(&fonts::efontTW_16_b); break;
    case S24B: spr.setFont(&fonts::efontTW_24_b); break;
    case S32B:
      spr.setFont(&fonts::efontTW_24_b);
      spr.setTextSize(4.0f / 3.0f);
      break;
  }
}

// ── 畫圖小工具（座標都是整個畫面的座標，這裡扣掉分段的 OY）──
static void fillR(int x, int y, int w, int h, uint32_t c, int r = 0) {
  if (y - OY >= bandH || y + h - OY <= 0) return;
  if (r) spr.fillRoundRect(x, y - OY, w, h, r, c);
  else spr.fillRect(x, y - OY, w, h, c);
}

static void outlineR(int x, int y, int w, int h, uint32_t c, int width, int r) {
  for (int k = 0; k < width; k++)
    spr.drawRoundRect(x + k, y + k - OY, w - 2 * k, h - 2 * k, r > k ? r - k : 0, c);
}

static void text(int x, int y, const String& s, Sz z, uint32_t c,
                 textdatum_t d = textdatum_t::top_left) {
  if (!s.length()) return;
  setF(z);
  spr.setTextColor(c);
  spr.setTextDatum(d);
  spr.drawString(s.c_str(), x, y - OY);
}

static uint32_t mix(uint32_t a, uint32_t b, float t) {
  uint32_t out = 0;
  for (int sh = 16; sh >= 0; sh -= 8) {
    int p = (a >> sh) & 0xFF, q = (b >> sh) & 0xFF;
    out |= (uint32_t)lroundf(p + (q - p) * t) << sh;
  }
  return out;
}

// ── UTF-8 斷行 ──
static int u8len(uint8_t c) {
  if (c < 0x80) return 1;
  if ((c >> 5) == 6) return 2;
  if ((c >> 4) == 14) return 3;
  if ((c >> 3) == 30) return 4;
  return 1;
}

static bool isBreak(const String& ch) { return ch == " " || ch == "/" || ch == "／"; }

static String rstripChars(String s, const char* const* chars, int n) {
  bool again = true;
  while (again && s.length()) {
    again = false;
    for (int i = 0; i < n; i++) {
      if (s.endsWith(chars[i])) {
        s.remove(s.length() - strlen(chars[i]));
        again = true;
      }
    }
  }
  return s;
}

static String rstripBreak(const String& s) {
  static const char* const c[] = {" ", "/", "／"};
  return rstripChars(s, c, 3);
}

// 依寬度斷行；「／」和空白當成可斷點，斷在行首時丟掉（同 display_sim.wrap）
static std::vector<String> wrap(const String& t, Sz z, int width) {
  setF(z);
  std::vector<String> lines;
  String cur;
  for (unsigned i = 0; i < t.length();) {
    int n = u8len((uint8_t)t[i]);
    String ch = t.substring(i, i + n);
    i += n;
    String cand = cur + ch;
    if (spr.textWidth(cand.c_str()) > width && cur.length()) {
      lines.push_back(rstripBreak(cur));
      cur = isBreak(ch) ? String() : ch;
    } else {
      cur = cand;
    }
  }
  if (cur.length()) lines.push_back(cur);
  std::vector<String> out;
  for (auto& l : lines)
    if (l.length()) out.push_back(l);
  return out;
}

// 沒有 rn 時從左右轉標籤推旋鈕總稱：「音量 -」「音量 +」→「音量」
static String knobName(const String& left, const String& right) {
  static const char* const c[] = {" ", "+", "-", "－", "＋"};
  String a = rstripChars(left, c, 5), b = rstripChars(right, c, 5);
  unsigned i = 0, chars = 0;
  while (i < a.length() && i < b.length()) {
    int n = u8len((uint8_t)a[i]);
    if (a.substring(i, i + n) != b.substring(i, i + n)) break;
    i += n;
    chars++;
  }
  if (chars < 2) return String();
  String p = a.substring(0, i);
  p.trim();
  return p;
}

// ── 各區塊 ──
static void drawModeBlock(uint32_t color, const String& name) {
  fillR(0, 0, 52, 56, color);
  for (int i = 0; i < 6; i++) fillR(15 + (i % 3) * 8, 10 + (i / 3) * 8, 6, 6, K_TX);
  text(26, 34, name, S14B, K_TX, textdatum_t::top_center);
}

static void drawWarning(int x, int y, int s, uint32_t color, uint32_t inner) {
  spr.fillTriangle(x + s / 2, y - OY, x + s, y + s - OY, x, y + s - OY, color);
  text(x + s / 2, y + s * 3 / 10, "!", S14B, inner, textdatum_t::top_center);
}

static void drawOffline() {
  drawModeBlock(K_OFFC, "離線");
  for (int i = 0; i < 6; i++) outlineR(XS[i % 3], YS[i / 3], TILE_W, TILE_H, K_LINE, 2, 6);
  fillR(56, 70, 260, 100, K_BG);
  // 斷線符號：一個圈加斜線
  spr.drawCircle(186, 92 - OY, 16, K_TX2);
  spr.drawCircle(186, 92 - OY, 15, K_TX2);
  spr.drawWideLine(175, 103 - OY, 197, 81 - OY, 2, K_TX2);
  text(186, 114, "電腦未連線", S24B, K_TX, textdatum_t::top_center);
  text(186, 146, "啟動 macropad 後自動恢復", S14, K_TX2, textdatum_t::top_center);
  int mins = (millis() - offSince) / 60000;
  text(26, 214, String(mins) + " 分", S14, K_TX2, textdatum_t::top_center);
}

static void drawKnob(const String& left, const String& right, const String& press,
                     const String& name) {
  bool hl = knobOn;
  if (hl) fillR(0, 62, 52, 92, K_WHITE);
  uint32_t fg = hl ? K_BLACK : K_TX;
  // 旋轉圖示：一段弧（缺口在右上）
  uint32_t ac = hl ? 0x555555 : K_TX2;
  spr.drawArc(26, 79 - OY, 9, 8, 0, 240, ac);
  spr.drawArc(26, 79 - OY, 9, 8, 320, 360, ac);
  if (name.length()) {
    auto lines = wrap(name, S24B, 48);
    for (size_t j = 0; j < lines.size() && j < 2; j++)
      text(26, 96 + j * 26, lines[j], S24B, fg, textdatum_t::top_center);
  } else {
    text(26, 98, left, S14B, fg, textdatum_t::top_center);
    text(26, 120, right, S14B, fg, textdatum_t::top_center);
  }
  fillR(14, 160, 24, 1, K_LINE);
  text(26, 170, "按下", S14, K_TX2, textdatum_t::top_center);
  auto pl = wrap(press, S14B, 40);
  for (size_t j = 0; j < pl.size() && j < 2; j++)
    text(26, 190 + j * 18, pl[j], S14B, K_TX, textdatum_t::top_center);
}

static void drawTile(int i, const String& label, uint32_t c, int src, bool err) {
  int x = XS[i % 3], y = YS[i / 3];
  if (!label.length()) {
    outlineR(x, y, TILE_W, TILE_H, K_LINE, 2, 6);
    text(x + 10, y + 8, String(i + 1), S14, K_TX2);
    return;
  }
  uint32_t bg = c, fg = K_TX, num = mix(K_TX, c, 0.3f);
  if (pressKey == i + 1) {
    bg = K_WHITE;
    fg = K_BLACK;
    num = 0x555555;
  }
  bool errSrc = err && src == i + 1;
  if (errSrc) {
    bg = K_ERRC;
    num = K_TX;
  }
  fillR(x, y, TILE_W, TILE_H, bg, 6);
  if (errSrc) drawWarning(x + 10, y + 10, 18, K_TX, bg);
  else text(x + 10, y + 8, String(i + 1), S14B, num);
  if (card.on && !err && src == i + 1 && i < 3) fillR(x, y + TILE_H - 6, TILE_W, 6, K_WHITE);

  Sz size = S24B;
  int lh = 26;
  auto lines = wrap(label, size, 64);
  if (lines.size() > 2) {
    // 字多的標籤降一級，避免蓋到左上的鍵號
    size = S16B;
    lh = 20;
    lines = wrap(label, size, 64);
    if (lines.size() > 3) lines.resize(3);
  }
  int bottom = y + TILE_H - 10;
  for (size_t j = 0; j < lines.size(); j++)
    text(x + 10, bottom - j * lh, lines[lines.size() - 1 - j], size, fg,
         textdatum_t::bottom_left);
}

static void drawCard() {
  const String& label = card.l;
  const String& result = card.x;
  int key = card.i;
  if (card.e) {
    fillR(56, 122, 260, 114, K_ERRC, 6);
    drawWarning(68, 134, 24, K_TX, K_ERRC);
    String head = (label.length() ? label : String("鍵 ") + key) + " 失敗";
    text(100, 132, head, S24B, K_TX);
    auto ls = wrap(result, S14, 236);
    for (size_t j = 0; j < ls.size() && j < 2; j++) text(68, 166 + j * 18, ls[j], S14, K_TX);
    text(68, 208, "任一鍵返回", S14, mix(K_TX, K_ERRC, 0.2f));
    return;
  }
  fillR(56, 122, 260, 114, K_CARD, 6);
  // 結果數值：放得下就 32 px，放不下降到 22 / 18 px 並斷行
  struct { Sz z; int lh; size_t n; } opts[] = {{S32B, 40, 1}, {S24B, 28, 2}, {S16B, 22, 3}};
  std::vector<String> lines;
  Sz z = S16B;
  int lh = 22;
  size_t n = 3;
  for (auto& o : opts) {
    lines = wrap(result, o.z, 236);
    z = o.z;
    lh = o.lh;
    n = o.n;
    if (lines.size() <= o.n) break;
  }
  if (lines.size() > n) lines.resize(n);
  int top = 130 + max(0, (72 - lh * (int)lines.size()) / 2);
  for (size_t j = 0; j < lines.size(); j++) text(68, top + j * lh, lines[j], z, K_CTX);
  String where = key >= 7 ? String("旋鈕") : String("鍵 ") + key;
  String foot = label.length() ? label + " · " + where : where;
  text(68, 208, foot + " · " + String(CARD_MS / 1000) + " 秒後收合", S14, K_CTX2);
}

static void drawLive() {
  uint32_t c = st.c;
  drawModeBlock(c, st.n);
  drawKnob(st.r[0], st.r[1], st.r[2], st.rn.length() ? st.rn : knobName(st.r[0], st.r[1]));

  if (switchOn) {
    fillR(56, 4, 260, 232, c, 6);
    text(186, 98, st.n, S32B, K_TX, textdatum_t::top_center);
    if (switchFrom.length())
      text(186, 146, "從 " + switchFrom + " 切換", S14, K_TX, textdatum_t::top_center);
    return;
  }
  bool err = card.on && card.e;
  int src = card.on ? card.i : 0;
  for (int i = 0; i < 6; i++) {
    if (card.on && i >= 3) continue;  // 下排被結果卡蓋住
    drawTile(i, st.k[i], c, src, err);
  }
  if (card.on) drawCard();
}

// 開機畫面：大字顯示面板驅動，方便確認燒的 env 對不對；色塊看紅藍有沒有顛倒
static void drawBoot() {
  spr.setTextSize(1);
  spr.setFont(&fonts::FreeSansBold24pt7b);
  spr.setTextColor(K_TX);
  spr.setTextDatum(textdatum_t::top_center);
  spr.drawString(PANEL_NAME, 160, 14 - OY);
  text(160, 74, "等待連線", S32B, K_TX, textdatum_t::top_center);
  static const uint32_t sw[4] = {0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF};
  static const char* const nm[4] = {"紅", "綠", "藍", "白"};
  for (int i = 0; i < 4; i++) {
    int x = 28 + i * 70;
    fillR(x, 132, 56, 40, sw[i]);
    text(x + 28, 178, nm[i], S14B, K_TX, textdatum_t::top_center);
  }
  text(160, 206,
       String("反相 ") + (invertNow ? "開" : "關") + " · 115200 · 方向 B",
       S14, K_TX2, textdatum_t::top_center);
}

static void render() {
  lcd.startWrite();
  for (OY = 0; OY < 240; OY += bandH) {
    spr.fillScreen(K_BG);
    if (!everConnected) drawBoot();
    else if (offline || !st.valid) drawOffline();
    else drawLive();
    spr.pushSprite(0, OY);
  }
  lcd.endWrite();
  OY = 0;
}

// ── 收資料 ──
static uint32_t parseColor(const char* s, uint32_t fallback) {
  if (!s || s[0] != '#' || strlen(s) != 7) return fallback;
  return strtoul(s + 1, nullptr, 16);
}

static void handle(JsonDocument& doc) {
  uint32_t now = millis();
  String t = doc["t"] | "";
  if (t == "inv") {
    invertNow = (doc["v"] | 1) != 0;
    lcd.invertDisplay(invertNow);
    dirty = true;
    return;
  }
  if (t == "rot") {
    lcd.setRotation((doc["v"] | PANEL_ROTATION) & 3);
    dirty = true;
    return;
  }
  if (t == "o") {
    everConnected = true;
    offline = true;
    offSince = now;
    card.on = false;
    dirty = true;
    return;
  }
  lastRx = now;
  bool wasOffline = offline;
  offline = false;
  everConnected = true;
  if (t == "s") {
    String oldM = st.m, oldN = st.n;
    bool hadState = st.valid;
    st.valid = true;
    st.m = doc["m"] | "";
    st.n = doc["n"] | "";
    st.rn = doc["rn"] | "";
    st.c = parseColor(doc["c"] | "", 0x44546A);
    for (int i = 0; i < 6; i++) st.k[i] = doc["k"][i] | "";
    for (int i = 0; i < 3; i++) st.r[i] = doc["r"][i] | "";
    if (hadState && oldM != st.m && !wasOffline) {
      switchOn = true;
      switchFrom = oldN;
      switchUntil = now + SWITCH_MS;
      card.on = false;
    }
  } else if (t == "p" && st.valid) {
    int i = doc["i"] | 0;
    switchOn = false;
    if (i >= 1 && i <= 6) {
      pressKey = i;
      pressUntil = now + PRESS_MS;
    } else {
      knobOn = true;
      knobUntil = now + KNOB_MS;
    }
    String x = doc["x"] | "";
    bool e = (doc["e"] | 0) != 0;
    if (e || x.length()) {
      card.on = true;
      card.i = i;
      card.l = doc["l"] | "";
      card.x = x;
      card.e = e;
      card.until = now + (e ? ERR_MS : CARD_MS);
    } else if (card.on && i >= 1 && i <= 6) {
      card.on = false;  // 任一鍵收掉結果卡
    }
  }
  dirty = true;
}

static char buf[2048];
static size_t blen = 0;
static bool overflow = false;

static void processLine() {
  buf[blen] = 0;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, buf, blen);
  if (err) {
    Serial.printf("err %s\n", err.c_str());
    return;
  }
  handle(doc);
  Serial.printf("ok %s\n", (const char*)(doc["t"] | "?"));
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

static bool expired(uint32_t until, uint32_t now) { return (int32_t)(now - until) >= 0; }

void setup() {
  Serial.setRxBufferSize(4096);
  Serial.begin(115200);
  lcd.init();
  lcd.setRotation(PANEL_ROTATION);
  lcd.setBrightness(255);
  lcd.fillScreen(K_BLACK);

  // 320×240 16 bit 要 150KB，ESP32 沒有這麼大的連續記憶體（實測失敗）；
  // 分段畫在實機上拼接會錯位，所以改用 8 bit（RGB332，約 77KB）整張 sprite
  spr.setColorDepth(8);
  bandH = 240;
  bool ok = spr.createSprite(320, 240) != nullptr;
  Serial.printf("boot %s sprite 320x240x8 %s free %u\n", PANEL_NAME, ok ? "ok" : "FAIL",
                (unsigned)ESP.getFreeHeap());
  render();
  dirty = false;
}

void loop() {
  readSerial();
  uint32_t now = millis();
  if (pressKey && expired(pressUntil, now)) { pressKey = 0; dirty = true; }
  if (card.on && expired(card.until, now)) { card.on = false; dirty = true; }
  if (switchOn && expired(switchUntil, now)) { switchOn = false; dirty = true; }
  if (knobOn && expired(knobUntil, now)) { knobOn = false; dirty = true; }
  if (everConnected && !offline && now - lastRx > OFFLINE_MS) {
    offline = true;
    offSince = lastRx;
    dirty = true;
  }
  if (everConnected && offline) {
    int m = (now - offSince) / 60000;
    if (m != drawnMinute) {
      drawnMinute = m;
      dirty = true;
    }
  }
  if (dirty) {
    dirty = false;
    render();
  }
  delay(5);
}
