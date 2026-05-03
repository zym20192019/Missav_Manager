import json
import time
import threading
from pathlib import Path
from typing import Optional

HISTORY_FILE = Path(__file__).parent.parent / "task_history.json"


class TaskManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: dict[str, list] = {}  # task_id -> [queue, ...]
        self.tasks: dict[str, dict] = {}
        self._load_history()

    def _load_history(self):
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r") as f:
                    self.tasks = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.tasks = {}

    def _save_history(self):
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.tasks, f, indent=2, ensure_ascii=False)
        except IOError:
            pass

    def create_task(self, task_id: str, url: str, **kwargs) -> dict:
        with self._lock:
            task = {
                "task_id": task_id,
                "url": url,
                "title": kwargs.get("title", ""),
                "thumbnail": kwargs.get("thumbnail", ""),
                "site": kwargs.get("site", ""),
                "status": "queued",
                "progress": 0.0,
                "speed": "",
                "eta": "",
                "total_segments": 0,
                "downloaded_segments": 0,
                "filepath": "",
                "filesize": 0,
                "error": "",
                "created_at": time.time(),
            }
            self.tasks[task_id] = task
            self._save_history()
            self._notify(task_id, task)
            return task

    def update_task(self, task_id: str, **kwargs):
        with self._lock:
            if task_id not in self.tasks:
                return
            for k, v in kwargs.items():
                self.tasks[task_id][k] = v
            self._save_history()
            self._notify(task_id, self.tasks[task_id])

    def get_task(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        return [t for t in self.tasks.values() if not t.get("is_child")]

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self.tasks:
                filepath = self.tasks[task_id].get("filepath", "")
                if filepath and Path(filepath).exists():
                    try:
                        Path(filepath).unlink()
                    except OSError:
                        pass
                del self.tasks[task_id]
                self._save_history()
                return True
            return False

    def set_done(self, task_id: str, filepath: str, filesize: int):
        self.update_task(task_id, status="done", filepath=filepath, filesize=filesize, progress=100.0)

    def set_error(self, task_id: str, error: str):
        self.update_task(task_id, status="error", error=error)

    def subscribe(self, task_id: str) -> "asyncio.Queue":
        import asyncio
        q = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q):
        if task_id in self._subscribers:
            self._subscribers[task_id] = [x for x in self._subscribers[task_id] if x is not q]

    def _notify(self, task_id: str, data: dict):
        import asyncio
        if task_id in self._subscribers:
            for q in self._subscribers[task_id]:
                try:
                    q.put_nowait(data)
                except Exception:
                    pass


task_manager = TaskManager()
