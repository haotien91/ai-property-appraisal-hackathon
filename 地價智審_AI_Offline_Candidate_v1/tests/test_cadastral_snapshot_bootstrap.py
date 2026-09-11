# -*- coding: utf-8 -*-
"""
Phase DEPLOY-1E: tests for backend/handlers/cadastral_snapshot_bootstrap.py
-- the S3-to-/tmp bootstrap for data/cadastral_dataset_cache.sqlite3
(~407MB, structurally too large for a Lambda Layer). This module ONLY
moves a file; RealExpropriationCaseProvider/RealLandPriceProvider (both
frozen, both UNMODIFIED by this round) remain the only code that ever
opens the snapshot as a database.

Central invariant under test throughout this file (Phase DEPLOY-1E's
explicit design correction): once `ensure_cadastral_snapshot()` returns,
CADASTRAL_DATASET_CACHE_DB_PATH is in exactly one of two states -- a
byte-for-byte VERIFIED snapshot, or ABSENT. Never a stale/checksum-
mismatched file. This is proven here at the FILE level (this module's own
contract) AND at the FROZEN PROVIDER level (RealExpropriationCaseProvider/
RealLandPriceProvider, using the real frozen classes, never mocked/
stubbed) -- both, per this round's explicit correction, using DIRECT
evidence rather than inferring one from the other's similar structure.

None of these tests mount the real host 407MB data/cadastral_dataset_
cache.sqlite3 into a fake "Lambda artifact" and call that a production
bootstrap PASS -- small in-memory/tmp_path fixture content stands in for
S3 objects throughout. The one exception (test_golden_...) explicitly
reads the REAL local snapshot directly via CADASTRAL_DATASET_CACHE_DB_PATH
(the same override this project already uses everywhere else), which is
a Golden Case regression check, not a claim about S3/Lambda packaging.
"""
import hashlib
import os
import sys
from decimal import Decimal

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402

import cadastral_snapshot_bootstrap as bootstrap  # noqa: E402
from cadastral_snapshot_bootstrap import (  # noqa: E402
    ensure_cadastral_snapshot, SnapshotBootstrapStatus,
    ENV_S3_BUCKET, ENV_S3_KEY, ENV_SHA256, ENV_DB_PATH,
)

REAL_SNAPSHOT_PATH = os.path.join(REPO_ROOT, "data", "cadastral_dataset_cache.sqlite3")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _FakeS3Client:
    """Test double for boto3's S3 client -- only implements the one
    method this module actually calls."""

    def __init__(self, *, content: "bytes | None" = None, raise_error: "Exception | None" = None,
                 partial_bytes: "bytes | None" = None):
        self.content = content
        self.raise_error = raise_error
        self.partial_bytes = partial_bytes  # simulates a connection drop mid-write
        self.calls = []

    def download_file(self, bucket, key, local_path):
        self.calls.append((bucket, key, local_path))
        if self.partial_bytes is not None:
            # Simulate an interrupted download: some bytes land on disk,
            # then the transfer fails -- exactly the scenario the
            # temp-file-then-atomic-replace design must survive.
            with open(local_path, "wb") as f:
                f.write(self.partial_bytes)
            raise ConnectionError("simulated connection drop mid-download")
        if self.raise_error is not None:
            raise self.raise_error
        with open(local_path, "wb") as f:
            f.write(self.content)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (ENV_S3_BUCKET, ENV_S3_KEY, ENV_SHA256, ENV_DB_PATH):
        monkeypatch.delenv(var, raising=False)


def _set_env(monkeypatch, db_path, *, bucket="test-bucket",
             key="datasets/cadastral/v2026-09-06/cadastral_dataset_cache.sqlite3", sha256=None):
    monkeypatch.setenv(ENV_S3_BUCKET, bucket)
    monkeypatch.setenv(ENV_S3_KEY, key)
    monkeypatch.setenv(ENV_DB_PATH, db_path)
    if sha256 is not None:
        monkeypatch.setenv(ENV_SHA256, sha256)


# -- 1/8: valid local snapshot / warm reuse -> no S3 call -----------------

def test_valid_local_snapshot_no_s3_call(tmp_path, monkeypatch):
    content = b"fake-sqlite-bytes-valid-snapshot"
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    with open(db_path, "wb") as f:
        f.write(content)
    _set_env(monkeypatch, db_path, sha256=_sha256(content))

    client = _FakeS3Client(content=b"should-never-be-used")
    result = ensure_cadastral_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.ALREADY_PRESENT
    assert result.local_path == db_path
    assert client.calls == []  # no S3 call at all
    with open(db_path, "rb") as f:
        assert f.read() == content  # untouched


def test_warm_reuse_no_redownload_across_repeated_calls(tmp_path, monkeypatch):
    content = b"fake-sqlite-bytes-warm-reuse"
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    with open(db_path, "wb") as f:
        f.write(content)
    _set_env(monkeypatch, db_path, sha256=_sha256(content))
    client = _FakeS3Client(content=b"should-never-be-used")

    r1 = ensure_cadastral_snapshot(s3_client=client)
    r2 = ensure_cadastral_snapshot(s3_client=client)
    r3 = ensure_cadastral_snapshot(s3_client=client)

    assert all(r.status == SnapshotBootstrapStatus.ALREADY_PRESENT for r in (r1, r2, r3))
    assert client.calls == []


# -- 2/3: missing snapshot -> download; success -> SHA PASS -> atomic publish

def test_missing_snapshot_triggers_download_and_atomic_publish(tmp_path, monkeypatch):
    content = b"fake-sqlite-bytes-freshly-downloaded"
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    assert not os.path.exists(db_path)
    _set_env(monkeypatch, db_path, sha256=_sha256(content))
    client = _FakeS3Client(content=content)

    result = ensure_cadastral_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.DOWNLOADED
    assert result.local_path == db_path
    assert len(client.calls) == 1
    bucket, key, local_path = client.calls[0]
    assert bucket == "test-bucket"
    assert key == "datasets/cadastral/v2026-09-06/cadastral_dataset_cache.sqlite3"
    with open(db_path, "rb") as f:
        assert f.read() == content
    # No orphaned temp file left behind in the target directory.
    assert os.listdir(tmp_path) == [os.path.basename(db_path)]


# -- 4: download checksum mismatch -> reject -------------------------------

def test_download_checksum_mismatch_rejected(tmp_path, monkeypatch):
    downloaded_content = b"wrong-content-does-not-match-expected-hash"
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    _set_env(monkeypatch, db_path, sha256=_sha256(b"completely-different-expected-content"))
    client = _FakeS3Client(content=downloaded_content)

    result = ensure_cadastral_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.DATASET_UNAVAILABLE
    assert not os.path.exists(db_path)  # never published
    assert os.listdir(tmp_path) == []  # temp file cleaned up, nothing orphaned


# -- 5/6: THE core correction -- stale existing snapshot ------------------

def test_stale_snapshot_successfully_replaced_by_new_verified_download(tmp_path, monkeypatch):
    stale_content = b"old-stale-snapshot-content"
    new_content = b"new-verified-snapshot-content"
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    with open(db_path, "wb") as f:
        f.write(stale_content)
    # Expected hash is for the NEW content -- the existing file's hash
    # will not match, triggering the stale-detection path.
    _set_env(monkeypatch, db_path, sha256=_sha256(new_content))
    client = _FakeS3Client(content=new_content)

    result = ensure_cadastral_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.DOWNLOADED
    assert len(client.calls) == 1  # stale file did NOT prevent a fresh download
    with open(db_path, "rb") as f:
        assert f.read() == new_content  # stale content is gone, new verified content in place


def test_checksum_mismatch_and_download_failure_never_uses_stale_snapshot(tmp_path, monkeypatch):
    """Phase DEPLOY-1E §2's named regression test: an existing snapshot
    already PROVEN wrong (checksum mismatch) must never be left in place
    for a Real provider to open, even when the subsequent re-download
    itself fails. The ONLY acceptable end states are a freshly-verified
    file, or no file at all -- never the stale one."""
    stale_content = b"old-stale-snapshot-content-known-wrong"
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    with open(db_path, "wb") as f:
        f.write(stale_content)
    _set_env(monkeypatch, db_path, sha256=_sha256(b"expected-content-that-download-will-fail-to-produce"))
    client = _FakeS3Client(raise_error=ConnectionError("S3 unreachable"))

    result = ensure_cadastral_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.DATASET_UNAVAILABLE
    assert not os.path.exists(db_path), "stale snapshot must be gone, never left in place as a fallback"
    assert os.listdir(tmp_path) == []  # no orphaned temp file either


# -- 7: partial download -> canonical final never becomes partial --------

def test_partial_download_never_exposed_as_canonical(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    _set_env(monkeypatch, db_path, sha256=_sha256(b"the-full-expected-content-that-never-arrives"))
    client = _FakeS3Client(partial_bytes=b"only-half-the-fi")  # connection drops mid-transfer

    result = ensure_cadastral_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.DATASET_UNAVAILABLE
    assert not os.path.exists(db_path)
    assert os.listdir(tmp_path) == []  # the partial temp file was cleaned up, not renamed into place


def test_partial_download_does_not_corrupt_a_previously_good_snapshot(tmp_path, monkeypatch):
    # A DIFFERENT scenario from the stale-detection tests above: here the
    # EXISTING file is already correct (checksum matches) when a later
    # invocation's re-verification + hypothetical re-download would be
    # triggered by a corrupted read -- but since it verifies fine, no
    # download should even be attempted, and a good file must never be
    # put at risk by an unrelated interrupted-download code path.
    good_content = b"already-good-and-verified-content"
    db_path = str(tmp_path / "cadastral_dataset_cache.sqlite3")
    with open(db_path, "wb") as f:
        f.write(good_content)
    _set_env(monkeypatch, db_path, sha256=_sha256(good_content))
    client = _FakeS3Client(partial_bytes=b"irrelevant")  # must never be reached

    result = ensure_cadastral_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.ALREADY_PRESENT
    assert client.calls == []
    with open(db_path, "rb") as f:
        assert f.read() == good_content


# -- 9: bootstrap unavailable -> NO Mock fallback --------------------------

def test_missing_configuration_is_dataset_unavailable_never_raises():
    # No env vars set at all (autouse fixture clears them) -- must
    # degrade cleanly, never crash the whole handler.
    result = ensure_cadastral_snapshot(s3_client=_FakeS3Client(content=b"unused"))
    assert result.status == SnapshotBootstrapStatus.DATASET_UNAVAILABLE
    assert result.local_path is None


def test_bootstrap_module_has_no_mock_or_golden_fallback_concept():
    # Structural guarantee, not just behavioral: this module's source
    # must never reference Mock/Golden data at all -- it has no business
    # knowing what those are.
    import inspect
    source = inspect.getsource(bootstrap)
    assert "Mock" not in source.replace("Mock/Golden", "").replace("Mock fallback", "").replace(
        "never falls back to Mock", "").replace("MOCK", "")
    assert "Golden" not in source.replace("Golden data", "").replace("fallback to Mock/Golden data", "").replace(
        "Golden Case", "")


# -- 10/11: frozen Real providers' DIRECT unavailable-semantics evidence --
# (per this round's explicit instruction: direct evidence for BOTH
# providers, not inferred from one to the other's similar structure)

def test_real_expropriation_provider_unknown_when_snapshot_unavailable(tmp_path, monkeypatch):
    from base import ProviderContext
    from cadastral_dataset_cache import CadastralDatasetCache
    from expropriation_case_provider import RealExpropriationCaseProvider
    from domain.models import ExpropriationCaseStatus

    # A cache pointed at a path where NOTHING has ever been synced --
    # CadastralDatasetCache.__init__ creates an empty schema (frozen,
    # unmodified behavior, confirmed this round by reading it directly),
    # so every lookup finds no snapshot_meta row and raises
    # CadastralDatasetCacheError, which the frozen provider itself (not
    # this test, not this round's new module) already converts to UNKNOWN.
    cache = CadastralDatasetCache(db_path=str(tmp_path / "never_synced.sqlite3"))
    provider = RealExpropriationCaseProvider(cache=cache)
    ctx = ProviderContext(case_no="X", city="新北市", district="金山區", segment_code="P002-00", parcel_id="金美段489地號")

    evidence = provider.query_case(ctx)
    assert evidence.status == ExpropriationCaseStatus.UNKNOWN
    assert evidence.requires_manual_review is True


def test_real_land_price_provider_unknown_when_snapshot_unavailable(tmp_path, monkeypatch):
    from base import ProviderContext
    from cadastral_dataset_cache import CadastralDatasetCache
    from land_price_provider import RealLandPriceProvider
    from domain.models import LandPriceFieldStatus

    cache = CadastralDatasetCache(db_path=str(tmp_path / "never_synced.sqlite3"))
    provider = RealLandPriceProvider(cache=cache)
    ctx = ProviderContext(case_no="X", city="新北市", district="金山區", segment_code="P002-00", parcel_id="金美段489地號")

    evidence = provider.query_land_price(ctx)
    assert evidence.announced_land_current_value_status == LandPriceFieldStatus.UNKNOWN
    assert evidence.requires_manual_review is True


# -- 12: Golden cadastral snapshot semantics (existing real local data) --

@pytest.mark.skipif(not os.path.isfile(REAL_SNAPSHOT_PATH),
                     reason="data/cadastral_dataset_cache.sqlite3 尚未由 scripts/sync_*_dataset.py 產生")
def test_golden_land_price_and_expropriation_semantics(monkeypatch):
    # NOTE (per this round's explicit correction): this Golden Case is
    # 金山區/金美段/489's 公告土地現值/徵收 data (the Official Cadastral
    # Evidence Pipeline) -- NOT F/F25/1027/04890000, which is the
    # UNRELATED NLSC Official Parcel Coordinate Pipeline's own Golden
    # Case (a different snapshot: nlsc_code_cache.sqlite3, already frozen
    # and tested separately). Deliberately reads the REAL local snapshot
    # directly via CADASTRAL_DATASET_CACHE_DB_PATH -- this is a Golden
    # Case regression check reusing this project's existing override
    # convention, not a claim about S3/Lambda packaging or bootstrap.
    from base import ProviderContext
    from cadastral_dataset_cache import CadastralDatasetCache
    from expropriation_case_provider import RealExpropriationCaseProvider
    from land_price_provider import RealLandPriceProvider
    from domain.models import ExpropriationCaseStatus, LandPriceFieldStatus

    cache = CadastralDatasetCache(db_path=REAL_SNAPSHOT_PATH)
    ctx = ProviderContext(case_no="GOLDEN", city="新北市", district="金山區", segment_code="P002-00", parcel_id="金美段489地號")

    land_price = RealLandPriceProvider(cache=cache).query_land_price(ctx)
    assert land_price.announced_land_current_value_status == LandPriceFieldStatus.AVAILABLE
    assert land_price.announced_land_current_value == Decimal("57305")

    expropriation = RealExpropriationCaseProvider(cache=cache).query_case(ctx)
    assert expropriation.status == ExpropriationCaseStatus.NOT_FOUND_IN_DATASET
