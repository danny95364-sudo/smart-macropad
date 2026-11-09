"""背單字排程（段 B）：FSRS 排程、輪播池、每日上限、存檔。純邏輯，不碰序列埠。

規則來源：design/cyd-vocab-plan.md（「學習演算法 v2」「評分規則」「排程細節」）與
design/cyd-vocab-impl-plan.md 段 B。兩者衝突時以前者為準。

規格未明或衝突處的解讀（皆取保守做法）：
- 字的 id：計畫書寫「單字＋詞性」(record|n.)，但實際 tsl.csv 的 id 是小寫單字（含 résumé、café、entrée）。
  本模組把 id 當不透明字串，直接用 csv 的 id 欄，不自行組合。
- FSRS 只在「畢業（Good）」「隨出隨篩（Easy）」「到期卡作答（Good/Again）」時寫入。
  學習中按會/不會只改畢業進度（grad），不寫 FSRS（計畫書：學習中第一次 FSRS 評分用 Good）。
  Scheduler 的 learning_steps、relearning_steps 設為空：短期學習由輪播池負責，FSRS 只管天數間隔。
- 隨出隨篩（Easy）資格：卡片還沒有 FSRS 狀態、從未顯示過背面、本次也沒看背面（2026-11-09 起不限第一次曝光）。
  卡片記錄多存 "seen"（曝光次數，只當統計）與 "back"（是否看過背面；作答後一律視為看過）。
- 新字涓流（2026-11-09）：一天開始先給 NEW_START 個，之後每累積 NEW_INTERVAL_S 秒「使用中」時間
  （today.active，由控制器 add_active() 累加，暫停／鎖定不算）再放 1 個，仍受每日上限（10／積壓時 3）限制。
- 學習卡按「會」（learn_good）：從本輪剩下的順序拿掉，下一整輪也不出（_rest，只存在記憶體）；
  整個池都在休息時就照常出。按「不會」取消休息、排下一輪優先。
- 「積壓」：計畫書定義為「當天 30 張答完後仍有到期卡」。為避免一早先加滿 10 個新字才發現積壓，
  採預測式判斷：目前到期數 > 今日剩餘複習額度 就視為積壓（新字上限 10 → 3）。結果只會更少新字。
- 「到期」以日為單位：FSRS due 早於今日換日點（下一個 04:00）即算今日到期。
- 狀態列（2026-11-09）：左「已學會N」（state 為 review 的卡數，含隨出隨篩，累計）；
  右「會X 不會Y」（今日作答，today.good／today.bad）。到期卡的「複習」色塊由韌體依 kind 畫。
- 跳過（60 秒無按鍵）：不評分，今天不再出（today.skipped，存檔，換日清空）。
  60 秒由 next_card() 檢查（目前是到期卡、沒作答、距上次按鍵 ≥ 60 秒 → 自動 skip_due）；
  控制器會先翻面給答案看再呼叫 skip_due()。back_shown()（翻面）算一次按鍵，會重新計時。
- 撤銷已移除（2026-11-09）。reviews.csv 仍保留 undone 欄（一律 0），舊檔可照讀。
- reviews.csv 的 rating：easy/good/again = 有寫入 FSRS；learn_good/learn_again = 學習中按鍵、只改畢業進度。
- 「下一輪優先」與輪播順序只存在記憶體，重開程式後重新打散。
- 打散只避免同主題連續；形近字（adapt/adopt）沒有資料可判斷，未實作。
- 換日備份檔名用「舊的那一天」的 day_key（內容是那天結束時的進度）。
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import fsrs

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "vocab"

DAY_START = time(4, 0)  # 換日 04:00
POOL_TARGET = 25  # 輪播池目標張數
NEW_PER_DAY = 10  # 每日新字上限
NEW_FLOOR = 3  # 積壓時的保底新字數
NEW_START = 3  # 一天開始先放的新字數
NEW_INTERVAL_S = 20 * 60  # 之後每這麼多秒使用中時間再放 1 個新字
REVIEW_CAP = 30  # 每日複習上限
LEARN_PER_DUE = 3  # 每 3 張學習卡插 1 張到期卡
DUE_TIMEOUT = 60.0  # 到期卡無按鍵秒數 → 跳過
DEBOUNCE = 2.0  # 同卡重複按鍵忽略秒數
BACKUP_KEEP = 14  # 備份保留份數


@dataclass
class Card:
    id: str
    kind: str  # "learn" 或 "due"
    word: str
    pos: str
    zh: str
    example: str
    topic: str
    status_left: str
    status_right: str


def load_words(path):
    """讀字表：UTF-8、略過 # 開頭的行、有標頭。回傳 {id: row}（保持檔案順序）。"""
    with open(path, encoding="utf-8", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    words = {}
    for row in csv.DictReader(lines):
        wid = row["id"].strip()
        if not wid:
            continue
        row["rank"] = int(row["rank"])
        words[wid] = row
    return words


def _to_local(dt):
    """注入的時鐘可給 naive（視為本地時間）或 aware；統一轉成 naive 本地時間。"""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _to_utc(dt):
    return _to_local(dt).astimezone(timezone.utc)


def day_key_of(dt):
    local = _to_local(dt)
    return (
        (local - timedelta(hours=DAY_START.hour, minutes=DAY_START.minute))
        .date()
        .isoformat()
    )


def _atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_csv(path, header, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


class Vocab:
    def __init__(
        self,
        data_dir=DEFAULT_DATA_DIR,
        words_path=None,
        now=None,
        rng=None,
        scheduler=None,
    ):
        self.dir = Path(data_dir)
        self.words_path = Path(words_path) if words_path else self.dir / "words.csv"
        self.now = now or datetime.now
        self.rng = rng or random.Random()
        self.sched = scheduler or fsrs.Scheduler(
            desired_retention=0.9, learning_steps=(), relearning_steps=()
        )
        self.progress_path = self.dir / "progress.json"
        self.reviews_path = self.dir / "reviews.csv"
        self.flags_path = self.dir / "flags.csv"
        self.backup_dir = self.dir / "backup"

        self.words = load_words(self.words_path)
        self.warnings = []
        self.data = self._load()
        for wid in self.data["cards"]:
            if wid not in self.words:
                msg = f"progress 裡的 {wid!r} 不在字表中，進度保留但不出題"
                log.warning(msg)
                self.warnings.append(msg)

        # 記憶體狀態（不存檔）
        self._queue = []  # 本輪輪播順序
        self._prio = []  # 下一輪優先（學習中按不會、到期卡按不會）
        self._learn_since_due = 0
        self._current = None  # (id, kind)
        self._last_key = None  # 目前卡最後一次按鍵／出現時間
        self._rest = set()  # 學習中按「會」→ 下一整輪不出
        self._last_answer = {}  # id → 作答時間（防重複按）
        self._last_served = None

        self._check_day()

    # ---------- 存讀檔 ----------
    def _fresh(self):
        return {
            "version": 1,
            "day_no": 1,
            "day_key": day_key_of(self.now()),
            "today": self._zero_today(),
            "cards": {},
        }

    @staticmethod
    def _zero_today():
        return {
            "answered": 0,
            "reviews": 0,
            "new": 0,
            "good": 0,
            "bad": 0,
            "active": 0.0,
            "skipped": [],
        }

    def _load(self):
        if self.progress_path.exists():
            with open(self.progress_path, encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("today", self._zero_today())
            for k, v in self._zero_today().items():
                data["today"].setdefault(k, v)
            data.setdefault("cards", {})
            return data
        return self._fresh()

    def save(self):
        _atomic_write_json(self.progress_path, self.data)

    def _backup(self, data):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        name = "progress-" + data["day_key"].replace("-", "") + ".json"
        _atomic_write_json(self.backup_dir / name, data)
        files = sorted(self.backup_dir.glob("progress-*.json"))
        for old in files[:-BACKUP_KEEP]:
            old.unlink()

    # ---------- 換日 ----------
    def _check_day(self):
        key = day_key_of(self.now())
        if key == self.data["day_key"]:
            return
        self._backup(self.data)
        if self.data["today"]["answered"] > 0:
            self.data["day_no"] += 1
        self.data["day_key"] = key
        self.data["today"] = self._zero_today()
        self._rest.clear()
        self.save()

    def _day_end_utc(self):
        d = date.fromisoformat(self.data["day_key"]) + timedelta(days=1)
        return _to_utc(datetime.combine(d, DAY_START))

    # ---------- 查詢 ----------
    def _fsrs_card(self, rec):
        return fsrs.Card.from_dict(rec["fsrs"]) if rec.get("fsrs") else None

    def _pool(self):
        return [
            wid
            for wid, rec in self.data["cards"].items()
            if rec["state"] == "learning" and wid in self.words
        ]

    def _due_ids(self):
        end = self._day_end_utc()
        out = []
        for wid, rec in self.data["cards"].items():
            if rec["state"] != "review" or wid not in self.words:
                continue
            c = self._fsrs_card(rec)
            if c is not None and c.due < end:
                out.append(wid)
        return out

    def _review_left(self):
        return max(0, REVIEW_CAP - self.data["today"]["reviews"])

    def _new_cap(self):
        backlog = len(self._due_ids()) > self._review_left()
        cap = NEW_FLOOR if backlog else NEW_PER_DAY
        trickle = NEW_START + int(self.data["today"]["active"] // NEW_INTERVAL_S)
        return min(cap, trickle)

    def add_active(self, seconds):
        """控制器在沒暫停、沒鎖定時累加使用中秒數（新字涓流用）。每跨一分鐘存檔一次。"""
        today = self.data["today"]
        before = int(today["active"] // 60)
        today["active"] += seconds
        if int(today["active"] // 60) != before:
            self.save()

    def _next_new_id(self):
        cards = self.data["cards"]
        best = None
        for wid, row in self.words.items():
            if wid in cards:
                continue
            if best is None or row["rank"] < self.words[best]["rank"]:
                best = wid
        return best

    # ---------- 出題 ----------
    def _pick_due(self):
        if self._review_left() <= 0:
            return None
        skipped = set(self.data["today"]["skipped"])
        ids = [w for w in self._due_ids() if w not in skipped]
        if not ids:
            return None
        if len(ids) > 1 and self._last_served in ids:
            ids.remove(self._last_served)  # 剛離開的卡不要馬上又出
        now = _to_utc(self.now())

        def key(wid):
            return self.sched.get_card_retrievability(
                self._fsrs_card(self.data["cards"][wid]), now
            )

        return min(ids, key=key)

    def _build_round(self):
        pool = self._pool()
        resting = self._rest & set(pool)
        self._rest -= resting  # 休息這一輪
        pool = [w for w in pool if w not in resting] or pool
        first = [w for w in dict.fromkeys(self._prio) if w in pool]
        self._prio = []
        rest = [w for w in pool if w not in first]
        self.rng.shuffle(rest)
        order = first + self._spread(rest, first[-1] if first else self._last_served)
        if len(order) > 1 and order[0] == self._last_served:
            order.append(order.pop(0))
        self._queue = order

    def _spread(self, ids, prev):
        """貪婪排列：盡量讓相鄰兩張主題不同。"""
        out, left = [], list(ids)
        while left:
            pt = self.words[prev]["topic"] if prev in self.words else None
            idx = next(
                (i for i, w in enumerate(left) if self.words[w]["topic"] != pt), 0
            )
            prev = left.pop(idx)
            out.append(prev)
        return out

    def _pick_learn(self):
        pool = self._pool()
        if len(pool) < POOL_TARGET and self.data["today"]["new"] < self._new_cap():
            wid = self._next_new_id()
            if wid is not None:
                self.data["cards"][wid] = {
                    "state": "learning",
                    "fsrs": None,
                    "grad": 0,
                    "grad_day": "",
                    "seen": 0,
                    "back": 0,
                }
                self.data["today"]["new"] += 1
                self.save()
                return wid
        if not pool:
            return None
        poolset = set(pool)
        for _ in range(2):
            while self._queue:
                wid = self._queue.pop(0)
                if wid in poolset:
                    return wid
            self._build_round()
        return None

    def next_card(self):
        self._check_day()
        if self._current and self._current[1] == "due" and self._last_key is not None:
            if (self.now() - self._last_key).total_seconds() >= DUE_TIMEOUT:
                self.skip_due(self._current[0])
        wid = kind = None
        if self._learn_since_due >= LEARN_PER_DUE:
            wid = self._pick_due()
            kind = "due" if wid else None
        if wid is None:
            wid = self._pick_learn()
            kind = "learn" if wid else None
        if wid is None:
            wid = self._pick_due()
            kind = "due" if wid else None
        if wid is None:
            self._current = None
            return None
        if kind == "due":
            self._learn_since_due = 0
        else:
            self._learn_since_due += 1
        self._current = (wid, kind)
        self._last_key = self.now()
        self._last_served = wid
        return self._make_card(wid, kind)

    def _make_card(self, wid, kind):
        row = self.words[wid]
        left, right = self.status()
        return Card(
            id=wid,
            kind=kind,
            word=row["word"],
            pos=row["pos"],
            zh=row["zh"],
            example=row["example"],
            topic=row["topic"],
            status_left=left,
            status_right=right,
        )

    def status(self):
        learned = sum(1 for r in self.data["cards"].values() if r["state"] == "review")
        t = self.data["today"]
        return f"已學會{learned}", f"會{t['good']} 不會{t['bad']}"

    # ---------- 按鍵事件 ----------
    def exposed(self, wid, delta=1):
        """被動曝光：只記次數，不寫 FSRS。delta=-1 用來收回（旋鈕右轉跳過不算曝光）。"""
        self._check_day()
        rec = self.data["cards"].get(wid)
        if rec is None:
            return
        rec["seen"] = max(0, rec.get("seen", 0) + delta)
        self.save()

    def back_shown(self, wid):
        self._check_day()
        rec = self.data["cards"].get(wid)
        if rec is None:
            return
        if self._current and self._current[0] == wid:
            self._last_key = self.now()
        if not rec.get("back"):
            rec["back"] = 1
            self.save()

    def skip_due(self, wid):
        """到期卡跳過：不評分，今天不再出。"""
        skipped = self.data["today"]["skipped"]
        if wid not in skipped:
            skipped.append(wid)
            self.save()
        if self._current and self._current[0] == wid:
            self._current = None
            self._last_key = None

    def _kind_of(self, wid, rec):
        if self._current and self._current[0] == wid:
            return self._current[1]
        return "due" if rec["state"] == "review" else "learn"

    def answer(self, wid, good, back_seen):
        """作答。回傳寫入的評分字串；被忽略（重複按、未知 id）回傳 None。"""
        self._check_day()
        rec = self.data["cards"].get(wid)
        if rec is None or wid not in self.words:
            return None
        now = self.now()
        last = self._last_answer.get(wid)
        if last is not None and (now - last).total_seconds() < DEBOUNCE:
            return None

        kind = self._kind_of(wid, rec)
        today = self.data["today"]
        now_utc = _to_utc(now)

        if rec["state"] == "review":
            card = self._fsrs_card(rec)
            if good:
                card, _ = self.sched.review_card(card, fsrs.Rating.Good, now_utc)
                rating = "good"
            else:
                card, _ = self.sched.review_card(card, fsrs.Rating.Again, now_utc)
                rating = "again"
                rec["state"] = "learning"
                rec["grad"], rec["grad_day"] = 0, ""
                self._prio.append(wid)
            rec["fsrs"] = card.to_dict()
            if kind == "due":
                today["reviews"] += 1
            if wid in today["skipped"]:
                today["skipped"].remove(wid)
        else:
            screen = (
                good
                and not back_seen
                and not rec.get("back")
                and rec.get("fsrs") is None
            )
            if screen:
                card, _ = self.sched.review_card(
                    fsrs.Card(card_id=0), fsrs.Rating.Easy, now_utc
                )
                rec["fsrs"] = card.to_dict()
                rec["state"] = "review"
                if today["new"] > 0:
                    today["new"] -= 1  # 不佔新字名額
                rating = "easy"
            elif good:
                if (
                    rec.get("grad", 0) == 1
                    and rec.get("grad_day") != self.data["day_key"]
                ):
                    base = self._fsrs_card(rec) or fsrs.Card(card_id=0)
                    card, _ = self.sched.review_card(base, fsrs.Rating.Good, now_utc)
                    rec["fsrs"] = card.to_dict()
                    rec["state"] = "review"
                    rec["grad"], rec["grad_day"] = 0, ""
                    rating = "good"
                else:
                    if rec.get("grad", 0) == 0:
                        rec["grad"], rec["grad_day"] = 1, self.data["day_key"]
                    rating = "learn_good"
                    self._rest.add(wid)  # 少出：本輪剩下的和下一整輪都不出
                    self._queue = [w for w in self._queue if w != wid]
            else:
                rec["grad"], rec["grad_day"] = 0, ""
                self._rest.discard(wid)
                self._prio.append(wid)
                rating = "learn_again"
        rec["back"] = 1
        today["answered"] += 1
        today["good" if good else "bad"] += 1
        ts = _to_local(now).isoformat(timespec="seconds")
        self._last_answer[wid] = now
        if self._current and self._current[0] == wid:
            self._current = None
            self._last_key = None
        self.save()
        _append_csv(
            self.reviews_path,
            ["ts", "id", "rating", "kind", "undone"],
            [ts, wid, rating, kind, 0],
        )
        return rating

    def flag(self, wid, note=""):
        """標錯字卡（電腦端使用）。"""
        ts = _to_local(self.now()).isoformat(timespec="seconds")
        _append_csv(self.flags_path, ["ts", "id", "note"], [ts, wid, note])


def main(argv=None):
    """電腦端小指令：uv run python -m macropad.vocab flag <id> <note>"""
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m macropad.vocab")
    ap.add_argument("--dir", default=str(DEFAULT_DATA_DIR), help="進度資料夾")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("flag", help="標錯字卡（記到 flags.csv）")
    p.add_argument("id")
    p.add_argument("note", nargs="*")
    a = ap.parse_args(argv)
    if sys.stdout is not None:
        sys.stdout.reconfigure(errors="replace")
    data_dir = Path(a.dir)
    # 不建 Vocab：常駐程式可能正開著，這裡只附加 flags.csv，不碰 progress.json
    words = load_words(data_dir / "words.csv") if (data_dir / "words.csv").exists() else {}
    if words and a.id not in words:
        print(f"警告：{a.id!r} 不在字表中（仍然記下）")
    note = " ".join(a.note)
    ts = _to_local(datetime.now()).isoformat(timespec="seconds")
    _append_csv(data_dir / "flags.csv", ["ts", "id", "note"], [ts, a.id, note])
    print(f"已記錄：{a.id} {note}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
