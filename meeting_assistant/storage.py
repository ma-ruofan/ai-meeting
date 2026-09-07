import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from .models import AppError, canonical, digest, validate_segments


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "app.db"
        self.lock = FileLock(str(self.root / "processing.lock"))
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
                INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
                CREATE TABLE IF NOT EXISTS meetings(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    duration REAL NOT NULL, source_kind TEXT NOT NULL,
                    audio_path TEXT, source_path TEXT, transcript_revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS segments(
                    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    segment_id TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                    model_text TEXT NOT NULL, edited_text TEXT,
                    PRIMARY KEY(meeting_id, segment_id)
                );
                CREATE TABLE IF NOT EXISTS analysis_runs(
                    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    created_at TEXT NOT NULL, revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL, input_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    elapsed REAL
                );
                CREATE TABLE IF NOT EXISTS agent_runs(
                    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    created_at TEXT NOT NULL, revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL, question TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, status TEXT NOT NULL,
                    trace_json TEXT NOT NULL DEFAULT '[]', result_json TEXT, error TEXT,
                    elapsed REAL
                );
                CREATE TABLE IF NOT EXISTS stage_runs(
                    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    stage TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, error TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
                );
            """)
            if db.execute("SELECT version FROM schema_version").fetchone()[0] != 1:
                raise AppError("数据库版本不兼容，请备份后按 README 升级。")
        self.recover()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def processing(self):
        try:
            self.lock.acquire(timeout=0)
        except Timeout as exc:
            raise AppError("另一个音频或模型任务正在运行，请完成后再试。") from exc
        try:
            yield
        finally:
            self.lock.release()

    def recover(self):
        # An OS-backed lock is released even when its owner crashes. Never infer
        # interruption merely from a browser rerun or a timestamp timeout.
        try:
            with self.processing(), self.connect() as db:
                for table in ("stage_runs", "analysis_runs", "agent_runs"):
                    db.execute(
                        f"UPDATE {table} SET status='interrupted', error=? WHERE status='running'",
                        ("上次任务已中断，可以重新执行；已完成结果保留。",),
                    )
        except AppError:
            pass

    def create_meeting(
        self, title, duration, source_kind, meeting_id=None, source_path=None, audio_path=None
    ):
        ident = meeting_id or uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO meetings(id,title,created_at,duration,source_kind,source_path,audio_path) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    ident,
                    title.strip()[:150] or "未命名会议",
                    now(),
                    duration,
                    source_kind,
                    source_path,
                    audio_path,
                ),
            )
        return ident

    def list_meetings(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM meetings ORDER BY rowid DESC")]

    def meeting(self, meeting_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if not row:
            raise AppError("会议不存在。")
        return dict(row)

    def path(self, relative):
        if not relative:
            return None
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise AppError("文件路径超出会议数据目录。")
        return path

    def segments(self, meeting_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT segment_id,start,end,COALESCE(edited_text,model_text) AS text "
                "FROM segments WHERE meeting_id=? ORDER BY start,segment_id",
                (meeting_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def replace_transcript(self, meeting_id, segments):
        meeting = self.meeting(meeting_id)
        valid = validate_segments(segments, meeting["duration"])
        with self.connect() as db:
            db.execute("DELETE FROM segments WHERE meeting_id=?", (meeting_id,))
            db.executemany(
                "INSERT INTO segments(meeting_id,segment_id,start,end,model_text) VALUES(?,?,?,?,?)",
                [(meeting_id, s["segment_id"], s["start"], s["end"], s["text"]) for s in valid],
            )
            db.execute(
                "UPDATE meetings SET transcript_revision=transcript_revision+1 WHERE id=?",
                (meeting_id,),
            )

    def edit_transcript(self, meeting_id, texts, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute(
                "SELECT transcript_revision FROM meetings WHERE id=?", (meeting_id,)
            ).fetchone()
            if revision is None or revision[0] != expected_revision:
                raise AppError("转录已在其他页面更新，请刷新后再保存，避免覆盖修改。")
            rows = db.execute("SELECT * FROM segments WHERE meeting_id=?", (meeting_id,)).fetchall()
            if set(texts) != {r["segment_id"] for r in rows}:
                raise AppError("编辑列表与当前转录不一致，请刷新。")
            changed = False
            for row in rows:
                text = str(texts[row["segment_id"]]).strip()
                if not text or len(text) > 5000:
                    raise AppError("每段文字需为 1～5000 个字符。")
                current = (
                    row["edited_text"] if row["edited_text"] is not None else row["model_text"]
                )
                if text != current:
                    db.execute(
                        "UPDATE segments SET edited_text=? WHERE meeting_id=? AND segment_id=?",
                        (
                            None if text == row["model_text"] else text,
                            meeting_id,
                            row["segment_id"],
                        ),
                    )
                    changed = True
            if changed:
                db.execute(
                    "UPDATE meetings SET transcript_revision=transcript_revision+1 WHERE id=?",
                    (meeting_id,),
                )
        return changed

    def snapshot(self, meeting_id):
        # Both reads occur in one SQLite read transaction to prevent mixed revisions.
        with self.connect() as db:
            db.execute("BEGIN")
            meeting = db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if meeting is None:
                raise AppError("会议不存在。")
            segments = [
                dict(r)
                for r in db.execute(
                    "SELECT segment_id,start,end,COALESCE(edited_text,model_text) AS text "
                    "FROM segments WHERE meeting_id=? ORDER BY start,segment_id",
                    (meeting_id,),
                )
            ]
        if not segments:
            raise AppError("请先转录音频或载入开发样例。")
        return {
            "meeting_id": meeting_id,
            "title": meeting["title"],
            "revision": meeting["transcript_revision"],
            "segments": segments,
            "source_kind": meeting["source_kind"],
            "audio_path": meeting["audio_path"],
        }

    def begin_analysis(self, snapshot, metadata):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO analysis_runs(id,meeting_id,created_at,revision,snapshot_json,"
                "input_hash,metadata_json,status) VALUES(?,?,?,?,?,?,?,'running')",
                (
                    ident,
                    snapshot["meeting_id"],
                    now(),
                    snapshot["revision"],
                    canonical(snapshot),
                    digest(snapshot),
                    canonical(metadata),
                ),
            )
        return ident

    def finish_analysis(self, ident, result=None, error=None, attempts=0, elapsed=0):
        with self.connect() as db:
            db.execute(
                "UPDATE analysis_runs SET status=?,result_json=?,error=?,attempts=?,elapsed=? "
                "WHERE id=?",
                (
                    "failed" if error else "succeeded",
                    canonical(result) if result is not None else None,
                    error,
                    attempts,
                    elapsed,
                    ident,
                ),
            )

    @staticmethod
    def decode_run(row):
        result = dict(row)
        for key in ("snapshot_json", "metadata_json", "result_json", "trace_json"):
            if key in result:
                value = result.pop(key)
                result[key.removesuffix("_json")] = json.loads(value) if value else None
        return result

    def analyses(self, meeting_id):
        with self.connect() as db:
            return [
                self.decode_run(r)
                for r in db.execute(
                    "SELECT * FROM analysis_runs WHERE meeting_id=? ORDER BY rowid DESC",
                    (meeting_id,),
                )
            ]

    def latest_analysis(self, meeting_id):
        return next((r for r in self.analyses(meeting_id) if r["status"] == "succeeded"), None)

    def begin_agent(self, snapshot, question, metadata):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO agent_runs(id,meeting_id,created_at,revision,snapshot_json,question,"
                "metadata_json,status) VALUES(?,?,?,?,?,?,?,'running')",
                (
                    ident,
                    snapshot["meeting_id"],
                    now(),
                    snapshot["revision"],
                    canonical(snapshot),
                    question,
                    canonical(metadata),
                ),
            )
        return ident

    def update_agent(self, ident, trace, result=None, error=None, elapsed=0, status="running"):
        with self.connect() as db:
            db.execute(
                "UPDATE agent_runs SET trace_json=?,result_json=?,error=?,elapsed=?,status=? "
                "WHERE id=?",
                (
                    canonical(trace),
                    canonical(result) if result else None,
                    error,
                    elapsed,
                    status,
                    ident,
                ),
            )

    def agent_runs(self, meeting_id):
        with self.connect() as db:
            return [
                self.decode_run(r)
                for r in db.execute(
                    "SELECT * FROM agent_runs WHERE meeting_id=? ORDER BY rowid DESC LIMIT 20",
                    (meeting_id,),
                )
            ]

    def begin_stage(self, meeting_id, stage, metadata):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO stage_runs(id,meeting_id,stage,status,started_at,metadata_json) "
                "VALUES(?,?,?,'running',?,?)",
                (ident, meeting_id, stage, now(), canonical(metadata)),
            )
        return ident

    def finish_stage(self, ident, error=None, metadata=None):
        with self.connect() as db:
            db.execute(
                "UPDATE stage_runs SET status=?,finished_at=?,error=?,metadata_json=? WHERE id=?",
                (
                    "failed" if error else "succeeded",
                    now(),
                    error,
                    canonical(metadata or {}),
                    ident,
                ),
            )

    def stages(self, meeting_id):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM stage_runs WHERE meeting_id=? ORDER BY rowid DESC LIMIT 10",
                    (meeting_id,),
                )
            ]
