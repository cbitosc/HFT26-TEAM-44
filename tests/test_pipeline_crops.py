"""Guards the train/serve skew found on 31 Oct: the probe was trained on 10%-padded crops averaged with their mirror,
while uploads fed it tight single crops (41.3% vs 60.9% on 92 new-camera images). Uploads must crop exactly like training."""
import re
from pathlib import Path

import numpy as np
from PIL import Image

from conftest import ROOT
from pipeline import CROP_PAD, RealPipeline, crop_views


def test_upload_crop_is_padded_like_training():
    img = Image.new("RGB", (1000, 500))
    views = crop_views(img, [0.2, 0.2, 0.4, 0.4])
    assert len(views) == 2
    assert views[0].size == (480, 240)                     # 400x200 box + 10% each side
    assert np.array_equal(np.asarray(views[1]), np.asarray(views[0].transpose(Image.FLIP_LEFT_RIGHT)))


def test_padding_never_leaves_the_image():
    img = Image.new("RGB", (640, 480))
    for bbox in ([0, 0, 1, 1], [0.95, 0.95, 0.05, 0.05], [0.999, 0.5, 0.0001, 0.0001]):
        v = crop_views(img, bbox)[0]
        assert 1 <= v.size[0] <= 640 and 1 <= v.size[1] <= 480


def test_pad_matches_the_training_script():
    src = (ROOT / "ml" / "tune_probe.py").read_text(encoding="utf-8")
    m = re.search(r"pad\s*=\s*([0-9.]+)", src)
    assert m and float(m.group(1)) == CROP_PAD
    assert "FLIP_LEFT_RIGHT" in src and "f.mean(0)" in src


def test_classify_box_feeds_both_views_to_the_model():
    import torch

    class Clip:
        seen = None
        def encode_image(self, batch):
            Clip.seen = batch.shape
            return torch.ones(batch.shape[0], 4)

    class Probe:
        classes_ = np.array(["a", "b"])
        def predict_proba(self, x):
            assert x.shape == (1, 4) and abs(np.linalg.norm(x) - 1) < 1e-5
            return np.array([[0.3, 0.7]])

    p = RealPipeline({"pipeline": {}})
    p.torch, p.device, p.clip, p.probe = torch, "cpu", Clip(), Probe()
    p.preprocess = lambda im: torch.zeros(3, 8, 8)
    prob = p.classify_box(Image.new("RGB", (100, 100)), [0.1, 0.1, 0.5, 0.5])
    assert Clip.seen[0] == 2 and list(prob) == [0.3, 0.7]
