"""Forward-chain alert-window facts through diagnostic rules to hypotheses.

Rules are configurable screening heuristics, not trained causal probabilities or
component diagnoses. Missing, stale and sparse signals never count as evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median


@dataclass(frozen=True)
class ReasoningConfig:
    min_window_coverage: float = .75
    max_sample_gap_s: float = 5.0
    min_samples: int = 8
    trim_screen_pct: float = 15.0
    idle_rpm_max: float = 1000.0
    raised_rpm_min: float = 1800.0
    idle_trim_difference_pct: float = 8.0
    coolant_warm_min_c: float = 70.0
    warmup_elapsed_s: float = 600.0
    running_voltage_min_v: float = 12.5
    # Set only from the particular vehicle's service specification.
    fuel_pressure_min_kpa: float | None = None

    def __post_init__(self):
        if not 0 < self.min_window_coverage <= 1 or self.min_samples < 2:
            raise ValueError("Reasoning coverage must be in (0, 1] and min_samples at least 2")
        positive = (self.max_sample_gap_s, self.trim_screen_pct, self.idle_rpm_max,
                    self.raised_rpm_min, self.idle_trim_difference_pct, self.coolant_warm_min_c,
                    self.warmup_elapsed_s, self.running_voltage_min_v)
        if any(not math.isfinite(v) or v <= 0 for v in positive):
            raise ValueError("Reasoning thresholds must be finite and positive")
        if self.fuel_pressure_min_kpa is not None and (not math.isfinite(self.fuel_pressure_min_kpa)
                                                       or self.fuel_pressure_min_kpa <= 0):
            raise ValueError("Vehicle fuel-pressure minimum must be finite and positive")


@dataclass(frozen=True)
class ForwardRule:
    name: str
    premises: tuple[str, ...]
    conclusion: str


# Rules only consume asserted facts and add new facts. No goal or hypothesis is
# queried to decide which readings to inspect.
FORWARD_RULES = (
    ForwardRule("warm_positive_correction", ("warm_closed_loop", "positive_trim"), "positive_correction_pattern"),
    ForwardRule("warm_negative_correction", ("warm_closed_loop", "negative_trim"), "negative_correction_pattern"),
    ForwardRule("idle_biased_positive_correction", ("positive_correction_pattern", "idle_trim_bias"),
                "hypothesis:intake_air_leak_pattern"),
    ForwardRule("positive_correction_low_pressure", ("positive_correction_pattern", "low_fuel_pressure"),
                "hypothesis:fuel_delivery_pressure"),
    ForwardRule("positive_correction_lean_code", ("positive_correction_pattern", "lean_dtc"),
                "hypothesis:lean_system_evidence"),
    ForwardRule("negative_correction_rich_code", ("negative_correction_pattern", "rich_dtc"),
                "hypothesis:rich_mixture_pattern"),
    ForwardRule("negative_correction_rich_lambda", ("negative_correction_pattern", "rich_lambda"),
                "hypothesis:rich_mixture_pattern"),
    ForwardRule("cold_after_warmup", ("cold_after_warmup",), "hypothesis:cold_running_pattern"),
    ForwardRule("low_voltage_with_code", ("low_running_voltage", "low_voltage_dtc"),
                "hypothesis:charging_voltage_pattern"),
)


def forward_chain(initial_facts, rules=FORWARD_RULES):
    """Apply every enabled rule until no further conclusions can be asserted."""
    facts = set(initial_facts)
    agenda = list(initial_facts)
    fired = set()
    trace = []
    while agenda:
        agenda.pop(0)
        for rule in rules:
            if rule.name in fired or not all(premise in facts for premise in rule.premises):
                continue
            fired.add(rule.name)
            trace.append({"rule": rule.name, "premises": list(rule.premises), "conclusion": rule.conclusion})
            if rule.conclusion not in facts:
                facts.add(rule.conclusion)
                agenda.append(rule.conclusion)
    return facts, trace


def _stable_values(samples, name, start, end, config):
    points = [(s["time_s"], s["values"][name]) for s in samples
              if start <= s["time_s"] <= end and name in s["values"]]
    if len(points) < config.min_samples or end <= start:
        return None
    times = [p[0] for p in points]
    if (times[-1] - times[0] < (end - start) * config.min_window_coverage or
            any(b - a > config.max_sample_gap_s for a, b in zip(times, times[1:]))):
        return None
    return [p[1] for p in points]


def _closed_loop_warm(samples, start, end, config):
    loop = _stable_values(samples, "closed_loop", start, end, config)
    coolant = _stable_values(samples, "coolant_c", start, end, config)
    return loop is not None and all(v == 1 for v in loop) and coolant is not None and median(coolant) >= config.coolant_warm_min_c


def _combined_trim(samples, start, end, config):
    points = []
    for s in samples:
        v = s["values"]
        if start <= s["time_s"] <= end and "stft1_pct" in v and "ltft1_pct" in v:
            points.append((s["time_s"], v["stft1_pct"] + v["ltft1_pct"], v.get("rpm")))
    if len(points) < config.min_samples or end <= start:
        return None
    times = [p[0] for p in points]
    if (times[-1] - times[0] < (end - start) * config.min_window_coverage or
            any(b - a > config.max_sample_gap_s for a, b in zip(times, times[1:]))):
        return None
    return points


def rank_reasons(samples, alert, fuel_method, config=ReasoningConfig()):
    """Use only readings from the alert's frozen time window."""
    start, end = alert["evidence_start_s"], alert["detected_at_s"]
    window = [s for s in samples if start <= s["time_s"] <= end]
    codes = sorted({code for s in window for code in s.get("trouble_codes", ())})
    origins = sorted({origin for s in window for origin in s.get("provenance", {}).values()})
    derived = fuel_method == "derived_maf_trims"
    data_checks = []
    if fuel_method != "measured_fuel_sensor":
        data_checks.append("Confirm the fuel drift with an independent fuel-rate or consumption measurement.")
    if "synthetic" in origins:
        data_checks.append("Synthetic input is only a scenario test; verify with vehicle measurements.")
    if not codes:
        data_checks.append("Read active and pending diagnostic trouble codes from the same vehicle.")

    reasons = []

    def add(key, title, support, missing, next_check, dependent=False, independent_support=0):
        reasons.append({"reason_code": key, "reason": title, "status": "possible",
                        "supporting_evidence": support, "contradicting_evidence": [],
                        "missing_evidence": missing, "next_check": next_check,
                        "fuel_estimate_dependency": dependent,
                        "independent_support_count": independent_support,
                        "evidence_strength": ("limited" if dependent or fuel_method != "measured_fuel_sensor"
                                              or "synthetic" in origins or independent_support == 0
                                              else "corroborated_screen")})

    # Assert facts from observed signals first; no hypothesis is used to fetch data.
    initial_facts = {"fuel_alert"}
    warm_loop = _closed_loop_warm(window, start, end, config)
    if warm_loop:
        initial_facts.add("warm_closed_loop")
    trims = _combined_trim(window, start, end, config)
    trim_median = median([p[1] for p in trims]) if trims else None
    if trim_median is not None:
        if trim_median >= config.trim_screen_pct:
            initial_facts.add("positive_trim")
        if trim_median <= -config.trim_screen_pct:
            initial_facts.add("negative_trim")
    idle = [p[1] for p in trims if p[2] is not None and p[2] <= config.idle_rpm_max] if trims else []
    raised = [p[1] for p in trims if p[2] is not None and p[2] >= config.raised_rpm_min] if trims else []
    if len(idle) >= config.min_samples and len(raised) >= config.min_samples and median(idle) - median(raised) >= config.idle_trim_difference_pct:
        initial_facts.add("idle_trim_bias")
    pressure = (_stable_values(window, "fuel_pressure_kpa", start, end, config)
                if config.fuel_pressure_min_kpa is not None else None)
    if pressure and median(pressure) < config.fuel_pressure_min_kpa:
        initial_facts.add("low_fuel_pressure")
    if any(c in codes for c in ("P0171", "P0174")):
        initial_facts.add("lean_dtc")
    if any(c in codes for c in ("P0172", "P0175")):
        initial_facts.add("rich_dtc")
    lambda_values = _stable_values(window, "lambda", start, end, config)
    if lambda_values and median(lambda_values) < .97:
        initial_facts.add("rich_lambda")
    coolant = _stable_values(window, "coolant_c", start, end, config)
    elapsed = max((s.get("trip_elapsed_s", 0) for s in window), default=0)
    if coolant and elapsed >= config.warmup_elapsed_s and median(coolant) < config.coolant_warm_min_c:
        initial_facts.add("cold_after_warmup")
    voltage = _stable_values(window, "battery_v", start, end, config)
    rpm = _stable_values(window, "rpm", start, end, config)
    if voltage and rpm and median(rpm) > 800 and median(voltage) < config.running_voltage_min_v:
        initial_facts.add("low_running_voltage")
    if "P0562" in codes:
        initial_facts.add("low_voltage_dtc")

    inferred_facts, trace = forward_chain(sorted(initial_facts))
    if "hypothesis:intake_air_leak_pattern" in inferred_facts:
        add("intake_air_leak_pattern", "Possible unmetered intake air",
            [f"Warm closed-loop bank 1 trim median {trim_median:.1f}%",
             f"Idle trim median {median(idle):.1f}% versus raised-RPM median {median(raised):.1f}%"],
            ["Intake leak inspection or smoke test"],
            "Inspect intake plumbing and compare trims after repairing any leak.", derived)
    if "hypothesis:fuel_delivery_pressure" in inferred_facts:
        add("fuel_delivery_pressure", "Possible fuel delivery restriction",
            [f"Warm closed-loop bank 1 trim median {trim_median:.1f}%",
             f"Fuel pressure median {median(pressure):.1f} kPa below configured vehicle minimum {config.fuel_pressure_min_kpa:.1f} kPa"],
            ["Fuel pressure test under manufacturer-specified conditions"],
            "Confirm pressure under load against this vehicle's specification; inspect pump, filter and regulator.",
            derived, independent_support=1)
    if "hypothesis:lean_system_evidence" in inferred_facts:
        add("lean_system_evidence", "Possible lean air/fuel imbalance",
            [f"Warm closed-loop bank 1 trim median {trim_median:.1f}%",
             "Lean-system diagnostic code reported in alert window"],
            ["Intake leak and fuel-pressure tests to distinguish causes"],
            "Verify the code's status, then test intake leaks and fuel delivery.", derived, independent_support=1)
    if "hypothesis:rich_mixture_pattern" in inferred_facts:
        rich_code, rich_exhaust = "rich_dtc" in initial_facts, "rich_lambda" in initial_facts
        support = [f"Warm closed-loop bank 1 trim median {trim_median:.1f}%"]
        if rich_code:
            support.append("Rich-system diagnostic code reported in alert window")
        if rich_exhaust:
            support.append(f"Measured lambda median {median(lambda_values):.2f} in alert window")
        add("rich_mixture_pattern", "Possible rich mixture or over-fueling",
            support, ["Injector, fuel-pressure and air-measurement checks"],
            "Verify the code and exhaust reading; inspect injectors, pressure regulation and air measurement.",
            derived, independent_support=int(rich_code) + int(rich_exhaust))
    if "hypothesis:cold_running_pattern" in inferred_facts:
        add("cold_running_pattern", "Possible cold-running or coolant-temperature issue",
            [f"Coolant median {median(coolant):.1f} C after {elapsed:.0f} s of this trip"],
            ["Independent coolant-temperature check and vehicle-specific warm range"],
            "Verify actual coolant temperature and inspect the thermostat and temperature sensor.", independent_support=1)
    if "hypothesis:charging_voltage_pattern" in inferred_facts:
        add("charging_voltage_pattern", "Possible charging or low-voltage issue",
            [f"Running voltage median {median(voltage):.2f} V",
             "Low-voltage diagnostic code reported in alert window"],
            ["Charging-system measurement under manufacturer-specified conditions"],
            "Measure charging voltage and inspect connections before attributing fuel drift to an electrical fault.",
            independent_support=2)

    if not warm_loop:
        data_checks.append("Collect a sustained warm, closed-loop fuel-trim window before using trim patterns.")
    # Inspection order is a transparent heuristic, not a calibrated fault likelihood.
    reasons.sort(key=lambda r: (-r["independent_support_count"], r["fuel_estimate_dependency"],
                                -len(r["supporting_evidence"]), r["reason_code"]))
    for index, reason in enumerate(reasons, 1):
        reason["inspection_rank"] = index
    return {"status": "possible_reasons" if reasons else "insufficient_diagnostic_evidence",
            "inference_method": "forward_chaining", "knowledge_base_version": 1,
            "asserted_facts": sorted(initial_facts),
            "rule_trace": trace,
            "evidence_window_s": [start, end], "likely_reasons": reasons,
            "data_checks": data_checks, "reported_trouble_codes": codes,
            "source_provenance": origins, "confirmed_cause": None,
            "limitations": "Rule screens indicate possible systems to inspect; they are not calibrated probabilities or confirmed repairs."}
