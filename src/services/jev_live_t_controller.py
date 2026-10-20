"""Explicit administrator control for a durable, continuous paper account."""
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
from threading import Event, RLock, Thread

from src.feature_availability import is_feature_available
from src.services.jev_live_t_engine import LiveTConfig
from src.services.jev_live_t_store import LiveTStore


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atomic(path, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('continuous_checkpoint_too_large')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ValueError('unexpected_control_path')
    fd, name = tempfile.mkstemp(prefix='.continuous-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read(path):
    if not path.exists():
        return None
    if path.is_symlink() or path.parent.resolve() != path.parent or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('invalid_control_asset')
    with path.open() as stream:
        return json.load(stream)


class LiveTController:
    """One explicitly authorized paper account; views never launch a worker."""
    def __init__(self, root=None, *, runner=None):
        self.store = LiveTStore(Path(root or LiveTStore().root).absolute())
        self.root = self.store.root / 'continuous'
        self.state_path = self.root / 'admin-control.json'
        self.checkpoint_path = self.root / 'admin-checkpoint.json'
        self.lock = RLock()
        self.shutdown = Event()
        self.thread = None
        self.runner = runner

    def _default(self):
        return {'schema': 'jev-continuous-control-v1', 'owner': 'admin', 'enabled': False,
                'phase': 'stopped', 'run_id': None, 'prior_cost_usd': 0., 'max_cost_usd': 10.,
                'updated_at': _now(), 'message': '持续纸上账户尚未开启', 'last_error': None}

    def _state(self):
        saved = _read(self.state_path)
        if saved is None and self.checkpoint_path.exists():
            raise ValueError('control_missing_for_existing_account')
        state = saved or self._default()
        if state.get('schema') != 'jev-continuous-control-v1' or state.get('owner') != 'admin':
            raise ValueError('invalid_control_owner_or_version')
        if not isinstance(state.get('enabled'), bool):
            raise ValueError('invalid_control_enabled')
        prior, maximum = state.get('prior_cost_usd'), state.get('max_cost_usd')
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in (prior, maximum)) or not 0 <= prior < maximum <= 10:
            raise ValueError('invalid_control_budget')
        return state

    def _joint(self):
        joint = _read(self.checkpoint_path)
        saved = _read(self.state_path)
        if joint is None and saved is not None and saved.get('run_id'):
            raise ValueError('checkpoint_missing_for_existing_account')
        if joint is not None and (joint.get('schema') != 'jev-continuous-checkpoint-v1'
                                 or joint.get('owner') != 'admin'
                                 or not isinstance(joint.get('run'), dict)
                                 or not isinstance(joint.get('checkpoint'), dict)):
            raise ValueError('invalid_continuous_checkpoint')
        return joint

    def view(self, owner):
        if owner != 'admin':
            return {'can_control': False, 'enabled': False, 'phase': 'stopped', 'run_id': None,
                    'message': '当前持续纸上账户由管理员管理，请使用已授权的管理员会话。',
                    'updated_at': _now(), 'config': {}, 'usage': {'cost_usd': 0, 'cost_is_estimate': False}}
        with self.lock:
            try:
                state, joint = self._state(), self._joint()
            except ValueError:
                return {'can_control': True, 'enabled': False, 'phase': 'failed', 'run_id': None,
                        'message': '账户保存状态不完整，已禁止重新建账；请恢复检查点后继续。',
                        'updated_at': _now(), 'config': {},
                        'usage': {'cost_usd': None, 'cost_is_estimate': True}}
            run = joint['run'] if joint else None
            usage = run.get('usage', {}) if run else {}
            phase = state['phase']
            if state['enabled'] and run and phase not in ('stopping', 'failed', 'budget_exhausted'):
                phase = 'market_closed' if run.get('status') == 'paused' else 'running' if run.get('status') == 'running' else 'starting'
            return {'can_control': True, 'enabled': state['enabled'], 'phase': phase,
                    'run_id': run['run_id'] if run else state['run_id'],
                    'updated_at': run.get('updated_at', state['updated_at']) if run else state['updated_at'],
                    'message': run.get('message', state['message']) if state['enabled'] and run else state['message'],
                    'config': {**asdict(LiveTConfig(confidence_sizing=True, slippage_bps=1, latency_ms=1000)),
                               'name': '华懋科技', 'currency': 'CNY', 'settlement': 't1',
                               'duration_seconds': None, 'max_model_requests': None,
                               'max_cost_usd': state['max_cost_usd'], 'continuous': True},
                    'usage': {'cost_usd': state['prior_cost_usd'] + usage.get('cost_usd', 0),
                              'cost_is_estimate': usage.get('cost_is_estimate', False),
                              'requests': usage.get('requests', 0)}}

    def configure_prior_cost(self, cost):
        """Explicit one-time operation before enabling; never resets a ledger."""
        with self.lock:
            if self._joint() is not None or self._state()['enabled']:
                raise ValueError('paper_account_already_started')
            state = self._state()
            state['prior_cost_usd'] = cost
            if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or not 0 <= cost < state['max_cost_usd']:
                raise ValueError('invalid_prior_cost')
            _atomic(self.state_path, state)

    def set_enabled(self, owner, enabled):
        if owner != 'admin':
            raise PermissionError('administrator_required')
        if not isinstance(enabled, bool):
            raise ValueError('enabled_requires_boolean')
        if enabled and not is_feature_available('jev'):
            raise ValueError('feature_unavailable')
        with self.lock:
            state = self._state()
            joint = self._joint()
            usage = joint['run'].get('usage', {}) if joint else {}
            if enabled and (state['phase'] == 'budget_exhausted'
                            or (joint and joint['run'].get('stop_reason') in ('model_budget_reached', 'model_budget_reserved'))
                            or state['prior_cost_usd'] + usage.get('cost_usd', 0) >= state['max_cost_usd']):
                raise ValueError('model_budget_exhausted')
            if enabled and self.shutdown.is_set():
                raise ValueError('server_is_shutting_down')
            if enabled and state['phase'] == 'stopping':
                raise ValueError('wait_until_stopped')
            live = self.thread is not None and self.thread.is_alive()
            state.update(enabled=enabled, updated_at=_now(),
                         phase=('running' if live else 'starting') if enabled else ('stopping' if live else 'stopped'),
                         message='正在开启持续纸上账户' if enabled else '正在停止并结算已发请求' if live else '已关闭；现金、持仓和费用已保留',
                         last_error=None)
            _atomic(self.state_path, state)
            if enabled:
                self._launch()
        return self.view(owner)

    def _launch(self):
        if not is_feature_available('jev'):
            return
        if self.thread is not None and self.thread.is_alive():
            return
        self.thread = Thread(target=self._work, name='jev-continuous-paper', daemon=True)
        self.thread.start()

    def recover(self):
        """Lifespan recovery only; GET routes must never call this method."""
        if not is_feature_available('jev'):
            return
        with self.lock:
            try:
                state = self._state()
                self._joint()
            except ValueError:
                return  # Keep the web controls available, but never reset a lost ledger.
            if state['enabled']:
                self._launch()

    def _persist(self, run, checkpoint):
        with self.lock:
            # This joint document is the durable truth. The separate run file is
            # a read-only web projection and may be repaired from it on recovery.
            _atomic(self.checkpoint_path, {'schema': 'jev-continuous-checkpoint-v1', 'owner': 'admin',
                                           'run': run, 'checkpoint': checkpoint})
            self.store.save('admin', run)
            state = self._state()
            state.update(run_id=run['run_id'], updated_at=_now())
            _atomic(self.state_path, state)

    def _stop_requested(self):
        return (not is_feature_available('jev') or self.shutdown.is_set()
                or not self._state()['enabled'])

    def _work(self):
        lock_stream = None
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock_path = self.root / 'admin-worker.lock'
            if lock_path.is_symlink() or self.root.resolve() != self.root:
                raise ValueError('unexpected_worker_lock')
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            lock_stream = os.fdopen(fd, 'w')
            try:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with self.lock:
                state, joint = self._state(), self._joint()
                if not state['enabled'] or self.shutdown.is_set():
                    if not state['enabled']:
                        state.update(phase='stopped', updated_at=_now(), message='已关闭；现金、持仓和费用已保留')
                        _atomic(self.state_path, state)
                    return
                if joint:
                    self.store.save('admin', joint['run'])
                state.update(phase='starting', updated_at=_now(), message='正在恢复账户并积累新行情')
                _atomic(self.state_path, state)
            if self.runner is None:
                from src.services.jev_live_t_service import run_live_paper
                runner = run_live_paper
            else:
                runner = self.runner
            audit_path = self.root / 'admin-events.jsonl'
            fd = os.open(audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'a') as audit_file:
                def audit(kind, item):
                    audit_file.write(json.dumps({'kind': kind, **item}, ensure_ascii=False, allow_nan=False) + '\n')
                    audit_file.flush()
                run = runner(LiveTConfig(confidence_sizing=True, slippage_bps=1, latency_ms=1000),
                             owner='admin', store=self.store, continuous=True,
                             duration_seconds=None, max_model_requests=None,
                             max_cost_usd=state['max_cost_usd'] - state['prior_cost_usd'],
                             enriched_context=True, stop_requested=self._stop_requested,
                             resume_run=deepcopy(joint['run']) if joint else None,
                             engine_checkpoint=deepcopy(joint['checkpoint']) if joint else None,
                             checkpoint_sink=self._persist, audit=audit)
            with self.lock:
                state = self._state()
                if self.shutdown.is_set() and state['enabled']:
                    state.update(phase='starting', message='服务重启后将继续已开启的纸上账户')
                else:
                    reason = run.get('stop_reason', '')
                    exhausted = reason in ('model_budget_reached', 'model_budget_reserved')
                    state.update(enabled=False, phase='budget_exhausted' if exhausted else 'failed' if run.get('status') == 'failed' else 'stopped',
                                 message='模型费用额度已用完，账户已暂停' if exhausted else '运行停止，请查看记录原因' if run.get('status') == 'failed' else '已关闭；现金、持仓和费用已保留')
                state.update(updated_at=_now(), run_id=run['run_id'])
                _atomic(self.state_path, state)
        except Exception as exc:
            with self.lock:
                try:
                    state = self._state()
                    state.update(enabled=False, phase='failed', last_error=type(exc).__name__, updated_at=_now(),
                                 message='持续运行已暂停，已保存账户记录；请检查服务状态后再开启。')
                    _atomic(self.state_path, state)
                except Exception:
                    pass
        finally:
            if lock_stream is not None:
                lock_stream.close()

    def close(self):
        self.shutdown.set()
        if self.thread is not None:
            self.thread.join(timeout=10)


_controller = None
_controller_lock = RLock()


def controller():
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = LiveTController()
        return _controller


@asynccontextmanager
async def live_t_lifespan(app):
    if not is_feature_available('jev'):
        yield
        return
    active = controller()
    active.recover()
    try:
        yield
    finally:
        active.close()
