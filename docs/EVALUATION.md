# Evaluation (measured 30-31 Oct 2026)

Dataset: Caltech Camera Traps, CCT20 benchmark subset. Detector: MegaDetector v5a. Crops: boxes with confidence >= 0.3.

| Model | Accuracy | Images | Notes |
|---|---|---|---|
| BioCLIP zero-shot (class names only) | 26.8% | 2,318 | Night infrared frames fool it; most opossums were called raccoon |
| Linear probe, C=10, **new cameras** (trans_test) | **62.8%** | 928 | The realistic number. 63.1% with voting across a burst |
| Linear probe, C=10, known cameras (cis_test) | 78.9% | 615 | 80.0% with burst voting |
| Probe on greyscale crops (rejected) | 59.4% | 928 | Worse than colour on new cameras (78.9% -> 76.7% on cis) |

Largest zero-shot confusions: opossum -> raccoon (270), opossum -> bobcat (98), opossum -> skunk (87).

## How the numbers were produced
`src/ml/tune_probe.py` trains only on crops from the CCT20 train split (11,518), chooses the regularisation `C` (10) on the
validation splits (174 images), and scores the cis and trans test splits separately, so the probe never saw a test image.
Crops are padded 10% and averaged with their mirror image. The earlier single score, 62.9% on all 1,717 non-train images mixed together,
came from `train_probe.py`; it is kept for history but the split numbers above replace it.

Weak spots on new cameras: raccoon (56%) is confused with opossum, skunk and rodent; bobcat 53%, coyote 50%, cat 42%.
Bird and rodent have 3 and 1 test images on trans_test, too few to judge.

## Measured 31 Oct 2026 (shown on the Model accuracy page)
| Metric | All | Day | Night |
|---|---|---|---|
| Empty filter F1 (MegaDetector, discard below 0.05; 1,941 test images) | 0.932 (P 0.972, R 0.896) | 0.918 | 0.942 |
| Animals wrongly discarded as empty | 10.4% | | |
| Species macro-F1, test splits | | 0.688 | 0.520 |
| Count error (MAE, boxes >= 0.30 vs labelled animals) | | 0.15 | 0.12 |

Macro-F1: 0.732 known cameras, 0.503 new cameras. Speeds (median of 20 real uploads, RTX 4050): detection 1,352 ms, classification 25 ms.
Leak check: 0 files, 0 sequences and 0 locations shared between train and test.
Caveat: MegaDetector v5 was trained on data including Caltech Camera Traps, so the detector numbers are likely optimistic.
Spot checks (random new-camera photos, seed 2026): live upload pipeline 56/100 correct (8 with no animal found); on a 10-photo draw the probe and greyscale probe scored 3/10 and zero-shot 1/10.
Still not measured: SpeciesNet.

## Upload-path check on new images (31 Oct 2026)
Real MegaDetector + BioCLIP + probe, run through `src/backend/pipeline.py` on 100 unseen trans_test images (`src/ml/diagnose_new_images.py`).
* Bug found and fixed: uploads used tight single crops while the probe was trained on 10%-padded crops averaged with their mirror.
  On the 92 images with an animal box the upload path scored **41.3%**; with training-style crops (now the upload path) **60.9%**.
  `tests/test_pipeline_crops.py` keeps the two in sync.
* 8 of 100 images had no animal box at all (5 raccoon, 2 opossum, 1 squirrel).
* The probe only knows 14 CCT species: anything else (e.g. cheetah) is forced into the nearest CCT label.

## Known limits
* A motion-blurred person can be missed entirely by MegaDetector (no person box even at threshold 0.001 on a real CCT frame).
* A detected person is labelled **PERSON – verify**, never "poacher": the model cannot tell a ranger from an intruder.
