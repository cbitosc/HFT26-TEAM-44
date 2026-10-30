import json, glob, os
from collections import Counter

# Ground truth: filename -> true species, from all CCT20 metadata files
truth = {}
for f in glob.glob("cct_meta/**/*.json", recursive=True):
    d = json.load(open(f))
    if "annotations" not in d: continue
    cats = {c["id"]: c["name"] for c in d["categories"]}
    id2file = {im["id"]: os.path.basename(im["file_name"]) for im in d["images"]}
    for a in d["annotations"]:
        truth[id2file[a["image_id"]]] = cats[a["category_id"]]

# Predictions: keep the most confident detection per image
best = {}
for p in json.load(open("cct_species.json")):
    name = os.path.basename(p["file"])
    if name not in best or p["det_conf"] > best[name]["det_conf"]:
        best[name] = p

correct, total, wrong = 0, 0, Counter()
for name, p in best.items():
    if name not in truth or truth[name] == "empty": continue
    total += 1
    if p["species"] == truth[name]:
        correct += 1
    else:
        wrong[(truth[name], p["species"])] += 1

print(f"Accuracy: {correct}/{total} = {correct/total:.1%}")
print("Top confusions (true -> predicted):")
for (t, pr), n in wrong.most_common(8):
    print(f"  {t} -> {pr}: {n}")
