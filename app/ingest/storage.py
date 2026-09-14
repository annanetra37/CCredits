"""Where the original file goes.

A container filesystem is ephemeral, so a deployment points at object storage
and local development writes to a directory. The original bytes are stored
unmodified alongside their hash — that file is the evidence the lineage view
ultimately points at, so it has to outlive the container that received it.

Azure Blob Storage is the deployment target. S3 is kept for anyone running
elsewhere; both are reached through the same two functions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

log = logging.getLogger("ccredits.storage")


def _key(sha256: str, filename: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y/%m")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    return f"{stamp}/{sha256[:12]}-{safe}"


def store(data: bytes, sha256: str, filename: str) -> str:
    """Write the original bytes. Returns the path recorded on the source file."""
    key = _key(sha256, filename)
    backend = settings.storage_backend.lower()
    if backend == "azure":
        return _store_azure(data, key)
    if backend == "s3":
        return _store_s3(data, key)
    return _store_local(data, key)


def _azure_container():
    """A container client, from a connection string or a managed identity.

    Managed identity is preferred: it puts no storage secret in configuration
    at all. Assign the app's identity the Storage Blob Data Contributor role on
    the account and set only AZURE_STORAGE_ACCOUNT.
    """
    from azure.storage.blob import BlobServiceClient

    if settings.azure_storage_connection_string:
        service = BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string
        )
    elif settings.azure_storage_account:
        from azure.identity import DefaultAzureCredential

        service = BlobServiceClient(
            account_url=f"https://{settings.azure_storage_account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
    else:
        raise RuntimeError(
            "STORAGE_BACKEND=azure needs AZURE_STORAGE_CONNECTION_STRING, or "
            "AZURE_STORAGE_ACCOUNT when using a managed identity"
        )
    container = service.get_container_client(settings.azure_storage_container)
    try:
        container.create_container()
    except Exception:
        pass  # already there, or the identity may only write blobs
    return container


def _store_azure(data: bytes, key: str) -> str:
    container = _azure_container()
    container.upload_blob(name=key, data=data, overwrite=False)
    return f"azure://{settings.azure_storage_container}/{key}"


def _store_local(data: bytes, key: str) -> str:
    target = Path(settings.storage_dir) / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return str(target)


def _store_s3(data: bytes, key: str) -> str:
    import boto3

    if not settings.s3_bucket:
        raise RuntimeError("STORAGE_BACKEND=s3 but S3_BUCKET is not set")
    client = boto3.client(
        "s3",
        region_name=settings.s3_region or None,
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
    )
    full_key = f"{settings.s3_prefix.rstrip('/')}/{key}" if settings.s3_prefix else key
    client.put_object(Bucket=settings.s3_bucket, Key=full_key, Body=data)
    return f"s3://{settings.s3_bucket}/{full_key}"


def read(storage_path: str) -> bytes:
    if storage_path.startswith("azure://"):
        _, _, rest = storage_path.partition("azure://")
        _container, _, key = rest.partition("/")
        return _azure_container().download_blob(key).readall()
    if storage_path.startswith("s3://"):
        import boto3

        _, _, rest = storage_path.partition("s3://")
        bucket, _, key = rest.partition("/")
        client = boto3.client(
            "s3",
            region_name=settings.s3_region or None,
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return Path(storage_path).read_bytes()
