import os
os.environ.setdefault('SFM_SITE','https://example.invalid')
os.environ.setdefault('SFM_SECRET','test')
os.environ.setdefault('JOB_ID','1')
from unittest.mock import Mock, patch
from agent import tally_agent as a

def test_shift_is_explicit_for_underscore_and_colon():
    d=Mock(); a.remote_type(d, 'D:\\SFM_TALLY')
    assert d.keyboard.down.call_count == 11
    assert d.keyboard.up.call_count == 11
    keys=[c.args[0] for c in d.keyboard.press.call_args_list]
    assert 'Semicolon' in keys and 'Minus' in keys and 'Backslash' in keys
    d.keyboard.type.assert_not_called()

def test_punctuation_is_not_discarded():
    path=r'D:\My_Applications\SFM_TALLY_IMPORT\NOV 2026'
    assert a.exact_path_visible(path,path)
    assert not a.exact_path_visible(path.replace('_','-'),path)
    assert not a.exact_path_visible(path.replace('NOV','OCT'),path)

def test_folder_then_full_filename_use_separate_rows():
    desk = Mock()
    folder = r'D:\NOV 2026'
    with patch.object(a, 'read_screen', return_value=('Import Transactions', '')), patch.object(a, 'wait_screen', return_value=('Specify Path', '')), patch.object(a, 'replace_import_field') as edit, patch.object(a, 'remote_type') as search, patch.object(a, 'click_setting') as click:
        a.select_import_path(desk, folder)
        edit.assert_called_once_with(desk, folder)
        search.assert_not_called()
        a.select_import_file(desk, folder, 'job26.xml')
        assert [c.args[1] for c in click.call_args_list] == [['File Path'], ['File to Import'], ['Specify Path', 'Specty Path']]
        search.assert_called_once_with(desk, 'Specify Path')
        edit.assert_called_with(desk, folder + r'\job26.xml')


def test_import_value_focus_never_presses_enter_or_types_into_backup():
    import io
    import pytest
    from PIL import Image, ImageDraw
    # OCR coordinates are at 2x screenshot resolution, as used by click_setting.
    tsv = 'block_num\tpar_num\tline_num\tleft\ttop\theight\ttext\n'
    for left, text in [(20,'File'),(60,'Path'),(250,':'),(300,'D:\\OCT')]:
        tsv += f'1\t1\t1\t{left}\t90\t20\t{text}\n'
    for correct in (True, False):
        im = Image.new('RGB', (400,200), 'white')
        y = 50 if correct else 80  # wrong backup row must fail before any typing
        ImageDraw.Draw(im).rectangle((150,y-5,300,y+5), fill=(255,240,160))
        buf=io.BytesIO();im.save(buf,format='PNG')
        desk=Mock();desk.screenshot.return_value=buf.getvalue()
        with patch.object(a.subprocess,'run',return_value=Mock(stdout=tsv)),patch.object(a.time,'sleep'):
            if correct:
                a.click_setting(desk,['File Path'],value_field=True)
            else:
                with pytest.raises(RuntimeError,match='not visibly focused'):
                    a.click_setting(desk,['File Path'],value_field=True)
        desk.mouse.click.assert_called_once_with(154,50)
        desk.keyboard.press.assert_not_called()
        desk.keyboard.type.assert_not_called()


def test_import_clear_does_not_navigate_to_format_from_path_or_filename():
    for original, value in [(r'D:\OCT 2026', r'D:\NOV 2026'), ('old.xml', 'new.xml')]:
        for caret in (0, 4, len(original)):
            state = {'field': 'requested', 'value': original, 'caret': caret}
            def press(key, **kwargs):
                assert key not in ('Home', 'End', 'Tab', 'Enter', 'Control+a')
                i=state['caret']
                if key == 'Backspace' and i:
                    state['value']=state['value'][:i-1]+state['value'][i:];state['caret']-=1
                elif key == 'Delete':
                    state['value']=state['value'][:i]+state['value'][i+1:]
            def type_value(desk, text):
                assert state['value']==''
                state['value']=text
            desk=Mock();desk.keyboard.press.side_effect=press
            with patch.object(a,'remote_type',side_effect=type_value):
                a.replace_import_field(desk,value)
            assert state['value']==value


def test_job22_format_popup_cannot_receive_folder_even_when_xml_visible():
    import pytest
    desk = Mock()
    popup = 'File Format D File to Import old.xml List of File Formats XML (Data Interchange) Spelling Error'
    for screens in [[(popup,'')], [('File Format XML (Data Interchange)',''), (popup,'')]]:
        with patch.object(a,'read_screen',side_effect=screens), patch.object(a,'click_setting'), patch.object(a,'replace_import_field') as edit:
            with pytest.raises(RuntimeError):
                a.select_import_path(desk, r'D:\NOV 2026')
            edit.assert_not_called()
            desk.keyboard.press.assert_not_called()


def test_job27_click_stays_on_xml_tab_once_and_verify_destination():
    import io
    import pytest
    from PIL import Image, ImageDraw

    def screenshot(y):
        frame = Image.new('RGB', (400, 200), 'white')
        ImageDraw.Draw(frame).rectangle((150, y-5, 300, y+5), fill=(255,240,160))
        buf = io.BytesIO()
        frame.save(buf, format='PNG')
        return buf.getvalue()

    tsv = 'block_num\tpar_num\tline_num\tleft\ttop\theight\ttext\n'
    for line, top, words in [(1, 50, ['File','Format','XML']), (2, 90, ['File','Path','D:'])]:
        for left, word in zip([20,60,300], words):
            tsv += f'1\t1\t{line}\t{left}\t{top}\t20\t{word}\n'
    for destination in (30, 50):
        desk = Mock()
        desk.screenshot.side_effect = [screenshot(y) for y in (30, 30, destination)]
        with patch.object(a.subprocess,'run',return_value=Mock(stdout=tsv)), patch.object(a,'read_screen',return_value=('Import Transactions File Format XML','')), patch.object(a.time,'sleep'):
            if destination == 50:
                a.click_setting(desk, ['File Path'], value_field=True)
            else:
                with pytest.raises(RuntimeError, match='not visibly focused'):
                    a.click_setting(desk, ['File Path'], value_field=True)
        desk.keyboard.press.assert_called_once_with('Tab')


def test_import_form_advances_from_xml_and_checks_actual_destination():
    import io
    import pytest
    from PIL import Image, ImageDraw

    def frame(row):
        image = Image.new('RGB', (800, 450), (180, 180, 180))
        draw = ImageDraw.Draw(image)
        draw.rectangle((152, 143, 564, 322), fill='white')
        y = {1:182, 2:192, 3:201}[row]
        draw.rectangle((345, y-3, 465, y+3), fill=(251,235,170))
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        return buf.getvalue()

    for destination in (1, 2, 3):
        desk = Mock()
        start, end = frame(1), frame(destination)
        desk.screenshot.side_effect = [start] + [end]*12
        with patch.object(a, 'read_screen', return_value=('Import Transactions','')), patch.object(a.time, 'sleep'):
            if destination == 2:
                a.click_setting(desk, ['File Path'], value_field=True)
            else:
                with pytest.raises(RuntimeError, match='did not highlight File Path'):
                    a.click_setting(desk, ['File Path'], value_field=True)
        desk.keyboard.press.assert_called_once_with('Enter')
        desk.mouse.click.assert_not_called()

def test_job30_open_october_picker_uses_complete_november_xml():
    picker = "Import Transactions\nSpecty Path\nSeow More\nOCT 2026"
    desk = Mock()
    folder = r'D:\My_Applications\SFM_TALLY_IMPORT\NOV 2026'
    with patch.object(a, 'read_screen', side_effect=[(picker,''), (picker,''), ('Specify File Path','')]), patch.object(a, 'click_setting') as click, patch.object(a,'wait_screen'), patch.object(a,'replace_import_field') as edit:
        a.select_import_path(desk, folder)
        a.select_import_file(desk, folder, 'job30.xml')
        click.assert_called_once_with(desk, ['Specify Path', 'Specty Path'], activate=False, ocr_mode=11)
        edit.assert_called_once_with(desk, folder + r'\job30.xml')
        desk.keyboard.press.assert_called_once_with('Enter')
