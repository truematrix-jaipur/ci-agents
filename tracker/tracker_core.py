import os
import json
import sqlite3
import datetime
import uuid
import logging
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict

# Derive DB path from this file's location so it works in any environment
_TRACKER_DIR = Path(__file__).parent / "data"
TRACKER_DB = str(_TRACKER_DIR / "swarm_tracker.db")

class EntryType(Enum):
    BUG = "bug"
    IMPROVEMENT = "improvement"
    DEVELOPMENT = "development"
    TASK = "task"

class Status(Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    DEFERRED = "deferred"

class SwarmTracker:
    """
    Universal Tracking System for all AI Agents (Claude, Gemini, Codex, etc.)
    Standardizes how bugs, improvements, and development tasks are logged and tracked.
    """
    def __init__(self):
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(TRACKER_DB), exist_ok=True)
        conn = sqlite3.connect(TRACKER_DB)
        cursor = conn.cursor()
        
        # Main Tracking Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tracker_entries (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                agent_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                priority INTEGER DEFAULT 2,
                tags TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                resolved_at TIMESTAMP,
                metadata TEXT
            )
        ''')
        
        # Activity/Log Table for history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entry_activity (
                id TEXT PRIMARY KEY,
                entry_id TEXT,
                agent_id TEXT,
                action TEXT,
                comment TEXT,
                timestamp TIMESTAMP,
                FOREIGN KEY(entry_id) REFERENCES tracker_entries(id)
            )
        ''')
        conn.commit()
        conn.close()

    def log(self, type: EntryType, title: str, description: str, agent_id: str, agent_type: str, priority: int = 2, tags: List[str] = [], metadata: Dict = {}) -> str:
        """Adds a new entry to the tracker."""
        entry_id = str(uuid.uuid4())[:8]
        now = datetime.datetime.now(datetime.UTC).isoformat()
        
        conn = sqlite3.connect(TRACKER_DB)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO tracker_entries (id, type, status, agent_id, agent_type, title, description, priority, tags, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            entry_id, type.value, Status.OPEN.value, agent_id, agent_type, 
            title, description, priority, ",".join(tags), now, now, json.dumps(metadata)
        ))
        conn.commit()
        conn.close()
        return entry_id

    def update_status(self, entry_id: str, status: Status, agent_id: str, comment: str = ""):
        """Updates the status of an existing entry."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        resolved_at = now if status in [Status.RESOLVED, Status.CLOSED] else None
        
        conn = sqlite3.connect(TRACKER_DB)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE tracker_entries 
            SET status = ?, updated_at = ?, resolved_at = ?
            WHERE id = ?
        ''', (status.value, now, resolved_at, entry_id))
        
        # Log activity
        cursor.execute('''
            INSERT INTO entry_activity (id, entry_id, agent_id, action, comment, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), entry_id, agent_id, f"Status changed to {status.value}", comment, now))
        
        conn.commit()
        conn.close()

    def get_entries(self, type: Optional[EntryType] = None, status: Optional[Status] = None) -> List[Dict]:
        """Queries entries from the tracker."""
        conn = sqlite3.connect(TRACKER_DB)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM tracker_entries WHERE 1=1"
        params = []
        if type:
            query += " AND type = ?"
            params.append(type.value)
        if status:
            query += " AND status = ?"
            params.append(status.value)
            
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

# Singleton instance
swarm_tracker = SwarmTracker()
