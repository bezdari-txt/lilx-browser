"""Download management on top of QWebEngineDownloadRequest."""

from __future__ import annotations

import dataclasses
import logging
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest

from lilx.core.settings import SettingsManager
from lilx.core.storage import StorageBackend, StorageError
from lilx.paths import default_download_dir

log = logging.getLogger(__name__)

DOWNLOADS_DOCUMENT = "downloads.json"
_MAX_RECORDS = 200

_State = QWebEngineDownloadRequest.DownloadState
_STATE_NAMES = {
    _State.DownloadRequested: "in_progress",
    _State.DownloadInProgress: "in_progress",
    _State.DownloadCompleted: "completed",
    _State.DownloadCancelled: "cancelled",
    _State.DownloadInterrupted: "interrupted",
}
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


@dataclass
class DownloadRecord:
    id: str
    url: str
    file_name: str
    directory: str
    state: str = "in_progress"
    received: int = 0
    total: int = -1
    started_at: float = 0.0
    finished_at: float | None = None
    error: str = ""

    @property
    def path(self) -> Path:
        return Path(self.directory) / self.file_name

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["exists"] = self.state == "completed" and self.path.exists()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DownloadRecord:
        known = {f.name for f in dataclasses.fields(cls)}
        record = cls(**{k: v for k, v in data.items() if k in known})
        if record.state == "in_progress":  # lilx was closed mid-download
            record.state = "interrupted"
            record.error = record.error or "lilx was closed"
        return record


def safe_file_name(name: str) -> str:
    name = _UNSAFE_CHARS.sub("_", name).strip().lstrip(".")
    return name[:200] or "download"


def unique_path(directory: Path, name: str) -> Path:
    """``file.zip`` -> ``file (1).zip`` if the name is taken."""
    candidate = directory / name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


class DownloadManager(QObject):
    changed = Signal()
    started = Signal(str)  # file name

    def __init__(self, storage: StorageBackend, settings: SettingsManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._storage = storage
        self._settings = settings
        self._records: dict[str, DownloadRecord] = {}
        self._requests: dict[str, QWebEngineDownloadRequest] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            data = self._storage.read_json(DOWNLOADS_DOCUMENT) or []
        except StorageError as exc:
            log.error("Download list is unreadable: %s", exc)
            return
        for item in data if isinstance(data, list) else []:
            try:
                record = DownloadRecord.from_dict(item)
            except TypeError:
                continue
            self._records[record.id] = record

    def save(self) -> None:
        records = list(self._records.values())[-_MAX_RECORDS:]
        try:
            self._storage.write_json(DOWNLOADS_DOCUMENT, [dataclasses.asdict(r) for r in records])
        except StorageError as exc:
            log.error("Could not save download list: %s", exc)

    # -- incoming downloads --------------------------------------------------
    def target_directory(self) -> Path:
        configured = self._settings.current.download_dir
        directory = Path(configured) if configured else default_download_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def handle_request(self, request: QWebEngineDownloadRequest) -> None:
        try:
            directory = self.target_directory()
        except OSError as exc:
            log.error("Download folder is not usable: %s", exc)
            request.cancel()
            return

        name = safe_file_name(request.downloadFileName() or request.suggestedFileName())
        target = unique_path(directory, name)
        request.setDownloadDirectory(str(directory))
        request.setDownloadFileName(target.name)

        record = DownloadRecord(
            id=uuid.uuid4().hex,
            url=request.url().toString(),
            file_name=target.name,
            directory=str(directory),
            total=request.totalBytes(),
            started_at=time.time(),
        )
        self._records[record.id] = record
        self._requests[record.id] = request
        request.receivedBytesChanged.connect(partial(self._on_progress, record.id))
        request.totalBytesChanged.connect(partial(self._on_progress, record.id))
        request.stateChanged.connect(partial(self._on_state, record.id))
        request.accept()
        log.info("Download started: %s", target)
        self.started.emit(record.file_name)
        self.changed.emit()

    def _on_progress(self, record_id: str, *_: Any) -> None:
        request = self._requests.get(record_id)
        record = self._records.get(record_id)
        if request is None or record is None:
            return
        record.received = request.receivedBytes()
        record.total = request.totalBytes()
        self.changed.emit()

    def _on_state(self, record_id: str, state: QWebEngineDownloadRequest.DownloadState) -> None:
        record = self._records.get(record_id)
        request = self._requests.get(record_id)
        if record is None:
            return
        record.state = _STATE_NAMES.get(state, "interrupted")
        if request is not None:
            record.received = request.receivedBytes()
            record.total = request.totalBytes()
            if record.state == "interrupted":
                record.error = request.interruptReasonString()
        if record.state != "in_progress":
            record.finished_at = time.time()
            self._requests.pop(record_id, None)
            self.save()
        self.changed.emit()

    # -- queries and actions -------------------------------------------------
    def records(self) -> list[DownloadRecord]:
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)

    def active_count(self) -> int:
        return len(self._requests)

    def cancel(self, record_id: str) -> None:
        request = self._requests.get(record_id)
        if request is not None:
            request.cancel()

    def open_file(self, record_id: str) -> bool:
        record = self._records.get(record_id)
        if record is None or not record.path.exists():
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(record.path)))

    def show_in_folder(self, record_id: str) -> bool:
        record = self._records.get(record_id)
        if record is None:
            return False
        path = record.path
        if sys.platform == "darwin" and path.exists():
            try:
                subprocess.Popen(["open", "-R", str(path)])  # reveal and select in Finder
                return True
            except OSError as exc:
                log.warning("Could not reveal file: %s", exc)
        folder = path.parent if path.parent.exists() else None
        return bool(folder) and QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_directory(self) -> bool:
        try:
            directory = self.target_directory()
        except OSError:
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def remove(self, record_id: str) -> None:
        """Remove from the list (the file itself is kept)."""
        if record_id in self._requests:
            return
        if self._records.pop(record_id, None) is not None:
            self.save()
            self.changed.emit()

    def clear_finished(self) -> None:
        self._records = {k: v for k, v in self._records.items() if k in self._requests}
        self.save()
        self.changed.emit()
