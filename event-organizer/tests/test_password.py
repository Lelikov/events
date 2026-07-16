from event_organizer.auth.password import PasswordService


def test_hash_and_verify() -> None:
    svc = PasswordService()
    h = svc.hash("secret123")
    assert h != "secret123"
    assert svc.verify("secret123", h) is True
    assert svc.verify("wrong", h) is False
