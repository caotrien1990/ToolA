# Deterministic train/heldout split for the rebuilt realuse_items_chat.pt (104 items after the
# game_test_v1 round, 2026-09-29). Rules, all asserted:
#  1. EVERY text that was in the OLD heldout (realuse_items_heldout.pt, 11 items) stays heldout --
#     those rows were already reported as UNSEEN gate rows; moving them to train launders the gate.
#  2. The rest is split by a SEEDED hash of the text: sha256(text) % 100 < HELD_PCT -> heldout.
#     Hash-of-text is order-independent, so re-running after MORE feedback arrives never moves an
#     existing item across the split (a random.shuffle split would).
#  3. Backs up the old train/heldout .pt as .pre_v14 before overwriting (recoverable).
import importlib
import hashlib
import io
import os
import shutil
import sys

torch = importlib.import_module("torch")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)
ROOT = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\49. CUA"
CHAT = os.path.join(ROOT, "realuse_items_chat.pt")
HELD_OLD = os.path.join(ROOT, "realuse_items_heldout.pt")
TRAIN = os.path.join(ROOT, "realuse_items_train.pt")
HELD = os.path.join(ROOT, "realuse_items_heldout.pt")
HELD_PCT = 20
SALT = "laya_v14"


def tt(it):
    o = it.get("orig") or {}
    st = o.get("state")
    if isinstance(st, dict):
        return str(st.get("task") or st.get("ctx") or "")
    return str(st)


items = torch.load(CHAT, weights_only=False)
old_held = torch.load(HELD_OLD, weights_only=False)
old_held_texts = {tt(it) for it in old_held}
print("chat items: %d | old heldout texts: %d" % (len(items), len(old_held_texts)))

train, held = [], []
n_forced = 0
for it in items:
    t = tt(it)
    if t in old_held_texts:
        held.append(it)
        n_forced += 1
        continue
    h = int(hashlib.sha256((SALT + "|" + t).encode("utf-8")).hexdigest(), 16) % 100
    (held if h < HELD_PCT else train).append(it)

print("forced-held (old heldout): %d | hash-held: %d | train: %d" % (n_forced, len(held) - n_forced, len(train)))
assert n_forced == len(old_held_texts & {tt(it) for it in items}), "some old heldout text vanished from chat.pt"
assert len(train) + len(held) == len(items)
tt_train = {tt(it) for it in train}
tt_held = {tt(it) for it in held}
assert not (tt_train & tt_held), "text appears on BOTH sides"
assert old_held_texts <= tt_held, "an old heldout text landed in train"
qt = {"train": {}, "held": {}}
QNAME = {0: "choice", 1: "score", 2: "noul"}
for name, rows in (("train", train), ("held", held)):
    for it in rows:
        k = QNAME.get(it.get("qtype"), str(it.get("qtype")))
        qt[name][k] = qt[name].get(k, 0) + 1
print("qtype train %s | held %s" % (qt["train"], qt["held"]))
for name, rows in (("train", train), ("held", held)):
    assert len(rows) > 0, "%s side is empty" % name

for p in (TRAIN, HELD):
    if os.path.exists(p):
        shutil.copy2(p, p + ".pre_v14")
torch.save(train, TRAIN)
torch.save(held, HELD)
print("-> %s (%d) | -> %s (%d) | backups .pre_v14" % (TRAIN, len(train), HELD, len(held)))
