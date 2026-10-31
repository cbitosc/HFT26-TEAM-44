import json, glob, os
import torch, open_clip, joblib
import numpy as np
from PIL import Image
from sklearn.linear_model import LogisticRegression

# Ground truth + which split each image belongs to
truth, split = {}, {}
for f in glob.glob("cct_meta/**/*.json", recursive=True):
    d = json.load(open(f))
    if "annotations" not in d: continue
    cats = {c["id"]: c["name"] for c in d["categories"]}
    id2file = {im["id"]: os.path.basename(im["file_name"]) for im in d["images"]}
    s = "train" if "train" in os.path.basename(f) else "test"
    for a in d["annotations"]:
        fn = id2file[a["image_id"]]
        truth[fn], split[fn] = cats[a["category_id"]], s

# BioCLIP as feature extractor
model, _, preprocess = open_clip.create_model_and_transforms("hf-hub:imageomics/bioclip")
model = model.to("cuda").eval()

X, y, S = [], [], []
seen = set()
dets = []
for dj in ["cct_output.json", "train_output.json"]:
    for img in json.load(open(dj))["images"]:
        for d in img.get("detections") or []:
            if d["category"] == "1" and d["conf"] >= 0.3:
                dets.append({"file": img["file"], "bbox": d["bbox"], "det_conf": d["conf"]})
for p in sorted(dets, key=lambda p: -p["det_conf"]):
    fn = os.path.basename(p["file"])
    if fn in seen or fn not in truth or truth[fn] == "empty": continue
    seen.add(fn)
    im = Image.open(p["file"]).convert("RGB"); W, H = im.size
    x, yy, w, h = p["bbox"]
    crop = im.crop((int(x*W), int(yy*H), int((x+w)*W), int((yy+h)*H)))
    with torch.no_grad():
        f = model.encode_image(preprocess(crop).unsqueeze(0).to("cuda"))
        f /= f.norm(dim=-1, keepdim=True)
    X.append(f[0].cpu().numpy()); y.append(truth[fn]); S.append(split[fn])

X, y, S = np.array(X), np.array(y), np.array(S)
tr, te = S == "train", S == "test"
print("Train:", tr.sum(), " Test:", te.sum())

clf = LogisticRegression(max_iter=3000, class_weight="balanced").fit(X[tr], y[tr])
print(f"Linear probe accuracy: {clf.score(X[te], y[te]):.1%}")
joblib.dump(clf, "species_probe.pkl")
