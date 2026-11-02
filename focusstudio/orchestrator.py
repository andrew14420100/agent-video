"""Orchestratore deterministico degli agenti.

Ogni esecuzione (*run*) è una sequenza di task, uno per agente, salvata in
``<progetto>/runs/<run_id>.json`` dopo ogni cambiamento di stato. Lo stato di un
task è sempre il risultato reale dell'agente: ``completed`` solo se il lavoro è
stato svolto, ``skipped`` se non necessario (con motivo), ``blocked`` se manca
un prerequisito, ``failed`` con l'errore.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from . import project as pj
from .media.ffmpeg import Cancelled
from .project import Project, new_id, now_iso


@dataclass
class TaskRecord:
    agent: str
    title: str
    status: str = "pending"  # pending | running | completed | skipped | blocked | failed | cancelled
    started_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0
    progress: float = 0.0
    summary: str = ""
    details: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class Run:
    id: str
    project_id: str
    kind: str  # generate | render
    prompt: str = ""
    status: str = "queued"  # queued | running | completed | completed_with_issues | failed | blocked | cancelled
    created_at: str = field(default_factory=now_iso)
    finished_at: str = ""
    tasks: list[TaskRecord] = field(default_factory=list)
    result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class AgentResult:
    def __init__(self, status: str = "completed", summary: str = "", details: Optional[dict] = None,
                 outputs: Optional[list[str]] = None, warnings: Optional[list[str]] = None):
        self.status, self.summary = status, summary
        self.details, self.outputs, self.warnings = details or {}, outputs or [], warnings or []


class Blocked(Exception):
    """Prerequisito mancante: l'agente non può svolgere il lavoro."""


@dataclass
class Context:
    project: Project
    run: Run
    task: TaskRecord
    cancel: threading.Event
    save_cb: Callable[[], None]
    shared: dict

    def log(self, msg: str) -> None:
        self.task.log.append(f"{time.strftime('%H:%M:%S')} {msg}")
        self.save_cb()

    def progress(self, f: float) -> None:
        f = max(0.0, min(1.0, f))
        if abs(f - self.task.progress) >= 0.02 or f in (0.0, 1.0):
            self.task.progress = f
            self.save_cb()

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("Annullato dall'utente")


class Agent:
    name = "agent"
    title = "Agente"
    critical = True  # se fallisce/si blocca, la pipeline si ferma

    def run(self, ctx: Context) -> AgentResult:  # pragma: no cover - interfaccia
        raise NotImplementedError


class RunManager:
    """Esegue una run alla volta in un thread di lavoro."""

    def __init__(self):
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._busy = threading.Lock()

    def _save(self, run: Run) -> None:
        try:
            p = pj.load(run.project_id)
        except KeyError:
            return
        p.runs_dir.mkdir(parents=True, exist_ok=True)
        tmp = p.runs_dir / f"{run.id}.json.tmp"
        tmp.write_text(json.dumps(run.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p.runs_dir / f"{run.id}.json")

    def get(self, run_id: str) -> Optional[Run]:
        return self._runs.get(run_id)

    def load_from_disk(self, project: Project, run_id: str) -> Optional[dict]:
        f = project.runs_dir / f"{run_id}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return None

    def active(self) -> Optional[Run]:
        for r in self._runs.values():
            if r.status in ("queued", "running"):
                return r
        return None

    def cancel(self, run_id: str) -> bool:
        ev = self._cancel.get(run_id)
        if ev:
            ev.set()
            return True
        return False

    def start(self, project_id: str, kind: str, agents: list[Agent], prompt: str = "",
              shared: Optional[dict] = None, wait: bool = False) -> Run:
        if self.active():
            raise RuntimeError("Un'altra elaborazione è in corso")
        run = Run(id=new_id("r_"), project_id=project_id, kind=kind, prompt=prompt,
                  tasks=[TaskRecord(agent=a.name, title=a.title) for a in agents])
        self._runs[run.id] = run
        self._cancel[run.id] = threading.Event()
        p = pj.load(project_id)
        p.runs.append(run.id)
        pj.save(p)
        self._save(run)
        th = threading.Thread(target=self._execute, args=(run, agents, shared or {}), daemon=True)
        self._threads[run.id] = th
        th.start()
        if wait:
            th.join()
        return run

    def _execute(self, run: Run, agents: list[Agent], shared: dict) -> None:
        with self._busy:
            run.status = "running"
            cancel = self._cancel[run.id]
            project = pj.load(run.project_id)
            stop_reason = ""
            for agent, task in zip(agents, run.tasks):
                if stop_reason:
                    task.status = "skipped"
                    task.summary = f"Non eseguito: {stop_reason}"
                    continue
                if cancel.is_set():
                    task.status = "cancelled"
                    stop_reason = "annullato"
                    continue
                task.status, task.started_at = "running", now_iso()
                t0 = time.time()
                self._save(run)
                ctx = Context(project=project, run=run, task=task, cancel=cancel,
                              save_cb=lambda: self._save(run), shared=shared)
                try:
                    res = agent.run(ctx)
                    task.status, task.summary = res.status, res.summary
                    task.details, task.outputs = res.details, res.outputs
                    task.warnings += res.warnings
                    pj.save(project)
                    if res.status in ("blocked", "failed") and agent.critical:
                        stop_reason = f"{agent.title}: {res.summary}"
                except Blocked as e:
                    task.status, task.summary = "blocked", str(e)
                    if agent.critical:
                        stop_reason = f"{agent.title} bloccato"
                except Cancelled:
                    task.status, task.summary = "cancelled", "Annullato"
                    stop_reason = "annullato"
                except Exception as e:  # noqa: BLE001
                    task.status = "failed"
                    task.summary = str(e).splitlines()[0][:300] if str(e) else type(e).__name__
                    task.error = traceback.format_exc()[-4000:]
                    if agent.critical:
                        stop_reason = f"{agent.title} non riuscito"
                task.finished_at = now_iso()
                task.seconds = round(time.time() - t0, 2)
                task.progress = 1.0 if task.status == "completed" else task.progress
                self._save(run)
            statuses = [t.status for t in run.tasks]
            if "cancelled" in statuses:
                run.status = "cancelled"
            elif any(s == "failed" for s in statuses) and stop_reason:
                run.status = "failed"
            elif any(s == "blocked" for s in statuses) and stop_reason:
                run.status = "blocked"
            elif any(s in ("failed", "blocked") for s in statuses) or any(t.warnings for t in run.tasks):
                run.status = "completed_with_issues"
            else:
                run.status = "completed"
            run.result = shared.get("result", {})
            run.finished_at = now_iso()
            self._save(run)


manager = RunManager()
