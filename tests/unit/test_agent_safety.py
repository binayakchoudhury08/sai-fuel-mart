"""Offline regression tests. No database, Tally session or WhatsApp sends."""
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault('SFM_SITE', 'https://example.invalid')
os.environ.setdefault('SFM_SECRET', 'test-only')
os.environ.setdefault('JOB_ID', '1')
from agent import tally_agent as robot
from sfm import tally_jobs as jobs, db, wa

NAME = 'SFM_Tally_Import_02-11-2026_to_02-11-2026.xml'
COMPANY = 'SAI FUEL MART 26-27'
FORM = f'Import Transactions File Format XML File Path {robot.IMPORT_ROOT}\\NOV 2026 File to Import {NAME} {COMPANY}'
RESULT = 'Import Statistics Created: 25 Altered: 0 Errors: 0 Ignored: 0'
COUNTS = {'created': 25, 'altered': 0, 'errors': 0, 'ignored': 0}


def run_import(answer='IMPORT', result=RESULT, changed=False):
    desk = Mock()
    stages = []
    def wait(_desk, predicate, seconds=30):
        screen = [FORM, FORM, result][len(stages)]
        stages.append(screen)
        if not predicate(screen):
            raise RuntimeError('unverified screen')
        return screen, 'image'
    with patch.object(robot, '_tally_on_screen', return_value=True), patch.object(robot, 'wait_screen', side_effect=wait), \
         patch.object(robot, 'read_screen', return_value=('wrong file' if changed else FORM, 'image')), \
         patch.object(robot, 'wait_answer', return_value=answer), patch.object(robot.time, 'sleep'), \
         patch.object(robot, 'select_import_path'), patch.object(robot, 'click_setting'), patch.object(robot, 'daybook_check', return_value=True), \
         patch.object(robot, 'step') as step:
        error = None
        try:
            robot.import_xml(desk, 'NOV 2026', NAME, COMPANY, 25)
        except RuntimeError as exc:
            error = exc
        return desk, step.call_args_list, error


def test_old_import_form_never_counts_as_result():
    assert robot.result_counts(FORM) is None
    assert robot.result_counts(FORM + RESULT) is None
    assert robot.result_counts('Gateway of Tally') is None


def test_complete_result_required():
    assert robot.result_counts(RESULT) == COUNTS
    assert robot.result_counts('Created 25 Altered 0') is None
    assert robot.result_counts(RESULT + ' Created 99') is None


def test_stop_does_not_accept():
    desk, steps, error = run_import('STOP')
    assert error
    assert 'Control+a' not in [c.args[0] for c in desk.keyboard.press.call_args_list]
    assert not any(c.kwargs.get('done') for c in steps)


def test_form_changed_after_approval_does_not_accept():
    desk, steps, error = run_import(changed=True)
    assert error
    assert 'Control+a' not in [c.args[0] for c in desk.keyboard.press.call_args_list]


def test_verified_result_with_single_accept():
    desk, steps, error = run_import()
    assert error is None
    assert [c.args[0] for c in desk.keyboard.press.call_args_list].count('Control+a') == 1
    assert steps[-1].kwargs['done'] is True
    assert steps[-1].kwargs['evidence']['counts'] == COUNTS
    assert any(c.args[0] == 'awaiting_import' for c in steps)
    assert any(c.args[0] == 'submitting' for c in steps)
    assert desk.keyboard.type.call_args_list[0].args[0] == 'XML'


def test_partial_result_needs_review():
    _, steps, error = run_import(result=RESULT.replace('25', '24'))
    assert error is None
    assert steps[-1].args[0] == 'review_required'
    assert not any(c.kwargs.get('done') for c in steps)


def test_stuck_form_after_accept_never_reports_done():
    _, steps, error = run_import(result=FORM)
    assert error
    assert not any(c.kwargs.get('done') for c in steps)


def test_evidence_rejects_wrong_file_missing_counts_errors_and_boolean_counts():
    params = {'name': NAME, 'vouchers': 25}
    assert jobs.valid_import_evidence(params, {'filename': NAME, 'counts': COUNTS})
    for evidence in (None, {}, {'filename': 'old.xml', 'counts': COUNTS},
                     {'filename': NAME, 'counts': {**COUNTS, 'errors': 1}},
                     {'filename': NAME, 'counts': {**COUNTS, 'created': True}},
                     {'filename': NAME, 'counts': {**COUNTS, 'created': 24}}):
        assert not jobs.valid_import_evidence(params, evidence)


def job(state='submitting', **extra):
    return dict(id=1, phone='test', kind='import', state=state, params={'name': NAME, 'vouchers': 25},
                waiting=None, answer=None, created_at=datetime.now(timezone.utc), answered_at=None, **extra)


@contextmanager
def backend(record, delivered=True):
    cursor = Mock()
    cursor.fetchone.return_value = record
    @contextmanager
    def tx():
        yield cursor
    sender = Mock()
    sender.send_image.return_value = delivered
    sender.send_text.return_value = delivered
    with patch.object(db, 'tx', tx), patch.object(jobs, '_wa', return_value=sender):
        yield cursor, sender


def test_backend_rejects_legacy_false_success():
    with backend(job()) as (cursor, sender):
        assert not jobs.agent_step(1, 'import', 'Imported', done=True)
        sender.send_text.assert_not_called()


def test_backend_accepts_verified_screenshot():
    with backend(job()) as (cursor, sender):
        assert jobs.agent_step(1, 'import_verified', 'result', png=b'image', done=True,
                               evidence={'filename': NAME, 'counts': COUNTS})
        sender.send_image.assert_called_once()
        assert sender.send_image.call_args.kwargs['strict']


def test_timeout_after_submit_is_uncertain():
    with backend(job()) as (cursor, sender):
        assert jobs.agent_step(1, 'error', 'timeout', failed=True)
        updates = [c.args for c in cursor.execute.call_args_list if 'log = log' in c.args[0]]
        assert updates[0][1][0] == 'review_required'
        assert 'UNCONFIRMED' in sender.send_text.call_args.args[1]


def test_failed_picture_prevents_continue():
    with backend(job('company'), delivered=False):
        assert not jobs.agent_step(1, 'awaiting_import', 'approve', png=b'image')


def test_terminal_job_cannot_be_replayed():
    for state in ('done', 'failed', 'review_required'):
        with backend(job(state)):
            assert not jobs.agent_step(1, 'login', 'replay')
            assert jobs.agent_wait(1, 'pin')['state'] == 'cancelled'


def test_pin_is_not_returned_as_company():
    record = job('running'); record['answer'] = '123456'
    with backend(record):
        assert jobs.agent_wait(1, 'company')['state'] == 'cancelled'
        assert jobs.agent_wait(1, 'pin') == {'state': 'ok', 'answer': '123456'}


def test_xml_snapshot_hash_checked_and_no_reconversion():
    import hashlib
    record = job('connect')
    record['params'].update(xml='<XML/>', sha256=hashlib.sha256(b'<XML/>').hexdigest())
    with backend(record):
        assert jobs.agent_xml(1)['xml'] == '<XML/>'
        record['params']['xml'] = 'changed'
        assert not jobs.agent_xml(1)['ok']


def test_strict_media_does_not_treat_text_fallback_as_picture():
    response = Mock(status_code=500, text='upload failed')
    with patch.object(wa, 'provider', return_value='meta'), patch.object(wa.requests, 'post', return_value=response), \
         patch.object(wa, 'send_text') as send:
        assert wa.send_image('test', b'png', strict=True) is False
        send.assert_not_called()
    with patch.object(wa, 'provider', return_value='twilio'):
        assert wa.send_image('test', b'png', strict=True) is False


def test_failed_step_api_stops_agent():
    response = Mock(); response.json.return_value = {'ok': False}
    with patch.object(robot, 'api', return_value=response):
        import pytest
        with pytest.raises(RuntimeError):
            robot.step('awaiting_import', 'approve', png='image')


def test_claim_conflict_and_import_route_auth():
    from flask import Flask
    from sfm.views.wa import bp
    app = Flask(__name__); app.register_blueprint(bp)
    client = app.test_client()
    with patch.dict(os.environ, {'CRON_SECRET': 'private'}), patch.object(jobs, 'claim_job', return_value=False):
        assert client.post('/api/tally/job/1/claim').status_code == 401
        assert client.post('/api/tally/job/1/claim', headers={'x-cron-secret': 'private'}).status_code == 409
    with patch.dict(os.environ, {'CRON_SECRET': 'private'}), patch.object(jobs, 'agent_wait', return_value={'state': 'waiting'}) as wait:
        assert client.get('/api/tally/job/1/wait/import', headers={'x-cron-secret': 'private'}).status_code == 200
        wait.assert_called_once_with(1, 'import')


def test_approve_and_stop_are_handled_before_other_commands():
    for reply in ('IMPORT', 'STOP', 'anything'):
        record = job('awaiting_import'); record['waiting'] = 'import'
        with backend(record) as (cursor, _):
            out = jobs.handle(cursor, 'test', reply.lower(), reply)
            assert out
            statements = ' '.join(c.args[0] for c in cursor.execute.call_args_list)
            if reply == 'IMPORT':
                assert "answer='IMPORT'" in statements
            elif reply == 'STOP':
                assert "state='failed'" in statements
            else:
                assert 'UPDATE' not in statements


def test_cannot_submit_without_approval():
    record = job('awaiting_import'); record['waiting'] = 'import'
    with backend(record):
        assert not jobs.agent_step(1, 'submitting', 'submit')


def test_no_blind_popup_keystrokes():
    desk = Mock(); desk.frames = []; desk.main_frame.get_by_role.return_value.count.return_value = 0
    with patch.object(robot, 'read_screen', return_value=(FORM, 'image')):
        robot.dismiss_popups(desk)
    desk.keyboard.press.assert_not_called()
    desk.mouse.click.assert_not_called()


def test_real_ocr_pipeline_reads_result():
    import io, shutil, pytest
    from PIL import Image, ImageDraw, ImageFont
    if not shutil.which('tesseract'):
        pytest.skip('tesseract not installed')
    image = Image.new('RGB', (1100, 480), 'white')
    font = ImageFont.truetype('DejaVuSansMono.ttf', 30)
    ImageDraw.Draw(image).multiline_text((40, 40), 'Import Statistics\nCreated: 25\nAltered: 0\nErrors: 0\nIgnored: 0', font=font, fill='black', spacing=22)
    data = io.BytesIO(); image.save(data, format='PNG')
    desk = Mock(); desk.screenshot.return_value = data.getvalue()
    text, png = robot.read_screen(desk)
    assert robot.result_counts(text) == COUNTS
    assert png


def test_ambiguous_ocr_zero_is_not_assumed():
    assert robot.result_counts(RESULT.replace("Altered: 0", "Altered: O")) is None


def test_status_shows_uncertain_job_without_restarting():
    record = job('review_required'); record['log'] = [{'message': 'Result not verified'}]
    with backend(record) as (cursor, _):
        out = jobs.handle(cursor, 'test', 'tally status', 'tally status')
        assert 'Day Book' in out and 'Result not verified' in out
        assert cursor.execute.call_count == 1


def test_runtime_reuses_installed_dependencies():
    with patch.object(robot.importlib.util, 'find_spec', return_value=object()), \
         patch.object(robot.shutil, 'which', return_value='/usr/bin/tesseract'), \
         patch.object(robot.subprocess, 'run') as run:
        robot.ensure_runtime()
        run.assert_not_called()


def test_runtime_missing_ocr_fails_outside_actions():
    import pytest
    with patch.object(robot.importlib.util, 'find_spec', return_value=object()), \
         patch.object(robot.shutil, 'which', return_value=None), \
         patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
        with pytest.raises(RuntimeError, match='Tesseract OCR is required'):
            robot.ensure_runtime()


def test_month_comes_from_import_date_not_previous_folder():
    assert robot.import_month(NAME) == 'NOV 2026'
    assert robot.import_month(NAME.replace('.xml', '_job10.xml')) == 'NOV 2026'
    assert robot.import_month(NAME.replace('02-11', '02-10')) == 'OCT 2026'


def test_path_error_never_advances_to_filename():
    import pytest
    desk = Mock()
    with patch.object(robot, 'read_screen', return_value=('Specify File Path', 'image')), \
         patch.object(robot, 'wait_screen', side_effect=lambda d, predicate, seconds: predicate('Incorrect File/Folder Path')):
        with pytest.raises(RuntimeError, match='filename was NOT entered'):
            robot.select_import_path(desk, robot.IMPORT_ROOT + '\\NOV 2026')
    desk.keyboard.type.assert_not_called()
    assert 'Backslash' in [c.args[0] for c in desk.keyboard.press.call_args_list]


def test_folder_acceptance_rejects_open_modal_and_old_month():
    desk = Mock()
    def wait(desk, predicate, seconds):
        assert not predicate('Specify File Path ' + robot.IMPORT_ROOT + '\\NOV 2026')
        assert not predicate('File Path ' + robot.IMPORT_ROOT + '\\OCT 2026')
        assert predicate('File Path ' + robot.IMPORT_ROOT + '\\NOV 2026')
    with patch.object(robot, 'read_screen', return_value=('Specify File Path', 'image')), \
         patch.object(robot, 'wait_screen', side_effect=wait):
        robot.select_import_path(desk, robot.IMPORT_ROOT + '\\NOV 2026')


def test_field_replacement_never_uses_accept_shortcut():
    desk = Mock(); robot.replace_field(desk, 'folder')
    keys = [c.args[0] for c in desk.keyboard.press.call_args_list]
    assert keys[0] == 'Home' and keys.count('Delete') == 260
    assert 'Control+a' not in keys and 'Shift+End' not in keys


def test_date_verification_requires_month_and_year():
    from datetime import date
    day = date(2026, 11, 2)
    assert robot.date_visible('2-Nov-26', day)
    assert robot.date_visible('02-11-2026', day)
    assert not robot.date_visible('2-Oct-26', day)
    assert not robot.date_visible('2-Nov-25', day)


def test_period_uses_alt_f2():
    from datetime import date
    desk = Mock()
    with patch.object(robot, 'wait_screen'), patch.object(robot, 'remote_type') as typed:
        robot.set_period(desk, date(2026,11,1), date(2026,11,30))
    assert desk.keyboard.press.call_args_list[0].args[0] == 'Alt+F2'
    assert [c.args[1] for c in typed.call_args_list] == ['01-11-2026', '30-11-2026']


def test_daybook_command_creates_read_only_report_job():
    import json
    cursor = Mock(); cursor.fetchone.side_effect = [None, None, {'id': 11}, None]
    with patch.object(jobs, 'start_github'):
        out = jobs.handle(cursor, 'test', 'tally check 2/11/2026', 'tally check 2/11/2026')
    insert = next(c for c in cursor.execute.call_args_list if 'INSERT INTO tally_jobs' in c.args[0])
    assert insert.args[1][1] == 'ledger'
    params = json.loads(insert.args[1][2])
    assert params == {'report': 'daybook', 'from': '2026-11-02', 'to': '2026-11-02'}
    assert 'read only' in out


def test_svt_nov_pdf_uses_full_november():
    import json
    from sfm import tally_site
    cursor = Mock(); cursor.fetchone.side_effect = [None, None, {'id': 12}, None]
    with patch.object(jobs, 'start_github'), \
         patch.object(tally_site, 'name_map', return_value={'party': {'svt': 'SVT LOGISTICS'}}), \
         patch.object(tally_site, 'current', return_value=(None, None)):
        jobs.handle(cursor, 'test', 'tally ledger svt nov 2026 pdf', 'tally ledger svt nov 2026 pdf')
    insert = next(c for c in cursor.execute.call_args_list if 'INSERT INTO tally_jobs' in c.args[0])
    params = json.loads(insert.args[1][2])
    assert params['from'] == '2026-11-01' and params['to'] == '2026-11-30'
    assert params['ledger'] == 'SVT LOGISTICS'


def test_strict_pdf_send_does_not_claim_text_fallback_is_attachment():
    response = Mock(status_code=500, text='upload failed')
    with patch.object(wa, 'provider', return_value='meta'), patch.object(wa.requests, 'post', return_value=response), \
         patch.object(wa, 'send_text') as send:
        assert wa.send_document('test', 'svt.pdf', b'%PDF-1.4', 'application/pdf', strict=True) is False
        send.assert_not_called()


def test_pdf_endpoint_returns_failure_when_delivery_fails():
    import io
    from flask import Flask
    from sfm.views.wa import bp
    app = Flask(__name__); app.register_blueprint(bp)
    with patch.dict(os.environ, {'CRON_SECRET': 'secret'}), \
         patch.object(db, 'one', return_value={'phone': 'test'}), \
         patch.object(wa, 'send_document', return_value=False):
        resp = app.test_client().post('/api/tally/job/1/file', headers={'x-cron-secret':'secret'},
            data={'file': (io.BytesIO(b'%PDF-1.4 test'), 'svt.pdf')}, content_type='multipart/form-data')
        assert resp.status_code == 502 and resp.json['ok'] is False


def test_daybook_evidence_preserves_uncertain_submission_marker():
    with backend(job()) as (cursor, sender):
        assert jobs.agent_step(1, 'daybook_evidence', 'Day Book screenshot', png=b'image')
        update = next(c for c in cursor.execute.call_args_list if 'log = log' in c.args[0])
        assert update.args[1][0] == 'submitting'


def test_abandoned_path_error_is_dismissed_before_company_typing():
    desk = Mock()
    screens = [('Incorrect File/Folder Path Specify File Path', ''), ('Specify File Path', ''),
               ('Import Transactions', ''), ('Gateway of Tally', '')]
    with patch.object(robot, 'read_screen', side_effect=screens), patch.object(robot.time, 'sleep'):
        robot.recover_tally_screen(desk)
    assert [c.args[0] for c in desk.keyboard.press.call_args_list] == ['Escape'] * 3
    desk.keyboard.type.assert_not_called()


def test_pdf_verification_accepts_exact_company_ledger_period():
    import io
    from datetime import date
    from reportlab.pdfgen.canvas import Canvas
    data = io.BytesIO(); canvas = Canvas(data)
    canvas.drawString(40, 800, COMPANY)
    canvas.drawString(40, 780, 'SVT LOGISTICS')
    canvas.drawString(40, 760, '1-Nov-26 to 30-Nov-26'); canvas.save()
    robot.verify_pdf(data.getvalue(), 'SVT LOGISTICS', COMPANY, date(2026,11,1), date(2026,11,30))


def test_pdf_verification_rejects_old_month():
    import io, pytest
    from datetime import date
    from reportlab.pdfgen.canvas import Canvas
    data = io.BytesIO(); canvas = Canvas(data)
    canvas.drawString(40, 800, COMPANY + ' SVT LOGISTICS 1-Oct-26 to 31-Oct-26'); canvas.save()
    with pytest.raises(RuntimeError, match='could not be verified'):
        robot.verify_pdf(data.getvalue(), 'SVT LOGISTICS', COMPANY, date(2026,11,1), date(2026,11,30))


def test_full_whatsapp_router_does_not_normalize_remote_check_to_workbook():
    from sfm import wa_tally
    cursor = Mock(); cursor.fetchone.side_effect = [None, None, {'id': 19}, None]
    with patch.object(wa, 'session', return_value={'state': {}}), \
         patch.object(wa_tally, 'normalize', side_effect=AssertionError('Must not normalize remote commands')), \
         patch.object(wa, 'documents_reply', side_effect=AssertionError('Must not return transporter bills')), \
         patch.object(jobs, 'start_github') as dispatch:
        out = wa.handle(cursor, 'test', {'role':'owner'}, {'type':'text','text':{'body':'tally check 2/11/2026'}})
    assert 'Day Book' in out and 'secure PIN' in out
    dispatch.assert_called_once_with(19)


def test_remote_ledger_pdf_bypasses_transporter_pdf_and_stale_confirmation():
    from sfm import wa_tally
    for state in ({}, {'mode':'confirm'}, {'mode':'adj'}, {'mode':'bill'}):
        with patch.object(wa, 'session', return_value={'state':state}), patch.object(wa, 'save_state'), \
             patch.object(jobs, 'handle', return_value='Remote ledger job') as handler, \
             patch.object(wa_tally, 'normalize', side_effect=AssertionError('wrong route')), \
             patch.object(wa, 'documents_reply', side_effect=AssertionError('wrong PDF')):
            out = wa.handle(Mock(), 'test', {'role':'owner'}, {'type':'text','text':{'body':'tally ledger svt nov 2026 pdf'}})
            assert out == 'Remote ledger job'
            assert handler.call_args.args[2] == 'tally ledger svt nov 2026 pdf'


def test_remote_commands_survive_normalization_and_are_not_bill_documents():
    from sfm import wa_tally
    for text in ('tally check 2/11/2026', 'tally daybook 2/11/2026', 'tally day book 2/11/2026',
                 'tally ledger svt nov 2026 pdf', 'tally status'):
        assert wa_tally.normalize(Mock(), text) == text
        assert not wa.wants_documents(text)
    assert wa.wants_documents('svt pdf november')


def test_malformed_remote_command_never_falls_into_workbook():
    with patch.object(wa, 'session', return_value={'state':{}}), patch.object(jobs, 'handle', return_value=None):
        out = wa.handle(Mock(), 'test', {'role':'owner'}, {'type':'text','text':{'body':'tally check'}})
        assert 'tally check 2/11/2026' in out


def test_workbook_daily_check_route_remains_available():
    from sfm import wa_tally
    with patch.object(wa, 'session', return_value={'state':{}}), \
         patch.object(wa_tally, 'normalize', return_value='daily check 2/11/2026'), \
         patch.object(jobs, 'handle', return_value=None), \
         patch.object(wa_tally, 'handle', return_value='Workbook check'):
        out = wa.handle(Mock(), 'test', {'role':'owner'}, {'type':'text','text':{'body':'daily check 2/11/2026'}})
        assert out == 'Workbook check'


def test_warm_report_is_queued_without_pin():
    cur = Mock(); cur.fetchone.side_effect = [None, None, {'id': 20}, {'id': 19}]
    with patch.object(jobs, 'start_github') as dispatch:
        out = jobs.handle(cur, 'test', 'tally check 3/11/2026', 'tally check 3/11/2026')
    assert 'no new PIN' in out
    assert any('waiting=NULL' in c.args[0] for c in cur.execute.call_args_list)
    dispatch.assert_called_once_with(20)


def test_check_tally_and_svt_ledger_aliases():
    for text, expected in [('check tally 3/11', 'tally check 3/11'), ('svt ledger','tally ledger svt this month pdf')]:
        with patch.object(wa, 'session', return_value={'state':{}}), patch.object(jobs, 'handle', return_value='job') as handler:
            assert wa.handle(Mock(), 'test', {'role':'owner'}, {'type':'text','text':{'body':text}}) == 'job'
            assert handler.call_args.args[2] == expected


def test_session_poll_claims_only_same_owner_queued_report():
    parent = job('done')
    child = job('starting'); child['id'] = 2
    with backend(parent) as (cur, sender):
        cur.fetchone.side_effect = [parent, child]
        result = jobs.session_next(1)
    assert result['job']['id'] == 2
    query = next(c for c in cur.execute.call_args_list if 'session_parent' in c.args[0] and c.args[0].startswith('SELECT'))
    assert query.args[1] == ('test', '1')


def test_active_job_cannot_advertise_idle_session():
    with backend(job('submitting')):
        assert jobs.session_next(1) == {'ok': False}
