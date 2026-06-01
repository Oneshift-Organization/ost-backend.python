from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass
class AnalysisJob:
    job_id: UUID
    user_id: UUID
    report_id: UUID
    external_id: UUID | None = None
    status: str = "PENDING"
    llm_output: str | None = None
    error: str | None = None
    ai_structured_output: dict | None = None
    llm_input_payload: dict | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def mark_processing(self) -> None:
        self.status = "PROCESSING"

    def mark_completed(
        self,
        output: str,
        structured: dict | None = None,
        llm_input: dict | None = None,
    ) -> None:
        self.status = "COMPLETED"
        self.llm_output = output
        self.ai_structured_output = structured
        self.llm_input_payload = llm_input

    def mark_failed(self, error: str) -> None:
        self.status = "FAILED"
        self.error = error
