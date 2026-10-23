"""Updater failures must distinguish local storage from network downloads."""
import errno
from pathlib import Path
from urllib.error import URLError

import pytest

import app_server as app
from test_app_auto_update import FakeDownloadResponse, update_status_for


SECRET = 'https://example.invalid/private?token=secret-PIN-1234'


def failure(kind):
    if kind.startswith('win'):
        error = OSError(SECRET)
        error.winerror = int(kind[3:])
        return error
    if not hasattr(errno, kind):
        pytest.skip(f'{kind} is not defined on this platform; Windows storage codes are tested separately')
    return OSError(getattr(errno, kind), SECRET)


@pytest.mark.parametrize('stage', ['mkdir', 'mkdtemp', 'open', 'write', 'flush', 'fsync', 'close', 'replace'])
@pytest.mark.parametrize('kind,code', [
    ('ENOSPC', 'update_disk_full'), ('EDQUOT', 'update_disk_full'),
    ('win112', 'update_disk_full'), ('win39', 'update_disk_full'),
    ('EACCES', 'update_storage_permission_denied'), ('EPERM', 'update_storage_permission_denied'),
    ('win5', 'update_storage_permission_denied'),
])
def test_storage_stage_failure_has_correct_cause_and_removes_owned_partial(tmp_path, monkeypatch, stage, kind, code):
    error = failure(kind)
    body = b'installer bytes'
    status = update_status_for(body)
    monkeypatch.setattr(app, 'urlopen', lambda *a, **kw: FakeDownloadResponse(body, status['downloadUrl']))

    def fail(*args, **kwargs):
        raise error

    if stage == 'mkdir':
        original = Path.mkdir
        def mkdir(path, *a, **kw):
            return fail() if path.name == 'updates' else original(path, *a, **kw)
        monkeypatch.setattr(Path, 'mkdir', mkdir)
    elif stage == 'mkdtemp':
        monkeypatch.setattr(app.tempfile, 'mkdtemp', fail)
    elif stage in {'fsync', 'replace'}:
        monkeypatch.setattr(app.os, stage, fail)
    else:
        original = Path.open
        class Output:
            def __init__(self, stream): self.stream = stream
            def write(self, value): return fail() if stage == 'write' else self.stream.write(value)
            def flush(self): return fail() if stage == 'flush' else self.stream.flush()
            def fileno(self): return self.stream.fileno()
            def close(self):
                self.stream.close()
                if stage == 'close': fail()
        def open_file(path, *a, **kw):
            if path.suffix != '.part': return original(path, *a, **kw)
            if stage == 'open': fail()
            return Output(original(path, *a, **kw))
        monkeypatch.setattr(Path, 'open', open_file)

    with pytest.raises(app.AppUpdateError) as caught:
        app._download_update_artifact(status, runtime_dir=tmp_path)
    assert caught.value.code == code
    assert '인터넷' not in ' '.join(caught.value.recovery_steps)
    assert SECRET not in str(caught.value)
    assert 'secret-PIN-1234' not in str(caught.value.recovery_steps)
    if code == 'update_disk_full':
        assert caught.value.status == app.HTTPStatus.INSUFFICIENT_STORAGE
        assert '공간' in str(caught.value)
    else:
        assert caught.value.status == app.HTTPStatus.FORBIDDEN
        assert '권한' in str(caught.value)
    assert not list(tmp_path.rglob('*.part'))
    assert not list(tmp_path.rglob('pending-*'))


def test_other_local_io_failure_is_staging_error_not_network(tmp_path, monkeypatch):
    status = update_status_for(b'installer')
    original = Path.open
    monkeypatch.setattr(app, 'urlopen', lambda *a, **kw: FakeDownloadResponse(b'installer', status['downloadUrl']))
    def open_file(path, *a, **kw):
        if path.suffix == '.part': raise OSError(errno.EIO, SECRET)
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(app.AppUpdateError) as caught:
        app._download_update_artifact(status, runtime_dir=tmp_path)
    assert caught.value.code == 'update_staging_failed'
    assert '로컬 폴더' in str(caught.value)
    assert SECRET not in str(caught.value)
    assert not list(tmp_path.rglob('pending-*'))


def test_network_failure_stays_download_error_without_url_or_pin(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs): raise URLError(SECRET)
    monkeypatch.setattr(app, 'urlopen', unavailable)
    with pytest.raises(app.AppUpdateError) as caught:
        app._download_update_artifact(update_status_for(b'installer'), runtime_dir=tmp_path)
    assert caught.value.code == 'update_download_failed'
    assert '인터넷' in ' '.join(caught.value.recovery_steps)
    assert SECRET not in str(caught.value)
    assert 'secret-PIN-1234' not in str(caught.value)
    assert not list(tmp_path.rglob('pending-*'))
