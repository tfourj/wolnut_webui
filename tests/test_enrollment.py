import os
import threading

import pytest

from wolnut.enrollment import EnrollmentError, EnrollmentStore


def test_enrollment_token_is_single_use_and_stored_as_hash(tmp_path):
    store = EnrollmentStore(tmp_path / "security")
    created = store.create("server", 8184)

    raw = store.path.read_text()
    assert created["token"] not in raw
    assert os.stat(store.directory).st_mode & 0o777 == 0o700
    assert os.stat(store.path).st_mode & 0o777 == 0o600

    enrollment_id, record = store.claim(created["token"])
    assert enrollment_id == created["enrollment_id"]
    assert record["client_name"] == "server"
    with pytest.raises(EnrollmentError, match="already been used"):
        store.claim(created["token"])


def test_new_enrollment_supersedes_previous_client_token(tmp_path):
    store = EnrollmentStore(tmp_path / "security")
    first = store.create("server", 8184)
    second = store.create("server", 9191)

    assert store.status(first["enrollment_id"])["status"] == "superseded"
    assert store.status(second["enrollment_id"])["status"] == "pending"


def test_expired_enrollment_cannot_be_claimed(tmp_path):
    store = EnrollmentStore(tmp_path / "security")
    created = store.create("server", 8184, lifetime_seconds=-1)

    with pytest.raises(EnrollmentError, match="expired"):
        store.claim(created["token"])

    assert store.status(created["enrollment_id"])["status"] == "expired"


def test_only_one_concurrent_claim_succeeds(tmp_path):
    store = EnrollmentStore(tmp_path / "security")
    created = store.create("server", 8184)
    successes = []

    def claim():
        try:
            successes.append(store.claim(created["token"])[0])
        except EnrollmentError:
            pass

    threads = [threading.Thread(target=claim) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert successes == [created["enrollment_id"]]


def test_same_agent_can_retry_after_response_loss(tmp_path):
    store = EnrollmentStore(tmp_path / "security")
    created = store.create("server", 8184)

    first = store.claim(created["token"], agent_id="agent", csr_hash="csr-hash")
    second = store.claim(created["token"], agent_id="agent", csr_hash="csr-hash")

    assert second == first
    with pytest.raises(EnrollmentError, match="already been used"):
        store.claim(created["token"], agent_id="other", csr_hash="other-csr")
