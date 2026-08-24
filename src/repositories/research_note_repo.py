# -*- coding: utf-8 -*-
"""Persistence for research notes synchronized through the ZSXQ MCP source."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_, select

from src.storage import DatabaseManager, ResearchNote, ZsxqSyncState, utc_naive_now
from src.research_note_fingerprint import research_note_information_hash


_SQL_IN_BATCH_SIZE = 500


def _batched(values: List[str]):
    for offset in range(0, len(values), _SQL_IN_BATCH_SIZE):
        yield values[offset:offset + _SQL_IN_BATCH_SIZE]


class ResearchNoteRepository:
    """Database access for normalized knowledge-planet research notes."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def upsert_notes(self, notes: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        note_rows = list(notes)
        created = 0
        updated = 0
        unchanged = 0
        changed_topic_ids: List[str] = []
        with self.db.get_session() as session:
            topic_ids = [str(fields["topic_id"]) for fields in note_rows]
            existing_rows: List[ResearchNote] = []
            for batch in _batched(topic_ids):
                existing_rows.extend(session.execute(
                    select(ResearchNote).where(ResearchNote.topic_id.in_(batch))
                ).scalars().all())
            existing_by_topic = {row.topic_id: row for row in existing_rows}
            for fields in note_rows:
                existing = existing_by_topic.get(str(fields["topic_id"]))
                if existing is None:
                    existing = ResearchNote(**fields)
                    session.add(existing)
                    existing_by_topic[str(fields["topic_id"])] = existing
                    created += 1
                    changed_topic_ids.append(str(fields["topic_id"]))
                    continue

                existing_information_hash = research_note_information_hash(
                    title=existing.title,
                    content=existing.content,
                    files=self._json_list(existing.files_json),
                    images=self._json_list(existing.images_json),
                )
                if existing.content_hash == fields["content_hash"] or existing_information_hash == fields["content_hash"]:
                    # Interaction counters and sync timestamps are deliberately
                    # ignored: an unchanged note must produce no database write.
                    unchanged += 1
                    continue

                for key, value in fields.items():
                    setattr(existing, key, value)
                updated += 1
                changed_topic_ids.append(str(fields["topic_id"]))
            if created or updated:
                session.commit()
        return {"created": created, "updated": updated, "unchanged": unchanged,
                "_changed_topic_ids": changed_topic_ids}

    @staticmethod
    def _json_list(value: Optional[str]) -> List[Any]:
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    def get_note(self, topic_id: str) -> Optional[ResearchNote]:
        with self.db.get_session() as session:
            return session.execute(
                select(ResearchNote).where(ResearchNote.topic_id == topic_id).limit(1)
            ).scalar_one_or_none()

    def list_notes(
        self,
        *,
        group_id: Optional[str] = None,
        query: Optional[str] = None,
        symbol: Optional[str] = None,
        digested: Optional[bool] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ResearchNote], int]:
        conditions = []
        if group_id:
            conditions.append(ResearchNote.group_id == group_id)
        if query:
            pattern = f"%{query.strip()}%"
            conditions.append(or_(
                ResearchNote.title.like(pattern),
                ResearchNote.content.like(pattern),
                ResearchNote.files_json.like(pattern),
            ))
        if symbol:
            conditions.append(ResearchNote.symbol_codes.like(f"%{symbol.strip().upper()}%"))
        if digested is not None:
            conditions.append(ResearchNote.digested.is_(digested))
        if created_from is not None:
            conditions.append(ResearchNote.created_at >= created_from)
        if created_to is not None:
            conditions.append(ResearchNote.created_at <= created_to)

        where_clause = and_(*conditions) if conditions else True
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 100))
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(ResearchNote.id)).select_from(ResearchNote).where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(ResearchNote)
                .where(where_clause)
                .order_by(desc(ResearchNote.created_at), desc(ResearchNote.id))
                .offset((safe_page - 1) * safe_size)
                .limit(safe_size)
            ).scalars().all()
            return list(rows), int(total)

    def source_summary(self) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    ResearchNote.group_id,
                    ResearchNote.group_name,
                    func.count(ResearchNote.id),
                    func.max(ResearchNote.created_at),
                    func.max(ResearchNote.synced_at),
                )
                .group_by(ResearchNote.group_id, ResearchNote.group_name)
                .order_by(desc(func.max(ResearchNote.synced_at)))
            ).all()
        return [
            {
                "group_id": row[0],
                "group_name": row[1],
                "note_count": int(row[2] or 0),
                "latest_note_at": f"{row[3].isoformat()}Z" if row[3] else None,
                "last_synced_at": f"{row[4].isoformat()}Z" if row[4] else None,
            }
            for row in rows
        ]

    def latest_created_by_group(self) -> Dict[str, datetime]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(ResearchNote.group_id, func.max(ResearchNote.created_at)).group_by(ResearchNote.group_id)
            ).all()
        return {str(group_id): created_at for group_id, created_at in rows if created_at is not None}

    def oldest_created_by_group(self) -> Dict[str, datetime]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(ResearchNote.group_id, func.min(ResearchNote.created_at)).group_by(ResearchNote.group_id)
            ).all()
        return {str(group_id): created_at for group_id, created_at in rows if created_at is not None}

    def update_sync_state(self, group_id: str, group_name: str, **fields: Any) -> None:
        with self.db.get_session() as session:
            row = session.get(ZsxqSyncState, str(group_id))
            if row is None:
                row = ZsxqSyncState(group_id=str(group_id), group_name=str(group_name)[:100])
                session.add(row)
            row.group_name = str(group_name)[:100]
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = utc_naive_now()
            session.commit()

    def list_sync_states(self) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(select(ZsxqSyncState).order_by(ZsxqSyncState.group_name)).scalars().all()
        return [{
            "group_id": row.group_id, "group_name": row.group_name,
            "last_topic_id": row.last_topic_id,
            "last_topic_at": self._iso(row.last_topic_at),
            "last_attempt_at": self._iso(row.last_attempt_at),
            "last_success_at": self._iso(row.last_success_at),
            "last_status": row.last_status, "last_error": row.last_error,
            "last_received": int(row.last_received or 0), "last_saved": int(row.last_saved or 0),
            "last_media_downloaded": int(row.last_media_downloaded or 0),
            "total_saved": int(row.total_saved or 0),
        } for row in rows]

    @staticmethod
    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() + "Z" if value else None
