"""Uploaded datasets in the cloud: files live in the private bucket, ownership in Firestore.

The data layer reads datasets from `<data_dir>/cache/datasets/<id>.*`. Cloud
Run instances have no shared disk, so an uploaded dataset is copied to the
bucket once (`push`) and materialised on whichever instance needs it
(`ensure_local`): the API instance for assets/preflight, the worker for the run.
Dataset ids are content addressed (`upload-<sha>`), so identical files dedupe.
"""

from __future__ import annotations

from pathlib import Path

from ..jobs.fsutil import atomic_write_bytes
from ..settings import Settings
from .blobs import BlobStore

SUFFIXES = (".parquet", ".close.parquet", ".json")


def is_upload_id(dataset_id: str | None) -> bool:
    return bool(dataset_id) and str(dataset_id).startswith("upload-")


def local_dir(settings: Settings) -> Path:
    return settings.data_dir / "cache" / "datasets"


def object_name(dataset_id: str, suffix: str) -> str:
    return f"datasets/{dataset_id}/{dataset_id}{suffix}"


def push(settings: Settings, blobs: BlobStore, dataset_id: str) -> list[str]:
    """Copy the dataset's local files to the bucket. Returns the object names written."""
    names: list[str] = []
    for suffix in SUFFIXES:
        p = local_dir(settings) / f"{dataset_id}{suffix}"
        if p.is_file():
            name = object_name(dataset_id, suffix)
            blobs.put(name, p.read_bytes(), "application/json" if suffix == ".json" else "application/octet-stream")
            names.append(name)
    return names


def ensure_local(settings: Settings, blobs: BlobStore, dataset_id: str, objects: list[str] | None = None) -> bool:
    """Make sure the dataset's files exist on this instance. Returns False if the bucket no longer has them."""
    d = local_dir(settings)
    if (d / f"{dataset_id}.json").is_file() and (d / f"{dataset_id}.parquet").is_file():
        return True
    wanted = objects if objects is not None else [object_name(dataset_id, s) for s in SUFFIXES]
    found = 0
    for name in wanted:
        if not name.startswith(f"datasets/{dataset_id}/"):
            continue
        data = blobs.get(name)
        if data is None:
            continue
        atomic_write_bytes(d / name.rsplit("/", 1)[-1], data)
        found += 1
    return found > 0 or objects == []
