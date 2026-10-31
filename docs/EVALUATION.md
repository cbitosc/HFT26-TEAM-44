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

## Not measured yet (the dashboard says so)
* Macro-F1 (only accuracy was measured), so the cis/trans macro-F1 tiles stay empty
* Day vs night accuracy
* Empty-frame filter precision / recall, and how many real animals it discards
* Count error
* SpeciesNet comparison

These fields are `null` in `src/reports/metrics.json`. Fill them in there once measured and the Model accuracy page updates.

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
