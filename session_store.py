import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _normalize(self, session):
        if not isinstance(session, dict):
            return None
        session.setdefault("schema_version", 3)
        session.setdefault("models", [])
        session.setdefault("messages", [])
        session.setdefault("rounds", [])
        session.setdefault("current_state", {})
        session.setdefault("council_state", {})
        session.setdefault("role_plan", {})
        session.setdefault("health_report", {})
        session.setdefault("selected_models", [])
        session.setdefault("approved_role_plan", None)
        session.setdefault("created_at", now())
        session.setdefault("updated_at", now())

        from parsers import parse_round_two
        for r in session.get("rounds", []):
            if r.get("round") == 2 or r.get("type") == "comparison":
                if "output" in r and "raw_outputs" not in r:
                    r["raw_outputs"] = {"comparator": r["output"]}
                if "structured" not in r and "output" in r:
                    raw_text = r["output"].get("response", "")
                    r["structured"] = parse_round_two(raw_text)

        return session

    def create(self, title, models, role_plan=None):
        sid = "MC-" + uuid.uuid4().hex[:8].upper()
        session = {
            "schema_version": 3,
            "session_id": sid,
            "title": title,
            "created_at": now(),
            "updated_at": now(),
            "models": models,
            "selected_models": models,
            "role_plan": role_plan or {},
            "approved_role_plan": role_plan or None,
            "health_report": {},
            "council_state": {},
            "messages": [],
            "rounds": [],
            "current_state": {},
        }
        self.save(session)
        return session

    def save(self, session):
        import time
        t0 = time.perf_counter()
        session["updated_at"] = now()
        
        if "telemetry" not in session:
            session["telemetry"] = {}
        if "persistence" not in session["telemetry"]:
            session["telemetry"]["persistence"] = []
            
        path = self.directory / f"{session['session_id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
            
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        size_bytes = path.stat().st_size if path.exists() else 0
        
        session["telemetry"]["persistence"].append({
            "action": "save",
            "duration_ms": duration_ms,
            "size_bytes": size_bytes,
            "timestamp": now()
        })
        
        # Second write to ensure the persisted file contains its own "save" telemetry entry
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

    def load(self, session_id):
        import time
        t0 = time.perf_counter()
        path = self.directory / f"{session_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            session = self._normalize(json.load(f))
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            size_bytes = path.stat().st_size
            
            if "telemetry" not in session:
                session["telemetry"] = {}
            if "persistence" not in session["telemetry"]:
                session["telemetry"]["persistence"] = []
                
            session["telemetry"]["persistence"].append({
                "action": "load",
                "duration_ms": duration_ms,
                "size_bytes": size_bytes,
                "timestamp": now()
            })
            return session

    def list_sessions(self):
        rows = []
        for path in sorted(self.directory.glob("MC-*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    s = self._normalize(json.load(f))
                if not s:
                    continue
                rows.append({
                    "session_id": s["session_id"],
                    "title": s["title"],
                    "updated_at": s.get("updated_at", "")
                })
            except Exception:
                continue
        return rows
