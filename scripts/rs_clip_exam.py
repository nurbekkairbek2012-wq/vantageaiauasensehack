"""Спутниковые модели «снимок + текст» (CLIP) на нашем экзамене.

── Зачем ───────────────────────────────────────────────────────────────

DINOv2 учили на обычных фотографиях, и понимать снимок сверху ей
приходится по линейному зонду на наших скудных данных. Есть модели,
которые с самого начала учились на спутниковых снимках с подписями:

  RemoteCLIP (Apache 2.0)  — chendelong/RemoteCLIP, ViT-B-32 и ViT-L-14;
  SkyCLIP    (MIT)         — SkyScript, ViT-L-14, 50% лучших пар.

GeoRSCLIP не берётся: лицензия весов указана как «cc» без уточнения.

Проверяются два способа, оба на 51 объекте северного кольца (7 свалок):

  * без обучения — сходство снимка с текстом «свалка» против «стройка,
    склад, площадка, пустырь»; ни одного нашего примера модель не видит;
  * линейный зонд — как у DINOv2: признаки модели + логистическая
    регрессия на дроновом наборе, казахстанском OSM и отказах человека из
    других поясов (те же данные, что у модели в продукте).

    python scripts/rs_clip_exam.py [--models remoteclip-l14,skyclip-l14]
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
WEIGHTS = ROOT / "models/rs_clip"
OUT = ROOT / "data/eval/rs_clip.json"
SKYCLIP_URL = ("https://opendatasharing.s3.us-west-2.amazonaws.com/SkyScript/ckpt/"
               "SkyCLIP_ViT_L14_top50pct.zip")

MODELS = {
    "remoteclip-b32": ("ViT-B-32", "remoteclip", "RemoteCLIP-ViT-B-32.pt"),
    "remoteclip-l14": ("ViT-L-14", "remoteclip", "RemoteCLIP-ViT-L-14.pt"),
    "skyclip-l14": ("ViT-L-14", "skyclip", None),
}

POSITIVE = [
    "a satellite image of an illegal garbage dump",
    "a satellite image of a landfill with piles of waste",
    "an aerial image of dumped construction waste and debris on bare ground",
    "an aerial image of scattered trash heaps",
]
NEGATIVE = [
    "a satellite image of a construction site",
    "a satellite image of a warehouse with a parking lot",
    "a satellite image of an industrial area with buildings",
    "a satellite image of bare soil",
    "a satellite image of a sand or gravel storage yard",
    "a satellite image of a road",
    "a satellite image of residential houses",
    "a satellite image of grassland",
]


def checkpoint(kind: str, filename: str | None) -> Path:
    """Скачать веса один раз в models/rs_clip/."""
    import urllib.request

    from huggingface_hub import hf_hub_download

    WEIGHTS.mkdir(parents=True, exist_ok=True)
    if kind == "remoteclip":
        return Path(hf_hub_download("chendelong/RemoteCLIP", filename, local_dir=WEIGHTS))
    target = WEIGHTS / "SkyCLIP_ViT_L14_top50pct" / "epoch_20.pt"
    found = list(WEIGHTS.glob("SkyCLIP*/**/*.pt"))
    if found:
        return found[0]
    archive = WEIGHTS / "skyclip.zip"
    if not archive.exists():
        urllib.request.urlretrieve(SKYCLIP_URL, archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(WEIGHTS)
    found = list(WEIGHTS.glob("SkyCLIP*/**/*.pt"))
    return found[0] if found else target


def load(name: str):
    import open_clip
    import torch

    arch, kind, filename = MODELS[name]
    model, _, preprocess = open_clip.create_model_and_transforms(arch)
    state = torch.load(checkpoint(kind, filename), map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    state = {k.removeprefix("module."): v for k, v in state.items()}
    model.load_state_dict(state)
    model.eval()
    return model, preprocess, open_clip.get_tokenizer(arch)


def encode_images(model, preprocess, pictures, batch: int = 16):
    import numpy as np
    import torch

    out = []
    with torch.no_grad():
        for i in range(0, len(pictures), batch):
            x = torch.stack([preprocess(p.convert("RGB")) for p in pictures[i:i + batch]])
            f = model.encode_image(x)
            out.append(torch.nn.functional.normalize(f, dim=-1).numpy())
    return np.concatenate(out) if out else np.zeros((0, 0), dtype="float32")


def zero_shot(model, tokenizer, feats):
    import numpy as np
    import torch

    with torch.no_grad():
        t = model.encode_text(tokenizer(POSITIVE + NEGATIVE))
        t = torch.nn.functional.normalize(t, dim=-1).numpy()
    logits = 100.0 * feats @ t.T
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    return p[:, :len(POSITIVE)].sum(axis=1)


def main() -> int:
    import warnings

    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from train_dinov2 import OWN_WEIGHT, drone, exam_pictures, interval, kz

    from vantage import env

    warnings.filterwarnings("ignore")
    env.configure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="remoteclip-b32,remoteclip-l14,skyclip-l14")
    args = parser.parse_args()

    frame, pictures = exam_pictures()
    decided = frame["truth"].isin(["landfill", "not_landfill"]).to_numpy()
    north = (frame["area"] == "outputs_real").to_numpy() & decided
    own = ((frame["truth"] == "not_landfill") & (frame["area"] != "outputs_real")).to_numpy()
    y = (frame["truth"] == "landfill").to_numpy().astype(int)
    print(f"── экзамен: север {int(north.sum())} объектов, свалок {int(y[north].sum())}")
    dr_pics, dr_y = drone()
    dr_pics = list(dr_pics)
    kz_pics, kz_y = kz()
    kz_pics = list(kz_pics)

    results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for name in args.models.split(","):
        model, preprocess, tokenizer = load(name)
        cache = ROOT / f"data/rs_clip_{name}.npz"
        if cache.exists():
            z = np.load(cache)
            ex, dr, kzf = z["ex"], z["dr"], z["kz"]
        else:
            ex = encode_images(model, preprocess, pictures)
            dr = encode_images(model, preprocess, dr_pics)
            kzf = encode_images(model, preprocess, kz_pics)
            np.savez_compressed(cache, ex=ex, dr=dr, kz=kzf)

        zs = zero_shot(model, tokenizer, ex)
        X = np.vstack([dr, kzf, ex[own]])
        Y = np.concatenate([np.asarray(dr_y), np.asarray(kz_y), np.zeros(int(own.sum()))])
        w = np.ones(len(Y))
        w[-int(own.sum()):] = OWN_WEIGHT
        clf = make_pipeline(StandardScaler(), LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=3000))
        clf.fit(X, Y, logisticregression__sample_weight=w)
        probe = clf.predict_proba(ex)[:, 1]

        results[name] = {}
        for method, score in (("zero_shot", zs), ("probe", probe)):
            s, t = score[north], y[north]
            auc = float(roc_auc_score(t, s))
            low, high = interval(t, s)
            below = int(((s < s[t == 1].min()) & (t == 0)).sum())
            results[name][method] = {"roc_auc": round(auc, 3), "low": round(low, 3),
                                     "high": round(high, 3), "negatives_below_worst_dump": below}
            print(f"   {name:16} {method:9} ROC-AUC {auc:.3f} ({low:.2f}–{high:.2f}); "
                  f"не-свалок ниже худшей свалки: {below} из {int((t == 0).sum())}", flush=True)
        results[name]["_scores"] = {"candidate_id": frame["candidate_id"][north].tolist(),
                                    "zero_shot": [round(float(v), 4) for v in zs[north]],
                                    "probe": [round(float(v), 4) for v in probe[north]]}
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
