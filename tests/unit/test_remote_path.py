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

def test_folder_selector_is_explicitly_selected():
    d=Mock()
    with patch.object(a,'read_screen',return_value=('Import Transactions','')), patch.object(a,'wait_screen'), patch.object(a,'replace_import_field'), patch.object(a,'remote_type'), patch.object(a,'click_setting') as click:
        a.select_import_path(d, r'D:\NOV 2026')
    click.assert_called_once_with(d,['File Path'],value_field=True)


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
