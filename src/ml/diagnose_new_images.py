"""Run the REAL upload pipeline on a list of images and print, per image: every MegaDetector box, the probe's raw top-3
for the best box, the final label, whether an alert would be raised, and the reason when it is not.
   python diagnose_new_images.py --probe species_probe.pkl --device cpu [--truth truth.json] img1.jpg img2.jpg ..."""
import argparse, json, os, sys, time
sys.path.insert(0, os.environ.get("WW_BACKEND", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")))
import numpy as np
from PIL import Image
from pipeline import RealPipeline

ap = argparse.ArgumentParser(); ap.add_argument("--probe", required=True); ap.add_argument("--device", default="cpu")
ap.add_argument("--truth"); ap.add_argument("--json"); ap.add_argument("images", nargs="+"); a = ap.parse_args()
truth = json.load(open(a.truth)) if a.truth else {}
cfg = {"pipeline": {"detector": "MDV5A", "detect_conf": 0.30, "empty_below": 0.05, "bioclip": "hf-hub:imageomics/bioclip",
                    "species_probe": a.probe, "device": a.device}}
p = RealPipeline(cfg); p.load()
out = []
for path in a.images:
    name = os.path.basename(path)
    res = p.run(path)
    raw = p.detector.generate_detections_one_image(Image.open(path).convert("RGB"), path, detection_threshold=0.05)["detections"]
    top3 = None
    best = max((d for d in raw if d["category"] == "1"), key=lambda d: d["conf"], default=None)
    if best:
        pr = p.classify_box(Image.open(path).convert("RGB"), best["bbox"]) if hasattr(p, "classify_box") else None
        top3 = [(str(p.probe.classes_[i]), round(float(pr[i]), 2)) for i in np.argsort(-pr)[:3]]
    alert = res["outcome"] != "empty" and bool(res.get("species"))
    if res["outcome"] == "empty":
        reason = "MegaDetector found no box >= 0.05 (frame discarded as empty)" if not raw else "only non-animal/low boxes"
    elif res["outcome"] == "review":
        reason = "weak box (0.05-0.30): kept for review, LOW alert"
    else:
        reason = ""
    out.append({"image": name, "truth": truth.get(name), "boxes": [(d["category"], round(d["conf"], 2)) for d in raw],
                "top3": top3, "label": res.get("species"), "outcome": res["outcome"], "count": res["count"], "alert": alert, "reason": reason})
    o = out[-1]
    print(f"{name[:34]:34s} truth={str(o['truth']):9s} boxes={o['boxes']}\n    top3={o['top3']} -> label={o['label']} outcome={o['outcome']} alert={'YES' if alert else 'NO'} {o['reason']}", flush=True)
if a.json: json.dump(out, open(a.json, "w"), indent=1)
