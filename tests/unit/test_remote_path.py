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
    with patch.object(a,'read_screen',return_value=('Import Transactions','')), patch.object(a,'wait_screen'), patch.object(a,'replace_field'), patch.object(a,'remote_type'), patch.object(a,'click_setting') as click:
        a.select_import_path(d, r'D:\NOV 2026')
    click.assert_called_once_with(d,['File Path'])
