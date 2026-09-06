from dataclasses import dataclass


@dataclass
class JudgeResult:
    score: float            # 0.0 ~ 1.0
    passed: bool
    reasoning: str | None = None
    error: str | None = None
