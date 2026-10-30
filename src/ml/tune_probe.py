"""Tune and honestly measure the species probe.   Run from D:\\hackathon:   python tune_probe.py
Step 1  extract BioCLIP features once and cache them (features.npz) - later runs take seconds
Step 2  pick C on the VALIDATION splits only (never on test)
Step 3  report on cis_test and trans_test SEPARATELY, per class, with and without sequence voting
Writes species_probe_tuned.pkl; your current species_probe.pkl is left untouched."""
import glob, json, os, sys
from collections import Counter, defaultdict

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression

GRAY = "gray" in sys.argv[1:]            # python tune_probe.py gray  -> same test with greyscale crops
CACHE = "features_gray.npz" if GRAY else "features.npz"
OUT = "species_probe_gray.pkl" if GRAY else "species_probe_tuned.pkl"


def load_meta():
    truth, split, seq = {}, {}, {}
    for f in glob.glob("cct_meta/**/*.json", recursive=True):
        d = json.load(open(f))
        if "annotations" not in d:
            continue
        name = os.path.basename(f).lower().replace("_annotations", "").replace(".json", "")
        cats = {c["id"]: c["name"] for c in d["categories"]}
        imgs = {im["id"]: im for im in d["images"]}
        for a in d["annotations"]:
            im = imgs[a["image_id"]]
            fn = os.path.basename(im["file_name"])
            truth[fn], split[fn], seq[fn] = cats[a["category_id"]], name, im.get("seq_id") or fn
    return truth, split, seq


def extract(truth, split, seq):
    import torch, open_clip
    from PIL import Image
    model, _, pre = open_clip.create_model_and_transforms("hf-hub:imageomics/bioclip")
    model = model.to("cuda").eval()
    dets = []
    for dj in ["cct_output.json", "train_output.json"]:
        for img in json.load(open(dj))["images"]:
            for d in img.get("detections") or []:
                if d["category"] == "1" and d["conf"] >= 0.3:
                    dets.append({"file": img["file"], "bbox": d["bbox"], "conf": d["conf"]})
    X, y, S, Q, seen = [], [], [], [], set()
    for p in sorted(dets, key=lambda p: -p["conf"]):
        fn = os.path.basename(p["file"])
        if fn in seen or fn not in truth or truth[fn] == "empty":
            continue
        seen.add(fn)
        im = Image.open(p["file"]).convert("RGB"); W, H = im.size
        x, yy, w, h = p["bbox"]
        pad = 0.10                                           # 10% padding so the animal is not cut off
        box = (max(0, int((x - pad * w) * W)), max(0, int((yy - pad * h) * H)),
               min(W, int((x + w * (1 + pad)) * W)), min(H, int((yy + h * (1 + pad)) * H)))
        crop = im.crop(box)
        if GRAY:
            crop = crop.convert("L").convert("RGB")             # drop colour: day colour and night infrared look alike
        with torch.no_grad():
            b = torch.stack([pre(crop), pre(crop.transpose(Image.FLIP_LEFT_RIGHT))]).to("cuda")
            f = model.encode_image(b)
            f /= f.norm(dim=-1, keepdim=True)
            f = f.mean(0)                                    # crop + mirrored crop averaged
            f /= f.norm()
        X.append(f.cpu().numpy()); y.append(truth[fn]); S.append(split[fn]); Q.append(str(seq[fn]))
        if len(X) % 1000 == 0:
            print("  features:", len(X), flush=True)
    np.savez(CACHE, X=np.array(X), y=np.array(y), S=np.array(S), Q=np.array(Q))


def vote(proba, classes, seqs):
    out = np.empty(len(seqs), dtype=object)
    for q in set(seqs):
        idx = [i for i, s in enumerate(seqs) if s == q]
        out[idx] = classes[proba[idx].mean(0).argmax()]
    return out


def report(name, clf, X, y, Q):
    if len(y) == 0:
        print(f"\n{name}: no images"); return
    pr = clf.predict_proba(X); pred = clf.classes_[pr.argmax(1)]
    pv = vote(pr, clf.classes_, list(Q))
    print(f"\n=== {name}: {len(y)} images ===")
    print(f"  accuracy per frame        : {(pred == y).mean():.1%}")
    print(f"  accuracy with burst voting: {(pv == y).mean():.1%}")
    print("  per class (frames):  correct/total")
    for c in sorted(set(y), key=lambda c: -(y == c).sum()):
        m = y == c
        print(f"    {c:12s} {(pred[m] == c).sum():4d}/{m.sum():<4d} {(pred[m] == c).mean():6.1%}")
    wrong = Counter((t, p) for t, p in zip(y, pred) if t != p)
    print("  top confusions (true -> predicted):")
    for (t, p), n in wrong.most_common(6):
        print(f"    {t} -> {p}: {n}")


def main():
    truth, split, seq = load_meta()
    if not os.path.exists(CACHE):
        print("extracting features once (several minutes on the GPU)...")
        extract(truth, split, seq)
    d = np.load(CACHE, allow_pickle=True)
    X, y, S, Q = d["X"], d["y"], d["S"], d["Q"]
    print("images per split:", dict(Counter(S)))
    tr = S == "train"
    val = np.isin(S, ["cis_val", "trans_val"])
    if val.sum() == 0:
        sys.exit("No cis_val/trans_val split found - check the file names in cct_meta (see the splits printed above).")
    best_c, best_acc = None, -1
    print("\ntuning C on validation only:")
    for C in [0.3, 1, 3, 10, 30, 100, 300]:
        clf = LogisticRegression(C=C, max_iter=3000, class_weight="balanced").fit(X[tr], y[tr])
        acc = clf.score(X[val], y[val])
        print(f"  C={C:<5} validation accuracy {acc:.1%}")
        if acc > best_acc:
            best_c, best_acc = C, acc
    print(f"-> best C = {best_c}")
    clf = LogisticRegression(C=best_c, max_iter=3000, class_weight="balanced").fit(X[tr], y[tr])
    for name in ("cis_test", "trans_test"):
        m = S == name
        report(name, clf, X[m], y[m], Q[m])
    joblib.dump(clf, OUT)
    print(f"\nsaved {OUT}  (only switch to it if it beats the old one on trans_test)")


if __name__ == "__main__":
    main()
