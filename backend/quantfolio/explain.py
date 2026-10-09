"""Deterministic plain-language findings for a run (no generated prose: every
sentence is a template filled with measured values from the result)."""

from __future__ import annotations

from typing import Any

from .contracts import SolverResult


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{100 * v:.2f}%"


def _inr(v: float) -> str:
    return f"INR {v:,.0f}"


def explain_run(
    *,
    n_assets: int,
    n_decision: int,
    n_slack: int,
    solvers: list[SolverResult],
    qaoa_feasible_probability: float | None,
    qaoa_depth: int | None,
    constraint_notes: list[str],
    dataset_label: str,
    survivorship_note: str,
    cost_start: str,
    noise_summary: dict[str, Any] | None = None,
) -> list[str]:
    out: list[str] = []
    out.append(f"{n_assets} assets were encoded into {n_decision + n_slack} qubits ({n_decision} decision bits + {n_slack} slack bits for inequality constraints).")
    by = {s.solver: s for s in solvers}
    q = by.get("qaoa")
    ex = by.get("exact")
    if q is not None:
        if q.status in ("ok", "time_limited") and q.portfolio is not None:
            msg = f"QAOA (depth {qaoa_depth}) returned a feasible portfolio from its final sampling batch"
            if qaoa_feasible_probability is not None:
                msg += f"; {_pct(qaoa_feasible_probability)} of final-batch shots were feasible"
            out.append(msg + ".")
        elif q.status == "no_feasible_sample":
            out.append(
                "QAOA produced no feasible sample in its final batch, so it reports no portfolio. This is not a proof that the problem is infeasible"
                + (" (the exact solver did find feasible portfolios)." if ex is not None and ex.status == "ok" else ".")
            )
        else:
            out.append(f"QAOA status: {q.status}" + (f" — {q.status_detail}" if q.status_detail else "") + ".")
    if ex is not None and ex.status == "ok" and ex.portfolio is not None:
        f_opt = ex.portfolio.objective
        hit = [s.solver for s in solvers if s.portfolio is not None and s.portfolio.feasibility.feasible and abs(s.portfolio.objective - f_opt) <= 1e-9]
        out.append(f"The exact optimum (objective {f_opt:.6f}) was matched by: {', '.join(hit)}.")
        miss = [s for s in solvers if s.portfolio is not None and s.solver not in hit and s.gap is not None]
        for s in miss:
            rel = f" ({100 * s.gap.relative:.1f}% of the feasible objective range)" if s.gap.relative is not None and s.gap.reference == "exact_optimum" else ""
            out.append(f"{s.solver} is {s.gap.absolute:.6f} above the optimum{rel}.")
    elif ex is not None and ex.status == "infeasible":
        out.append("Exhaustive enumeration proved that no portfolio satisfies all constraints.")
    elif ex is not None and ex.status == "unsupported":
        out.append("The exact solver was not run (problem too large for exhaustive enumeration); gaps are relative to the best solution found by any solver.")
    out.extend(constraint_notes)
    best = None
    for name in ("qaoa", "exact", "simulated_annealing", "relaxation_rounding", "greedy"):
        s = by.get(name)
        if s is not None and s.portfolio is not None and s.portfolio.feasibility.feasible:
            best = s
            break
    if best is not None and best.portfolio is not None:
        pe = best.portfolio
        start = "the current holdings" if cost_start == "holdings" else "cash"
        out.append(
            f"The {best.solver} portfolio costs {_inr(pe.cost_inr)} to reach from {start} ({_pct(pe.cost_fraction)} of budget, one-off), "
            f"for an expected training-period return of {_pct(pe.expected_return)} p.a. at {_pct(pe.volatility)} volatility."
        )
        if pe.validation is not None and pe.validation.total_return is not None:
            out.append(f"Held-out validation ({pe.validation.start}..{pe.validation.end}): total return {_pct(pe.validation.total_return)}, max drawdown {_pct(pe.validation.max_drawdown)}.")
        if pe.test is not None and pe.test.total_return is not None:
            out.append(f"Held-out test ({pe.test.start}..{pe.test.end}): total return {_pct(pe.test.total_return)}. A single held-out period is a noisy check, not a forecast.")
    if noise_summary:
        d = noise_summary.get("delta_feasible_probability")
        if d:
            out.append(
                f"With fake-backend noise the feasible-sample probability changed by {_pct(d.get('mean'))} ± {_pct(d.get('std'))} (mean ± std over {noise_summary.get('trials')} matched trials)."
            )
    if dataset_label != "downloaded historical":
        out.append(f"Data label: {dataset_label}.")
    out.append(survivorship_note)
    return out
