from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional

from sqlalchemy import func

from .models import WorkflowEvent


class EventWriter:
    def __init__(self, run_id: str, workflow_id: str, db_factory) -> None:
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.db_factory = db_factory
        self._lock = asyncio.Lock()
        db = db_factory()
        try:
            self._sequence = int(db.query(func.max(WorkflowEvent.sequence)).filter(WorkflowEvent.workflow_run_id == run_id).scalar() or 0)
        finally:
            db.close()

    async def emit(self, event_type: str, payload: Dict[str, Any], node_id: Optional[str] = None) -> Dict[str, Any]:
        async with self._lock:
            self._sequence += 1
            timestamp = datetime.now(timezone.utc)
            event = {
                "run_id": self.run_id,
                "workflow_id": self.workflow_id,
                "node_id": node_id,
                "sequence": self._sequence,
                "timestamp": timestamp.isoformat(),
                "type": event_type,
                "payload": payload,
            }
            db = self.db_factory()
            try:
                db.add(WorkflowEvent(
                    workflow_run_id=self.run_id,
                    workflow_id=self.workflow_id,
                    node_id=node_id,
                    sequence=self._sequence,
                    event_type=event_type,
                    timestamp=timestamp.replace(tzinfo=None),
                    payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                ))
                db.commit()
            finally:
                db.close()
            return event
