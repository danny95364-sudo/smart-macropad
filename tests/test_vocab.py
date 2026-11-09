import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import fsrs
import pytest

from macropad import vocab as V

ROOT = Path(__file__).resolve().parent.parent
TSL = ROOT / "vocab" / "tsl.csv"
TOPICS = ["辦公", "財務", "人事", "旅行", "採購", "製造", "行銷", "日常"]


class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, seconds=10):
        self.t += timedelta(seconds=seconds)


def write_words(path, n=60, skip=()):
    lines = ["# test list", "id,word,pos,zh,example,topic,rank"]
    for i in range(n):
        if i in skip:
            continue
        lines.append(f"w{i:03d},word{i},名,字{i},Example {i}.,{TOPICS[i % 8]},{i + 1}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path):
    clock = Clock(datetime(2026, 11, 9, 9, 0))
    words = write_words(tmp_path / "words.csv")

    def make(**kw):
        return V.Vocab(
            data_dir=tmp_path / "data",
            words_path=words,
            now=clock,
            rng=random.Random(1),
            scheduler=fsrs.Scheduler(
                learning_steps=(), relearning_steps=(), enable_fuzzing=False
            ),
            **kw,
        )

    return clock, make, tmp_path


def make_review(v, wid, due_days_ago=1, stability=5.0):
    """直接把一張卡設成到期的複習卡。"""
    now = V._to_utc(v.now())
    card = fsrs.Card(
        card_id=0,
        state=fsrs.State.Review,
        stability=stability,
        difficulty=5.0,
        due=now - timedelta(days=due_days_ago),
        last_review=now - timedelta(days=due_days_ago + 5),
    )
    v.data["cards"][wid] = {
        "state": "review",
        "fsrs": card.to_dict(),
        "grad": 0,
        "grad_day": "",
        "seen": 3,
        "back": 1,
    }


def test_rule1_first_seen_easy_screens_and_refills(env):
    clock, make, _ = env
    v = make()
    c = v.next_card()
    assert c.kind == "learn" and v.data["today"]["new"] == 1
    v.exposed(c.id)
    assert v.answer(c.id, True, back_seen=False) == "easy"
    rec = v.data["cards"][c.id]
    assert rec["state"] == "review" and rec["fsrs"]["state"] == fsrs.State.Review.value
    assert v.data["today"]["new"] == 0  # 不佔名額
    clock.tick()
    c2 = v.next_card()  # 立即補位
    assert c2.id != c.id and v.data["today"]["new"] == 1
    # 看過背面就不算隨出隨篩
    v.back_shown(c2.id)
    clock.tick()
    assert v.answer(c2.id, True, back_seen=True) == "learn_good"
    assert v.data["cards"][c2.id]["state"] == "learning"


def test_rule2_learning_needs_two_different_days(env):
    clock, make, _ = env
    v = make()
    ids = [v.next_card().id for _ in range(10)]
    a, b = ids[0], ids[1]
    assert v.answer(a, True, back_seen=True) == "learn_good"
    clock.tick(60)
    assert v.answer(a, True, back_seen=True) == "learn_good"  # 同日第二次不算
    rec = v.data["cards"][a]
    assert rec["state"] == "learning" and rec["grad"] == 1 and rec["fsrs"] is None
    clock.t += timedelta(days=1)
    assert v.answer(a, True, back_seen=True) == "good"
    rec = v.data["cards"][a]
    assert rec["state"] == "review" and rec["fsrs"]["last_review"] is not None
    # 不會 → grad 歸零、下一輪優先
    v.answer(b, True, back_seen=True)
    clock.tick(5)
    assert v.answer(b, False, back_seen=True) == "learn_again"
    assert v.data["cards"][b]["grad"] == 0 and v.data["cards"][b]["grad_day"] == ""
    v.data["today"]["new"] = 10  # 名額用完，不再加新字
    v._queue = []
    clock.tick(5)
    nxt = v.next_card()
    assert nxt.id == b


def test_rule3_due_card_good_and_again(env):
    clock, make, _ = env
    v = make()
    make_review(v, "w050")
    make_review(v, "w051")
    before = dict(v.data["cards"]["w050"]["fsrs"])
    assert v.answer("w050", True, back_seen=True) == "good"
    after = v.data["cards"]["w050"]
    assert after["state"] == "review" and after["fsrs"]["due"] > before["due"]
    clock.tick()
    assert v.answer("w051", False, back_seen=False) == "again"
    rec = v.data["cards"]["w051"]
    assert rec["state"] == "learning" and rec["grad"] == 0
    assert v.data["today"]["reviews"] == 2


def test_rule4_cap_order_and_interleave(env):
    clock, make, _ = env
    v = make()
    for i in range(20, 60):  # 40 張到期，穩定度不同 → 記得機率不同
        make_review(v, f"w{i:03d}", due_days_ago=3, stability=1.0 + i)
    now = V._to_utc(v.now())
    r = {
        w: v.sched.get_card_retrievability(
            fsrs.Card.from_dict(v.data["cards"][w]["fsrs"]), now
        )
        for w in v._due_ids()
    }
    seq = []
    for _ in range(200):
        c = v.next_card()
        if c is None:
            break
        seq.append(c)
        v.answer(c.id, True, back_seen=True)
        clock.tick(3)
    kinds = "".join("D" if c.kind == "due" else "L" for c in seq)
    assert kinds.startswith("LLLDLLLD")  # 每 3 張學習卡插 1 張到期卡
    due = [c.id for c in seq if c.kind == "due"]
    assert len(due) == 30 and v.data["today"]["reviews"] == 30  # 每日上限
    rs = [r[w] for w in due]
    assert rs == sorted(rs)  # 記得機率低 → 高
    assert kinds[-40:] == "L" * 40  # 上限到了只剩學習卡


def test_rule5_new_cap_and_backlog_floor(env):
    clock, make, tmp = env
    v = make()
    for _ in range(20):
        v.next_card()
        clock.tick()
    assert v.data["today"]["new"] == V.NEW_START  # 一開始只有 3 個
    v.add_active(V.NEW_INTERVAL_S * 20)
    for _ in range(20):
        v.next_card()
        clock.tick()
    assert v.data["today"]["new"] == 10 and len(v._pool()) == 10
    # 積壓：40 張到期（> 30）→ 新字只有 3
    clock2 = Clock(datetime(2026, 11, 9, 9, 0))
    v2 = V.Vocab(
        data_dir=tmp / "data2",
        words_path=tmp / "words.csv",
        now=clock2,
        rng=random.Random(2),
    )
    v2.add_active(V.NEW_INTERVAL_S * 20)
    for i in range(20, 60):
        make_review(v2, f"w{i:03d}")
    for _ in range(200):
        c = v2.next_card()
        if c is None:
            break
        v2.answer(c.id, True, back_seen=True)
        clock2.tick(3)
    assert v2.data["today"]["reviews"] == 30
    assert v2.data["today"]["new"] == 3
    assert len(v2._due_ids()) > 0  # 答完 30 張仍有到期


def test_rule6_due_timeout_skip(env):
    clock, make, _ = env
    v = make()
    make_review(v, "w050", stability=2.0)
    make_review(v, "w051", stability=50.0)
    v.data["today"]["new"] = 10  # 不加新字，池為空 → 只出到期卡
    a = v.next_card()
    assert a.kind == "due" and a.id == "w050"
    fs = dict(v.data["cards"]["w050"]["fsrs"])
    clock.tick(61)
    b = v.next_card()
    assert b.id == "w051" and v.data["today"]["skipped"] == ["w050"]
    assert v.data["cards"]["w050"]["fsrs"] == fs and v.data["today"]["reviews"] == 0
    # 翻面算按鍵，重新計時
    clock.tick(50)
    v.back_shown("w051")
    clock.tick(20)
    c = v.next_card()
    assert c.id == "w051"  # 被跳過的 w050 今天不再出
    clock.tick(61)
    assert v.next_card() is None  # 兩張都跳過了
    # 跳過存檔，重開程式今天仍不出；換日清空
    v2 = make()
    assert v2.data["today"]["skipped"] == ["w050", "w051"]
    clock.t += timedelta(days=1)
    assert v2.next_card().kind == "learn" and v2.data["today"]["skipped"] == []


def test_rule7_debounce(env):
    clock, make, tmp = env
    v = make()
    make_review(v, "w050")
    assert v.answer("w050", False, back_seen=True) == "again"
    clock.tick(1)
    assert v.answer("w050", True, back_seen=True) is None  # 2 秒內重複
    assert v.data["today"]["answered"] == 1
    assert not hasattr(v, "undo")  # 撤銷已移除
    with open(tmp / "data" / "reviews.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["undone"] for r in rows] == ["0"]  # 欄位保留，一律 0


def test_rule8_passive_exposure_no_fsrs(env):
    clock, make, _ = env
    v = make()
    make_review(v, "w050")
    fs = dict(v.data["cards"]["w050"]["fsrs"])
    c = v.next_card()
    for _ in range(5):
        v.exposed("w050")
        v.exposed(c.id)
        clock.tick(14)
    assert v.data["cards"]["w050"]["fsrs"] == fs
    assert v.data["cards"][c.id]["fsrs"] is None
    assert v.data["today"]["answered"] == 0


def test_rule9_day_rollover(env):
    clock, make, tmp = env
    v = make()
    c = v.next_card()
    v.answer(c.id, True, back_seen=True)
    assert v.data["day_no"] == 1
    clock.t = datetime(2026, 11, 10, 3, 59)
    v.status()
    v.next_card()
    assert v.data["day_no"] == 1 and v.data["today"]["answered"] == 1
    clock.t = datetime(2026, 11, 10, 4, 0)
    v.next_card()
    assert v.data["day_no"] == 2 and v.data["day_key"] == "2026-11-10"
    assert v.data["today"]["answered"] == 0 and v.data["today"]["new"] <= 1
    assert (tmp / "data" / "backup" / "progress-20261109.json").exists()
    # 沒作答的日子不算一天
    clock.t = datetime(2026, 11, 11, 9, 0)
    v.next_card()
    clock.t = datetime(2026, 11, 12, 9, 0)
    v.next_card()
    assert v.data["day_no"] == 2
    # 備份保留 14 份
    for d in range(20):
        clock.t = datetime(2026, 11, 13, 9, 0) + timedelta(days=d)
        v.next_card()
    assert len(list((tmp / "data" / "backup").glob("progress-*.json"))) == 14


def test_rule10_unknown_ids_kept_with_warning(env):
    clock, make, tmp = env
    v = make()
    make_review(v, "w005")
    v.save()
    write_words(tmp / "words.csv", skip={5})
    v2 = make()
    assert any("w005" in w for w in v2.warnings)
    assert "w005" in v2.data["cards"]
    for _ in range(30):
        c = v2.next_card()
        assert c is None or c.id != "w005"
        clock.tick()
    v2.save()
    saved = json.loads((tmp / "data" / "progress.json").read_text(encoding="utf-8"))
    assert "w005" in saved["cards"]


def test_status_strings(env):
    clock, make, _ = env
    v = make()
    make_review(v, "w050")
    c = v.next_card()
    assert c.kind == "learn"
    assert (c.status_left, c.status_right) == ("已學會1", "會0 不會0")
    v.answer(c.id, False, back_seen=True)
    clock.tick(3)
    v.answer("w050", True, back_seen=True)
    assert v.status() == ("已學會1", "會1 不會1")
    clock.tick(3)
    c2 = v.next_card()
    v.answer(c2.id, True, back_seen=False)  # 隨出隨篩也算已學會
    assert v.status() == ("已學會2", "會2 不會1")


def test_new_words_trickle_by_active_time(env):
    clock, make, _ = env
    v = make()
    for _ in range(10):
        v.next_card()
        clock.tick()
    assert v.data["today"]["new"] == 3
    v.add_active(V.NEW_INTERVAL_S - 1)
    v.next_card()
    assert v.data["today"]["new"] == 3
    v.add_active(1)
    v.next_card()
    assert v.data["today"]["new"] == 4
    v.add_active(V.NEW_INTERVAL_S * 100)
    for _ in range(30):
        v.next_card()
        clock.tick()
    assert v.data["today"]["new"] == V.NEW_PER_DAY  # 每日上限不變
    v2 = make()  # 使用中時間有存檔
    assert v2.data["today"]["active"] >= V.NEW_INTERVAL_S * 100


def test_learn_good_rests_one_round(env):
    clock, make, _ = env
    v = make()
    v.add_active(V.NEW_INTERVAL_S * 3)  # 6 個新字
    ids = [v.next_card().id for _ in range(6)]
    for w in ids:
        v.back_shown(w)
    v._build_round()  # 新的一輪，6 張都在
    a = v._queue[2]
    left_this_round = len(v._queue) - 1
    assert v.answer(a, True, back_seen=True) == "learn_good"
    assert a not in v._queue  # 本輪剩下的不出
    served = [
        v.next_card().id for _ in range(left_this_round + 5)
    ]  # 本輪剩下＋下一整輪
    assert a not in served and len(set(served[-5:])) == 5
    assert a in [v.next_card().id for _ in range(6)]  # 再下一輪回來
    # 整池都在休息時照常出
    v2 = make()
    v2._rest = set(v2._pool())
    assert v2.next_card().id in ids


def test_easy_whenever_back_never_shown(env):
    clock, make, _ = env
    v = make()
    c = v.next_card()
    for _ in range(3):
        v.exposed(c.id)  # 曝光好幾次，沒看過背面
    assert v.answer(c.id, True, back_seen=False) == "easy"
    clock.tick(3)
    c2 = v.next_card()
    v.back_shown(c2.id)
    assert v.answer(c2.id, True, back_seen=False) == "learn_good"  # 看過背面就不算


def test_real_wordlist_loads():
    words = V.load_words(TSL)
    assert len(words) == 1250
    assert {"résumé", "café", "entrée"} <= set(words)


def test_simulation_30_days(tmp_path):
    rnd = random.Random(42)
    clock = Clock(datetime(2026, 11, 9, 9, 0))  # 星期一

    def make():
        return V.Vocab(
            data_dir=tmp_path, words_path=TSL, now=clock, rng=random.Random(7)
        )

    v = make()
    new_per_day = []
    for day in range(30):
        clock.t = datetime(2026, 11, 9, 9, 0) + timedelta(days=day)
        if clock.t.weekday() >= 5:  # 週末不開
            continue
        for _ in range(150):
            c = v.next_card()
            if c is None:
                break
            v.exposed(c.id)
            v.add_active(15)
            clock.tick(5)
            if rnd.random() < 0.8:
                v.back_shown(c.id)
            clock.tick(5)
            v.answer(c.id, rnd.random() < 0.7, back_seen=True)
            clock.tick(5)
        assert v.data["today"]["reviews"] <= V.REVIEW_CAP
        new_per_day.append(v.data["today"]["new"])
        snapshot = json.loads(json.dumps(v.data))
        v = make()  # 存檔可重載
        assert v.data == snapshot
    assert len(new_per_day) == 22
    for i in range(len(new_per_day) - 2):
        assert sum(new_per_day[i : i + 3]) > 0, new_per_day
    assert v.data["day_no"] == 22
    assert sum(1 for r in v.data["cards"].values() if r["state"] == "review") > 0
