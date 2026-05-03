import enum
from typing import Optional
from pydantic import BaseModel, Field


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    MOVING = "moving"
    DONE = "done"
    MOVED = "moved"
    ERROR = "error"
    PAUSED = "paused"


class DownloadRequest(BaseModel):
    url: str = Field(..., description="JableTV or MissAV video URL")


class TaskResponse(BaseModel):
    task_id: str
    url: Optional[str] = ""
    title: Optional[str] = ""
    thumbnail: Optional[str] = ""
    site: Optional[str] = ""
    status: Optional[str] = "pending"
    progress: float = 0.0
    speed: Optional[str] = ""
    eta: Optional[str] = ""
    total_segments: int = 0
    downloaded_segments: int = 0
    filepath: Optional[str] = ""
    filesize: Optional[int] = 0
    error: Optional[str] = ""
    created_at: float = 0.0


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


class MoveRequest(BaseModel):
    task_id: str
    target_path: str
    target_name: Optional[str] = None


class MoveResponse(BaseModel):
    success: bool
    task_id: str
    original_path: Optional[str] = None
    new_path: Optional[str] = None
    target: Optional[str] = None


class BatchMoveRequest(BaseModel):
    task_ids: list[str]
    target_path: str
    target_name: Optional[str] = None


class CloudPath(BaseModel):
    id: str = Field(..., description="Path ID")
    name: str = Field(..., description="Display name")
    path: str = Field(..., description="Full path")
    icon: Optional[str] = Field(default="📁", description="Icon emoji")
    auto_move: bool = Field(default=False, description="Auto-move downloaded files to this path")
