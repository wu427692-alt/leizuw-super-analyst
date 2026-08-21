from src.services.startup_warmup_service import StartupWarmupService


def test_startup_warmup_isolates_failed_views():
    calls = []

    def ready():
        calls.append("ready")

    def broken():
        calls.append("broken")
        raise RuntimeError("temporary")

    def still_ready():
        calls.append("still-ready")

    result = StartupWarmupService([
        ("ready", ready),
        ("broken", broken),
        ("still-ready", still_ready),
    ]).warm()

    assert calls == ["ready", "broken", "still-ready"]
    assert result["completed"] == ["ready", "still-ready"]
    assert result["failed"] == {"broken": "RuntimeError"}
