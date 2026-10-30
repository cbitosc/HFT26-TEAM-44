# ML scripts (offline, run from the data folder)

These are the scripts used to build and measure the species model. They expect to be run from the folder that holds the
CCT data (`cct_images/`, `cct_meta/`, `cct_subset/`), which on the build machine was `D:\hackathon`, inside its venv:

```
python make_train.py                       # copy the CCT20 train-split images into cct_train/
python -m megadetector.detection.run_detector_batch MDV5A cct_subset cct_output.json
python -m megadetector.detection.run_detector_batch MDV5A cct_train  train_output.json
python classify.py cct_output.json cct_species.json    # zero-shot BioCLIP baseline
python evaluate.py                                      # baseline accuracy: 26.8%
python train_probe.py                                   # trains the linear probe -> species_probe.pkl, accuracy 62.9%
```
`species_probe.pkl` is what the dashboard backend (`src/backend/pipeline.py`) loads; its path is set in `src/config.yaml`.
Copies of the scripts as written during the hackathon; they were not changed to run from this folder.
