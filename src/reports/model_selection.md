# Model selection (measured, 30 Oct 2026)

| Model | Accuracy | Images | Notes |
|---|---|---|---|
| BioCLIP zero-shot | 26.8% | 2,318 | Mostly predicts raccoon on night infrared frames |
| Linear probe on BioCLIP | 62.9% | 1,717 | Trained on 11,518 CCT20 train crops, class_weight=balanced |
| SpeciesNet | not run | - | |

Winner for now: linear probe on BioCLIP. Not yet measured: cis vs trans, day vs night, empty-frame filter, count error.
