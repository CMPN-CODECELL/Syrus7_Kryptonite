"""Data layer: universe, immutable hashed price datasets, uploads, synthetic fixture."""

from .store import (
    MISSING_DATA_POLICY,
    SYNTHETIC_ID,
    Dataset,
    asset_infos,
    dataset_status,
    default_dataset_id,
    get_dataset,
    list_assets,
    refresh_from_yahoo,
)
from .universe import SURVIVORSHIP_NOTE, Asset, Universe, load_universe


def ingest_upload(data: bytes, filename: str):  # -> DatasetInfo
    from .upload import ingest_upload as _ingest

    return _ingest(data, filename)


__all__ = [
    "MISSING_DATA_POLICY",
    "SURVIVORSHIP_NOTE",
    "SYNTHETIC_ID",
    "Asset",
    "Dataset",
    "Universe",
    "asset_infos",
    "dataset_status",
    "default_dataset_id",
    "get_dataset",
    "ingest_upload",
    "list_assets",
    "load_universe",
    "refresh_from_yahoo",
]
