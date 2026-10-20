"""仅本地协议：认证内存配置、回执与敏感字段日志保护。"""
import json
import sys

import pytest

from knowledge_product_workflow.production_host import NativeAppServerClient, NativeProtocolError


def server(tmp_path):
    path=tmp_path/"auth-server.py"
    path.write_text("""import sys,json,os
logged=False
def send(v):print(json.dumps(v),flush=True)
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r:continue
 method=r['method']; ident=r['id']
 if method=='config/read':result={'config':{'cli_auth_credentials_store':'ephemeral'}}
 elif method=='thread/read':result={'thread':{'id':'parent','ephemeral':True,'environments':[],'status':{'type':'idle'},'turns':[]}}
 elif method=='account/login/start':
  result={'type':'chatgpt','loginId':'own-login','authUrl':'https://auth.openai.com/authorize?state=private-state-canary'}
  send({'id':ident,'result':result})
  send({'method':'account/login/completed','params':{'loginId':'foreign-login','success':True}})
  send({'method':'account/login/completed','params':{'loginId':'own-login','success':True}})
  logged=True
  continue
 elif method=='account/read':result={'account':{'type':'chatgpt','email':'private-email-canary','planType':'plus'} if logged else None,'requiresOpenaiAuth':True}
 else:result={}
 send({'id':ident,'result':result})
""",encoding="utf-8")
    return path


def test_memory_only_login_receipt_and_redacted_disk_journal(tmp_path):
    path=server(tmp_path)
    command=[sys.executable,"-u",str(path),"-c",'cli_auth_credentials_store="ephemeral"']
    with NativeAppServerClient(command,cwd=tmp_path,private_home=tmp_path/"private") as client:
        assert client.verify_ephemeral_auth()
        assert client.preflight_ephemeral_metadata("parent")["supported"]
        assert not client.auth_status()["authenticated"]
        challenge=client.begin_managed_login()
        assert challenge["auth_url"].startswith("https://auth.openai.com/")
        assert client.poll_managed_login(timeout=.2)["state"]=="authenticated"
        with pytest.raises(NativeProtocolError):
            client.request("account/login/start",{"type":"apiKey","apiKey":"private-api-canary"})
        with pytest.raises(NativeProtocolError):
            client.request("account/read",{"refreshToken":True})
        with pytest.raises(NativeProtocolError):
            client.request("account/logout",{})
        assert client.model_turns==0
    text="\n".join(p.read_text(encoding="utf-8") for p in (tmp_path/"private").rglob("*.jsonl"))
    assert all(value not in text for value in ["private-state-canary","private-email-canary","private-api-canary"])
    assert not list((tmp_path/"private").rglob("auth.json"))


def test_unknown_login_request_is_not_resent_and_does_not_grant_models(tmp_path):
    path=tmp_path/"eof-auth.py"
    path.write_text("import sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n if r['method']=='account/login/start':break\n result={'config':{'cli_auth_credentials_store':'ephemeral'}} if r['method']=='config/read' else {'thread':{'id':'parent','ephemeral':True,'environments':[],'status':{'type':'idle'},'turns':[]}}\n print(json.dumps({'id':r['id'],'result':result}),flush=True)\n",encoding="utf-8")
    command=[sys.executable,"-u",str(path),"-c",'cli_auth_credentials_store="ephemeral"']
    with NativeAppServerClient(command,cwd=tmp_path,private_home=tmp_path/"private",model_turn_limit=1) as client:
        client.verify_ephemeral_auth()
        client.preflight_ephemeral_metadata("parent")
        with pytest.raises(NativeProtocolError,match="EOF"):
            client.request("account/login/start",{"type":"chatgpt"})
        with pytest.raises(NativeProtocolError):
            client.request("account/login/start",{"type":"chatgpt"})
        with pytest.raises(NativeProtocolError,match="模型"):
            client.request("turn/start",{})
        assert client.model_turns==0
    audit=[json.loads(line) for line in (tmp_path/"private/request-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len([row for row in audit if row['method']=='account/login/start'])==1


def test_memory_auth_must_be_confirmed_by_actual_server_and_stderr_is_redacted(tmp_path):
    path=tmp_path/"wrong-store.py"
    path.write_text("import sys,json,os\nprint('access_token=opaque-canary',file=sys.stderr,flush=True)\nfor line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({'id':r['id'],'result':{'config':{'cli_auth_credentials_store':'file'}}}),flush=True)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(path),"-c",'cli_auth_credentials_store="ephemeral"'],cwd=tmp_path,private_home=tmp_path/"private") as client:
        with pytest.raises(NativeProtocolError,match="确认"):
            client.begin_managed_login()
        assert not client._login_attempted and client.model_turns==0
    assert 'opaque-canary' not in (tmp_path/"private/server.stderr.log").read_text(encoding="utf-8")


def test_native_cancel_not_found_keeps_terminal_unknown(tmp_path):
    path=server(tmp_path)
    with NativeAppServerClient([sys.executable,"-u",str(path),"-c",'cli_auth_credentials_store="ephemeral"'],cwd=tmp_path,private_home=tmp_path/"private") as client:
        client.preflight_ephemeral_metadata("parent")
        client.begin_managed_login()
        result=client.cancel_managed_login()
        assert result["state"]=="unknown" and result["requested"]
        assert client.model_turns==0


def test_login_challenge_is_blocked_until_metadata_rpc_shape_is_verified(tmp_path):
    path=server(tmp_path)
    with NativeAppServerClient([sys.executable,"-u",str(path),"-c",'cli_auth_credentials_store="ephemeral"'],cwd=tmp_path,private_home=tmp_path/"private") as client:
        with pytest.raises(NativeProtocolError,match="元数据预检"):
            client.begin_managed_login()
        assert not client._login_attempted and client.model_turns==0
        assert not any(r["method"]=="account/login/start" for r in client.request_receipts)
        client.preflight_ephemeral_metadata("parent")
        assert client.begin_managed_login()["state"]=="pending"
        methods=[r["method"] for r in client.request_receipts]
        assert methods.index("thread/read")<methods.index("account/login/start")
