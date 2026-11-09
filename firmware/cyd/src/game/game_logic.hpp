// 六鎖保險箱：純遊戲邏輯（不碰畫面、序列埠），電腦上也能用 g++ / clang 編譯測試。
// 規則見 design/cyd-game-plan.md。
#pragma once
#include <stdint.h>
#include <stdlib.h>

namespace safe {

enum Phase : uint8_t { TITLE, PLAY, WIN, LOSE };
enum Mark : uint8_t { NONE = 0, GREEN, YELLOW, RED };

static const int LOCKS = 6;
static const int MAX_HIST = 8;  // 一關最多 8 次機會，全部記得下

struct Attempt {
  uint8_t digit[LOCKS];
  uint8_t mark[LOCKS];
};

// 第 1、2 關 8 次；第 3 關起每關少 1，最少 5 次
inline int triesFor(int level) {
  if (level <= 2) return 8;
  int t = 8 - (level - 2);
  return t < 5 ? 5 : t;
}
// 黃色範圍：第 1、2 關差 1～2，第 3 關起差 1～3
inline int yellowRange(int level) { return level >= 3 ? 3 : 2; }
// 只有第 1 關有往大／往小箭頭
inline bool hasArrows(int level) { return level == 1; }

inline uint8_t judge(int guess, int code, int level) {
  int d = abs(guess - code);
  if (d == 0) return GREEN;
  if (d <= yellowRange(level)) return YELLOW;
  return RED;
}

struct Game {
  Phase phase = TITLE;
  int level = 1;
  int score = 0;  // 各關剩餘次數加總
  uint8_t code[LOCKS] = {};
  uint8_t cur[LOCKS] = {};
  bool locked[LOCKS] = {};
  int8_t hint[LOCKS] = {};  // 上次試開的方向：+1 要更大、-1 要更小、0 無
  uint8_t lastTry[LOCKS] = {};
  uint8_t lastMark[LOCKS] = {};
  int cursor = 0;
  int triesMax = 8;
  int triesLeft = 8;
  int used = 0;
  Attempt hist[MAX_HIST];
  int histN = 0;
  uint32_t (*rng)() = nullptr;

  void startLevel() {
    phase = PLAY;
    triesMax = triesLeft = triesFor(level);
    used = 0;
    histN = 0;
    cursor = 0;
    for (int i = 0; i < LOCKS; i++) {
      code[i] = rng ? rng() % 10 : 0;
      cur[i] = 0;
      locked[i] = false;
      hint[i] = 0;
      lastTry[i] = 0;
      lastMark[i] = NONE;
    }
  }

  void newGame() {
    level = 1;
    score = 0;
    startLevel();
  }

  int nextUnlocked(int from) const {
    for (int s = 0; s < LOCKS; s++) {
      int i = (from + s) % LOCKS;
      if (!locked[i]) return i;
    }
    return from;
  }

  bool allLocked() const {
    for (int i = 0; i < LOCKS; i++)
      if (!locked[i]) return false;
    return true;
  }

  void tryOpen() {
    Attempt& a = hist[histN < MAX_HIST ? histN : MAX_HIST - 1];
    if (histN >= MAX_HIST) {  // 理論上不會發生（最多 8 次），保險起見往前捲
      for (int h = 1; h < MAX_HIST; h++) hist[h - 1] = hist[h];
    } else {
      histN++;
    }
    for (int i = 0; i < LOCKS; i++) {
      uint8_t m = locked[i] ? GREEN : judge(cur[i], code[i], level);
      a.digit[i] = cur[i];
      a.mark[i] = m;
      lastTry[i] = cur[i];
      lastMark[i] = m;
      hint[i] = cur[i] < code[i] ? 1 : (cur[i] > code[i] ? -1 : 0);
      if (m == GREEN) locked[i] = true;
    }
    used++;
    triesLeft--;
    if (allLocked()) {
      score += triesLeft;
      phase = WIN;
    } else if (triesLeft <= 0) {
      phase = LOSE;
    } else if (locked[cursor]) {
      cursor = nextUnlocked(cursor + 1);
    }
  }

  // key：1～6 選鎖芯、7 數字 -1、8 數字 +1、9 試開／確認。回傳畫面要不要重畫。
  bool press(int key) {
    if (key < 1 || key > 9) return false;
    switch (phase) {
      case TITLE:
        newGame();
        return true;
      case WIN:
        if (key != 9) return false;
        level++;
        startLevel();
        return true;
      case LOSE:
        if (key != 9) return false;
        newGame();
        return true;
      case PLAY:
        break;
    }
    if (key <= 6) {
      int i = key - 1;
      if (locked[i] || i == cursor) return false;
      cursor = i;
      return true;
    }
    if (key == 7 || key == 8) {
      if (locked[cursor]) return false;
      cur[cursor] = (cur[cursor] + (key == 8 ? 1 : 9)) % 10;
      return true;
    }
    tryOpen();
    return true;
  }
};

}  // namespace safe
