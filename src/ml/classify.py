import json, sys, os, glob
import torch, open_clip
from PIL import Image

DET_JSON = sys.argv[1]          # e.g. output.json or cct_output.json
OUT_JSON = sys.argv[2]          # e.g. species_output.json
CONF_MIN = 0.3                  # ignore weak detections

# --- 1. Species names from CCT metadata ---
names = None
for f in glob.glob("cct_meta/**/*.json", recursive=True):
    data = json.load(open(f))
    if "categories" in data:
        names = [c["name"] for c in data["categories"] if c["name"] != "empty"]
        break
print("Species:", names)

# --- 2. Load BioCLIP on GPU ---
device = "cuda"
model, _, preprocess = open_clip.create_model_and_transforms("hf-hub:imageomics/bioclip")
tokenizer = open_clip.get_tokenizer("hf-hub:imageomics/bioclip")
model = model.to(device).eval()

with torch.no_grad():
    text = tokenizer([f"a camera trap photo of a {n}." for n in names]).to(device)
    text_feat = model.encode_text(text)
    text_feat /= text_feat.norm(dim=-1, keepdim=True)

# --- 3. Crop each animal box and classify ---
results = []
for img in json.load(open(DET_JSON))["images"]:
    path = img["file"]
    for det in img.get("detections") or []:
        if det["category"] != "1" or det["conf"] < CONF_MIN:
            continue
        im = Image.open(path).convert("RGB")
        W, H = im.size
        x, y, w, h = det["bbox"]
        crop = im.crop((int(x*W), int(y*H), int((x+w)*W), int((y+h)*H)))

        with torch.no_grad():
            f = model.encode_image(preprocess(crop).unsqueeze(0).to(device))
            f /= f.norm(dim=-1, keepdim=True)
            probs = (100 * f @ text_feat.T).softmax(dim=-1)[0]
        best = probs.argmax().item()

        results.append({"file": path, "species": names[best],
                        "species_conf": round(probs[best].item(), 3),
                        "det_conf": det["conf"], "bbox": det["bbox"]})
        print(path, "->", names[best], round(probs[best].item(), 2))

json.dump(results, open(OUT_JSON, "w"), indent=1)
print("Saved", len(results), "animals to", OUT_JSON)
