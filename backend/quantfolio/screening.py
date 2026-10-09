"""Training-only pre-screening of a large request down to the encoded subset.

Both rules use ONLY training-segment estimates (no validation/test data):

* top_sharpe_train   keep the `keep` assets with the highest training
                     Sharpe estimate (mu_i - rf) / sigma_i.
* min_variance_train keep the `keep` assets with the lowest training
                     variance Sigma_ii.

Ties are broken by the requested order. Every solver (QAOA and baselines)
receives the same screened subset, so comparisons stay like-for-like.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .contracts import ScreeningConfig
from .risk import Estimate

RULE_TEXT = {
    "none": "no screening: every usable requested asset is encoded",
    "top_sharpe_train": "keep the assets with the highest training Sharpe estimate (mu - rf) / sigma",
    "min_variance_train": "keep the assets with the lowest training variance",
}


def screen(est: Estimate, cfg: ScreeningConfig, risk_free_rate: float) -> tuple[list[str], list[str], dict[str, Any]]:
    """Return (kept symbols in original order, screened-out symbols, report)."""
    syms = list(est.symbols)
    keep = cfg.keep
    if cfg.rule == "none" or keep is None or keep >= len(syms):
        report = {"rule": cfg.rule, "keep": keep, "description": RULE_TEXT[cfg.rule], "scores": {}, "applied": False}
        if cfg.rule != "none" and keep is not None and keep >= len(syms):
            report["note"] = f"keep={keep} >= {len(syms)} usable assets: nothing screened out"
        return syms, [], report
    if cfg.rule == "top_sharpe_train":
        vol = np.where(est.vol > 1e-12, est.vol, np.inf)
        score = (est.mu - risk_free_rate) / vol
        order = sorted(range(len(syms)), key=lambda i: (-score[i], i))
    else:  # min_variance_train
        score = np.diag(est.sigma)
        order = sorted(range(len(syms)), key=lambda i: (score[i], i))
    kept_idx = sorted(order[:keep])
    kept = [syms[i] for i in kept_idx]
    out = [syms[i] for i in order[keep:]]
    report = {
        "rule": cfg.rule,
        "keep": keep,
        "description": RULE_TEXT[cfg.rule],
        "scores": {syms[i]: float(score[i]) for i in range(len(syms))},
        "applied": True,
        "training_only": True,
    }
    return kept, out, report
