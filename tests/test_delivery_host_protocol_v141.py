"""宿主原生失败短路及恢复消息，不调用真实kp/出版工具链。"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from knowledge_product_workflow.operation_receipts import (
    operation_scope,
    operation_status,
    recover_wait_owner,
)
from knowledge_product_workflow.workspace import Workspace


def test_documented_native_exit_check_stops_later_calls(tmp_path):
    pwsh = shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell unavailable; host example remains unverified')
    doc = (Path(__file__).resolve().parents[1]/'docs/operator-runbook.md').read_text(encoding='utf8')
    snippet = re.search(r'```powershell\n(function Invoke-Kp .*?)\n```',doc,re.DOTALL)[1]
    script = tmp_path/'owned-host.ps1'
    script.write_text("$script:seen=@()\nfunction kp { $script:seen += $args[0]; $global:LASTEXITCODE=9 }\n"+
        snippet+"\ntry { Invoke-Kp -KpArguments @('owned-failure'); Invoke-Kp -KpArguments @('must-not-run') } catch {}\n"+
        "ConvertTo-Json -InputObject @($script:seen) -Compress\n", encoding='utf8')
    result = subprocess.run([pwsh,'-NoProfile','-NonInteractive','-File',str(script)],
        cwd=tmp_path,capture_output=True,text=True,encoding='utf8',timeout=10,check=False)
    assert result.returncode==0, result.stderr
    assert json.loads(result.stdout)==['owned-failure']


def test_absent_wait_owner_does_not_clear_original_receipt(tmp_path):
    w = Workspace.create(tmp_path,'owner-message')
    with pytest.raises(RuntimeError), operation_scope(w, action='submit-chapter', chapter_number=1, operation_id='op-owned-message'):
        raise RuntimeError('owned interruption')
    before = operation_status(w,'op-owned-message')
    with pytest.raises(ValueError, match='没有可释放.*原操作未知状态未改变'):
        recover_wait_owner(w,'op-owned-message')
    assert operation_status(w,'op-owned-message')==before
