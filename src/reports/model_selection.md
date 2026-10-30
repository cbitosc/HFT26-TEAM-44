# Model selection (measured, 30-31 Oct 2026)

| Model | Accuracy | Images | Notes |
|---|---|---|---|
| BioCLIP zero-shot | 26.8% | 2,318 | Mostly predicts raccoon on night infrared frames |
| Linear probe on BioCLIP, C=10, new cameras (trans_test) | 62.8% | 928 | Trained on 11,518 train crops, C tuned on validation |
| Linear probe, known cameras (cis_test) | 78.9% | 615 | |
| Probe on greyscale crops | 59.4% | 928 | Rejected: worse than colour |
| SpeciesNet | not run | - | |

Winner for now: linear probe on BioCLIP. Not yet measured: macro-F1, day vs night, empty-frame filter, count error.
