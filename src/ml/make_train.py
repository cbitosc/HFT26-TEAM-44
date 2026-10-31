import json, glob, os, shutil
files = set()
for f in glob.glob("cct_meta/**/*train*.json", recursive=True):
    files |= {os.path.basename(im["file_name"]) for im in json.load(open(f))["images"]}
os.makedirs("cct_train", exist_ok=True)
n = 0
for p in glob.glob("cct_images/**/*.jpg", recursive=True):
    if os.path.basename(p) in files:
        shutil.copy(p, "cct_train"); n += 1
print("Copied", n)
