"""Shared API/pipeline contract for QuantFolio.

Single source of truth for request/response shapes exchanged between the
numerical pipeline, the job platform, the HTTP API and the frontend.

Rules: additive, backwards-compatible changes only (new optional fields with
defaults). Bump SCHEMA_VERSION when a persisted shape changes meaning.
All money is INR. All returns/volatilities are decimals (0.12 == 12 %),
annualised with 252 trading days unless a field name says otherwise.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
DISCLAIMER = "Research demonstration — not investment advice."

# --------------------------------------------------------------------------- #
# Enumerations (kept as Literals so they appear in OpenAPI and TS types)
# --------------------------------------------------------------------------- #
EncodingMode = Literal["selection", "lots"]
Optimizer = Literal["COBYLA", "SPSA"]
InitStrategy = Literal["random", "warm_interp"]
Estimator = Literal["mean", "cvar"]
NoiseMode = Literal["ideal", "fake_backend"]
PresetName = Literal["quick", "balanced", "research", "custom"]
ScreeningRule = Literal["none", "top_sharpe_train", "min_variance_train"]
SectorCapBasis = Literal["count", "weight"]
DatasetLabel = Literal["downloaded historical", "stale cache", "uploaded", "synthetic test"]
SolverName = Literal["qaoa", "exact", "relaxation_rounding", "simulated_annealing", "greedy"]
SolverStatus = Literal[
    "ok",  # produced a portfolio that passed the shared validator
    "infeasible",  # solver proved / certified no feasible portfolio exists
    "no_feasible_sample",  # QAOA: no observed sample satisfied the constraints (NOT proof of infeasibility)
    "no_feasible_found",  # heuristic search finished without a feasible portfolio (NOT proof)
    "unsupported",  # solver not applicable to this size / mode (reason in status_detail)
    "time_limited",  # stopped by its budget; result (if any) is best-so-far
    "failed",  # exception; redacted message in status_detail
    "skipped",  # not requested
]
JobKind = Literal["run", "experiment"]
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]
Stage = Literal[
    "queued",
    "loading_data",
    "estimating",
    "screening",
    "building_qubo",
    "transpiling",
    "optimising",
    "final_sampling",
    "baselines",
    "frontier",
    "noise",
    "experiment",
    "reporting",
    "done",
]
EventType = Literal["status", "stage", "iteration", "log", "result", "error", "heartbeat"]
IssueSeverity = Literal["error", "warning", "info"]
ConflictKind = Literal["proven_infeasible", "preflight_conflict", "resource_limit"]
LiveStatus = Literal["live", "delayed", "closed", "connecting", "offline", "off"]
QuoteKind = Literal["index", "stock"]
CandleInterval = Literal["5m", "15m", "1d"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)


# --------------------------------------------------------------------------- #
# Request: run configuration
# --------------------------------------------------------------------------- #
class DateSplit(_Model):
    """Chronological, non-overlapping segments. Estimation/screening/tuning use
    train (+validation for setting selection). Test is evaluated last, once."""

    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date

    @model_validator(mode="after")
    def _ordered(self) -> "DateSplit":
        seq = [
            self.train_start,
            self.train_end,
            self.validation_start,
            self.validation_end,
            self.test_start,
            self.test_end,
        ]
        if not (seq[0] < seq[1] < seq[2] <= seq[3] < seq[4] <= seq[5]):
            raise ValueError("date split must satisfy train_start < train_end < validation_start <= validation_end < test_start <= test_end")
        return self


class ScreeningConfig(_Model):
    """Optional training-data-only pre-screen of a large universe down to the
    encoded subset. The same subset is given to every baseline."""

    rule: ScreeningRule = "none"
    keep: int | None = Field(default=None, ge=2, le=50, description="Number of assets to encode after screening")


class SectorCaps(_Model):
    basis: SectorCapBasis = "count"
    default_max: float | None = Field(default=None, ge=0, description="Cap for every sector unless overridden; count (int) or weight fraction")
    per_sector: dict[str, float] = Field(default_factory=dict)


class LotConfig(_Model):
    """Bounded lot/weight mode. Weight of asset i = n_i / total_lots, n_i in
    [0, max_lots_per_asset] encoded with binary bits. Lot units are modelling
    choices (fractions of the budget), not exchange lot sizes."""

    total_lots: int = Field(default=4, ge=2, le=16)
    max_lots_per_asset: int = Field(default=2, ge=1, le=7)
    allow_cash_remainder: bool = Field(default=False, description="If true budget is sum(n_i) <= total_lots with slack bits; else equality")


class StartingPortfolio(_Model):
    """Disclosed starting point for transaction costs.
    type='cash'     -> initial-purchase model (every selected asset is a buy).
    type='holdings' -> current weights by symbol (must sum to <= 1; rest is cash)."""

    type: Literal["cash", "holdings"] = "cash"
    weights: dict[str, float] = Field(default_factory=dict)


class TransactionCosts(_Model):
    enabled: bool = True
    proportional_bps: float = Field(default=10.0, ge=0, le=500, description="Cost per unit traded weight, basis points")
    fixed_inr_per_trade: float = Field(default=0.0, ge=0, le=10_000)
    starting_portfolio: StartingPortfolio = Field(default_factory=StartingPortfolio)


class ConstraintsConfig(_Model):
    cardinality: int | None = Field(default=None, ge=1, le=50, description="Selection mode: exact number of assets held")
    sector_caps: SectorCaps | None = None
    target_return: float | None = Field(default=None, ge=-1, le=5, description="Annualised expected (training) return floor, decimal")
    lots: LotConfig | None = Field(default=None, description="Required when mode='lots'")
    transaction_costs: TransactionCosts = Field(default_factory=TransactionCosts)


class QaoaSettings(_Model):
    depth: int = Field(default=1, ge=1, le=5)
    optimizer: Optimizer = "COBYLA"
    init_strategy: InitStrategy = "random"
    shots: int = Field(default=512, ge=64, le=8192, description="Shots per objective evaluation during optimisation")
    final_shots: int = Field(default=2048, ge=128, le=16384, description="Shots in the declared final sampling batch")
    max_evaluations: int = Field(default=30, ge=5, le=2000)
    estimator: Estimator = "mean"
    cvar_alpha: float = Field(default=0.25, gt=0, le=1)
    seed: int = Field(default=7, ge=0, le=2**31 - 1)
    noise: NoiseMode = "ideal"
    fake_backend: str | None = Field(default=None, description="Fake backend name when noise='fake_backend'")
    penalty_scale: float = Field(default=1.0, ge=0.25, le=20, description="Multiplier on the derived penalty weights")


class BaselineSettings(_Model):
    exact: bool = True
    relaxation_rounding: bool = True
    simulated_annealing: bool = True
    greedy: bool = False
    sa_sweeps: int = Field(default=2000, ge=10, le=200_000)
    sa_restarts: int = Field(default=8, ge=1, le=200)


class RunConfig(_Model):
    symbols: list[str] = Field(min_length=2, max_length=50)
    mode: EncodingMode = "selection"
    risk_aversion: float = Field(default=1.0, ge=0, le=100, description="q in  q*w'Σw - μ'w + cost  (annualised units)")
    budget_inr: float = Field(default=1_000_000, gt=0, le=1e11)
    risk_free_rate: float = Field(default=0.065, ge=-0.05, le=0.3, description="Annual risk-free rate for Sharpe (assumption)")
    dates: DateSplit
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    qaoa: QaoaSettings = Field(default_factory=QaoaSettings)
    baselines: BaselineSettings = Field(default_factory=BaselineSettings)
    preset: PresetName = "custom"
    noise_comparison: bool = Field(default=False, description="Also re-evaluate the fixed trained parameters under fake-backend noise")
    noise_trials: int = Field(default=3, ge=1, le=20)
    timeout_seconds: int = Field(default=120, ge=10, le=3600)
    dataset_id: str | None = Field(default=None, description="Server dataset id; None = default cached dataset")

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        out = [s.strip().upper() for s in v]
        if len(set(out)) != len(out):
            raise ValueError("duplicate symbols")
        for s in out:
            if not s or len(s) > 20 or not all(c.isalnum() or c in "-&_" for c in s):
                raise ValueError(f"invalid symbol {s!r}")
        return out


class ExperimentConfig(_Model):
    """Controlled study. Each factor list is swept while others stay at base."""

    base: RunConfig
    depths: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    optimizers: list[Optimizer] = Field(default_factory=lambda: ["COBYLA", "SPSA"])
    init_strategies: list[InitStrategy] = Field(default_factory=lambda: ["random", "warm_interp"])
    seeds: list[int] = Field(default_factory=lambda: [1, 2, 3], max_length=20)
    universe_sizes: list[int] = Field(default_factory=list, description="Optional: re-run base at these encoded universe sizes")
    design: Literal["one_factor_at_a_time", "full_factorial"] = "one_factor_at_a_time"
    max_cells: int = Field(default=60, ge=1, le=2000)


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
class Issue(_Model):
    severity: IssueSeverity
    code: str
    message: str
    constraints: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ConstraintEncodingInfo(_Model):
    name: str
    kind: str
    active: bool
    explanation: str
    slack_bits: int = 0
    penalty_weight: float | None = None
    binding_note: str | None = None


class PreflightReport(_Model):
    ok: bool
    can_submit: bool
    conflict_kind: ConflictKind | None = None
    requested_symbols: list[str]
    encoded_symbols: list[str]
    screened_out: list[str] = Field(default_factory=list)
    screening_rule: ScreeningRule = "none"
    portfolio_size: int | None = None
    decision_bits: int
    slack_bits: int
    total_qubits: int
    depth: int
    shots: int
    final_shots: int
    evaluation_budget: int
    timeout_seconds: int
    memory_estimate_mb: float
    estimated_runtime_s: float | None = None
    max_qubits: int
    constraints: list[ConstraintEncodingInfo] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    dataset_id: str | None = None
    dataset_label: DatasetLabel | None = None


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
class DatasetInfo(_Model):
    dataset_id: str
    label: DatasetLabel
    source: str
    downloaded_at: datetime | None = None
    first_date: date
    last_date: date
    n_symbols: int
    sha256: str
    stale: bool = False
    stale_reason: str | None = None
    survivorship_note: str
    universe_source: str
    universe_as_of: date | None = None
    missing_data_policy: str
    issues: list[str] = Field(default_factory=list)  # data-quality findings (gaps, late listings, ...)


class SegmentInfo(_Model):
    start: date
    end: date
    n_obs: int


class EstimationInfo(_Model):
    return_type: str
    annualisation_days: int
    covariance_method: str
    shrinkage_intensity: float | None = None
    psd_corrected: bool
    min_eigenvalue_before: float
    min_eigenvalue_after: float
    train: SegmentInfo
    validation: SegmentInfo
    test: SegmentInfo
    notes: list[str] = Field(default_factory=list)


class VariableInfo(_Model):
    index: int  # qubit index (Qiskit little-endian: bitstring char at position [-1-index])
    name: str
    kind: Literal["decision", "lot_bit", "slack"]
    asset: str | None = None
    constraint: str | None = None
    coefficient: float | None = None  # value contributed by this bit (lot units / slack units)


class PenaltyInfo(_Model):
    constraint: str
    weight: float
    derivation: str


class QuboInfo(_Model):
    n_variables: int
    n_decision: int
    n_slack: int
    offset: float
    ising_offset: float
    n_linear_terms: int
    n_quadratic_terms: int
    max_abs_coefficient: float
    scale: float
    penalties: list[PenaltyInfo]
    variables: list[VariableInfo]
    objective_definition: str


class Residual(_Model):
    constraint: str
    value: float
    limit: float | None = None
    satisfied: bool
    explanation: str


class Feasibility(_Model):
    feasible: bool
    residuals: list[Residual]


class PeriodMetrics(_Model):
    start: date | None = None
    end: date | None = None
    n_obs: int = 0
    total_return: float | None = None
    annualised_return: float | None = None
    annualised_volatility: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    net_total_return: float | None = None  # total_return after the one-off transaction cost
    insufficient: bool = False
    note: str | None = None


class Holding(_Model):
    symbol: str
    sector: str
    weight: float
    amount_inr: float
    indicative_shares: int | None = None
    price_inr: float | None = None
    lots: int | None = None


class PortfolioEval(_Model):
    holdings: list[Holding]
    cash_weight: float
    cash_inr: float
    expected_return: float  # annualised gross, training estimate
    variance: float  # annualised
    volatility: float  # annualised
    cost_fraction: float  # one-off transaction cost as fraction of budget
    cost_inr: float
    net_expected_return: float
    objective: float  # economic objective (minimised): q*var - mu'w + cost
    sharpe_estimate: float | None = None
    feasibility: Feasibility
    validation: PeriodMetrics | None = None
    test: PeriodMetrics | None = None
    sector_weights: dict[str, float] = Field(default_factory=dict)


class GapInfo(_Model):
    reference: Literal["exact_optimum", "best_known"]
    reference_solver: str
    absolute: float
    relative: float | None = None
    normalisation: str


class SolverResult(_Model):
    solver: SolverName
    status: SolverStatus
    status_detail: str | None = None
    bitstring: str | None = None  # decision/lot bits only, in encoded-symbol order (char i == asset i)
    portfolio: PortfolioEval | None = None
    runtime_s: dict[str, float] = Field(default_factory=dict)
    gap: GapInfo | None = None
    notes: list[str] = Field(default_factory=list)
    work: dict[str, Any] = Field(default_factory=dict)  # evaluations, sweeps, nodes, etc.


class IterationPoint(_Model):
    evaluation: int
    energy: float  # raw estimator value of the QUBO energy at these parameters
    best_feasible_objective: float | None = None  # best economic objective among feasible samples seen so far
    feasible_fraction: float
    elapsed_s: float
    phase: Literal["warm_start_preparation", "optimisation"] = "optimisation"


class CircuitStats(_Model):
    n_qubits: int
    depth: int
    size: int
    gate_counts: dict[str, int]
    two_qubit_gates: int
    basis_gates: list[str]
    transpile_seconds: float
    optimization_level: int


class SampleRow(_Model):
    bitstring: str  # full measured register, Qiskit order (qubit 0 rightmost)
    count: int
    probability: float
    decision_bits: str  # asset order (char i == asset i)
    feasible: bool
    objective: float | None = None  # economic objective if decodable
    qubo_energy: float


class FinalBatch(_Model):
    shots: int
    seed: int
    distinct_bitstrings: int
    feasible_probability: float
    selection_policy: str
    best_bitstring: str | None = None
    top_samples: list[SampleRow]  # top N by count (full counts in artifact file)
    objective_histogram: list[dict[str, float]] = Field(default_factory=list)  # [{bin_start, bin_end, probability, feasible_probability}]
    counts_sha256: str


class QaoaDetails(_Model):
    depth: int
    optimizer: Optimizer
    optimizer_settings: dict[str, Any]
    init_strategy: InitStrategy
    init_description: str
    estimator: Estimator
    cvar_alpha: float | None = None
    primitive: str
    simulator_method: str
    noise: NoiseMode
    noise_model: str | None = None
    circuit: CircuitStats
    initial_parameters: list[float]
    optimal_parameters: list[float]
    parameter_order: list[str]
    evaluations_used: int
    evaluation_budget: int
    preparation_evaluations: int = 0
    iterations: list[IterationPoint]
    final_batch: FinalBatch
    seeds: dict[str, int]


class FrontierPoint(_Model):
    expected_return: float
    volatility: float
    kind: Literal["continuous_relaxation", "discrete_feasible", "solver"]
    label: str | None = None
    objective: float | None = None


class NoiseTrial(_Model):
    seed: int
    feasible_probability: float
    best_objective: float | None = None
    expected_return: float | None = None
    volatility: float | None = None
    runtime_s: float


class NoiseComparison(_Model):
    backend: str
    noise_model_summary: str
    shots: int
    parameters_source: str
    ideal: list[NoiseTrial]
    noisy: list[NoiseTrial]
    summary: dict[str, Any]  # mean/std/deltas with uncertainty


class RunResult(_Model):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    created_at: datetime
    implementation_version: str
    config: RunConfig
    config_hash: str
    cache_key: str
    dataset: DatasetInfo
    estimation: EstimationInfo
    encoded_symbols: list[str]
    sectors: dict[str, str]
    screening: dict[str, Any]
    qubo: QuboInfo
    qaoa: QaoaDetails | None
    solvers: list[SolverResult]
    frontier: list[FrontierPoint]
    noise_comparison: NoiseComparison | None = None
    timings_s: dict[str, float]
    software: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)  # deterministic plain-language findings
    disclaimer: str = DISCLAIMER


class ExperimentCell(_Model):
    factor: str
    depth: int
    optimizer: Optimizer
    init_strategy: InitStrategy
    seed: int
    n_assets: int
    total_qubits: int
    status: SolverStatus
    feasible_probability: float | None = None
    best_objective: float | None = None
    exact_objective: float | None = None
    gap_abs: float | None = None
    approximation_ratio: float | None = None
    evaluations: int = 0
    preparation_evaluations: int = 0
    runtime_s: float = 0.0
    validation_sharpe: float | None = None
    error: str | None = None


class ExperimentResult(_Model):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    created_at: datetime
    implementation_version: str
    config: ExperimentConfig
    dataset: DatasetInfo
    cells: list[ExperimentCell]
    summary: list[dict[str, Any]]
    selected_configuration: dict[str, Any] | None = None
    selection_rule: str
    noise_comparison: NoiseComparison | None = None
    timings_s: dict[str, float]
    software: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


# --------------------------------------------------------------------------- #
# Jobs / events / misc API shapes
# --------------------------------------------------------------------------- #
class JobError(_Model):
    code: str
    message: str


class JobProgress(_Model):
    stage: Stage = "queued"
    evaluations_done: int = 0
    evaluation_budget: int | None = None
    message: str | None = None


class JobStatus(_Model):
    run_id: str
    kind: JobKind
    status: JobState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queue_position: int | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    error: JobError | None = None
    config_hash: str
    idempotency_key: str | None = None
    cached_from: str | None = None  # run_id whose stored result this replays (not a new quantum run)
    cached_original_time: datetime | None = None
    label: str | None = None
    is_example: bool = False
    cancel_requested: bool = False  # cancellation asked for while running; final state follows
    timeout_seconds: int | None = None  # wall-clock limit enforced by the worker


class RunEvent(_Model):
    id: int
    run_id: str
    ts: datetime
    type: EventType
    stage: Stage | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RunSummary(_Model):
    run_id: str
    kind: JobKind
    status: JobState
    created_at: datetime
    finished_at: datetime | None = None
    label: str | None = None
    n_symbols: int
    total_qubits: int | None = None
    depth: int | None = None
    optimizer: str | None = None
    qaoa_status: SolverStatus | None = None
    best_objective: float | None = None
    is_example: bool = False
    cached_from: str | None = None
    error: JobError | None = None


class AssetInfo(_Model):
    symbol: str
    name: str
    sector: str
    isin: str
    first_date: date | None = None
    last_date: date | None = None
    n_obs: int = 0
    coverage: float = 0.0  # fraction of dataset dates with a valid price
    last_price_inr: float | None = None  # closing price on last_date, rupees per share
    change_1y: float | None = None  # adjusted-close change over the year to last_date (fraction; includes dividends)
    trend: list[float] = Field(default_factory=list)  # about one year of adjusted closes, sampled weekly, for a sparkline
    data_ok: bool = True
    data_note: str | None = None
    price_note: str | None = None  # load-time correction or large one-day move in this asset's prices


class Preset(_Model):
    name: PresetName
    title: str
    description: str
    max_assets: int
    qaoa: QaoaSettings
    baselines: BaselineSettings
    timeout_seconds: int
    measured_median_s: float | None = None
    measured_note: str | None = None


class Capabilities(_Model):
    max_qubits: int
    max_qubits_noise: int
    max_exact_assets: int
    depths: list[int]
    optimizers: list[Optimizer]
    init_strategies: dict[str, str]
    fake_backends: list[str]
    modes: dict[str, list[str]]  # mode -> supported constraint names
    queue_capacity: int
    ibm_configured: bool
    limits: dict[str, Any]


# --------------------------------------------------------------------------- #
# API envelope / wrapper shapes (platform workstream, additive)
# --------------------------------------------------------------------------- #
class ErrorBody(_Model):
    code: str
    message: str
    details: dict[str, Any] | list[Any] | None = None
    request_id: str | None = None


class ErrorEnvelope(_Model):
    """Every non-2xx JSON response from /api has this shape."""

    error: ErrorBody


class HealthResponse(_Model):
    status: Literal["ok"]
    version: str
    schema_version: str = SCHEMA_VERSION


class ReadyResponse(_Model):
    ready: bool
    checks: dict[str, bool]
    detail: dict[str, str] = Field(default_factory=dict)


class AssetsResponse(_Model):
    dataset: DatasetInfo
    assets: list[AssetInfo]


class DataStatusResponse(_Model):
    active: DatasetInfo
    available: list[DatasetInfo] = Field(default_factory=list)


class PresetsResponse(_Model):
    presets: list[Preset]
    default_config: RunConfig


class ExampleResponse(_Model):
    run_id: str | None
    label: str | None = None
    recorded_at: datetime | None = None
    detail: str | None = None


class IbmStatus(_Model):
    configured: bool
    detail: str
    instance_configured: bool = False
    checked: bool = False  # true only after an explicit, user-requested access check
    backends: list[str] = Field(default_factory=list)


class LiveQuote(_Model):
    """One live price for the ticker. Display only: live prices never enter a run."""

    symbol: str  # app symbol ("RELIANCE") or Yahoo index symbol ("^NSEI")
    label: str  # "RELIANCE", "NIFTY 50"
    kind: QuoteKind
    price: float | None = None  # rupees (index points for an index)
    change: float | None = None  # versus prev_close
    change_pct: float | None = None  # percent, e.g. 1.25 = +1.25%
    prev_close: float | None = None
    at: datetime | None = None  # exchange time of the last tick (None for a snapshot or stored close)
    delayed: bool = False  # this feed runs behind the exchange (Yahoo's SENSEX is about 15 minutes late)


class LiveQuotesResponse(_Model):
    """`/api/market/live` body and each `/api/market/stream` frame (a `delta` frame lists only changed quotes)."""

    status: LiveStatus  # live = ticks streaming; delayed = market open, snapshots only; closed; connecting;
    # offline = Yahoo unreachable, stored closes; off = live prices disabled, stored closes
    source: str  # "Yahoo Finance" or "Stored dataset"
    updated_at: datetime | None = None
    market_open: bool = False  # NSE session clock (09:15-15:30 IST, Monday to Friday)
    as_of: date | None = None  # trading day of the prices
    note: str | None = None
    seq: int = 0
    quotes: list[LiveQuote] = Field(default_factory=list)


class Candle(_Model):
    """One price bar: open, high, low and close over one interval."""

    t: datetime  # start of the bar, exchange time (IST)
    o: float
    h: float
    l: float
    c: float


class CandlesResponse(_Model):
    """`/api/market/candles` body. Display only: candles never enter a run, and none are ever made up."""

    status: LiveStatus  # live / delayed = the session is trading; closed = last session; offline = Yahoo
    # unreachable, no candles; off = live prices disabled, no candles
    symbol: str  # app symbol ("RELIANCE") or Yahoo index symbol ("^NSEI")
    label: str  # "RELIANCE", "NIFTY 50"
    interval: CandleInterval
    as_of: date | None = None  # trading day of the last candle
    prev_close: float | None = None  # close of the session before `as_of`
    source: str | None = None  # "Yahoo Finance"; None when there are no candles
    updated_at: datetime | None = None  # when the candles were fetched
    candles: list[Candle] = Field(default_factory=list)

