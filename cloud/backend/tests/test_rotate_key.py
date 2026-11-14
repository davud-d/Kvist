# -*- coding: utf-8 -*-
"""Master key rotation: every secret ends up under the new key, or nothing changes."""
from __future__ import annotations

import pytest
from conftest import make_user
from cryptography.fernet import Fernet

from kvist_cloud import rotate_key
from kvist_cloud.secrets import SecretError, Vault

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()


@pytest.fixture
def seeded(db):
    user = make_user(db)
    user.github_token_enc = Vault(OLD).encrypt("gho_" + "a" * 36)
    user.model_key_enc = Vault(OLD).encrypt("sk-" + "b" * 40)
    other = make_user(db, "second", 2)
    other.model_key_enc = Vault(NEW).encrypt("sk-" + "c" * 40)  # already on the new key
    db.commit()
    return user, other


def test_every_secret_moves_to_the_first_key(db, seeded):
    user, other = seeded
    before = other.model_key_enc
    outcome = rotate_key.rotate(db, f"{NEW},{OLD}", dry_run=False)
    db.commit()
    assert (outcome.rotated, outcome.already_current, outcome.unreadable) == (2, 1, [])
    only_new = Vault(NEW)
    assert only_new.decrypt(user.github_token_enc) == "gho_" + "a" * 36
    assert only_new.decrypt(user.model_key_enc) == "sk-" + "b" * 40
    assert other.model_key_enc == before  # nothing re-written needlessly
    with pytest.raises(SecretError):
        Vault(OLD).decrypt(user.model_key_enc)


def test_dry_run_changes_nothing(db, seeded):
    user, _ = seeded
    before = user.model_key_enc
    outcome = rotate_key.rotate(db, f"{NEW},{OLD}", dry_run=True)
    assert outcome.rotated == 2 and user.model_key_enc == before


def test_a_secret_no_key_opens_is_reported_by_id(db, seeded):
    user, _ = seeded
    user.chatgpt_enc = Vault(Fernet.generate_key().decode()).encrypt("{}")
    outcome = rotate_key.rotate(db, f"{NEW},{OLD}", dry_run=False)
    assert outcome.unreadable == [f"users.chatgpt_enc id={user.id}"]


def test_one_key_is_not_a_rotation(db):
    with pytest.raises(rotate_key.ConfigError):
        rotate_key.rotate(db, NEW, dry_run=False)


def test_main_rolls_back_when_something_is_unreadable(tmp_path, database_url, capsys):
    from kvist_cloud.db import make_engine, make_session_factory, session_scope
    from kvist_cloud.models import User

    factory = make_session_factory(make_engine(database_url))
    with session_scope(factory) as db:
        good = make_user(db)
        good.model_key_enc = Vault(OLD).encrypt("sk-" + "b" * 40)
        bad = make_user(db, "bad", 9)
        bad.model_key_enc = Vault(Fernet.generate_key().decode()).encrypt("sk-" + "z" * 40)
        before = good.model_key_enc
    env = tmp_path / "cloud.env"
    env.write_text(
        f"KVIST_CLOUD_SESSION_SECRET={'s' * 40}\nKVIST_CLOUD_MASTER_KEY={NEW},{OLD}\nDATABASE_URL={database_url}\n"
    )
    assert rotate_key.main(["--env-file", str(env)]) == 1
    assert "nothing was changed" in capsys.readouterr().err
    with session_scope(factory) as db:
        assert db.get(User, good.id).model_key_enc == before


def test_every_encrypted_column_is_rotated():
    """A new *_enc column that is not listed would keep the old key alive forever."""
    from kvist_cloud.models import Base

    found = {
        (table.name, column.name) for table in Base.metadata.tables.values() for column in table.columns
        if column.name.endswith("_enc")
    }
    listed = {(model.__tablename__, column) for model, column in rotate_key.ENCRYPTED_COLUMNS}
    assert found == listed
