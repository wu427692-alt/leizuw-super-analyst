from src.services.essay_daily_report_worker import EssayDailyReportWorker


def test_status_does_not_repeat_complete_daily_report_body():
    worker = EssayDailyReportWorker()
    worker._last_result = {
        "report_date": "2026-08-18",
        "source_count": 408,
        "models": [{
            "model": "deepseek-v4-flash",
            "status": "completed",
            "report": {"executive_summary": "x" * 100_000},
        }],
    }

    result = worker.status()["last_result"]

    assert result == {
        "report_date": "2026-08-18",
        "source_count": 408,
        "models": [{"model": "deepseek-v4-flash", "status": "completed"}],
    }


def test_automatic_run_reuses_completed_report(monkeypatch):
    observed = {}

    class FakeService:
        def generate(self, **kwargs):
            observed.update(kwargs)
            return {"report_date": kwargs["report_date"], "models": []}

    monkeypatch.setattr(
        "src.services.essay_daily_report_worker.EssayDailyReportService",
        FakeService,
    )
    worker = EssayDailyReportWorker()

    worker.run_now("2026-08-30", automatic=True)

    assert observed == {
        "report_date": "2026-08-30",
        "force": False,
        "reuse_completed": True,
    }
