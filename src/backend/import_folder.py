"""Copy photos into data/incoming/ so the running server's folder watcher processes them with the real pipeline.
   python import_folder.py D:\\hackathon\\cct_subset 46 --limit 200
The capture time is the file's modified time (CCT file names carry no time), so analytics ranges follow the copy time."""
import argparse
import shutil
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("camera", nargs="?", default="46")
ap.add_argument("--limit", type=int, default=100)
a = ap.parse_args()
dest = Path(__file__).resolve().parent.parent / "data" / "incoming"
dest.mkdir(parents=True, exist_ok=True)
n = 0
for p in sorted(Path(a.folder).rglob("*")):
    if p.suffix.lower() in {".jpg", ".jpeg", ".png"} and n < a.limit:
        shutil.copy2(p, dest / f"CAM{a.camera}_{p.name}")
        n += 1
print(f"Copied {n} photos into {dest}. Watch the Upload page queue.")
