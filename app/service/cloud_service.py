import os
import time
import asyncio
import logging
import threading
from datetime import timedelta
from typing import Optional, Tuple
from google.cloud import storage

logger = logging.getLogger("cloud_service")

BLOB_PREFIX = "ai_call_recordings"
UPLOAD_ATTEMPTS = 3
UPLOAD_TIMEOUT_SEC = 120

class CloudStorageService:
    def __init__(
        self,
        environment: Optional[str] = None,
        bucket_name: Optional[str] = None,
        creds_path: Optional[str] = None,
    ):
        env_val = (environment or os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "").lower()
        if not env_val:
            call_url = os.getenv("CALL_SERVICE_URL", "").lower()
            if "prod" in call_url and "dev" not in call_url:
                env_val = "prod"
            else:
                env_val = "dev"

        self.environment = "prod" if "prod" in env_val else "dev"
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        creds_path = creds_path or os.getenv("GCS_CREDS_PATH") or None
        if creds_path:
            if not os.path.isabs(creds_path):
                creds_path = os.path.join(base_dir, creds_path)
            self.creds_path = creds_path
        else:
            env_creds_filename = f"green-light-eld-access-{self.environment}.json"
            self.creds_path = os.path.join(base_dir, "app", "utils", "creds", env_creds_filename)

        self.bucket_name = bucket_name or os.getenv("GCS_BUCKET_NAME") or f"util-{self.environment}"

        self._client: Optional[storage.Client] = None
        self._bucket: Optional[storage.Bucket] = None
        self._lock = threading.Lock()

    def _get_bucket(self) -> storage.Bucket:
        with self._lock:
            if self._bucket is None:
                if not os.path.exists(self.creds_path):
                    raise FileNotFoundError(f"[GCS] Service Account credentials not found at: {self.creds_path}")
                self._client = storage.Client.from_service_account_json(self.creds_path)
                self._bucket = self._client.bucket(self.bucket_name)
            return self._bucket

    @staticmethod
    def delete_local(local_path: str) -> bool:
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.info(f"[GCS] Local file deleted: {local_path}")
            return True
        except FileNotFoundError:
            return True
        except Exception as exc:
            logger.error(f"[GCS] Could not delete local file {local_path}: {exc}")
            return False

    def generate_signed_url(self, call_id_or_blob: str, expiration_days: int = 7) -> Optional[str]:
        try:
            if call_id_or_blob.startswith(f"{BLOB_PREFIX}/"):
                blob_name = call_id_or_blob
            else:
                clean_call_id = call_id_or_blob.removesuffix(".mp3")
                blob_name = f"{BLOB_PREFIX}/{clean_call_id}.mp3"

            bucket = self._get_bucket()
            blob = bucket.blob(blob_name)
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(days=expiration_days),
                method="GET",
            )
            return url
        except Exception as exc:
            logger.error(f"[GCS] Failed to generate signed URL for {call_id_or_blob}: {exc}")
            return None

    def upload_audio_sync(
        self,
        local_path: str,
        call_id: str,
        delete_local: bool = True,
        generate_signed: bool = True,
        signed_url_days: int = 7,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not os.path.exists(local_path):
            logger.warning(f"[GCS] Local audio file does not exist: {local_path}")
            return None, None

        clean_call_id = call_id.removesuffix(".mp3")
        blob_name = f"{BLOB_PREFIX}/{clean_call_id}.mp3"
        local_size = os.path.getsize(local_path)

        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            try:
                bucket = self._get_bucket()
                blob = bucket.blob(blob_name)
                blob.upload_from_filename(
                    local_path,
                    content_type="audio/mpeg",
                    timeout=UPLOAD_TIMEOUT_SEC,
                )

                if blob.size is not None and int(blob.size) != local_size:
                    raise IOError(f"size mismatch: local={local_size} remote={blob.size}")

                gcs_uri = f"gs://{self.bucket_name}/{blob_name}"
                logger.info(f"[GCS] Audio uploaded successfully -> {gcs_uri}")

                signed_url = None
                if generate_signed:
                    try:
                        signed_url = blob.generate_signed_url(
                            version="v4",
                            expiration=timedelta(days=signed_url_days),
                            method="GET",
                        )
                    except Exception as s_exc:
                        logger.warning(f"[GCS] Failed to generate signed url on upload: {s_exc}")

                if delete_local:
                    self.delete_local(local_path)
                return gcs_uri, signed_url

            except FileNotFoundError as exc:
                logger.error(f"[GCS] {exc}")
                return None, None
            except Exception as exc:
                logger.error(
                    f"[GCS] Upload attempt {attempt}/{UPLOAD_ATTEMPTS} failed for {local_path}: {exc}",
                    exc_info=True,
                )
                if attempt < UPLOAD_ATTEMPTS:
                    time.sleep(2 ** attempt)

        logger.error(f"[GCS] Giving up on {local_path}; local file is kept.")
        return None, None

    async def upload_audio(
        self,
        local_path: str,
        call_id: str,
        delete_local: bool = True,
        generate_signed: bool = True,
        signed_url_days: int = 7,
    ) -> Tuple[Optional[str], Optional[str]]:
        return await asyncio.to_thread(
            self.upload_audio_sync,
            local_path,
            call_id,
            delete_local,
            generate_signed,
            signed_url_days,
        )

    def upload_pending_sync(self, recordings_dir: str, min_age_sec: int = 300) -> int:
        if not os.path.isdir(recordings_dir):
            return 0

        now = time.time()
        uploaded = 0
        for name in sorted(os.listdir(recordings_dir)):
            path = os.path.join(recordings_dir, name)
            try:
                if not os.path.isfile(path):
                    continue
                age = now - os.path.getmtime(path)

                if name.endswith(".mp3.part"):
                    if age > 3600:
                        self.delete_local(path)
                    continue

                if not name.endswith(".mp3") or age < min_age_sec:
                    continue

                gcs_uri, _ = self.upload_audio_sync(path, name[: -len(".mp3")], delete_local=True)
                if gcs_uri:
                    uploaded += 1
            except Exception as exc:
                logger.warning(f"[GCS] Pending upload skipped for {path}: {exc}")
        return uploaded

    async def upload_pending(self, recordings_dir: str, min_age_sec: int = 300) -> int:
        return await asyncio.to_thread(self.upload_pending_sync, recordings_dir, min_age_sec)

_default_cloud_service: Optional[CloudStorageService] = None

def get_cloud_service() -> CloudStorageService:
    global _default_cloud_service
    if _default_cloud_service is None:
        _default_cloud_service = CloudStorageService()
    return _default_cloud_service
