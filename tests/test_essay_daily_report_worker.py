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
