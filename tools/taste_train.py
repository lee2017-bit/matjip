#!/usr/bin/env python3
"""주인장 취향 모델 학습: data/picks.json 의 주인장 픽(양성) vs 같은 건물의 다른 가게(음성).

  pip install scikit-learn numpy   # 학습할 때만 필요 (사이트 빌드는 표준 라이브러리만 사용)
  python3 tools/taste_train.py     # → data/taste_model.json

같은 건물 가게를 음성으로 쓰면 위치의 영향이 빠지고 '어떤 가게를 고르는지'만 남는다. 매출 백분위는 입력에서 뺀다.
목록(카카오맵 폴더)이 바뀌어 tools/picks.py 를 다시 돌렸을 때 함께 다시 학습한다.
"""
import json
import pathlib
import re
import sys
from datetime import datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

picks = json.loads((ROOT / "data" / "picks.json").read_text())
pick_urls = {p["merUrl"] for p in picks["picks"]}
items = {r["merUrl"]: r for r in picks["items"]}
everything = dict(items)
for f in (ROOT / "data" / "cache").glob("*.json"):
    for r in json.loads(f.read_text())["items"]:
        everything.setdefault(r["merUrl"], r)
counts = {}
for r in everything.values():  # 사이트와 같은 프랜차이즈 판정
    b, branch = build.brand_parts(r)
    if branch:
        counts[b] = counts.get(b, 0) + 1
build.FRANCHISE.update(b for b, n in counts.items() if n >= build.CHAIN_MIN and len(b) >= 2)

rows = [r for r in items.values() if r.get("merTpbuzNm") not in build.SKIP_CATS]
# 매출 백분위는 일부러 뺀다: 사이트의 가게들은 원래 매출 상위라, 넣으면 '잘 팔리는 집'으로 끌려간다.
# 취향 점수는 매출점수와 따로 '어떤 가게인가'만 본다.
SALES = {"log_all", "log_ccg", "all_minus_ccg"}
names = [k for k in build.taste_features(rows[0]) if k not in SALES]
X = np.array([[build.taste_features(r)[k] for k in names] for r in rows])
y = np.array([1 if r["merUrl"] in pick_urls else 0 for r in rows])


def building(addr):
    a = re.sub(r"\(.*?\)", "", addr or "").replace(",", " ").split()
    for i in range(1, len(a) - 1):
        if re.search(r"(로|길)$", a[i]) and re.match(r"^\d+(-\d+)?$", a[i + 1]):
            return " ".join(a[1:i + 2])
    return " ".join(a[:4])


groups = np.array([building(r.get("merAddr")) for r in rows])
mean, scale = X.mean(axis=0), X.std(axis=0)
scale[scale == 0] = 1.0
Z = (X - mean) / scale
pred = np.zeros(len(y))
for tr, te in StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=0).split(Z, y, groups):
    m = LogisticRegression(max_iter=3000, C=0.5).fit(Z[tr], y[tr])
    pred[te] = m.predict_proba(Z[te])[:, 1]
auc = roc_auc_score(y, pred)
model = LogisticRegression(max_iter=3000, C=0.5).fit(Z, y)
out = {"trained_at": datetime.now().isoformat(timespec="seconds"), "positives": int(y.sum()), "negatives": int((1 - y).sum()),
       "cv_auc": round(float(auc), 3), "features": names, "mean": [round(float(v), 6) for v in mean],
       "scale": [round(float(v), 6) for v in scale], "coef": [round(float(v), 6) for v in model.coef_[0]],
       "intercept": round(float(model.intercept_[0]), 6)}
(ROOT / "data" / "taste_model.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(f"positives {out['positives']} negatives {out['negatives']} cross-validated AUC {auc:.3f} → data/taste_model.json")
