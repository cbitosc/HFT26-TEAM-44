# Evaluation (measured 30 Oct 2026)

Dataset: Caltech Camera Traps, CCT20 benchmark subset. Detector: MegaDetector v5a. Crops: boxes with confidence >= 0.3.

| Model | Accuracy | Images | Notes |
|---|---|---|---|
| BioCLIP zero-shot (class names only) | 26.8% | 2,318 | Night infrared frames fool it; most opossums were called raccoon |
| Linear probe on BioCLIP features | **62.9%** | 1,717 | Trained on 11,518 CCT20 train crops, `class_weight="balanced"` |

Largest zero-shot confusions: opossum -> raccoon (270), opossum -> bobcat (98), opossum -> skunk (87).

## How the numbers were produced
`src/ml/train_probe.py` trains only on crops from the CCT20 train split and scores on the other CCT20 splits
(cis and trans, validation and test together), so the probe never saw a test image.

## Not measured yet (the dashboard says so)
* Cis (known cameras) vs trans (new cameras) accuracy separately
* Day vs night accuracy
* Empty-frame filter precision / recall, and how many real animals it discards
* Count error
* SpeciesNet comparison

These fields are `null` in `src/reports/metrics.json`. Fill them in there once measured and the Model accuracy page updates.
