"""What happened when we looked — run identity, events, and a manifest.

Everything else in this package describes the *product*. This describes the
*run*: the thing that produced the artifacts, and whether you can trust it.

A capture could always say what it found. It could not say who ran it, against
what, under whose authorization, how long each stage took, or what happened
along the way. Three files fix that, all written beside the capture:

    run.json      the manifest — one screen of "what was this run?"
    events.jsonl  an ordered, append-only record of what happened
    runs.jsonl    one line per run at the output root — every run against
                  this target, and how they trend

Deliberately files-only. No service, no exporter, no new dependency, nothing
listening. A run is accountable because it writes down what it did — which
keeps principle #11 (the runtime is self-contained) intact, and means the
record survives on a laptop with no network exactly as it does in CI.

`RunContext` is the whole API:

    with RunContext.begin(output_dir, target=url) as run:
        with run.stage("crawl"):
            ...
        run.emit("page.captured", url=..., elements=...)

Both the stage timing and the terminal event are handled by the context
managers, so a run that crashes still leaves a well-formed record — which is
exactly the run whose record you most want.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import socket
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, TypeVar

from pydantic import ValidationError

from . import SCHEMA_VERSION, __version__
from .models import (
    DataHandling,
    RunEvent,
    RunManifest,
    SafetyEnvelope,
    StageRecord,
)

# The manifest sections a run *describes* rather than measures — `G2`'s safety
# envelope and `G3`'s data-handling posture. Both are validated the same way.
_Section = TypeVar("_Section", SafetyEnvelope, DataHandling)

EVENTS_FILE = "events.jsonl"
MANIFEST_FILE = "run.json"
INDEX_FILE = "runs.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    """A short, unique id for one pipeline run.

    Twelve hex characters, matching `crawl_id`'s shape so the two read as
    siblings in a log rather than as different kinds of thing.
    """
    return uuid.uuid4().hex[:12]


def _operator() -> str:
    """Who ran this. Enough to tell two people's runs apart on a shared
    machine, and deliberately no more — no full name, no email, no home path."""
    for getter in (lambda: os.environ.get("GITHUB_ACTOR"), getpass.getuser):
        try:
            value = getter()
            if value:
                return str(value)[:64]
        except Exception:
            continue
    return "unknown"


def _host() -> str:
    try:
        return socket.gethostname()[:64]
    except Exception:
        return platform.node()[:64] or "unknown"


def config_digest(payload: Any) -> str:
    """A stable hash of a resolved configuration.

    Taken over the *resolved* scope, not the file on disk: two runs are then
    provably the same configuration even when one passed flags and the other
    used a config file, and differ the moment one setting does. Sorted keys,
    so dict ordering never moves the hash.
    """
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RunContext:
    """One pipeline run: its id, its event stream, and its manifest.

    Events are flushed as they happen rather than buffered to the end. A run
    that dies mid-crawl is precisely the run whose events you want, and a
    buffer would lose them.
    """

    def __init__(self, run_id: str, output_dir: str, *, target: str = "",
                 emit_events: bool = True,
                 index_dir: Optional[str] = None) -> None:
        self.run_id = run_id
        self.output_dir = Path(output_dir)
        self.target = target
        self.emit_events = emit_events
        # O5: where `runs.jsonl` lives. The *root* the captures are written
        # under, not this capture's own folder — an index inside the folder it
        # indexes would hold exactly one line and answer nothing. Defaults to
        # the parent, which is that root for every layout but a dated one.
        self.index_dir = Path(index_dir) if index_dir else self.output_dir.parent
        self.started_at = _now()
        self._t0 = time.monotonic()
        self._seq = 0
        self.stages: list[StageRecord] = []
        self.crawl_id: Optional[str] = None
        self.failed_stages: list[str] = []
        self._meta: dict[str, Any] = {}
        self._stats: dict[str, Any] = {}
        # The stage currently being timed, so `count()` needs no argument the
        # caller would only get wrong.
        self._open: list[StageRecord] = []
        self._indexed = False

    # --- lifecycle ---------------------------------------------------------

    @classmethod
    def begin(cls, output_dir: str, *, target: str = "",
              run_id: Optional[str] = None,
              emit_events: bool = True,
              index_dir: Optional[str] = None) -> "RunContext":
        run = cls(run_id or new_run_id(), output_dir, target=target,
                  emit_events=emit_events, index_dir=index_dir)
        run.emit("run.started", target=target, engine_version=__version__)
        return run

    def describe(self, **meta: Any) -> None:
        """Record facts about the run for the manifest — target, config,
        authorization, auth posture. Called as they become known."""
        self._meta.update({k: v for k, v in meta.items() if v is not None})

    def record_stats(self, **stats: Any) -> None:
        self._stats.update(stats)

    def safety_envelope(self) -> dict[str, Any]:
        """G2: the envelope recorded so far.

        `describe` merges at the top level only, so a caller adding one key to
        a nested dict has to hand back the whole thing. Returning a copy means
        it cannot be mutated in place by accident — which would change the
        manifest without going through `describe`, and so without any of the
        None-filtering it does.
        """
        return dict(self._meta.get("safety") or {})

    # --- events ------------------------------------------------------------

    def emit(self, event: str, /, *, stage: str = "", level: str = "info",
             message: str = "", **data: Any) -> RunEvent:
        """Append one event. Never raises — an unwritable log must not take a
        capture down with it.

        The event name is positional-only so a payload key called `event` (or
        `name`, in the crawler's wrapper) lands in `data` instead of colliding
        with the parameter. That collision is a `TypeError` the never-raises
        guarantee would swallow, which is how it went unnoticed the first time.
        """
        self._seq += 1
        record = RunEvent(
            run_id=self.run_id, seq=self._seq, at=_now(), stage=stage,
            event=event, level=level, message=message, data=data,
        )
        if not self.emit_events:
            return record
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with (self.output_dir / EVENTS_FILE).open(
                    "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(record.model_dump(), ensure_ascii=False,
                                    default=str) + "\n")
        except Exception:
            pass
        return record

    # --- stages ------------------------------------------------------------

    @contextmanager
    def stage(self, name: str) -> Iterator[StageRecord]:
        """Time one stage and record how it ended.

        A raising stage is recorded as failed and the exception re-raised: the
        caller decides whether a failed stage is fatal (the pipeline treats a
        failed report as a warning and keeps the crawl), and that decision does
        not belong here.

        Yields the stage's own record, so a caller holding it can attach counts
        directly; `count()` is the same thing for code too deep to hold it.
        """
        started, t0 = _now(), time.monotonic()
        self.emit("stage.started", stage=name)
        record = StageRecord(name=name, started_at=started)
        self.stages.append(record)
        self._open.append(record)
        try:
            yield record
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc).splitlines()[0][:300] if str(exc) else repr(exc)
            self.failed_stages.append(name)
            self.emit("stage.finished", stage=name, level="error",
                      message=record.error, status="failed")
            raise
        else:
            self.emit("stage.finished", stage=name, status="ok",
                      duration_ms=int((time.monotonic() - t0) * 1000),
                      **record.counts)
        finally:
            record.finished_at = _now()
            record.duration_ms = int((time.monotonic() - t0) * 1000)
            self._open.pop()

    def count(self, **counts: Any) -> None:
        """O4: record what the stage now running produced.

        A no-op outside a stage rather than an error — a count is a nicety, and
        losing a capture because a nicety was recorded in the wrong place would
        be an absurd trade.
        """
        if not self._open:
            return
        self._open[-1].counts.update(
            {k: int(v) for k, v in counts.items() if v is not None})

    def skipped(self, name: str, reason: str = "") -> None:
        """Record a stage that was deliberately not run. A skipped stage and a
        stage that produced nothing are different facts."""
        self.stages.append(StageRecord(name=name, status="skipped", error=reason or None))
        self.emit("stage.skipped", stage=name, message=reason)

    # --- manifest ----------------------------------------------------------

    def _artifacts(self) -> list[str]:
        """Every file this run left behind, relative to the output folder.

        Read from disk rather than tracked as we go: what is actually there is
        the honest answer, and a stage that half-failed still gets credit for
        what it wrote.
        """
        try:
            return sorted(
                str(p.relative_to(self.output_dir)).replace("\\", "/")
                for p in self.output_dir.rglob("*")
                if p.is_file() and p.name != MANIFEST_FILE
            )[:2000]
        except Exception:
            return []

    # --- metrics (O4) ------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Where the time went — the derived view of `stages`.

        `QA.3` asks whether probing every page by default costs too much. That
        is not a question anyone should answer from memory, so this reduces a
        run to the handful of numbers that answer it: seconds per screen, the
        share each stage took, and how much of the crawl was spent interacting
        rather than reading.

        Everything here is derived from facts recorded elsewhere in the
        manifest. Nothing is measured twice, so nothing can disagree.
        """
        total = int((time.monotonic() - self._t0) * 1000)
        ran = [s for s in self.stages if s.status != "skipped"]
        stage_ms = {s.name: s.duration_ms for s in ran}
        accounted = sum(stage_ms.values())
        pages = int(self._stats.get("pages_crawled") or 0)
        crawl_ms = stage_ms.get("crawl", 0)
        probe_ms = int(self._stats.get("probe_ms") or 0)

        def pct(part: int, whole: int) -> Optional[float]:
            return round(part * 100 / whole, 1) if whole > 0 else None

        return {
            "total_ms": total,
            "stage_ms": stage_ms,
            # The pipeline writes reports, the inventory and the module folders
            # between stages. That work is real and belongs somewhere, or the
            # shares below quietly sum to less than the run.
            "outside_stages_ms": max(total - accounted, 0),
            "stage_share_pct": {n: pct(ms, total) for n, ms in stage_ms.items()},
            "slowest_stage": (max(stage_ms, key=lambda n: stage_ms[n])
                              if stage_ms else None),
            "skipped_stages": [s.name for s in self.stages
                               if s.status == "skipped"],
            "counts": {s.name: dict(s.counts) for s in self.stages if s.counts},
            "pages": pages,
            "crawl_ms": crawl_ms,
            "ms_per_page": round(crawl_ms / pages) if pages else None,
            "pages_per_minute": (round(pages / (crawl_ms / 60000), 1)
                                 if crawl_ms > 0 and pages else None),
            # Cumulative across pages: under concurrency this can exceed the
            # crawl's wall clock, which is why it is reported as a share of the
            # work rather than presented as elapsed time.
            "probe_ms": probe_ms,
            "probe_share_of_crawl_pct": pct(probe_ms, crawl_ms) if probe_ms else None,
        }

    def _described(self, key: str, model: type[_Section]) -> Optional[_Section]:
        """A manifest section the run described, validated.

        Covers `G2`'s safety envelope and `G3`'s data-handling posture, which
        are the same shape of thing: a *description* of what the engine did,
        assembled by the modules that did it.

        Tolerant on purpose, and for a reason that applies to both. The gates
        in `safety.py` are what actually refuse a control; the redactions in
        `network`, `browser` and `extraction` are what actually drop the data.
        These sections only describe them — so one that will not validate is
        dropped rather than allowed to take a capture down at the last step,
        and a missing section reads as `null` rather than as a guarantee that
        was never applied.
        """
        payload = self._meta.get(key)
        if not payload:
            return None
        try:
            return model.model_validate(payload)
        except ValidationError:
            return None

    def manifest(self, outcome: Optional[str] = None) -> RunManifest:
        if outcome is None:
            outcome = "failed" if self._meta.get("fatal") else (
                "partial" if self.failed_stages else "ok")
        return RunManifest(
            schema_version=SCHEMA_VERSION,
            engine_version=__version__,
            run_id=self.run_id,
            crawl_id=self.crawl_id,
            started_at=self.started_at,
            finished_at=_now(),
            duration_ms=int((time.monotonic() - self._t0) * 1000),
            outcome=outcome,
            failed_stages=list(self.failed_stages),
            target=self.target,
            config_file=self._meta.get("config_file"),
            config_sha256=self._meta.get("config_sha256", ""),
            command=self._meta.get("command", ""),
            operator=_operator(),
            host=_host(),
            authorized=self._meta.get("authorized"),
            authorized_by=self._meta.get("authorized_by"),
            environment=self._meta.get("environment"),
            safety=self._described("safety", SafetyEnvelope),
            data_handling=self._described("data_handling", DataHandling),
            auth_used=bool(self._meta.get("auth_used")),
            auth_source=self._meta.get("auth_source"),
            auth_expires_in_hours=self._meta.get("auth_expires_in_hours"),
            stages=list(self.stages),
            stats=dict(self._stats),
            metrics=self.metrics(),
            artifacts=self._artifacts(),
            event_count=self._seq,
        )

    def finish(self, outcome: Optional[str] = None) -> RunManifest:
        """Emit the terminal event, write `run.json`, and index the run.

        The terminal event is emitted *before* the manifest is built so that
        `event_count` counts it — a manifest claiming N events beside a file
        holding N+1 would be a small lie that costs a reader real time.
        """
        record = self.manifest(outcome)
        self.emit("run.finished" if record.outcome != "failed" else "run.failed",
                  level="info" if record.outcome == "ok" else "warning",
                  outcome=record.outcome, duration_ms=record.duration_ms,
                  failed_stages=record.failed_stages)
        record.event_count = self._seq
        write_manifest(record, str(self.output_dir))
        # O5: one line per run, and only ever one. `finish` can be reached
        # twice — a caller that finishes explicitly inside a `with` block gets
        # a second pass through `__exit__` — and a run counted twice would
        # corrupt exactly the trend the index exists to show.
        if not self._indexed:
            self._indexed = True
            append_index(record, str(self.index_dir),
                         folder=self.output_dir.name)
        return record

    def __enter__(self) -> "RunContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self._meta["fatal"] = True
            self.emit("run.failed", level="error",
                      message=str(exc).splitlines()[0][:300] if exc else "")
        self.finish()
        return False


def write_manifest(manifest: RunManifest, output_dir: str) -> str:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / MANIFEST_FILE
    path.write_text(
        json.dumps(manifest.model_dump(), indent=2, ensure_ascii=False,
                   default=str),
        encoding="utf-8",
    )
    return str(path)


# --- O5: the run index -------------------------------------------------------
#
# "Every run against this target, and how they trend" — without a database.
# One line per run at the output root, deliberately narrow: enough to spot a
# capture that shrank, slowed or started failing, and a folder name to go and
# read the full manifest in. `X6` is where a database would go if the volume
# ever justified one; a hundred runs is a 30KB file, so it does not.


def index_row(manifest: RunManifest, folder: str = "") -> dict[str, Any]:
    """One run, reduced to the columns you would actually scan down."""
    m = manifest.metrics or {}
    return {
        "run_id": manifest.run_id,
        "at": manifest.finished_at or manifest.started_at,
        "target": manifest.target,
        "folder": folder,
        "outcome": manifest.outcome,
        "duration_ms": manifest.duration_ms,
        "pages": m.get("pages", 0),
        "elements": manifest.stats.get("elements", 0),
        "ms_per_page": m.get("ms_per_page"),
        "probe_ms": m.get("probe_ms", 0),
        "failed_stages": list(manifest.failed_stages),
        "engine_version": manifest.engine_version,
        "config_sha256": manifest.config_sha256,
        "crawl_id": manifest.crawl_id,
    }


def append_index(manifest: RunManifest, index_dir: str,
                 folder: str = "") -> Optional[str]:
    """Append this run to `runs.jsonl`. Never raises — an index that cannot be
    written is a lost trend line, not a lost capture."""
    try:
        root = Path(index_dir)
        root.mkdir(parents=True, exist_ok=True)
        path = root / INDEX_FILE
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(index_row(manifest, folder),
                                ensure_ascii=False, default=str) + "\n")
        return str(path)
    except Exception:
        return None


def read_index(index_dir: str) -> list[dict[str, Any]]:
    """Read the run index back, oldest first. Tolerates a truncated final line
    — two runs finishing at once is the ordinary case, not a corruption."""
    path = Path(index_dir) / INDEX_FILE
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def read_events(output_dir: str) -> list[RunEvent]:
    """Read an event stream back. Tolerates a truncated final line, which is
    what a killed process leaves behind."""
    path = Path(output_dir) / EVENTS_FILE
    if not path.exists():
        return []
    events: list[RunEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(RunEvent.model_validate(json.loads(line)))
        except Exception:
            continue
    return events


def command_line() -> str:
    """The invocation, for the manifest. Argument *values* are kept — they are
    flags and URLs, not secrets — but a path to a session file is reduced to
    its name so the manifest never advertises where credentials live."""
    parts: list[str] = []
    skip_next = False
    for arg in sys.argv:
        if skip_next:
            parts.append(Path(arg).name)
            skip_next = False
            continue
        parts.append(Path(arg).name if arg.endswith(".py") else arg)
        if arg in ("--auth-state", "--output"):
            skip_next = True
    return " ".join(parts)[:500]
