import time
from datetime import date, timedelta

import pytest

from smart_license import core, ed25519

SECRET = bytes(range(32))
DAY = 86400


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AST_LICENSE_DIR", str(tmp_path))
    monkeypatch.setattr(core, "USE_REGISTRY", False)
    monkeypatch.setattr(core, "PUBLIC_KEY", ed25519.public_key(SECRET))
    monkeypatch.setattr(core, "machine_code", lambda: "AAAA-BBBB-CCCC-DDDD")
    monkeypatch.setattr(core, "_cache", None)
    return tmp_path


def key(**over):
    payload = {"v": 1, "id": "t1", "to": "ООО Тест", "mc": "AAAA-BBBB-CCCC-DDDD", "mod": ["search"],
               "iss": "2026-10-28", "exp": None, **over}
    return core.encode_key(payload, SECRET)


def test_rfc8032_vector():
    secret = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    signature = ed25519.sign(secret, b"")
    assert ed25519.public_key(secret).hex().startswith("d75a980182b10ab7")
    assert signature.hex().startswith("e5564300c360ac72") and ed25519.verify(ed25519.public_key(secret), b"", signature)


def test_trial_starts_on_first_run_and_ends_after_14_days():
    now = time.time()
    first = core.evaluate(now)
    assert first.state == "trial" and first.days_left == 14 and set(first.modules) == set(core.MODULES)
    assert core.evaluate(now + 13 * DAY).state == "trial"
    late = core.evaluate(now + 14 * DAY + 60)
    assert late.state == "expired" and not late.allows("search")


def test_clock_turned_back_blocks():
    now = time.time()
    core.evaluate(now + 5 * DAY)
    assert core.evaluate(now).state == "clock"
    assert core.evaluate(now + 5 * DAY - 600).state == "trial"  # десять минут — не перевод часов


def test_tampered_state_is_not_a_fresh_trial(isolated):
    core.evaluate(time.time())
    (isolated / ".state").write_text("abc.def", encoding="ascii")
    assert core.evaluate(time.time()).state == "invalid"


def test_key_grants_only_its_modules_and_survives_trial_end(isolated):
    now = time.time()
    core.evaluate(now)
    (isolated / "license.key").write_text(key(mod=["search", "documents"]), encoding="utf-8")
    result = core.evaluate(now + 30 * DAY)
    assert result.state == "licensed" and result.allows("search") and not result.allows("dashboard")
    assert result.customer == "ООО Тест" and result.expires is None


def test_key_for_other_machine_expired_or_forged_is_refused(isolated):
    with pytest.raises(ValueError, match="другого компьютера"):
        core.check_key(key(mc="1111-2222-3333-4444"))
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="закончился"):
        core.check_key(key(exp=yesterday))
    good = key()
    forged = good[:-6] + ("AAAAAA" if not good.endswith("AAAAAA") else "BBBBBB")
    with pytest.raises(ValueError):
        core.check_key(forged)
    with pytest.raises(ValueError):
        core.install_key(forged)
    assert not (isolated / "license.key").exists()


def test_bad_key_falls_back_to_trial_with_reason(isolated):
    (isolated / "license.key").write_text(key(mc="1111-2222-3333-4444"), encoding="utf-8")
    result = core.evaluate(time.time())
    assert result.state == "trial" and "другого компьютера" in result.message


def test_web_guard(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from smart_license import web

    monkeypatch.setenv("AST_LICENSE_REQUIRED", "1")
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/work")
    def work():
        return {"ok": True}

    web.install(app, "dashboard")
    client = TestClient(app)
    assert client.get("/work").status_code == 200  # пробный период
    assert client.post("/license/key", json={"key": key(mod=["search"])}).status_code == 200
    blocked = client.get("/work")
    assert blocked.status_code == 403 and "dashboard" in blocked.json()["detail"]
    page = client.get("/work", headers={"accept": "text/html"})
    assert page.status_code == 403 and "Справка" in page.text
    assert client.get("/health").status_code == 200 and client.get("/license").json()["state"] == "licensed"
    assert client.post("/license/key", json={"key": "AST1.x.y"}).status_code == 400
