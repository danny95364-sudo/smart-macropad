import csv
import random
from datetime import datetime, timedelta

import fsrs
import pytest

from macropad import vocab as V
from macropad import vocab_app as A

TOPICS = ["辦公", "財務", "人事", "旅行", "採購", "製造", "行銷", "日常"]


class Clock:
    """同一個假時鐘同時給 Vocab（datetime）和控制器（秒數）。"""

    def __init__(self):
        self.t = 0.0
        self.base = datetime(2026, 11, 9, 9, 0)

    def mono(self):
        return self.t

    def now(self):
        return self.base + timedelta(seconds=self.t)

    def tick(self, s):
        self.t += s


class FakeSer:
    def __init__(self):
        self.out = []
        self.inbox = b""
        self.broken = False
        self.closed = False

    def write(self, data):
        if self.broken:
            raise OSError("unplugged")
        self.out.append(data.decode("utf-8").rstrip("\n"))

    def read(self, n):
        if self.broken:
            raise OSError("unplugged")
        d, self.inbox = self.inbox, b""
        return d

    def close(self):
        self.closed = True

    def feed(self, *lines):
        self.inbox += "".join(ln + "\n" for ln in lines).encode("utf-8")


class Opener:
    def __init__(self):
        self.fail = False
        self.opened = []

    def __call__(self, port):
        if self.fail:
            raise OSError("busy")
        s = FakeSer()
        self.opened.append(s)
        return s

    @property
    def ser(self):
        return self.opened[-1]


def write_words(path, n=40):
    lines = ["id,word,pos,zh,example,topic,rank"]
    for i in range(n):
        lines.append(f"w{i:03d},word{i},名,字{i},Example {i}.,{TOPICS[i % 8]},{i + 1}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    words = write_words(tmp_path / "words.csv")
    v = V.Vocab(
        data_dir=tmp_path / "data",
        words_path=words,
        now=clock.now,
        rng=random.Random(1),
        scheduler=fsrs.Scheduler(
            learning_steps=(), relearning_steps=(), enable_fuzzing=False
        ),
    )
    op = Opener()
    link = A.SerialLink("COM9", opener=op, clock=clock.mono, log=lambda *_: None)
    ctl = A.VocabController(v, link, clock=clock.mono, log=lambda *_: None)
    return clock, v, op, ctl, tmp_path


def cards(ser):
    return [ln.split("\t") for ln in ser.out if ln.startswith("card\t")]


def toasts(ser):
    return [ln.split("\t")[1:] for ln in ser.out if ln.startswith("toast\t")]


def reviews(tmp_path):
    p = tmp_path / "data" / "reviews.csv"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make_due(v, wid):
    now = V._to_utc(v.now())
    card = fsrs.Card(
        card_id=0,
        state=fsrs.State.Review,
        stability=5.0,
        difficulty=5.0,
        due=now - timedelta(days=1),
        last_review=now - timedelta(days=6),
    )
    v.data["cards"][wid] = {
        "state": "review",
        "fsrs": card.to_dict(),
        "grad": 0,
        "grad_day": "",
        "seen": 3,
        "back": 1,
    }


# ---------- 鍵位 ----------


def test_key_roles_physical_and_flip_keys_ignored():
    assert A.key_role("1") == "bad" and A.key_role("2") == "good"
    assert A.key_role("7") == "prev" and A.key_role("8") == "next"
    assert A.key_role("9") == "flip"
    for k in ("3", "4", "5", "6"):
        assert A.key_role(k) is None
    # display_flip_keys 只影響看板；熱鍵編號本來就是實體鍵號，不做 7−k
    assert A.physical_key("2") == 2 and A.physical_key("5") == 5
    assert A.key_role("8", knob_reverse=True) == "prev"


def test_keys_3_to_6_not_handled(env):
    _, _, op, ctl, _ = env
    ctl.start()
    for k in "3456":
        assert ctl.on_key(k) is False
    for k in "12789":
        assert ctl.on_key(k) is True


# ---------- 出卡、作答、toast ----------


def test_start_sends_card_and_answer_toasts_and_review_rows(env):
    clock, v, op, ctl, tmp = env
    ctl.start()
    ser = op.ser
    first = cards(ser)
    assert len(first) == 1 and first[0][2] == "learn"
    cid = first[0][1]
    assert v.data["cards"][cid]["seen"] == 1  # 曝光一次
    ctl.on_key("2")
    assert toasts(ser)[-1] == ["會", "ok"]
    assert "flip" not in ser.out  # 作答不翻面，直接換下一張
    assert reviews(tmp)[-1]["id"] == cid and reviews(tmp)[-1]["rating"] == "easy"
    clock.tick(0.1)
    ctl.tick()
    assert len(cards(ser)) == 2 and cards(ser)[-1][1] != cid
    # 不會
    clock.tick(1)
    ser.feed(f"back\t{cards(ser)[-1][1]}")
    ctl.tick()
    ctl.on_key("1")
    assert toasts(ser)[-1] == ["不會", "bad"]
    assert reviews(tmp)[-1]["rating"] == "learn_again"


def test_learn_done_goes_to_next_card(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    ser.feed("done\tsomething-else")
    ctl.tick()
    assert len(cards(ser)) == 1  # id 不符不理
    ser.feed(f"back\t{cid}", f"done\t{cid}")
    ctl.tick()
    assert len(cards(ser)) == 2
    assert v.data["cards"][cid]["back"] == 1  # back 有轉給 Vocab


def test_back_seen_blocks_easy(env):
    clock, v, op, ctl, tmp = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    ser.feed(f"back\t{cid}")
    ctl.tick()
    ctl.on_key("2")
    assert reviews(tmp)[-1]["rating"] == "learn_good"
    assert "flip" not in ser.out  # 已在背面，不必再翻


# ---------- 上一張（撤銷已移除）、跳過 ----------


def test_knob_left_right_after_answer_is_previous_card(env):
    clock, v, op, ctl, tmp = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    ctl.on_key("2")
    clock.tick(0.1)
    ctl.tick()
    second = cards(ser)[-1][1]
    clock.tick(1)  # 作答後 1 秒：以前是撤銷，現在只是上一張
    ctl.on_key("7")
    assert cards(ser)[-1][1] == cid
    assert len(reviews(tmp)) == 1 and reviews(tmp)[-1]["undone"] == "0"
    seen = v.data["cards"][cid]["seen"]
    # 重看已作答的卡：按會不評分
    clock.tick(3)
    ctl.on_key("2")
    assert len(reviews(tmp)) == 1 and toasts(ser)[-1] == ["已作答", "info"]
    assert v.data["cards"][cid]["seen"] == seen  # 重看不算曝光
    # 右轉回到原本那張，再右轉才是新卡
    ctl.on_key("8")
    assert cards(ser)[-1][1] == second
    ctl.on_key("8")
    assert cards(ser)[-1][1] not in (cid, second)


def test_knob_right_skip_is_not_exposure(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    assert v.data["cards"][cid]["seen"] == 1
    ctl.on_key("8")
    assert v.data["cards"][cid]["seen"] == 0  # 跳過收回曝光
    nxt = cards(ser)[-1][1]
    ser.feed(f"back\t{nxt}", f"done\t{nxt}")  # 自動輪播看完的照算
    ctl.tick()
    assert v.data["cards"][nxt]["seen"] == 1


def test_knob_left_without_history(env):
    _, _, op, ctl, _ = env
    ctl.start()
    ctl.on_key("7")
    assert toasts(op.ser)[-1] == ["沒有上一張", "info"]


# ---------- 翻面、觸控 ----------


def test_bad_flips_to_answer_then_next_after_3s(env):
    clock, v, op, ctl, tmp = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    ctl.on_key("1")
    assert toasts(ser)[-1] == ["不會", "bad"] and ser.out[-1] == "flip"
    ser.feed(f"back\t{cid}", f"done\t{cid}")  # 板子翻面；作答後的 done 不理
    clock.tick(2.9)
    ctl.tick()
    assert len(cards(ser)) == 1
    clock.tick(0.2)
    ctl.tick()
    assert len(cards(ser)) == 2


def test_status_line_text(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    ser = op.ser
    assert cards(ser)[0][7:] == ["已學會0", "會0 不會0"]
    ctl.on_key("2")  # 隨出隨篩
    assert ser.out[-1] == "status\t已學會1\t會1 不會0"


def test_active_time_only_when_not_paused(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    for _ in range(10):
        clock.tick(0.1)
        ctl.tick()
    assert abs(v.data["today"]["active"] - 0.9) < 1e-6  # 第一次 tick 只記時間
    ctl.toggle_pause()
    clock.tick(0.1)
    ctl.tick()
    clock.tick(30)  # 睡眠後一次跳很多也只算 ACTIVE_STEP_MAX
    ctl.tick()
    assert abs(v.data["today"]["active"] - 0.9) < 1e-6
    ctl.toggle_pause()
    clock.tick(30)
    ctl.tick()
    assert abs(v.data["today"]["active"] - 0.9 - A.ACTIVE_STEP_MAX) < 1e-6


def test_tap_flips_and_hold_toggles_pause(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    ser = op.ser
    ser.feed("tap")
    ctl.tick()
    assert ser.out[-1] == "flip"
    ctl.on_key("9")
    assert ser.out[-1] == "flip"
    ser.feed("hold")
    ctl.tick()
    assert ser.out[-1] == "pause" and ctl.user_paused
    ser.feed("hold")
    ctl.tick()
    assert ser.out[-1] == "resume" and not ctl.user_paused


# ---------- 到期卡 60 秒 ----------


def test_due_card_ignored_60s_shows_answer_then_skips(env):
    clock, v, op, ctl, tmp = env
    make_due(v, "w030")
    v._learn_since_due = V.LEARN_PER_DUE  # 下一張就出到期卡
    ctl.start()
    ser = op.ser
    c = cards(ser)[0]
    assert c[1] == "w030" and c[2] == "due"
    clock.tick(50)
    ctl.on_key("9")  # 翻面算按鍵，重新計時
    ctl.on_key("9")  # 翻回正面
    clock.tick(50)
    ctl.tick()
    assert len(cards(ser)) == 1
    n = ser.out.count("flip")
    clock.tick(10.5)
    ctl.tick()  # 60 秒到：PC 翻面給答案看
    assert ser.out.count("flip") == n + 1 and len(cards(ser)) == 1
    ser.feed("back\tw030")  # 板子回 back 不重新計時
    clock.tick(7.9)
    ctl.tick()
    assert len(cards(ser)) == 1
    clock.tick(0.2)
    ctl.tick()
    assert len(cards(ser)) == 2 and cards(ser)[-1][1] != "w030"
    assert reviews(tmp) == []  # 跳過不評分
    assert v.data["today"]["skipped"] == ["w030"]  # 今天不再出


def test_due_timer_stops_while_paused(env):
    clock, v, op, ctl, _ = env
    make_due(v, "w030")
    v._learn_since_due = V.LEARN_PER_DUE
    ctl.start()
    ser = op.ser
    ctl.toggle_pause()
    clock.tick(120)
    ctl.tick()
    assert len(cards(ser)) == 1
    ctl.toggle_pause()
    clock.tick(59)
    ctl.tick()
    assert "flip" not in ser.out
    clock.tick(2)
    ctl.tick()
    assert ser.out[-1] == "flip" and len(cards(ser)) == 1
    ctl.toggle_pause()  # 翻面後等換卡時暫停也停住
    clock.tick(30)
    ctl.tick()
    assert len(cards(ser)) == 1
    ctl.toggle_pause()
    clock.tick(8.1)
    ctl.tick()
    assert len(cards(ser)) == 2


def test_due_answer_before_flip_accepted(env):
    clock, v, op, ctl, tmp = env
    make_due(v, "w030")
    v._learn_since_due = V.LEARN_PER_DUE
    ctl.start()
    ctl.on_key("2")
    assert reviews(tmp)[-1]["rating"] == "good" and reviews(tmp)[-1]["kind"] == "due"


# ---------- 鎖定 ----------


def test_lock_pauses_and_stops_cards_unlock_reconnects(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    ctl.lock()
    assert ser.out[-1] == "pause"
    n = len(ser.out)
    ser.feed(f"done\t{cid}", "tap")
    ctl.tick()
    assert ctl.on_key("8") is True  # 按鍵吞掉
    clock.tick(300)
    ctl.tick()
    assert len(ser.out) == n  # 鎖定中什麼都不送
    ctl.unlock()
    assert ser.closed and len(op.opened) == 2  # 重連序列埠
    new = op.ser
    assert "resume" in new.out
    assert cards(new)[-1][1] == cid  # 續播同一張


def test_session_event_mapping_and_drain():
    import queue

    assert A.session_event(A.WM_WTSSESSION_CHANGE, A.WTS_SESSION_LOCK) == "lock"
    assert A.session_event(A.WM_WTSSESSION_CHANGE, A.WTS_SESSION_UNLOCK) == "unlock"
    assert A.session_event(A.WM_WTSSESSION_CHANGE, 0x1) is None
    assert A.session_event(0x0010, A.WTS_SESSION_LOCK) is None

    class Ctl:
        def __init__(self):
            self.calls = []

        def lock(self):
            self.calls.append("lock")

        def unlock(self):
            self.calls.append("unlock")

    q = queue.Queue()
    q.put("lock")
    q.put("unlock")
    c = Ctl()
    A.drain_session_events(q, c)
    assert c.calls == ["lock", "unlock"]


# ---------- 斷線重連 ----------


def test_unplug_retries_every_5s_and_resyncs(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    ser.broken = True
    op.fail = True
    ctl.tick()  # 讀取失敗 → 丟掉連線
    assert not ctl.link.connected
    clock.tick(4.9)
    ctl.tick()
    assert len(op.opened) == 1
    clock.tick(0.2)
    ctl.tick()  # 5 秒到，重試但還是失敗
    assert not ctl.link.connected
    op.fail = False
    clock.tick(3)
    ctl.tick()
    assert not ctl.link.connected  # 距上次重試未滿 5 秒
    clock.tick(2.1)
    ctl.tick()
    assert ctl.link.connected and len(op.opened) == 2
    assert cards(op.ser)[-1][1] == cid  # 重送目前這張


def test_board_reboot_hello_resends_card_and_pause(env):
    clock, v, op, ctl, _ = env
    ctl.start()
    ser = op.ser
    cid = cards(ser)[0][1]
    ctl.toggle_pause()
    ser.out.clear()
    ser.feed("hello\tcyd_vocab\t2")
    ctl.tick()
    assert "pause" in ser.out and cards(ser)[-1][1] == cid


def test_no_port_found_then_found(tmp_path):
    clock = Clock()
    op = Opener()
    ports = {"p": None}
    link = A.SerialLink(
        "", opener=op, finder=lambda: ports["p"], clock=clock.mono, log=lambda *_: None
    )
    assert link.ensure() is False
    ports["p"] = "COM4"
    assert link.ensure() is False  # 還沒到 5 秒
    clock.tick(5)
    assert link.ensure() is True


# ---------- 雜項 ----------


def test_card_line_fields_and_length():
    c = V.Card(
        "a\tb", "learn", "w", "名", "中", "x" * 900, "辦公", "已學會0", "會0 不會0"
    )
    line = A.card_line(c)
    f = line.split("\t")
    assert len(f) == 9 and f[1] == "a b"
    assert len(line.encode("utf-8")) <= A.LINE_MAX


def test_empty_day_toast_and_retry(env):
    clock, v, op, ctl, _ = env
    v.next_card = lambda: None
    ctl.start()
    assert toasts(op.ser)[-1][1] == "info"
    assert cards(op.ser) == []


def test_flag_cli(tmp_path, capsys):
    write_words(tmp_path / "tsl.csv")
    assert V.main(["--dir", str(tmp_path), "flag", "w001", "中文", "錯"]) == 0
    rows = list(csv.DictReader(open(tmp_path / "flags.csv", encoding="utf-8")))
    assert rows[0]["id"] == "w001" and rows[0]["note"] == "中文 錯"
