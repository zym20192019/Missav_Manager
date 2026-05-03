import os
import json
import uuid
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.models import (
    DownloadRequest, TaskResponse, TaskListResponse,
    MoveRequest, MoveResponse, BatchMoveRequest, CloudPath,
)
from app.tasks import task_manager
from app.downloader import download_video, move_to_cloud_drive, detect_site

PATH_CONFIG_FILE = Path(__file__).parent.parent / "path_config.json"


def load_path_config() -> list[dict]:
    if PATH_CONFIG_FILE.exists():
        try:
            with open(PATH_CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return [{"id": "default", "name": "下载目录", "path": "/root/jable-downloader/downloads", "icon": "📁", "auto_move": False}]


def save_path_config(paths: list[dict]):
    with open(PATH_CONFIG_FILE, "w") as f:
        json.dump(paths, f, indent=2, ensure_ascii=False)


app = FastAPI(title="Jable/MissAV Downloader", description="M3U8 Video Downloader with Liquid Glass UI")

# Download queue
MAX_CONCURRENT_DOWNLOADS = 2
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
download_queue: asyncio.Queue = asyncio.Queue()


async def download_worker():
    while True:
        task_id, url = await download_queue.get()
        try:
            async with DOWNLOAD_SEMAPHORE:
                await download_video(task_id, url)
                # Auto-move if configured
                auto_path = get_auto_move_path()
                if auto_path:
                    task = task_manager.get_task(task_id)
                    if task and task.get("status") == "done":
                        try:
                            await move_to_cloud_drive(task_id, auto_path["path"], auto_path["name"])
                        except Exception:
                            pass
        except Exception as e:
            task_manager.set_error(task_id, str(e))
        finally:
            download_queue.task_done()


def get_auto_move_path() -> Optional[dict]:
    for p in load_path_config():
        if p.get("auto_move", False):
            return p
    return None


@app.on_event("startup")
async def startup():
    asyncio.create_task(download_worker())


# Auth
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "jable2026"
ACTIVE_TOKENS: dict[str, str] = {}

AUTH_WHITELIST = {"/", "/api/login", "/api/health", "/api/queue/status", "/favicon.ico"}


def get_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth[7:]
    if token not in ACTIVE_TOKENS:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in AUTH_WHITELIST or path.startswith("/static") or path.startswith("/ws"):
        return await call_next(request)
    try:
        get_token(request)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    return await call_next(request)


app.middleware("http")(auth_middleware)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/login")
async def login(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")
    username = body.get("username", "")
    password = body.get("password", "")
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = str(uuid.uuid4())
    ACTIVE_TOKENS[token] = username
    return {"token": token}


@app.post("/api/logout")
async def logout(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    ACTIVE_TOKENS.pop(token, None)
    return {"success": True}


@app.get("/api/queue/status")
async def queue_status():
    return {"pending": download_queue.qsize(), "max_concurrent": MAX_CONCURRENT_DOWNLOADS}


# ---- Download ----

@app.post("/api/download", response_model=TaskResponse)
async def create_download(req: DownloadRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    site = detect_site(url)
    if not site:
        raise HTTPException(status_code=400, detail="Unsupported URL. Please use JableTV or MissAV links.")

    task_id = f"task_{uuid.uuid4().hex[:8]}"
    task_manager.create_task(task_id, url, site=site)
    await download_queue.put((task_id, url))
    return TaskResponse(**task_manager.get_task(task_id))


@app.post("/api/downloads/batch")
async def batch_download(request: Request):
    body = await request.json()
    urls = body.get("urls", [])
    results = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        site = detect_site(url)
        if not site:
            results.append({"url": url, "error": "Unsupported URL"})
            continue
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task_manager.create_task(task_id, url, site=site)
        await download_queue.put((task_id, url))
        results.append({"url": url, "task_id": task_id})
    return {"created": len(results), "tasks": results}


# ---- Tasks ----

@app.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks(q: Optional[str] = None, status: Optional[str] = None):
    tasks = task_manager.list_tasks()
    if q:
        q_lower = q.lower()
        tasks = [t for t in tasks if q_lower in (t.get("title") or "").lower() or q_lower in (t.get("url") or "").lower()]
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    tasks.sort(key=lambda t: t.get("created_at", 0), reverse=True)
    return TaskListResponse(tasks=[TaskResponse(**t) for t in tasks], total=len(tasks))


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    if not task_manager.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


@app.post("/api/tasks/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") != "error":
        raise HTTPException(status_code=400, detail="Only failed tasks can be retried")
    url = task["url"]
    task_manager.delete_task(task_id)
    new_id = f"task_{uuid.uuid4().hex[:8]}"
    task_manager.create_task(new_id, url, site=task.get("site", ""))
    await download_queue.put((new_id, url))
    return TaskResponse(**task_manager.get_task(new_id))


@app.post("/api/tasks/batch-move")
async def batch_move(req: BatchMoveRequest):
    results = {"success": 0, "failed": 0, "details": []}
    for task_id in req.task_ids:
        result = await move_to_cloud_drive(task_id, req.target_path, req.target_name)
        if result:
            results["success"] += 1
        else:
            results["failed"] += 1
    return results


@app.post("/api/tasks/batch-delete")
async def batch_delete(request: Request):
    body = await request.json()
    task_ids = body.get("task_ids", [])
    results = {"success": 0, "failed": 0}
    for tid in task_ids:
        if task_manager.delete_task(tid):
            results["success"] += 1
        else:
            results["failed"] += 1
    return results


# ---- Move ----

@app.post("/api/move", response_model=MoveResponse)
async def move_file(req: MoveRequest):
    task = task_manager.get_task(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.get("filepath"):
        raise HTTPException(status_code=400, detail="No file to move")
    result = await move_to_cloud_drive(req.task_id, req.target_path, req.target_name)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to move file")
    src, dest = result
    return MoveResponse(success=True, task_id=req.task_id, original_path=src, new_path=dest, target=req.target_name or req.target_path)


# ---- Paths ----

@app.get("/api/paths", response_model=list[CloudPath])
async def get_paths():
    return load_path_config()


@app.post("/api/paths", response_model=CloudPath)
async def add_path(req: CloudPath):
    paths = load_path_config()
    for p in paths:
        if p["path"] == req.path:
            raise HTTPException(status_code=400, detail="Path already exists")
    paths.append(req.model_dump())
    save_path_config(paths)
    return req


@app.delete("/api/paths/{path_id}")
async def delete_path(path_id: str):
    paths = load_path_config()
    new_paths = [p for p in paths if p["id"] != path_id]
    if len(new_paths) == len(paths):
        raise HTTPException(status_code=404, detail="Path not found")
    save_path_config(new_paths)
    return {"success": True}


@app.post("/api/paths/{path_id}/auto-move")
async def toggle_auto_move(path_id: str):
    paths = load_path_config()
    target = None
    for p in paths:
        if p["id"] == path_id:
            target = p
            break
    if not target:
        raise HTTPException(status_code=404, detail="Path not found")
    new_state = not target.get("auto_move", False)
    for p in paths:
        p["auto_move"] = False
    target["auto_move"] = new_state
    save_path_config(paths)
    return {"success": True, "auto_move": new_state, "path_id": path_id}


@app.get("/api/paths/auto-move")
async def get_auto_move():
    for p in load_path_config():
        if p.get("auto_move", False):
            return {"enabled": True, "path": p["path"], "name": p["name"], "path_id": p["id"]}
    return {"enabled": False}


# ---- WebSocket ----

@app.websocket("/ws/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str):
    await websocket.accept()
    q = task_manager.subscribe(task_id)
    try:
        while True:
            data = await q.get()
            await websocket.send_json(data)
    except WebSocketDisconnect:
        pass
    finally:
        task_manager.unsubscribe(task_id, q)


# Mount static files
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "static")), name="static")


@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    return FileResponse(str(Path(__file__).parent.parent / "static" / "index.html"))
