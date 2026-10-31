"""Compare zero-shot prompt styles for BioCLIP using the features cached by tune_probe.py (features.npz).
No images are re-processed, so this takes under a minute.   Run from D:\\hackathon:   python zeroshot_prompts.py
BioCLIP was trained on TAXONOMIC names, so common-name prompts (what classify.py used) are the weak baseline."""
import numpy as np
import torch, open_clip

TAXON = {   # CCT20 classes -> taxonomic string in the form BioCLIP saw in training (family level where the species is unknown)
    "opossum":  "Animalia Chordata Mammalia Didelphimorphia Didelphidae Didelphis virginiana",
    "raccoon":  "Animalia Chordata Mammalia Carnivora Procyonidae Procyon lotor",
    "cat":      "Animalia Chordata Mammalia Carnivora Felidae Felis catus",
    "coyote":   "Animalia Chordata Mammalia Carnivora Canidae Canis latrans",
    "dog":      "Animalia Chordata Mammalia Carnivora Canidae Canis familiaris",
    "bobcat":   "Animalia Chordata Mammalia Carnivora Felidae Lynx rufus",
    "rabbit":   "Animalia Chordata Mammalia Lagomorpha Leporidae Sylvilagus",
    "squirrel": "Animalia Chordata Mammalia Rodentia Sciuridae",
    "rodent":   "Animalia Chordata Mammalia Rodentia",
    "skunk":    "Animalia Chordata Mammalia Carnivora Mephitidae",
    "deer":     "Animalia Chordata Mammalia Artiodactyla Cervidae",
    "bird":     "Animalia Chordata Aves",
}
STYLES = {
    "A  old: 'a camera trap photo of a X.'":        lambda n, t: f"a camera trap photo of a {n}.",
    "B  'a photo of X.'":                            lambda n, t: f"a photo of {n}.",
    "C  taxonomy only":                              lambda n, t: t,
    "D  'a photo of <taxonomy>.'":                   lambda n, t: f"a photo of {t}.",
    "E  'a photo of <taxonomy>, common name X.'":    lambda n, t: f"a photo of {t}, common name {n}.",
}


def main():
    d = np.load("features.npz", allow_pickle=True)
    X, y, S = d["X"], d["y"], d["S"]
    classes = sorted(set(y))
    missing = [c for c in classes if c not in TAXON]
    if missing:
        raise SystemExit(f"Add taxonomy strings for: {missing}")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, _ = open_clip.create_model_and_transforms("hf-hub:imageomics/bioclip")
    tok = open_clip.get_tokenizer("hf-hub:imageomics/bioclip")
    model = model.to(dev).eval()
    Xt = torch.tensor(X, dtype=torch.float32, device=dev)
    best = (None, -1)
    print(f"{'prompt style':48s} {'cis_test':>9s} {'trans_test':>11s}")
    for label, fn in STYLES.items():
        with torch.no_grad():
            tf = model.encode_text(tok([fn(c.replace('_', ' '), TAXON[c]) for c in classes]).to(dev))
            tf /= tf.norm(dim=-1, keepdim=True)
            pred = np.array(classes)[(Xt @ tf.T).argmax(1).cpu().numpy()]
        acc = {s: (pred[S == s] == y[S == s]).mean() for s in ("cis_test", "trans_test")}
        print(f"{label:48s} {acc['cis_test']:9.1%} {acc['trans_test']:11.1%}")
        if acc["trans_test"] > best[1]:
            best = (label, acc["trans_test"])
    print(f"\nbest on new cameras: {best[0]}  ({best[1]:.1%})   linear probe for comparison: 62.8%")


if __name__ == "__main__":
    main()
