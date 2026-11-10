"""Owner/workspace-bound, durable choices for the investment meeting agent.

Only continuation data is persisted; request cookies and provider credentials
must never be included. Waiting for a person does not occupy a worker thread.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from src.services.workspace import workspace_directory, workspace_id, workspace_owner


class QuestionStateError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class CommandQuestionService:
    TTL_SECONDS = 24 * 3600

    def __init__(self, root=None):
        if root is None:
            from src.config import get_config
            root = Path(get_config().database_path).expanduser().absolute().parent / "command_questions"
        self.root = Path(root)

    def _path(self, question_id, workspace, *, create=False):
        if not re.fullmatch(r"q-[a-f0-9]{32}", str(question_id)):
            raise QuestionStateError("无效的确认问题编号", 404)
        folder = workspace_directory(self.root, workspace, create=create)
        path = folder / (question_id + ".json")
        if path.is_symlink():
            raise QuestionStateError("确认问题不存在或不属于当前账号", 404)
        return path

    @contextmanager
    def _locked(self, path):
        lock = path.parent / ".questions.lock"
        if lock.is_symlink():
            raise QuestionStateError("无效的确认问题目录")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _write(path, record):
        data = json.dumps(record, ensure_ascii=False, default=str)
        if len(data.encode("utf-8")) > 2_000_000:
            raise QuestionStateError("待确认上下文过大，请缩小任务范围")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            try:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def _read(self, path, workspace):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            raise QuestionStateError("确认问题不存在或不属于当前账号", 404) from None
        if record.get("owner") != workspace_owner() or record.get("workspace") != workspace_id(workspace):
            raise QuestionStateError("确认问题不存在或不属于当前账号", 404)
        if record.get("expires_at", 0) < time.time():
            raise QuestionStateError("该确认问题已过期，请重新发起任务", 410)
        return record

    @staticmethod
    def public(record):
        question = dict(record["question"])
        question["status"] = record["status"]
        if record.get("answer"):
            question["answer"] = record["answer"]
        return question

    def create(self, question, continuation, workspace=None):
        text = str(question.get("question") or "").strip()
        options = question.get("options") or []
        if not text or len(text) > 1000 or not isinstance(options, list) or not 2 <= len(options) <= 6:
            raise QuestionStateError("确认问题需要清晰的问题和 2–6 个选项")
        cleaned, seen = [], set()
        for option in options:
            if not isinstance(option, dict):
                raise QuestionStateError("选项格式不正确")
            ident = str(option.get("id") or "").strip()
            label = str(option.get("label") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", ident) or ident in seen or not label or len(label) > 150:
                raise QuestionStateError("选项编号或标题不正确")
            seen.add(ident)
            item = {"id": ident, "label": label, "description": str(option.get("description") or "")[:600]}
            if isinstance(option.get("count"), int) and option["count"] >= 0:
                item["count"] = option["count"]
            cleaned.append(item)
        ident = "q-" + uuid.uuid4().hex
        now = time.time()
        record = {
            "owner": workspace_owner(), "workspace": workspace_id(workspace),
            "created_at": now, "expires_at": now + self.TTL_SECONDS, "status": "pending",
            "question": {"id": ident, "question": text, "options": cleaned,
                         "allow_custom": bool(question.get("allow_custom", True)),
                         "context": str(question.get("context") or "")[:1200]},
            "continuation": continuation,
        }
        path = self._path(ident, workspace, create=True)
        with self._locked(path):
            self._write(path, record)
        return self.public(record)

    def get(self, question_id, workspace=None):
        return self.public(self._read(self._path(question_id, workspace), workspace))

    def snapshot(self, question_id, workspace=None):
        record = self._read(self._path(question_id, workspace), workspace)
        result = {"clarification": self.public(record)}
        if record["status"] == "answered":
            result["result"] = record.get("result")
        return result

    def claim(self, question_id, option_id=None, text=None, workspace=None):
        path = self._path(question_id, workspace)
        if not path.is_file():
            raise QuestionStateError("确认问题不存在或不属于当前账号", 404)
        with self._locked(path):
            record = self._read(path, workspace)
            option_id, text = str(option_id or "").strip(), str(text or "").strip()
            option = next((o for o in record["question"]["options"] if o["id"] == option_id), None)
            if option_id and option is None:
                raise QuestionStateError("请选择该问题提供的有效选项")
            if text and (not record["question"]["allow_custom"] or len(text) > 3000):
                raise QuestionStateError("补充回答不正确或过长")
            if not option and not text:
                raise QuestionStateError("请选择一个选项或填写补充说明")
            answer = {"option_id": option_id, "label": option["label"] if option else "补充说明", "text": text}
            if record["status"] == "answered" and record.get("answer") == answer:
                return {"cached_result": record["result"]}
            if record["status"] != "pending":
                raise QuestionStateError("这个问题已提交处理，请勿重复执行", 409)
            record.update(status="running", answer=answer)
            self._write(path, record)
            return record

    def finish(self, question_id, result=None, error=None, workspace=None):
        path = self._path(question_id, workspace)
        with self._locked(path):
            record = self._read(path, workspace)
            record["status"] = "failed" if error else "answered"
            if error:
                record["error"] = str(error)[:300]
            else:
                record["result"] = result
            self._write(path, record)
