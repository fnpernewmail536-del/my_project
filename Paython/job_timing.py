"""Duration estimates from successful jobs and confirmed server milestones.

Only timing data and a settings digest enter the sample table; codes, tokens,
account IDs and error text are never used for learning.
"""
import hashlib
import json
import math
import sqlite3
import statistics
import threading
import time
from contextlib import contextmanager

STAGES = {"connect", "protect", "create", "apply", "save"}
PLANS = {"daiko": ["connect", "protect", "apply", "save"],
         "create": ["create", "apply", "save"],
         "clone": ["connect", "protect", "create", "save"]}


def timing_profile(data, operation, count=1):
    """Group similar settings, without keeping their values or credentials."""
    shape = {"operation": operation}
    if operation == "create":
        shape["account_type"] = str(data.get("account_type", "new"))[:24]
    selected = data.get("selected", {})
    if isinstance(selected, dict):
        shape["selected"] = {key: sorted(str(x)[:40] for x in selected.get(key, [])[:100])
                             for key in ("s1", "s2", "s3")
                             if isinstance(selected.get(key, []), list)}
    for key in ("char_list", "character_settings", "legend_stages", "vip_facilities",
                "vip_talent_orbs", "main_story_stages", "event_stage_settings",
                "lineup_settings", "ototo_settings", "custom_amounts"):
        value = data.get(key)
        size = len(value) if isinstance(value, (dict, list)) else 0
        shape[key] = 0 if size == 0 else 1 if size <= 10 else 2 if size <= 100 else 3
    encoded = json.dumps(shape, sort_keys=True, separators=(",", ":"))
    return {"timing_profile": hashlib.sha256(encoded.encode()).hexdigest()[:16],
            "timing_count": max(1, min(5, int(count)))}


def advance_timing(job, changes, now):
    """Called under the existing job lock. No I/O and no changes to job results."""
    previous = job.get("status")
    job.update(changes)
    stage = changes.get("timing_stage")
    if stage in STAGES and job.get("status") == "running":
        unit = max(0, min(5, int(job.get("timing_unit", 0))))
        marks = job.setdefault("timing_marks", [])
        if not marks or (marks[-1]["stage"], marks[-1]["unit"]) != (stage, unit):
            if len(marks) < 40:
                marks.append({"stage": stage, "unit": unit, "at": now})
    if previous not in {"done", "error"} and job.get("status") in {"done", "error"}:
        job["finished_at"] = now
        job["timing_learnable"] = previous == "running" and job.get("status") == "done"


def _stage_durations(job):
    marks = job.get("timing_marks", [])
    end = float(job.get("finished_at", job.get("updated_at", 0)))
    output = {}
    for index, mark in enumerate(marks):
        if mark.get("stage") not in STAGES:
            continue
        next_at = float(marks[index + 1]["at"]) if index + 1 < len(marks) else end
        duration = max(0, next_at - float(mark["at"]))
        output.setdefault(mark["stage"], []).append(duration)
    return {key: statistics.mean(values) for key, values in output.items()}


def _robust(values):
    """Recent observations get more weight; isolate occasional very slow runs."""
    values = [float(x) for x in values if isinstance(x, (int, float)) and math.isfinite(x) and x > 0]
    if not values:
        return None, None
    median = statistics.median(values)
    deviations = [abs(x - median) for x in values]
    mad = statistics.median(deviations)
    limit = max(median * .65, 3 * mad, 2)
    useful = [x for x in values if abs(x - median) <= limit] or values
    weights = [.94 ** i for i in range(len(useful))]
    mean = sum(x * w for x, w in zip(useful, weights)) / sum(weights)
    # This is a historical spread, not a promise or a confidence interval.
    spread = sorted(abs(x - mean) for x in values)
    error = max(1, spread[min(len(spread) - 1, int(len(spread) * .8))])
    return mean, error


class TimingStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self.cache = {}
        self.lock = threading.Lock()
        self.ready = False

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=1)
        try:
            if not self.ready:
                conn.execute("""CREATE TABLE IF NOT EXISTS job_timing_samples (
                    job_id TEXT PRIMARY KEY, operation TEXT NOT NULL, count INTEGER NOT NULL,
                    profile TEXT NOT NULL, duration REAL NOT NULL, stages TEXT NOT NULL,
                    finished_at REAL NOT NULL)""")
                conn.execute("CREATE INDEX IF NOT EXISTS timing_sample_kind ON job_timing_samples(operation,count,finished_at)")
                conn.commit()
                self.ready = True
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def remember(self, job_id, job):
        if job.get("status") != "done" or not job.get("timing_marks") or not job.get("timing_learnable"):
            return
        start, end = job.get("started_at"), job.get("finished_at")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return
        duration = end - start
        kind = job.get("operation_type")
        if kind not in PLANS or not 1 <= duration <= 7200:
            return
        try:
            with self._connect() as conn:
                conn.execute("INSERT OR IGNORE INTO job_timing_samples VALUES(?,?,?,?,?,?,?)",
                             (job_id, kind, int(job.get("timing_count", 1)),
                              str(job.get("timing_profile", "")), duration,
                              json.dumps(_stage_durations(job)), end))
                conn.execute("DELETE FROM job_timing_samples WHERE finished_at < ?", (end - 90 * 86400,))
                conn.execute("DELETE FROM job_timing_samples WHERE job_id NOT IN "
                             "(SELECT job_id FROM job_timing_samples ORDER BY finished_at DESC LIMIT 2000)")
            with self.lock:
                self.cache.clear()
        except sqlite3.Error:
            # Timing must never turn a successful operation into an error.
            pass

    def _samples(self, kind, count, now):
        key = (kind, count)
        with self.lock:
            cached = self.cache.get(key)
            if cached and now - cached[0] < 30:
                return cached[1]
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT profile,duration,stages FROM job_timing_samples "
                                    "WHERE operation=? AND count=? AND finished_at>? "
                                    "ORDER BY finished_at DESC LIMIT 60",
                                    (kind, count, now - 90 * 86400)).fetchall()
            samples = [{"profile": row[0], "duration": row[1], "stages": json.loads(row[2])}
                       for row in rows]
        except (sqlite3.Error, ValueError):
            samples = []
        with self.lock:
            self.cache[key] = (now, samples)
        return samples

    def estimate(self, job, now=None):
        now = time.time() if now is None else now
        kind = job.get("operation_type", "daiko")
        count = max(1, min(5, int(job.get("timing_count", 1))))
        samples = self._samples(kind, count, now)
        matched = [s for s in samples if s["profile"] == job.get("timing_profile")]
        exact = len(matched) >= 3
        samples = (matched if exact else samples)[:30]
        total, error = _robust([s["duration"] for s in samples])
        source = "server_history" if total is not None else "initial"
        defaults = {"connect": 16, "protect": 30, "create": 25, "apply": 12, "save": 33}
        stages = {}
        for stage in PLANS.get(kind, PLANS["daiko"]):
            value, _ = _robust([s["stages"].get(stage) for s in samples])
            stages[stage] = value if value is not None else defaults[stage]
        if total is None:
            total = (sum(stages.values()) if kind == "daiko" else
                     count * sum(stages.values()) if kind == "create" else
                     stages["connect"] + stages["protect"] + count * (stages["create"] + stages["save"]))
        start = job.get("started_at")
        terminal = job.get("status") in {"done", "error"}
        end = float(job.get("finished_at", now)) if terminal else now
        elapsed = max(0, end - float(start)) if isinstance(start, (int, float)) and start > 0 else 0
        remaining = None
        marks = job.get("timing_marks", [])
        completed = max(0, min(count, int(job.get("timing_completed_units", 0))))
        observed = [x for x in job.get("timing_unit_durations", []) if isinstance(x, (int, float)) and 1 <= x <= 7200]
        if job.get("status") == "running":
            remaining = total - elapsed
            if marks:
                mark = marks[-1]
                stage = mark["stage"]
                in_stage = max(0, now - float(mark["at"]))
                plan = PLANS.get(kind, PLANS["daiko"])
                if stage in plan:
                    if kind == "daiko":
                        later = plan[plan.index(stage) + 1:]
                        remaining = max(0, stages[stage] - in_stage) + sum(stages[x] for x in later)
                    elif stage in {"connect", "protect"}:
                        remaining = (max(0, stages[stage] - in_stage) +
                                     (stages.get("protect", 0) if stage == "connect" else 0) +
                                     count * (stages["create"] + stages["save"]))
                    else:
                        unit_plan = [x for x in plan if x not in {"connect", "protect"}]
                        unit_mean, _ = _robust(list(reversed(observed)))
                        if unit_mean is not None:
                            source = "current_run"
                            scale = unit_mean / sum(stages[x] for x in unit_plan)
                        else:
                            scale = 1
                        later = unit_plan[unit_plan.index(stage) + 1:]
                        remaining = (max(0, stages[stage] * scale - in_stage) +
                                     sum(stages[x] * scale for x in later) +
                                     max(0, count - max(1, int(mark.get("unit", 1)))) *
                                     sum(stages[x] * scale for x in unit_plan))
                    # Once the current phase exceeds its guide, do not pretend
                    # the deadline is known. The next confirmed phase recalibrates.
                    if in_stage > stages[stage] * (scale if kind != "daiko" and stage not in {"connect", "protect"} else 1) + max(3, error or 3):
                        remaining = None
        elif job.get("status") == "done":
            remaining = 0
            completed = count
        sample_count = len(samples)
        confidence = "learning" if sample_count < 5 else "stable" if error is not None and error / max(1, total) < .2 else "variable"
        return {"server_time": now, "elapsed_seconds": round(elapsed, 2),
                "remaining_seconds": round(max(0, remaining), 2) if remaining is not None else None,
                "estimated_total_seconds": round(total, 2), "estimate_source": source,
                "sample_count": sample_count, "profile_matched": exact,
                "historical_error_seconds": round(error, 1) if sample_count >= 3 and error is not None else None,
                "confidence": confidence, "completed_units": completed, "total_units": count,
                "stage": job.get("timing_stage", "waiting"), "profile": job.get("timing_profile", "")}
