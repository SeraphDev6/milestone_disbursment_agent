"""Custody of held deliverables (#11): encryption at rest and retention."""

import hashlib
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import text

from app.db import utcnow
from app.integrations import filecrypt
from app.models import Evidence, Milestone, StoredFile
from app.services import custody
from tests.helpers import h
from tests.test_delivery_records import captured_and_revealed
from tests.test_payouts import run_payouts
from tests.test_verified_delivery import REPORT, SECRET, setup, upload


def raw(client, file_id) -> bytes:
    with client.app.state.sessionmaker() as s:
        return bytes(s.connection().execute(text("SELECT data FROM files WHERE id = :i"), {"i": file_id}).scalar())


def set_raw(client, file_id, data: bytes) -> None:
    with client.app.state.sessionmaker() as s:
        s.connection().execute(text("UPDATE files SET data = :d WHERE id = :i"), {"d": data, "i": file_id})
        s.commit()


def file_of(client, eid) -> str:
    with client.app.state.sessionmaker() as s:
        return s.get(Evidence, eid).file_id


def test_files_are_encrypted_at_rest_and_decrypted_on_download(client, draft):
    _, m2, ctx = setup(client, draft, deliverable_release="on_submit")
    eid = upload(client, m2, ctx)
    stored = raw(client, file_of(client, eid))
    assert stored.startswith(filecrypt.MAGIC) and SECRET.encode() not in stored
    r = client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"]))
    assert r.status_code == 200 and r.content == REPORT.encode()


def test_an_altered_ciphertext_is_refused_not_served(client, draft):
    _, m2, ctx = setup(client, draft, deliverable_release="on_submit")
    eid = upload(client, m2, ctx)
    fid = file_of(client, eid)
    blob = bytearray(raw(client, fid))
    blob[-1] ^= 1
    set_raw(client, fid, bytes(blob))
    assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"])).status_code == 409


def test_plain_files_from_before_stay_readable_and_encrypt_files_converts_them(client, draft):
    _, m2, ctx = setup(client, draft, deliverable_release="on_submit")
    eid = upload(client, m2, ctx)
    fid = file_of(client, eid)
    set_raw(client, fid, REPORT.encode())  # as stored before encryption existed
    assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"])).content == REPORT.encode()
    with client.app.state.sessionmaker() as s:
        assert custody.encrypt_existing(s) == 1
        assert custody.encrypt_existing(s) == 0
    assert raw(client, fid).startswith(filecrypt.MAGIC)

    # Rotation: a new key encrypts, the old one still decrypts, encrypt-files moves everything to the new key.
    secret = client.app.state.settings.secret_key.get_secret_value().encode()
    derived = hashlib.sha256(b"mde-files:" + secret).digest()  # the key used when FILE_ENCRYPTION_KEY is unset
    assert filecrypt.FileCipher([derived]).current(raw(client, fid))
    before = filecrypt.cipher()
    try:
        filecrypt.configure(filecrypt.FileCipher([b"k" * 32, derived]))
        assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"])).content == REPORT.encode()
        with client.app.state.sessionmaker() as s:
            assert custody.encrypt_existing(s) == 1
        assert filecrypt.FileCipher([b"k" * 32]).current(raw(client, fid))
        assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"])).content == REPORT.encode()
    finally:
        filecrypt.configure(before)


def settled(client, draft):
    aid, m2, ctx, eid = captured_and_revealed(client, draft)
    run_payouts(client, utcnow() + timedelta(days=4))  # the 3-day window closes
    with client.app.state.sessionmaker() as s:
        assert s.get(Milestone, m2).status == "settled"
    return aid, m2, ctx, eid


def retention(client, at, action="delete", days=30):
    with client.app.state.sessionmaker() as s:
        return custody.run(s, SimpleNamespace(retention_days=days, retention_action=action), at)


def test_retention_deletes_originals_but_keeps_the_record(client, draft):
    _, m2, ctx, eid = settled(client, draft)
    assert retention(client, utcnow() + timedelta(days=60)) == {"custody ended": 0, "files purged": 0}  # chargebacks
    assert retention(client, utcnow() + timedelta(days=181)) == {"custody ended": 1, "files purged": 1}

    for who in ("payer", "payee"):
        r = client.get(f"/evidence/{eid}/file", headers=h(ctx[who]))
        assert r.status_code == 410 and "deleted by retention" in r.json()["detail"]
    with client.app.state.sessionmaker() as s:
        ev = s.get(Evidence, eid)
        f = s.get(StoredFile, ev.file_id)
        assert f.purged_at and f.data == b"" and f.sha256 and SECRET not in ev.content
        assert ev.pinned_ref in ev.content
    pack = client.get(f"/milestones/{m2}/dispute-pack", headers=h(ctx["payee"])).json()
    assert pack["dispute"]["commitments"]  # the hashes outlive the files
    assert [m for m in client.app.state.mailer.sent if "Retention period ended" in m.subject]
    assert retention(client, utcnow() + timedelta(days=400))["custody ended"] == 0  # once


def test_payee_only_retention_keeps_the_files_for_the_payee(client, draft):
    _, _, ctx, eid = settled(client, draft)
    assert retention(client, utcnow() + timedelta(days=181), action="payee_only")["custody ended"] == 1
    assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"])).status_code == 410
    assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payee"])).content == REPORT.encode()


def test_a_file_shared_with_a_tranche_still_held_isnt_purged(client, draft):
    _, _, ctx, eid = settled(client, draft)
    other = upload(client, ctx["m1"], ctx)  # the same bytes on a tranche that's still open
    assert file_of(client, other) == file_of(client, eid)
    assert retention(client, utcnow() + timedelta(days=181)) == {"custody ended": 1, "files purged": 0}
    assert client.get(f"/evidence/{other}/file", headers=h(ctx["payee"])).content == REPORT.encode()


def test_retention_is_off_unless_set(client, draft):
    settled(client, draft)
    assert retention(client, utcnow() + timedelta(days=999), days=None) == {}
