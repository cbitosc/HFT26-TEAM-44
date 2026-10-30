"""The REAL pipeline: MegaDetector -> crop each box -> BioCLIP features -> trained linear probe.

run(path, on_stage) returns a plain dict:
  outcome   animal | person | review | empty
  detections [{category, conf, bbox, species, species_conf}]   (bbox normalised x, y, w, h)
  species, count, person, detector_ms, classifier_ms, device

Frames are discarded as 'empty' ONLY when the best box is below empty_below. Anything between
empty_below and detect_conf is a 'review' frame: it still gets a species vote and a count of at least 1.
"""
import os
import threading
import time

from PIL import Image


class PipelineUnavailable(RuntimeError):
    pass


class RealPipeline:
    def __init__(self, cfg):
        self.cfg = cfg["pipeline"]
        self._lock = threading.Lock()
        self._ready = False
        self.device = None

    # ---- loading -------------------------------------------------------------------------------
    def load(self):
        with self._lock:
            if self._ready:
                return
            try:
                import joblib
                import open_clip
                import torch
                from megadetector.detection.run_detector import load_detector
            except Exception as e:  # missing package
                raise PipelineUnavailable(f"Pipeline packages are not installed in this Python: {e}")
            probe = self.cfg["species_probe"]
            if not os.path.exists(probe):
                raise PipelineUnavailable(f"Species probe not found at {probe}. Run train_probe.py first or fix config.yaml.")
            dev = self.cfg.get("device", "auto")
            self.device = ("cuda" if torch.cuda.is_available() else "cpu") if dev == "auto" else dev
            self.torch = torch
            self.detector = load_detector(self.cfg["detector"])
            self.clip, _, self.preprocess = open_clip.create_model_and_transforms(self.cfg["bioclip"])
            self.clip = self.clip.to(self.device).eval()
            self.probe = joblib.load(probe)
            self._ready = True

    # ---- running -------------------------------------------------------------------------------
    def run(self, path, on_stage=lambda s: None):
        self.load()
        c = self.cfg
        img = Image.open(path).convert("RGB")
        W, H = img.size

        on_stage("detect")
        t0 = time.time()
        res = self.detector.generate_detections_one_image(img, str(path), detection_threshold=c["empty_below"])
        if res.get("failure"):
            raise RuntimeError(f"MegaDetector could not process this image: {res['failure']}")
        dets = [d for d in res.get("detections", []) if d["conf"] >= c["empty_below"]]
        detector_ms = (time.time() - t0) * 1000

        animals = [d for d in dets if d["category"] == "1"]
        persons = [d for d in dets if d["category"] == "2" and d["conf"] >= c["detect_conf"]]
        confident = [d for d in animals if d["conf"] >= c["detect_conf"]]
        out = {"detections": [], "species": None, "count": 0, "person": bool(persons), "device": self.device,
               "detector_ms": round(detector_ms, 1), "classifier_ms": 0.0}

        if not animals and not persons:
            out["outcome"] = "empty"
            return out

        on_stage("species")
        t1 = time.time()
        to_classify = confident or sorted(animals, key=lambda d: -d["conf"])[:1]
        votes = {}
        for d in to_classify:
            x, y, w, h = d["bbox"]
            crop = img.crop((int(x * W), int(y * H), max(int(x * W) + 1, int((x + w) * W)), max(int(y * H) + 1, int((y + h) * H))))
            with self.torch.no_grad():
                f = self.clip.encode_image(self.preprocess(crop).unsqueeze(0).to(self.device))
                f = f / f.norm(dim=-1, keepdim=True)
            prob = self.probe.predict_proba(f[0].cpu().numpy().reshape(1, -1))[0]
            k = int(prob.argmax())
            d["species"], d["species_conf"] = str(self.probe.classes_[k]), float(prob[k])
            for cls, p in zip(self.probe.classes_, prob):          # confidence-weighted vote across boxes
                votes[str(cls)] = votes.get(str(cls), 0.0) + d["conf"] * float(p)
        out["classifier_ms"] = round((time.time() - t1) * 1000, 1)

        for d in dets:
            out["detections"].append({"category": d["category"], "conf": round(d["conf"], 4), "bbox": d["bbox"],
                                      "species": d.get("species"), "species_conf": d.get("species_conf")})
        on_stage("count")
        if persons:
            out.update(outcome="person", species="person", count=len(persons))
        else:
            species = max(votes, key=votes.get) if votes else None
            out.update(outcome="animal" if confident else "review", species=species,
                       count=max(1, len(confident)) if species else 0)
        return out
