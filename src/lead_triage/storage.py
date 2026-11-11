"""Append-only CSV sink with de-duplication by lead_id."""

from __future__ import annotations

import csv
import threading
from pathlib import Path

from lead_triage.models import TriagedLead

_FIELDS = list(TriagedLead.model_fields)


class CsvLeadStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._seen = self._load_ids()

    def _load_ids(self) -> set[str]:
        if not self._path.exists():
            return set()
        with self._path.open(newline="", encoding="utf-8") as fh:
            return {row["lead_id"] for row in csv.DictReader(fh)}

    def save(self, lead: TriagedLead) -> bool:
        """Return True if the lead was new, False if it was a duplicate."""
        with self._lock:
            if lead.lead_id in self._seen:
                return False
            is_new_file = not self._path.exists()
            with self._path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_FIELDS)
                if is_new_file:
                    writer.writeheader()
                writer.writerow(lead.model_dump(mode="json"))
            self._seen.add(lead.lead_id)
            return True
