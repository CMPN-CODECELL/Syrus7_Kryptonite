"""Run reports: printable self-contained HTML, JSON, CSV and raw counts CSV.

All exports are rendered from the stored result JSON (the same bytes the API
returns from /result), so the numbers in every format are the stored numbers.
No credentials, tokens or local paths are ever included.
"""

from __future__ import annotations

import csv
import functools
import html
import io
import json
import math
import subprocess
from datetime import datetime, timezone
from typing import Any, Iterable

from ..contracts import DISCLAIMER, SCHEMA_VERSION
from ..settings import PROJECT_ROOT

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "|", "%")


# --------------------------------------------------------------------------- #
# provenance helpers
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            sha = out.stdout.strip()
            dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=str(PROJECT_ROOT),
                                   capture_output=True, text=True, timeout=3)
            return sha + ("-dirty" if dirty.returncode == 0 and dirty.stdout.strip() else "")
        return "no commit yet (uncommitted working tree)"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def report_meta(job: dict[str, Any] | None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "report_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report_schema_version": SCHEMA_VERSION,
        "git_commit": git_commit(),
        "disclaimer": DISCLAIMER,
    }
    if job:
        meta["job"] = {k: job.get(k) for k in ("run_id", "kind", "status", "created_at", "started_at", "finished_at", "cached_from",
                                                "cached_original_time", "is_example", "label")}
    return meta


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def render_json(result: dict[str, Any], job: dict[str, Any] | None) -> bytes:
    return json.dumps({"report": report_meta(job), "result": result}, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def csv_cell(v: Any) -> str:
    """Exact, injection-safe cell. Floats use repr() so they round-trip
    exactly; strings that a spreadsheet could treat as a formula are
    prefixed with an apostrophe."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else ""
    s = str(v)
    if s.startswith(_FORMULA_PREFIXES):
        return "'" + s
    return s


def _write_csv(header: list[str], rows: Iterable[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(header)
    for r in rows:
        w.writerow([csv_cell(c) for c in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel reads UTF-8 (₹, —)


RUN_CSV_HEADER = [
    "run_id", "solver", "status", "feasible", "objective", "expected_return", "volatility", "variance", "net_expected_return",
    "cost_fraction", "cost_inr", "cash_weight", "sharpe_estimate", "gap_absolute", "gap_relative", "gap_reference",
    "validation_sharpe", "validation_total_return", "test_sharpe", "test_total_return", "test_max_drawdown", "runtime_total_s",
    "bitstring", "symbol", "sector", "weight", "amount_inr", "indicative_shares", "price_inr", "lots",
]

EXPERIMENT_CSV_HEADER = [
    "run_id", "factor", "depth", "optimizer", "init_strategy", "seed", "n_assets", "total_qubits", "status", "feasible_probability",
    "best_objective", "exact_objective", "gap_abs", "approximation_ratio", "evaluations", "preparation_evaluations", "runtime_s",
    "validation_sharpe", "error",
]


def _runtime_total(rt: dict[str, Any]) -> Any:
    if not rt:
        return None
    if "total" in rt:
        return rt["total"]
    vals = [v for v in rt.values() if isinstance(v, (int, float))]
    return float(sum(vals)) if vals else None


def run_csv_rows(result: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    rid = result.get("run_id")
    for s in result.get("solvers") or []:
        p = s.get("portfolio") or {}
        gap = s.get("gap") or {}
        val = p.get("validation") or {}
        test = p.get("test") or {}
        feas = (p.get("feasibility") or {}).get("feasible") if p else None
        base = [
            rid, s.get("solver"), s.get("status"), feas, p.get("objective"), p.get("expected_return"), p.get("volatility"), p.get("variance"),
            p.get("net_expected_return"), p.get("cost_fraction"), p.get("cost_inr"), p.get("cash_weight"), p.get("sharpe_estimate"),
            gap.get("absolute"), gap.get("relative"), gap.get("reference_solver"), val.get("sharpe"), val.get("total_return"),
            test.get("sharpe"), test.get("total_return"), test.get("max_drawdown"), _runtime_total(s.get("runtime_s") or {}),
            s.get("bitstring"),
        ]
        holdings = p.get("holdings") or []
        if not holdings:
            rows.append(base + [None] * 7)
        for h in holdings:
            rows.append(base + [h.get("symbol"), h.get("sector"), h.get("weight"), h.get("amount_inr"), h.get("indicative_shares"),
                                h.get("price_inr"), h.get("lots")])
    return rows


def render_csv(result: dict[str, Any]) -> bytes:
    if "cells" in result:
        rid = result.get("run_id")
        rows = [[rid] + [c.get(k) for k in EXPERIMENT_CSV_HEADER[1:]] for c in result.get("cells") or []]
        return _write_csv(EXPERIMENT_CSV_HEADER, rows)
    return _write_csv(RUN_CSV_HEADER, run_csv_rows(result))


def render_counts_csv(counts: dict[str, int] | None, result: dict[str, Any]) -> bytes:
    """Full final-batch counts. Falls back to the stored top samples (and says
    so in a header comment column) if the raw counts artifact is missing."""
    qaoa = result.get("qaoa") or {}
    fb = qaoa.get("final_batch") or {}
    shots = fb.get("shots") or (sum(counts.values()) if counts else 0)
    if counts:
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        rows = [[b, c, (c / shots) if shots else None] for b, c in items]
        return _write_csv(["bitstring_qiskit_order", "count", "probability"], rows)
    rows = [[r.get("bitstring"), r.get("count"), r.get("probability")] for r in fb.get("top_samples") or []]
    return _write_csv(["bitstring_qiskit_order", "count", "probability", "note"],
                      [r + ["top samples only: raw counts artifact unavailable"] for r in rows])


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _num(v: Any, digits: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "—"
        return f"{v:.{digits}f}"
    return _e(v)


def _pct(v: Any, digits: int = 2) -> str:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        return "—"
    return f"{v * 100:.{digits}f}%"


def _inr(v: Any) -> str:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        return "—"
    return f"₹{v:,.0f}"


def _table(header: list[str], rows: list[list[str]], cls: str = "") -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body or "<tr><td colspan=99>none</td></tr>"}</tbody></table>'


def _kv(pairs: list[tuple[str, str]]) -> str:
    return '<table class="kv"><tbody>' + "".join(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in pairs) + "</tbody></table>"


def _svg_line(points: list[tuple[float, float]], w: int = 640, h: int = 200, label_x: str = "", label_y: str = "") -> str:
    pts = [(x, y) for x, y in points if math.isfinite(x) and math.isfinite(y)]
    if len(pts) < 2:
        return "<p class=muted>Not enough points to plot.</p>"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 == x0:
        x1 = x0 + 1
    if y1 == y0:
        y1 = y0 + 1
    pad = 36
    sx = lambda x: pad + (x - x0) / (x1 - x0) * (w - 2 * pad)  # noqa: E731
    sy = lambda y: h - pad + -(y - y0) / (y1 - y0) * (h - 2 * pad)  # noqa: E731
    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{_e(label_y)} by {_e(label_x)}">'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff" stroke="#ccc"/>'
        f'<path d="{path}" fill="none" stroke="#1f4e9c" stroke-width="1.5"/>'
        f'<text x="{pad}" y="{h - 8}" font-size="11">{_e(label_x)}: {x0:g} – {x1:g}</text>'
        f'<text x="{pad}" y="14" font-size="11">{_e(label_y)}: {y0:.4g} – {y1:.4g}</text></svg>'
    )


def _svg_frontier(frontier: list[dict[str, Any]], solvers: list[dict[str, Any]], w: int = 640, h: int = 260) -> str:
    pts = [(f.get("volatility"), f.get("expected_return"), f.get("kind"), f.get("label")) for f in frontier]
    for s in solvers:
        p = s.get("portfolio") or {}
        if p:
            pts.append((p.get("volatility"), p.get("expected_return"), "solver", s.get("solver")))
    pts = [p for p in pts if isinstance(p[0], (int, float)) and isinstance(p[1], (int, float))]
    if not pts:
        return "<p class=muted>No frontier data.</p>"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    x1 = x1 if x1 > x0 else x0 + 1e-6
    y1 = y1 if y1 > y0 else y0 + 1e-6
    pad = 40
    sx = lambda x: pad + (x - x0) / (x1 - x0) * (w - 2 * pad)  # noqa: E731
    sy = lambda y: h - pad - (y - y0) / (y1 - y0) * (h - 2 * pad)  # noqa: E731
    colors = {"continuous_relaxation": "#999", "discrete_feasible": "#6a9", "solver": "#c33"}
    marks = []
    for x, y, kind, label in pts:
        c = colors.get(kind or "", "#555")
        r = 5 if kind == "solver" else 3
        marks.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="{r}" fill="{c}"><title>{_e(label or kind)}: '
                     f'return {y:.4f}, volatility {x:.4f}</title></circle>')
        if kind == "solver":
            marks.append(f'<text x="{sx(x) + 6:.1f}" y="{sy(y) - 6:.1f}" font-size="10">{_e(label)}</text>')
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Risk-return frontier">'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff" stroke="#ccc"/>{"".join(marks)}'
        f'<text x="{pad}" y="{h - 10}" font-size="11">volatility (annualised): {x0:.3f} – {x1:.3f}</text>'
        f'<text x="{pad}" y="14" font-size="11">expected return (annualised, training): {y0:.3f} – {y1:.3f}</text></svg>'
    )


_CSS = """
body{font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif;color:#111;max-width:1100px;margin:24px auto;padding:0 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;border-bottom:1px solid #ccc;padding-bottom:4px;margin-top:28px}
h3{font-size:15px;margin:18px 0 6px}.muted{color:#555}.disclaimer{border:2px solid #b45309;background:#fff7ed;padding:8px 12px;font-weight:600}
.warn{border-left:4px solid #b45309;background:#fffbeb;padding:6px 10px;margin:6px 0}
table{border-collapse:collapse;width:100%;margin:6px 0 12px;font-size:12.5px}th,td{border:1px solid #ddd;padding:3px 6px;text-align:left;vertical-align:top}
th{background:#f3f4f6}table.kv th{width:28%}td.num{text-align:right;font-variant-numeric:tabular-nums}
code,.mono{font-family:Consolas,ui-monospace,monospace;font-size:12px;word-break:break-all}
@media print{body{margin:0;max-width:none}h2{break-after:avoid}table,svg{break-inside:avoid}a{color:#000;text-decoration:none}}
"""


def render_html(result: dict[str, Any], job: dict[str, Any] | None, counts_available: bool) -> bytes:
    meta = report_meta(job)
    is_exp = "cells" in result
    title = f"QuantFolio {'experiment' if is_exp else 'run'} report — {result.get('run_id')}"
    parts: list[str] = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>",
        f"<h1>{_e(title)}</h1>",
        f'<p class="disclaimer">{_e(result.get("disclaimer") or DISCLAIMER)}</p>',
    ]
    if job and job.get("cached_from"):
        parts.append(f'<p class="warn">This is a replay of the stored result of run <code>{_e(job["cached_from"])}</code> recorded at '
                     f'{_e(job.get("cached_original_time"))}. No new quantum computation was performed.</p>')
    if job and job.get("is_example"):
        parts.append('<p class="warn">Saved example run: a genuine run recorded earlier with the default example configuration.</p>')
    for w in result.get("warnings") or []:
        parts.append(f'<p class="warn">{_e(w)}</p>')
    parts.append(_provenance(result, meta))
    if is_exp:
        parts.append(_experiment_sections(result))
    else:
        parts.append(_run_sections(result, counts_available))
    parts.append(f'<p class="muted">Generated {_e(meta["report_generated_at"])} from the stored result. '
                 f'{_e(DISCLAIMER)}</p></body></html>')
    return "".join(parts).encode("utf-8")


def _provenance(result: dict[str, Any], meta: dict[str, Any]) -> str:
    ds = result.get("dataset") or {}
    sw = result.get("software") or {}
    rows = [
        ("Schema version", _e(result.get("schema_version"))),
        ("Run id", f"<code>{_e(result.get('run_id'))}</code>"),
        ("Created", _e(result.get("created_at"))),
        ("Implementation version", _e(result.get("implementation_version"))),
        ("Git commit", f"<code>{_e(meta['git_commit'])}</code>"),
        ("Config hash", f"<code>{_e(result.get('config_hash'))}</code>"),
        ("Cache key", f"<code>{_e(result.get('cache_key'))}</code>"),
        ("Dataset", f"{_e(ds.get('dataset_id'))} — <b>{_e(ds.get('label'))}</b>; source: {_e(ds.get('source'))}"),
        ("Dataset dates", f"{_e(ds.get('first_date'))} to {_e(ds.get('last_date'))}; downloaded {_e(ds.get('downloaded_at') or '—')}"),
        ("Dataset SHA-256", f"<code>{_e(ds.get('sha256'))}</code>"),
        ("Stale", f"{_num(ds.get('stale'))} {_e(ds.get('stale_reason') or '')}"),
        ("Universe", f"{_e(ds.get('universe_source'))} (as of {_e(ds.get('universe_as_of') or '—')})"),
        ("Survivorship", _e(ds.get("survivorship_note"))),
        ("Missing data policy", _e(ds.get("missing_data_policy"))),
        ("Software", _e(", ".join(f"{k} {v}" for k, v in sorted(sw.items())))),
        ("Timings (s)", _e(", ".join(f"{k}: {v:.3f}" if isinstance(v, (int, float)) else f"{k}: {v}"
                                     for k, v in (result.get("timings_s") or {}).items()))),
    ]
    return "<h2>Provenance</h2>" + _kv(rows)


def _config_section(cfg: dict[str, Any]) -> str:
    d = cfg.get("dates") or {}
    c = cfg.get("constraints") or {}
    tc = c.get("transaction_costs") or {}
    q = cfg.get("qaoa") or {}
    rows = [
        ("Mode", _e(cfg.get("mode"))),
        ("Preset", _e(cfg.get("preset"))),
        ("Requested symbols (order)", f"<span class=mono>{_e(', '.join(cfg.get('symbols') or []))}</span>"),
        ("Train", f"{_e(d.get('train_start'))} → {_e(d.get('train_end'))}"),
        ("Validation", f"{_e(d.get('validation_start'))} → {_e(d.get('validation_end'))}"),
        ("Test (evaluated once, last)", f"{_e(d.get('test_start'))} → {_e(d.get('test_end'))}"),
        ("Risk aversion q", _num(cfg.get("risk_aversion"))),
        ("Budget", _inr(cfg.get("budget_inr"))),
        ("Risk-free rate (assumption)", _pct(cfg.get("risk_free_rate"))),
        ("Screening", _e(json.dumps(cfg.get("screening")))),
        ("Cardinality", _num(c.get("cardinality"))),
        ("Sector caps", _e(json.dumps(c.get("sector_caps")))),
        ("Target return", _pct(c.get("target_return"))),
        ("Lots", _e(json.dumps(c.get("lots")))),
        ("Transaction costs", _e(json.dumps(tc))),
        ("QAOA", _e(json.dumps(q))),
        ("Baselines", _e(json.dumps(cfg.get("baselines")))),
        ("Noise comparison", f"{_num(cfg.get('noise_comparison'))} (trials {_num(cfg.get('noise_trials'))})"),
        ("Timeout", f"{_num(cfg.get('timeout_seconds'))} s"),
    ]
    return "<h2>Configuration</h2>" + _kv(rows)


def _run_sections(r: dict[str, Any], counts_available: bool) -> str:
    out: list[str] = []
    for line in r.get("explanations") or []:
        out.append(f"<p>{_e(line)}</p>")
    solvers = r.get("solvers") or []
    out.append("<h2>Solver comparison</h2>")
    rows = []
    for s in solvers:
        p = s.get("portfolio") or {}
        gap = s.get("gap") or {}
        test = p.get("test") or {}
        rows.append([
            _e(s.get("solver")), _e(s.get("status")) + (f"<br><span class=muted>{_e(s.get('status_detail'))}</span>" if s.get("status_detail") else ""),
            _num((p.get("feasibility") or {}).get("feasible")) if p else "—", _num(p.get("objective"), 6), _pct(p.get("expected_return")),
            _pct(p.get("volatility")), _pct(p.get("net_expected_return")), _inr(p.get("cost_inr")),
            _num(gap.get("absolute"), 6), _pct(gap.get("relative")), _num(test.get("sharpe"), 3),
            _num(_runtime_total(s.get("runtime_s") or {}), 3), f"<code>{_e(s.get('bitstring') or '—')}</code>",
        ])
    out.append(_table(["Solver", "Status", "Feasible", "Objective", "Exp. return", "Volatility", "Net return", "Cost", "Gap (abs)",
                       "Gap (rel)", "Test Sharpe", "Runtime s", "Bitstring"], rows))
    for s in solvers:
        p = s.get("portfolio") or {}
        if not p:
            continue
        out.append(f"<h3>{_e(s.get('solver'))}: holdings</h3>")
        out.append(_table(["Symbol", "Sector", "Weight", "Amount", "Indicative shares", "Price", "Lots"], [
            [_e(h.get("symbol")), _e(h.get("sector")), _pct(h.get("weight")), _inr(h.get("amount_inr")), _num(h.get("indicative_shares")),
             _inr(h.get("price_inr")), _num(h.get("lots"))] for h in p.get("holdings") or []]))
        res = (p.get("feasibility") or {}).get("residuals") or []
        out.append(_table(["Constraint", "Value", "Limit", "Satisfied", "Explanation"], [
            [_e(x.get("constraint")), _num(x.get("value"), 6), _num(x.get("limit"), 6), _num(x.get("satisfied")), _e(x.get("explanation"))]
            for x in res]))
        periods = [(name, p.get(name) or {}) for name in ("validation", "test")]
        out.append(_table(["Period", "Start", "End", "Obs", "Total return", "Ann. return", "Ann. vol", "Sharpe", "Max DD", "Note"], [
            [name, _e(m.get("start")), _e(m.get("end")), _num(m.get("n_obs")), _pct(m.get("total_return")), _pct(m.get("annualised_return")),
             _pct(m.get("annualised_volatility")), _num(m.get("sharpe"), 3), _pct(m.get("max_drawdown")),
             _e(m.get("note") or ("insufficient data" if m.get("insufficient") else ""))] for name, m in periods if m]))
    out.append(_config_section(r.get("config") or {}))
    est = r.get("estimation") or {}
    if est:
        out.append("<h2>Preprocessing and estimation</h2>")
        out.append(_kv([
            ("Return type", _e(est.get("return_type"))), ("Annualisation days", _num(est.get("annualisation_days"))),
            ("Covariance", f"{_e(est.get('covariance_method'))}; shrinkage {_num(est.get('shrinkage_intensity'))}"),
            ("PSD corrected", f"{_num(est.get('psd_corrected'))} (min eigenvalue {_num(est.get('min_eigenvalue_before'), 8)} → "
                              f"{_num(est.get('min_eigenvalue_after'), 8)})"),
            ("Segments", _e("; ".join(f"{k}: {(est.get(k) or {}).get('start')} → {(est.get(k) or {}).get('end')} "
                                      f"({(est.get(k) or {}).get('n_obs')} obs)" for k in ("train", "validation", "test")))),
            ("Notes", _e(" ".join(est.get("notes") or []))),
            ("Encoded symbols (order)", f"<span class=mono>{_e(', '.join(r.get('encoded_symbols') or []))}</span>"),
            ("Screening", _e(json.dumps(r.get("screening")))),
        ]))
    qubo = r.get("qubo") or {}
    if qubo:
        out.append("<h2>QUBO formulation</h2>")
        out.append(_kv([
            ("Objective (units)", _e(qubo.get("objective_definition")) + " — annualised decimals; costs as a fraction of budget"),
            ("Variables", f"{_num(qubo.get('n_variables'))} ({_num(qubo.get('n_decision'))} decision, {_num(qubo.get('n_slack'))} slack)"),
            ("Terms", f"{_num(qubo.get('n_linear_terms'))} linear, {_num(qubo.get('n_quadratic_terms'))} quadratic"),
            ("Offset / Ising offset", f"{_num(qubo.get('offset'), 6)} / {_num(qubo.get('ising_offset'), 6)}"),
            ("Scale / max |coef|", f"{_num(qubo.get('scale'), 6)} / {_num(qubo.get('max_abs_coefficient'), 6)}"),
        ]))
        out.append("<h3>Penalties</h3>" + _table(["Constraint", "Weight", "Derivation"], [
            [_e(p.get("constraint")), _num(p.get("weight"), 6), _e(p.get("derivation"))] for p in qubo.get("penalties") or []]))
        out.append("<h3>Variable map (qubit index → meaning)</h3>" + _table(["Qubit", "Name", "Kind", "Asset", "Constraint", "Coefficient"], [
            [_num(v.get("index")), _e(v.get("name")), _e(v.get("kind")), _e(v.get("asset")), _e(v.get("constraint")),
             _num(v.get("coefficient"))] for v in qubo.get("variables") or []]))
    q = r.get("qaoa")
    if q:
        circ = q.get("circuit") or {}
        fb = q.get("final_batch") or {}
        out.append("<h2>QAOA</h2>")
        out.append(_kv([
            ("Depth p", _num(q.get("depth"))),
            ("Optimizer", f"{_e(q.get('optimizer'))} {_e(json.dumps(q.get('optimizer_settings')))}"),
            ("Initialisation", f"{_e(q.get('init_strategy'))}: {_e(q.get('init_description'))}"),
            ("Estimator", f"{_e(q.get('estimator'))} (CVaR α {_num(q.get('cvar_alpha'))})"),
            ("Backend", f"{_e(q.get('primitive'))}; method {_e(q.get('simulator_method'))}; noise {_e(q.get('noise'))} "
                        f"{_e(q.get('noise_model') or '')}"),
            ("Circuit", f"{_num(circ.get('n_qubits'))} qubits, depth {_num(circ.get('depth'))}, size {_num(circ.get('size'))}, "
                        f"2-qubit gates {_num(circ.get('two_qubit_gates'))}, gates {_e(json.dumps(circ.get('gate_counts')))}, "
                        f"basis {_e(', '.join(circ.get('basis_gates') or []))}, opt level {_num(circ.get('optimization_level'))}, "
                        f"transpile {_num(circ.get('transpile_seconds'), 3)} s"),
            ("Evaluation budget", f"{_num(q.get('evaluations_used'))} used of {_num(q.get('evaluation_budget'))} "
                                  f"(+{_num(q.get('preparation_evaluations'))} warm-start preparation)"),
            ("Parameter order", _e(", ".join(q.get("parameter_order") or []))),
            ("Initial parameters", f"<span class=mono>{_e(json.dumps(q.get('initial_parameters')))}</span>"),
            ("Optimal parameters", f"<span class=mono>{_e(json.dumps(q.get('optimal_parameters')))}</span>"),
            ("Seeds", _e(json.dumps(q.get("seeds")))),
        ]))
        its = q.get("iterations") or []
        out.append("<h3>Optimisation trace (every measured evaluation)</h3>")
        out.append(_svg_line([(float(i.get("evaluation", 0)), float(i.get("energy", 0.0))) for i in its
                              if isinstance(i.get("energy"), (int, float))], label_x="evaluation", label_y="energy estimate"))
        out.append("<h3>Final sampling batch</h3>")
        out.append(_kv([
            ("Shots / seed", f"{_num(fb.get('shots'))} / {_num(fb.get('seed'))}"),
            ("Distinct bitstrings", _num(fb.get("distinct_bitstrings"))),
            ("Feasible probability", _pct(fb.get("feasible_probability"))),
            ("Selection policy", _e(fb.get("selection_policy"))),
            ("Best bitstring (Qiskit order)", f"<code>{_e(fb.get('best_bitstring') or '—')}</code>"),
            ("Raw counts", ("Full counts in <code>counts.csv</code>" if counts_available else "Raw counts artifact unavailable") +
             f"; SHA-256 <code>{_e(fb.get('counts_sha256'))}</code>"),
        ]))
        out.append(_table(["Bitstring (qubit 0 rightmost)", "Decision bits (asset order)", "Count", "Probability", "Feasible", "Objective",
                           "QUBO energy"], [
            [f"<code>{_e(s.get('bitstring'))}</code>", f"<code>{_e(s.get('decision_bits'))}</code>", _num(s.get("count")),
             _pct(s.get("probability")), _num(s.get("feasible")), _num(s.get("objective"), 6), _num(s.get("qubo_energy"), 6)]
            for s in fb.get("top_samples") or []]))
    nc = r.get("noise_comparison")
    if nc:
        out.append("<h2>Noise comparison</h2>")
        out.append(_kv([("Backend", _e(nc.get("backend"))), ("Noise model", _e(nc.get("noise_model_summary"))),
                        ("Shots", _num(nc.get("shots"))), ("Parameters", _e(nc.get("parameters_source"))),
                        ("Summary", _e(json.dumps(nc.get("summary"))))]))
        trial_rows = []
        for kind in ("ideal", "noisy"):
            for t in nc.get(kind) or []:
                trial_rows.append([kind, _num(t.get("seed")), _pct(t.get("feasible_probability")), _num(t.get("best_objective"), 6),
                                   _num(t.get("runtime_s"), 3)])
        out.append(_table(["Mode", "Seed", "Feasible prob.", "Best objective", "Runtime s"], trial_rows))
    out.append("<h2>Risk–return view</h2>")
    out.append(_svg_frontier(r.get("frontier") or [], solvers))
    return "".join(out)


def _experiment_sections(r: dict[str, Any]) -> str:
    out = ["<h2>Experiment</h2>", _kv([
        ("Selection rule", _e(r.get("selection_rule"))),
        ("Selected configuration", _e(json.dumps(r.get("selected_configuration")))),
        ("Design", _e((r.get("config") or {}).get("design"))),
    ])]
    out.append("<h3>Summary</h3>")
    summ = r.get("summary") or []
    keys = sorted({k for row in summ for k in row})
    out.append(_table(keys, [[_num(row.get(k), 6) if isinstance(row.get(k), float) else _e(row.get(k)) for k in keys] for row in summ]))
    out.append("<h3>Cells</h3>")
    out.append(_table(EXPERIMENT_CSV_HEADER[1:], [
        [_num(c.get(k), 6) if isinstance(c.get(k), float) else _e(c.get(k)) for k in EXPERIMENT_CSV_HEADER[1:]] for c in r.get("cells") or []]))
    base = (r.get("config") or {}).get("base")
    if base:
        out.append(_config_section(base))
    return "".join(out)
