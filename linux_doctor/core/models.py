from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class Severity(enum.IntEnum):
    OK = 0
    INFO = 1
    ATTENTION = 2
    ACTION = 3

    @property
    def label(self) -> str:
        return {0: "Normal", 1: "Informational", 2: "Attention recommended",
                3: "Action recommended"}[int(self)]


class Confidence(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Finding:
    code: str
    title: str
    severity: Severity
    explanation: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    confidence: Confidence = Confidence.MEDIUM
    learn: str = ""          # command name in the learning library
    status: str = "new"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "title": self.title, "severity": self.severity.name.lower(),
                "explanation": self.explanation, "evidence": self.evidence,
                "recommendation": self.recommendation, "confidence": self.confidence.value,
                "learn": self.learn, "status": self.status}


@dataclass
class RunResult:
    workflow: str
    title: str
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    status: str = "completed"       # completed | cancelled | failed
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_id: int | None = None

    @property
    def worst(self) -> Severity:
        return max((f.severity for f in self.findings), default=Severity.OK)

    @property
    def summary(self) -> str:
        n = sum(1 for f in self.findings if f.severity >= Severity.ATTENTION)
        return f"{n} item(s) need attention" if n else "No issues detected by these checks"

    def to_dict(self) -> dict[str, Any]:
        return {"workflow": self.workflow, "title": self.title, "status": self.status,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "summary": self.summary, "errors": self.errors,
                "findings": [f.to_dict() for f in self.findings]}
