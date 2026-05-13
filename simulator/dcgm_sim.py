#!/usr/bin/env python3
"""
DCGM metric simulator.
Pushes all 9 DCGM metrics for 10 nodes × 8 GPUs to Prometheus pushgateway
every PUSH_INTERVAL seconds.

Pre-failure signal patterns (from spec):
  S01 gpu03/GPU0  GPU_HARDWARE:    ECC_SBE rising rate + ECC_DBE/XID at T-5m
  S02 gpu01/GPU0  NCCL:            NVLINK_CRC incrementing from T-90m (bursty)
  S04 gpu07/GPU0  THERMAL:         GPU_TEMP 72→86°C with false-recovery dip at midpoint
                                   SM_CLOCK 1410→1185 MHz
  S14 gpu05/GPU0  NEAR-MISS:       ECC_SBE rises to 15/hr peak at T-60m, drops to 2/hr
                                   Never crosses 30/hr threshold; job completes normally

Noise model:
  - All healthy nodes carry cumulative SBE background noise (~1 error/hr per GPU)
  - GPU_TEMP: ±2°C jitter every tick for all nodes
  - SM_CLOCK: ±10 MHz jitter every tick for all nodes
  - GPU_UTIL / POWER / MEM_COPY_UTIL: ±3% multiplicative variance
  - S01 ECC_SBE curve: ±20% jitter per tick
  - S02 NVLINK_CRC: ~15% chance of a zero burst reading each tick
  - S04 GPU_TEMP: triangular false-recovery dip of −3°C at the midpoint of the rise
"""

import os
import random
import time

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

PUSHGATEWAY   = os.environ.get("PUSHGATEWAY_URL", "http://localhost:9091")
PUSH_INTERVAL = int(os.environ.get("PUSH_INTERVAL", 30))
CYCLE         = 14400   # 4-hour cycle in seconds

MODEL_NAME = "A100-SXM4-80GB"
NODES      = [f"gpu{i:02d}" for i in range(1, 11)]
GPU_RANGE  = range(8)

# Failure / scenario offsets within cycle (seconds) — shared with slurm_log_sim.py
S01_T = 10800   # GPU_HARDWARE  gpu03/GPU0
S02_T = 11700   # NCCL          gpu01/GPU0
S04_T = 12600   # THERMAL       gpu07/GPU0
S14_T = 13800   # NEAR-MISS     gpu05/GPU0  (job completes normally)

# S04 false-recovery dip: triangular −3°C centred at midpoint of the 45-min rise
_DIP_CENTER = S04_T - 1350    # T − 22.5 min
_DIP_HALF_W = 300             # ±5 min either side
_DIP_DEPTH  = 3.0             # °C

# ---------------------------------------------------------------------------
# Cumulative SBE background noise state
# Tracks per-(node, gpu) accumulated single-bit errors for all healthy nodes.
# Incremented occasionally (~1 event/hr on average) rather than every tick.
# ---------------------------------------------------------------------------
_sbe_noise: dict[tuple[str, int], float] = {}


def _init_sbe_noise() -> None:
    for node in NODES:
        for gpu in GPU_RANGE:
            _sbe_noise[(node, gpu)] = float(random.randint(0, 5))


def _tick_sbe_noise() -> None:
    """Advance cumulative SBE background. Called once per push cycle."""
    p = PUSH_INTERVAL / 3600.0      # probability of an event this tick at 1/hr rate
    for node in NODES:
        for gpu in GPU_RANGE:
            if random.random() < p:
                _sbe_noise[(node, gpu)] = _sbe_noise.get((node, gpu), 0.0) + random.randint(0, 2)


# ---------------------------------------------------------------------------
# Metric value functions  (node, gpu_index, cycle_t) → float
# ---------------------------------------------------------------------------

def _ecc_sbe(node: str, gpu: int, t: float) -> float:
    # --- S01: gpu03/GPU0, three-phase rising rate with ±20% jitter ---
    if node == "gpu03" and gpu == 0:
        t0 = max(S01_T - 3 * 3600, 0)
        t1 = S01_T - 2 * 3600
        t2 = S01_T - 90 * 60
        if t < t0:
            base = 0.0
        elif t < t1:
            base = (t - t0) * 5.0 / 3600.0
        elif t < t2:
            p1  = (t1 - t0) * 5.0 / 3600.0
            base = p1 + (t - t1) * 18.0 / 3600.0
        elif t < S01_T:
            p1  = (t1 - t0) * 5.0 / 3600.0
            p2  = (t2 - t1) * 18.0 / 3600.0
            base = p1 + p2 + (t - t2) * 67.0 / 3600.0
        else:
            p1  = (t1 - t0) * 5.0 / 3600.0
            p2  = (t2 - t1) * 18.0 / 3600.0
            p3  = (S01_T - t2) * 67.0 / 3600.0
            base = p1 + p2 + p3
        return max(0.0, base * random.uniform(0.8, 1.2))

    # --- S14: gpu05/GPU0, near-miss — rises to 15/hr at T-60m, drops to 2/hr ---
    if node == "gpu05" and gpu == 0:
        onset = S14_T - 5400    # T − 90 min: start of rise
        peak  = S14_T - 3600    # T − 60 min: rate = 15/hr (peak)
        bg    = _sbe_noise.get((node, gpu), 0.0)
        if onset <= t < peak:
            elapsed = t - onset
            # Rate rises 0 → 15/hr over 1800s; cumulative = integral
            extra = 15.0 * elapsed * elapsed / (2.0 * 1800.0 * 3600.0)
            return bg + extra
        elif peak <= t <= S14_T:
            p1_total  = 15.0 * 1800.0 / (2.0 * 3600.0)    # 3.75 errors
            ed        = t - peak                             # elapsed since peak
            # Rate drops 15 → 2/hr over 3600s
            additional = (15.0 * ed - 13.0 * ed * ed / (2.0 * 3600.0)) / 3600.0
            return bg + p1_total + additional
        # Outside S14 window: fall through to background

    # --- All other nodes: cumulative background noise ---
    return _sbe_noise.get((node, gpu), 0.0)


def _ecc_dbe(node: str, gpu: int, t: float) -> float:
    if node == "gpu03" and gpu == 0 and t >= S01_T - 5 * 60:
        return 1.0
    return 0.0


def _xid(node: str, gpu: int, t: float) -> float:
    if node == "gpu03" and gpu == 0 and t >= S01_T - 5 * 60:
        return 48.0
    return 0.0


def _nvlink_crc(node: str, gpu: int, t: float) -> float:
    if node != "gpu01" or gpu != 0:
        return 0.0
    onset = S02_T - 90 * 60
    if t < onset:
        return 0.0
    # Bursty: ~15% of ticks return 0 (packet errors are not perfectly monotonic)
    if random.random() < 0.15:
        return 0.0
    elapsed = t - onset
    return elapsed * 2.0 / 60.0   # +2/min nominal, non-zero ticks


def _gpu_temp(node: str, gpu: int, t: float) -> float:
    if node == "gpu07" and gpu == 0:
        onset = S04_T - 45 * 60
        if t < onset:
            return 72.0 + random.uniform(-2.0, 2.0)
        if t < S04_T:
            progress  = (t - onset) / (45.0 * 60.0)
            temp      = 72.0 + progress * 14.0
            # False-recovery dip: triangular −3°C centred at midpoint of rise
            dist = t - _DIP_CENTER
            if abs(dist) <= _DIP_HALF_W:
                temp -= _DIP_DEPTH * (1.0 - abs(dist) / _DIP_HALF_W)
            return temp + random.uniform(-0.5, 0.5)
        return 86.0 + random.uniform(-0.5, 0.5)
    # All other nodes: normal range ± jitter
    return random.uniform(65.0, 78.0) + random.uniform(-2.0, 2.0)


def _sm_clock(node: str, gpu: int, t: float) -> float:
    jitter = random.uniform(-10.0, 10.0)
    if node == "gpu07" and gpu == 0:
        onset = S04_T - 40 * 60
        if t < onset:
            return 1410.0 + jitter
        if t < S04_T:
            progress = (t - onset) / (40.0 * 60.0)
            return 1410.0 - progress * 225.0 + jitter
        return 1185.0 + jitter
    return 1410.0 + jitter


def _gpu_util(_node: str, _gpu: int, _t: float) -> float:
    return random.uniform(80.0, 95.0) * random.uniform(0.97, 1.03)


def _power_usage(_node: str, _gpu: int, _t: float) -> float:
    return random.uniform(300.0, 400.0) * random.uniform(0.97, 1.03)


def _mem_copy_util(_node: str, _gpu: int, _t: float) -> float:
    return random.uniform(40.0, 80.0) * random.uniform(0.97, 1.03)


# ---------------------------------------------------------------------------
# Metric registry — created once, label values updated each push.
# ---------------------------------------------------------------------------

METRIC_DEFS = [
    ("DCGM_FI_DEV_GPU_UTIL",                          "GPU utilization %",                     _gpu_util),
    ("DCGM_FI_DEV_ECC_SBE_VOL_TOTAL",                 "ECC single-bit error count (volatile)",  _ecc_sbe),
    ("DCGM_FI_DEV_ECC_DBE_VOL_TOTAL",                 "ECC double-bit error count (volatile)",  _ecc_dbe),
    ("DCGM_FI_DEV_GPU_TEMP",                           "GPU temperature °C",                    _gpu_temp),
    ("DCGM_FI_DEV_SM_CLOCK",                           "SM clock frequency MHz",                _sm_clock),
    ("DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_COUNT_TOTAL", "NVLink CRC flit error count",           _nvlink_crc),
    ("DCGM_FI_DEV_XID_ERRORS",                        "XID error value",                       _xid),
    ("DCGM_FI_DEV_POWER_USAGE",                       "GPU power draw W",                      _power_usage),
    ("DCGM_FI_DEV_MEM_COPY_UTIL",                     "Memory copy engine utilization %",      _mem_copy_util),
]

LABELS = ["hostname", "gpu", "modelName"]


def build_registry() -> tuple[CollectorRegistry, dict]:
    registry = CollectorRegistry()
    gauges: dict[str, Gauge] = {}
    for name, doc, _ in METRIC_DEFS:
        gauges[name] = Gauge(name, doc, LABELS, registry=registry)
    return registry, gauges


def push_metrics(registry: CollectorRegistry, gauges: dict) -> None:
    _tick_sbe_noise()
    cycle_t = time.time() % CYCLE

    for node in NODES:
        for gpu_idx in GPU_RANGE:
            lv = {"hostname": node, "gpu": str(gpu_idx), "modelName": MODEL_NAME}
            for name, _doc, fn in METRIC_DEFS:
                gauges[name].labels(**lv).set(fn(node, gpu_idx, cycle_t))

    push_to_gateway(PUSHGATEWAY, job="dcgm_simulator", registry=registry)


def main() -> None:
    _init_sbe_noise()
    registry, gauges = build_registry()

    while True:
        now = time.time()
        try:
            push_metrics(registry, gauges)
            print(
                f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}] "
                f"Pushed DCGM metrics  cycle_t={now % CYCLE:.0f}s / {CYCLE}s"
            )
        except Exception as exc:
            print(f"[WARN] Push failed: {exc}")

        time.sleep(PUSH_INTERVAL)


if __name__ == "__main__":
    main()
