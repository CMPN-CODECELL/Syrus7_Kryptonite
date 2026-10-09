"""Constraints, QUBO and Ising encodings checked by exhaustive enumeration (§5, §10)."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import all_bits, make_config, problem_of

from quantfolio.constraints import SectorCaps as SectorCapsC
from quantfolio.constraints import TargetReturn
from quantfolio.contracts import LotConfig, SectorCaps
from quantfolio.portfolio import bounded_coefficients, canonical_bits, objective_quadform
from quantfolio.qubo import build_qubo, count_qubits

CASES = ["selection_count_cap", "selection_weight_cap", "lots_budget_eq", "lots_cash_remainder"]


@pytest.mark.parametrize("name", CASES)
def test_penalty_zero_iff_feasible_and_energy_decomposition(cases, name):
    p = problem_of(cases[name])
    q = build_qubo(p)
    assert q.n <= 18
    Z = all_bits(q.n)
    X = Z[:, : q.n_decision]
    E = q.energy(Z)
    f = q.objective.energy(X)
    # objective quadratic form reproduces the portfolio objective (incl. transaction costs) exactly
    np.testing.assert_allclose(f, p.objective(p.enc.weights(X)), atol=1e-9)
    # QUBO energy = objective + P * penalty (+ the QUBO offset, which is folded into `offset`)
    pen = (E - f) / q.penalty.weight  # integer-valued in penalty units (up to coefficient quantisation)
    tol = 1e-6  # penalties are integers: 0 when satisfied, >= 1 when violated
    assert pen.min() >= -tol
    # per decision vector: min over slack of the penalty
    keys = X @ (1 << np.arange(q.n_decision))
    min_pen = np.full(1 << q.n_decision, np.inf)
    np.minimum.at(min_pen, keys, pen)
    Xd = all_bits(q.n_decision)
    kd = Xd @ (1 << np.arange(q.n_decision))
    feas = p.feasible_mask(Xd)
    assert feas.any() and (~feas).any(), "fixture should contain feasible and infeasible portfolios"
    assert np.all(np.abs(min_pen[kd][feas]) <= tol), "feasible portfolio with nonzero minimum penalty"
    assert np.all(min_pen[kd][~feas] >= 1 - tol), "infeasible portfolio with penalty < 1 (integer constraints)"
    # complete_slack attains the minimum
    np.testing.assert_allclose(q.decision_energy(Xd), q.objective.energy(Xd) + q.penalty.weight * min_pen[kd], rtol=1e-9, atol=1e-6 * q.penalty.weight)


@pytest.mark.parametrize("name", CASES)
def test_ground_state_is_exact_constrained_optimum(cases, name):
    """Penalty weight is large enough: the QUBO minimum decodes to the best feasible portfolio."""
    p = problem_of(cases[name])
    q = build_qubo(p)
    Z = all_bits(q.n)
    z = Z[int(np.argmin(q.energy(Z)))]
    x = z[: q.n_decision]
    assert p.feasible_mask(x)[0]
    Xd = all_bits(q.n_decision)
    feas = p.feasible_mask(Xd)
    f_opt = p.objective(p.enc.weights(Xd[feas])).min()
    assert abs(p.objective(p.enc.weights(x)) - f_opt) <= 1e-9


@pytest.mark.parametrize("name", CASES)
def test_ising_and_pauli_operator_match_qubo(cases, name):
    p = problem_of(cases[name])
    q = build_qubo(p)
    Z = all_bits(q.n)
    h, J, off = q.ising()
    S = 1 - 2 * Z  # x = (1 - z)/2  <=>  z = +1 for bit 0
    Ei = off + S @ h + sum(v * S[:, i] * S[:, j] for (i, j), v in J.items())
    np.testing.assert_allclose(Ei, q.energy(Z), atol=1e-8)
    # SparsePauliOp diagonal in the computational basis, Qiskit little-endian: basis index k has qubit q = bit q of k
    diag = np.real(q.to_sparse_pauli_op().to_matrix(sparse=True).diagonal())
    k = np.arange(1 << q.n)
    Zk = ((k[:, None] >> np.arange(q.n)) & 1).astype(np.int64)
    np.testing.assert_allclose(diag + off, q.energy(Zk), atol=1e-8)


def test_bit_order_little_endian(cases):
    q = build_qubo(problem_of(cases["selection_count_cap"]))
    s = "0" * (q.n - 1) + "1"
    z = q.bits_from_string(s)
    assert z[0] == 1 and z[1:].sum() == 0  # variable 0 is the rightmost character
    assert q.names[0].startswith("x[") and q.kinds[0] == "decision"
    rng = np.random.default_rng(0)
    for _ in range(20):
        z = rng.integers(0, 2, q.n)
        assert np.array_equal(q.bits_from_string(q.string_from_bits(z)), z)
    with pytest.raises(ValueError):
        q.bits_from_string("01")


@pytest.mark.parametrize("m", range(0, 41))
def test_bounded_slack_coefficients_cover_exact_range(m):
    coefs = bounded_coefficients(m)
    assert sum(coefs) == m
    for v in range(m + 1):
        bits = canonical_bits(v, coefs)
        assert int(np.dot(bits, coefs)) == v


@pytest.mark.parametrize("name", CASES)
def test_slack_ranges_match_constraint_bounds(cases, name):
    p = problem_of(cases[name])
    q = build_qubo(p)
    n_dec, n_slack, per = count_qubits(p)
    assert (n_dec, n_dec + n_slack) == (q.n_decision, q.n)
    Xd = all_bits(q.n_decision)
    feas = p.feasible_mask(Xd)
    for b in q.blocks:
        if b.form.sense not in ("<=", ">="):
            assert b.indices == []
            continue
        lhs = b.form.lhs(Xd[feas])
        need = (b.form.rhs - lhs) if b.form.sense == "<=" else (lhs - b.form.rhs)
        # bounded slack: range [0, slack_max] exactly, covering the slack every feasible portfolio needs
        assert sum(b.coefs) == b.form.slack_max()
        assert need.min() >= 0 and need.max() <= b.form.slack_max()


def test_constraint_masks_match_definitions(cases):
    p = problem_of(cases["selection_count_cap"])
    X = all_bits(p.n)
    W = p.enc.weights(X)
    fs = np.array([s == "Financial Services" for s in p.sectors])
    target = next(c.target for c in p.constraints if isinstance(c, TargetReturn))
    expect = (X.sum(1) == 3) & (X[:, fs].sum(1) <= 1) & (W @ p.mu >= target - 1e-12)
    assert np.array_equal(p.feasible_mask(X), expect)
    bad = np.zeros(p.n, dtype=np.int64)
    bad[:3] = 1  # three Financial Services stocks, K satisfied, sector cap violated
    res = {r.constraint: r for r in p.residuals(bad)}
    assert res["sector_cap:Financial Services"].satisfied is False
    assert res["cardinality"].satisfied is True

    pl = problem_of(cases["lots_cash_remainder"])
    Xl = all_bits(pl.enc.n_decision)
    units = pl.enc.lots(Xl)
    t = next(c.target for c in pl.constraints if isinstance(c, TargetReturn))
    # weight cap 0.5 on Financial Services = at most floor(0.5 * 4) = 2 of the 4 lots in HDFCBANK + ICICIBANK
    expect_l = (units.sum(1) <= 4) & (units <= 3).all(1) & (units[:, :2].sum(1) <= 2) & (pl.enc.weights(Xl) @ pl.mu >= t - 1e-12)
    assert np.array_equal(pl.feasible_mask(Xl), expect_l)
    sc = next(c for c in pl.constraints if isinstance(c, SectorCapsC))
    assert sc.basis == "weight" and sc.unit_limit(pl, 0.5) == 2


def test_transaction_costs_and_budget_accounting(cases):
    from quantfolio.portfolio import evaluate

    p = problem_of(cases["selection_count_cap"])  # holdings HDFCBANK 0.5, TCS 0.5; 25 bps + INR 20 per trade
    x = np.zeros(p.n, dtype=np.int64)
    x[[p.symbols.index("TCS"), p.symbols.index("INFY"), p.symbols.index("ITC")]] = 1
    w = p.enc.weights(x)
    traded = np.abs(w - p.w0)
    expect = 25e-4 * traded.sum() + (20.0 / 1_000_000) * int((traded > 1e-9).sum())
    assert abs(p.cost(w) - expect) < 1e-15
    pe = evaluate(p, x)
    assert abs(sum(h.weight for h in pe.holdings) + pe.cash_weight - 1) < 1e-12
    assert abs(pe.cost_inr - round(expect * 1_000_000, 2)) < 0.01
    for h in pe.holdings:
        assert h.indicative_shares is not None and h.indicative_shares * h.price_inr <= h.amount_inr + 1e-6

    pl = problem_of(cases["lots_cash_remainder"])
    xl = pl.enc.bits_from_units(np.array([1, 1, 0]))
    pel = evaluate(pl, xl)
    assert abs(pel.cash_weight - 0.5) < 1e-12  # 2 of 4 lots invested
    assert [h.lots for h in pel.holdings] == [1, 1]


def test_quadratic_objective_with_costs_from_holdings_is_exact():
    """Fixed per-trade fees and |w - w0| are non-quadratic in w but exact as a pseudo-Boolean function of the bits."""
    cfg = make_config(["HDFCBANK", "TCS", "ITC"], mode="lots", K=None, lots=LotConfig(total_lots=6, max_lots_per_asset=3), holdings={"HDFCBANK": 1 / 3, "ITC": 0.5})
    p = problem_of(cfg)
    obj = objective_quadform(p)
    X = all_bits(p.enc.n_decision)
    np.testing.assert_allclose(obj.energy(X), p.objective(p.enc.weights(X)), atol=1e-9)


def test_weight_cap_converted_to_integer_units():
    p = problem_of(make_config(K=4, sector_caps=SectorCaps(basis="weight", per_sector={"Financial Services": 0.6})))
    X = all_bits(p.n)
    fs = np.array([s == "Financial Services" for s in p.sectors])
    # 0.6 of budget with 1/4 per holding -> at most 2 holdings (floor)
    assert np.array_equal(p.feasible_mask(X), (X.sum(1) == 4) & (X[:, fs].sum(1) <= 2))
