"""Backup selection/health and official artifact contracts, without live requests."""
from datetime import datetime
from io import BytesIO

from openpyxl import load_workbook
import pytest

from src.services.cninfo_announcement_service import CninfoAnnouncementError
from src.services.investment_monitor_service import InvestmentMonitorService, BUILTIN_MONITORING_SOURCES
from src.services.announcement_artifact_service import AnnouncementArtifactError, AnnouncementArtifactService
from tests.test_cninfo_worker_health import _worker, _state
from tests.test_cninfo_announcements import FakePdfSession


class Backup:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or []
        self.error = error

    def fetch(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def prepare(*, history=False, backup_error=None, primary_error=None):
    symbol = '603306.SH'
    primary_error = primary_error or CninfoAnnouncementError('巨潮 HTTP 504', status_code=504)
    worker, repo, service = _worker([_state(symbol, history_complete=not history)], {symbol: [primary_error]})
    service.exchanges = Backup(error=backup_error)
    repo.get_source = lambda key: next((s for s in BUILTIN_MONITORING_SOURCES if s['source_key'] == key), None)
    source_updates = []
    repo.update_source_status = lambda key, **kw: source_updates.append((key, kw))
    return worker, repo, service, source_updates


def test_backup_success_preserves_primary_error_and_updates_backup_source_only():
    worker, repo, service, updates = prepare()
    result = worker.run_once()
    assert result['status'] == 'partial'
    assert result['fallback_succeeded'] == 1
    assert result['incremental'][0]['source_key'] == 'sse.announcements'
    assert repo.states['603306.SH']['status'] == 'retry'
    assert '504' in repo.states['603306.SH']['last_error']
    assert repo.states['603306.SH']['next_retry_at']
    assert worker.status()['status'] == 'degraded'
    assert service.exchanges.calls[0]['symbol'] == '603306.SH'
    assert ('sse.announcements', 'success') in [(k, v['status']) for k, v in updates]
    assert ('cninfo.announcements', 'success') not in [(k, v['status']) for k, v in updates]


def test_backup_can_finish_history_while_primary_cooldown_is_preserved():
    worker, repo, service, _ = prepare(history=True)
    result = worker.run_once()
    assert result['incremental'][0]['status'] == 'fallback_success'
    assert result['backfill'][0]['status'] == 'fallback_success'
    assert len(service.cninfo.calls) == 1
    assert len(service.exchanges.calls) == 2
    state = repo.states['603306.SH']
    assert state['completed_windows']
    assert state['last_backfill_success_at']
    assert state['next_retry_at']
    assert state['consecutive_failures'] == 1


def test_both_fail_preserve_history_gap_and_backup_retry_after():
    error = CninfoAnnouncementError('SSE HTTP 429', status_code=429, retry_after_seconds=1800)
    worker, repo, service, _ = prepare(history=True, backup_error=error)
    result = worker.run_once()
    assert result['status'] == 'failed'
    assert '429' in result['incremental'][0]['fallback_error']
    assert not repo.states['603306.SH']['completed_windows']
    assert repo.states['603306.SH']['last_incremental_success_at'] is None
    assert worker.run_once()['status'] == 'idle'
    assert len(service.exchanges.calls) == 1
    assert worker._last_success_at is None


def test_non_upstream_fault_does_not_trigger_backup():
    worker, _, service, _ = prepare(primary_error=RuntimeError('database failure'))
    assert worker.run_once()['status'] == 'failed'
    assert not service.exchanges.calls


def test_truncated_primary_window_is_not_marked_complete_if_backup_fails():
    worker, repo, service, _ = prepare(history=True, backup_error=CninfoAnnouncementError('backup unavailable'))
    def truncated(**kwargs):
        kwargs['diagnostics']['truncated'] = True
        return []
    service.cninfo.fetch = truncated
    assert worker.run_once()['status'] == 'failed'
    assert not repo.states['603306.SH']['completed_windows']


def test_primary_recovery_does_not_keep_using_backup():
    worker, repo, service, _ = prepare()
    worker.run_once()
    repo.update_announcement_sync_state('603306.SH', next_retry_at=None, last_incremental_success_at=None)
    service.cninfo.outcomes['603306.SH'] = [[]]
    assert worker.run_once()['status'] == 'success'
    assert repo.states['603306.SH']['last_error'] is None
    assert len(service.exchanges.calls) == 1


def test_exchange_evidence_uses_actual_provider_and_official_link():
    service = object.__new__(InvestmentMonitorService)
    source = next(s for s in BUILTIN_MONITORING_SOURCES if s['source_key'] == 'sse.announcements')
    event = service._announcement_events(source, [{
        'announcement_id': 'sse-123', 'code': '603306', 'name': '华懋科技', 'title': '发行公告',
        'announcement_at': datetime(2026, 9, 7, 16), 'disclosure_date': '2026-09-08',
        'pdf_url': 'https://static.sse.com.cn/disclosure/listedinfo/announcement/test.pdf',
    }])[0]
    assert event['source_key'] == 'sse.announcements'
    assert event['metrics']['_evidence']['origin_apis'] == ['sse_company_bulletin']
    assert event['metrics']['_evidence']['provider'] == 'sse'
    assert event['metrics']['disclosure_date'] == '2026-09-08'


@pytest.mark.parametrize('host', ['static.cninfo.com.cn', 'static.sse.com.cn', 'disc.static.szse.cn'])
def test_official_pdf_download_still_works_when_explicitly_requested(tmp_path, monkeypatch, host):
    monkeypatch.setenv('ANNOUNCEMENT_FILE_DIR', str(tmp_path))
    service = AnnouncementArtifactService(session=FakePdfSession(b'%PDF-1.4 test'))
    path, cached = service.download_pdf({'external_id': 'test', 'symbols': ['603306.SH'], 'url': f'https://{host}/test.pdf'})
    assert path.read_bytes().startswith(b'%PDF')
    assert not cached


@pytest.mark.parametrize('url', ['http://static.sse.com.cn/a.pdf', 'https://static.sse.com.cn.evil.test/a.pdf',
                               'https://user:pass@static.sse.com.cn/a.pdf', 'https://static.sse.com.cn/a.doc'])
def test_unapproved_pdf_url_rejected_without_network(tmp_path, monkeypatch, url):
    monkeypatch.setenv('ANNOUNCEMENT_FILE_DIR', str(tmp_path))
    service = AnnouncementArtifactService(session=object())
    with pytest.raises(AnnouncementArtifactError):
        service.download_pdf({'url': url})


def test_export_uses_shanghai_disclosure_date_and_source(tmp_path, monkeypatch):
    monkeypatch.setenv('ANNOUNCEMENT_FILE_DIR', str(tmp_path))
    service = AnnouncementArtifactService(session=object())
    blob = service.excel_bytes([{'event_at': '2026-09-07T16:00:00Z', 'source_name': '上交所官方公告', 'metrics': {}}])
    rows = list(load_workbook(BytesIO(blob), read_only=True).active.values)
    assert rows[1][4] == '2026-09-08'
    assert rows[1][-1] == '上交所官方公告'


def test_date_filter_uses_shanghai_boundaries():
    class Repo:
        def list_announcements(self, **kw):
            assert kw['start_at'] == datetime(2026, 9, 7, 16)
            assert kw['end_at'] == datetime(2026, 9, 8, 15, 59, 59, 999999)
            return [], 0
    service = object.__new__(InvestmentMonitorService)
    service.repo = Repo()
    assert service.list_announcements(start_date='2026-09-08', end_date='2026-09-08')['total'] == 0


def test_idle_backup_sources_do_not_wake_generic_monitor(monkeypatch):
    from tests.test_sync_watchdog_worker import _FakeWorker, _watchdog
    investment = _FakeWorker({'running': True, 'poll_seconds': 10, 'last_sync_age_seconds': 1})
    zsxq = _FakeWorker({'running': True, 'available': True, 'syncing': False,
                       'poll_seconds': 30, 'last_sync_age_seconds': 1})
    market = _FakeWorker({'running': True, 'collecting_window': False,
                         'poll_seconds': 1, 'last_run_age_seconds': None})
    guard = _watchdog(monkeypatch, investment=investment, zsxq=zsxq, market=market,
                      due=[{'source_key': key} for key in ['sse.announcements', 'szse.announcements']])
    assert guard.audit_once()['repairs'] == []
    assert investment.triggers == 0
