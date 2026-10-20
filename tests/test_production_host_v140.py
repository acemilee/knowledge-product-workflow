"""真实本地stdio进程与原生事件契约；不调用任何模型。"""
import json
import sys

import pytest

from knowledge_product_workflow.production_host import (
    NativeAppServerClient,
    NativeProtocolError,
    NativeTaskLedger,
)


def test_stdio_matches_response_after_notification_and_refuses_model_by_default(tmp_path):
    fixture=tmp_path/"server.py"
    fixture.write_text("import sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n if 'id' not in r: continue\n print(json.dumps({'method':'notice','params':{'value':1}}),flush=True)\n print(json.dumps({'id':r['id'],'result':{'method':r['method']}}),flush=True)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home") as client:
        assert client.request("initialize",{})=={"method":"initialize"}
        assert client.next_event(timeout=1)["method"]=="notice"
        with pytest.raises(NativeProtocolError,match="模型"):
            client.request("turn/start",{"threadId":"parent","input":[]})
        process=client.process
    assert process.poll() is not None


def event(parent,item,method="item/completed"):
    return {"method":method,"params":{"threadId":parent,"turnId":"turn-"+parent,"item":item}}


def start_turn(state, identity):
    state.consume({"method":"turn/started","params":{"threadId":identity,"turn":{"id":"turn-"+identity}}})


def ledger():
    return NativeTaskLedger("parent",{"writer-one":{"candidate_version":1,"input_manifest_sha256":"a"*64}})


def test_real_started_activity_completion_is_not_child_completion_or_release():
    state=ledger()
    state.consume(event("parent",{"id":"call-one","type":"subAgentActivity","kind":"started","agentThreadId":"child","agentPath":"/root/writer-one"}))
    assert state.active_children=={"child"}
    assert state.result_for("writer-one") is None
    state.consume(event("parent",{"id":"wait","type":"collabAgentToolCall","tool":"wait","status":"completed","senderThreadId":"parent","receiverThreadIds":[],"agentsStates":{}}))
    assert state.active_children=={"child"}


def test_parent_text_cannot_fake_child_result_and_shutdown_requires_native_close():
    state=ledger()
    state.consume(event("parent",{"id":"spawn","type":"subAgentActivity","kind":"started","agentThreadId":"child","agentPath":"/root/writer-one"}))
    start_turn(state,"child")
    result=json.dumps({"task_id":"writer-one","candidate_version":1,"input_manifest_sha256":"a"*64,"result":{"body":"候选正文"}})
    state.consume(event("parent",{"id":"text","type":"agentMessage","phase":"final_answer","text":result}))
    assert state.result_for("writer-one") is None
    state.consume(event("child",{"id":"text","type":"agentMessage","phase":"final_answer","text":result}))
    state.consume({"method":"turn/completed","params":{"threadId":"child","turn":{"id":"turn-child","status":"completed"}}})
    assert state.result_for("writer-one")["result"]["body"]=="候选正文"
    assert state.active_children=={"child"}
    state.consume(event("parent",{"id":"close","type":"collabAgentToolCall","tool":"closeAgent","status":"completed","senderThreadId":"parent","receiverThreadIds":["child"],"agentsStates":{"child":{"status":"shutdown"}}}))
    assert not state.active_children


def test_foreign_or_stale_result_is_never_accepted():
    state=ledger()
    state.consume(event("parent",{"id":"spawn","type":"subAgentActivity","kind":"started","agentThreadId":"child","agentPath":"/root/writer-one"}))
    start_turn(state,"child")
    state.consume(event("foreign",{"id":"message","type":"agentMessage","phase":"final_answer","text":"{}"}))
    payload=json.dumps({"task_id":"writer-one","candidate_version":0,"input_manifest_sha256":"a"*64,"result":{"body":"旧稿"}})
    state.consume(event("child",{"id":"message","type":"agentMessage","phase":"final_answer","text":payload}))
    state.consume({"method":"turn/completed","params":{"threadId":"child","turn":{"id":"turn-child","status":"completed"}}})
    with pytest.raises(NativeProtocolError,match="版本"):
        state.result_for("writer-one")


def test_closed_interrupted_worker_cannot_become_completed_result():
    state=ledger()
    state.consume(event("parent",{"id":"spawn","type":"subAgentActivity","kind":"started","agentThreadId":"child","agentPath":"/root/writer-one"}))
    start_turn(state,"child")
    payload=json.dumps({"task_id":"writer-one","candidate_version":1,"input_manifest_sha256":"a"*64,"result":{"body":"未完成稿"}})
    state.consume(event("child",{"id":"text","type":"agentMessage","phase":"final_answer","text":payload}))
    state.consume({"method":"turn/completed","params":{"threadId":"child","turn":{"id":"turn-child","status":"interrupted"}}})
    state.consume(event("parent",{"id":"close","type":"collabAgentToolCall","tool":"closeAgent","status":"completed","senderThreadId":"parent","receiverThreadIds":["child"],"agentsStates":{"child":{"status":"shutdown"}}}))
    assert not state.active_children and state.result_for("writer-one") is None


def test_nested_child_counts_and_parent_completion_does_not_release():
    state=ledger()
    for parent,identity,path in [("parent","child","/root/writer-one"),("child","nested","/root/writer-one/nested")]:
        state.consume(event(parent,{"id":"start-"+identity,"type":"subAgentActivity","kind":"started","agentThreadId":identity,"agentPath":path}))
    state.consume({"method":"turn/completed","params":{"threadId":"parent","turn":{"id":"turn-parent","status":"completed"}}})
    assert state.active_children=={"child","nested"}
    assert state.result_for("writer-one") is None


def test_notification_flood_cannot_extend_rpc_deadline(tmp_path):
    fixture=tmp_path/"flood.py"
    fixture.write_text("import sys,json,time\njson.loads(sys.stdin.readline())\nwhile True:\n print(json.dumps({'method':'notice','params':{}}),flush=True)\n time.sleep(.01)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home") as client:
        with pytest.raises(NativeProtocolError,match="超时"):
            client.request("initialize",{},timeout=.15)
        process=client.process
    assert process.poll() is not None


def test_eof_after_model_send_is_unknown_and_never_retried(tmp_path):
    fixture=tmp_path/"eof.py"
    fixture.write_text("import sys,json\njson.loads(sys.stdin.readline())\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home",model_turn_limit=1) as client:
        with pytest.raises(NativeProtocolError,match="EOF"):
            client.request("turn/start",{"threadId":"parent","input":[]})
        with pytest.raises(NativeProtocolError,match="模型"):
            client.request("turn/start",{"threadId":"parent","input":[]})
        assert client.model_turns==1
    audits=[json.loads(line) for line in (tmp_path/"home/request-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(audits)==1 and audits[0]["method"]=="turn/start"


def test_idle_event_wait_does_not_turn_into_model_failure(tmp_path):
    fixture=tmp_path/"quiet.py"
    fixture.write_text("import sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({'id':r['id'],'result':{}}),flush=True)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home") as client:
        assert client.next_event(timeout=.02) is None
        assert client.request("initialize",{})=={}


def test_inline_bridge_and_queue_receive_use_real_ipc_without_native_qualification(tmp_path):
    from knowledge_product_workflow.production_controller import (
        NativeInlineBridge,
        receive_native_results,
    )
    from knowledge_product_workflow.production_tasks import (
        ProductionPolicy,
        ProductionRun,
        next_dispatches,
        resume_production_run,
        save_run,
    )
    from knowledge_product_workflow.workspace import Workspace
    workspace=Workspace.create(tmp_path,"bridge")
    run=ProductionRun.diagnostic_run(1,ProductionPolicy())
    task=next_dispatches(run)[0]
    save_run(workspace,run)
    script=tmp_path/"native-fixture.py"
    script.write_text("""import sys,json,os

def send(value): print(json.dumps(value),flush=True)
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 method=r['method']; number=r['id']
 if method=='initialize': send({'id':number,'result':{'codexHome':os.environ['CODEX_HOME']}})
 elif method=='thread/start': send({'id':number,'result':{'thread':{'id':'parent','ephemeral':True,'environments':[]},'sandbox':{'type':'readOnly','networkAccess':False},'runtimeWorkspaceRoots':[]}})
 elif method=='thread/read': send({'id':number,'result':{'thread':{'id':'parent','ephemeral':True,'environments':[],'status':{'type':'idle'},'turns':[]}}})
 elif method=='turn/start':
  send({'id':number,'result':{'turn':{'id':'parent-turn'}}})
  text=r['params']['input'][0]['text']; data=json.loads(text.split('任务数据：\\n',1)[1])
  for task in data:
   child='child-'+task['task_id']
   send({'method':'thread/started','params':{'thread':{'id':child,'environments':[]}}})
   send({'method':'item/completed','params':{'threadId':'parent','item':{'type':'subAgentActivity','id':'spawn-'+child,'kind':'started','agentThreadId':child,'agentPath':'/root/'+task['agent_name']}}})
   send({'method':'turn/started','params':{'threadId':child,'turn':{'id':'child-turn','status':'inProgress'}}})
   payload={key:task[key] for key in ['task_id','candidate_version','input_manifest_sha256']};payload['result']={'body':'明确标注的离线协议夹具'}
   send({'method':'item/completed','params':{'threadId':child,'turnId':'child-turn','item':{'type':'agentMessage','id':'message','phase':'final_answer','text':json.dumps(payload)}}})
   send({'method':'turn/completed','params':{'threadId':child,'turn':{'id':'child-turn','status':'completed'}}})
   send({'method':'item/completed','params':{'threadId':'parent','item':{'type':'collabAgentToolCall','id':'close-'+child,'tool':'closeAgent','status':'completed','senderThreadId':'parent','receiverThreadIds':[child],'agentsStates':{child:{'status':'shutdown'}}}}})
 else: send({'id':number,'result':{}})
""",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(script)],cwd=workspace.root,private_home=workspace.root/"runs/host",model_turn_limit=1) as client:
        bridge=NativeInlineBridge(client,model="protocol-fixture-no-model",max_children=1)
        assert bridge.connect()["model_turns_requested"]==0
        bridge.create_parent()
        bridge.dispatch([task.model_dump(mode="json")])
        result=bridge.collect(timeout=2)
        assert result["released"] and not result["native_binary_verified"]
        assert receive_native_results(workspace,run.run_id,bridge)==[task.task_id]
        assert receive_native_results(workspace,run.run_id,bridge)==[]
        loaded=resume_production_run(workspace,run.run_id)
        assert loaded.task(task.task_id).release_confirmed
        assert loaded.task(task.task_id).host_task_id=="child-"+task.task_id
        assert loaded.diagnostic_only and not loaded.capabilities.admits_live_parallelism()
        assert not (workspace.root/"chapters/chapter-01.md").exists()
        with pytest.raises(NativeProtocolError,match="资格"):
            bridge.certified_capabilities(workspace=workspace.root,run_id=run.run_id)


def test_json_flags_cannot_restore_live_capabilities(tmp_path):
    from knowledge_product_workflow.production_tasks import (
        ProductionHostCapabilities,
        ProductionPolicy,
        ProductionRun,
        resume_production_run,
        save_run,
    )
    from knowledge_product_workflow.workspace import Workspace
    workspace=Workspace.create(tmp_path,"json-flags")
    run=ProductionRun.diagnostic_run(1,ProductionPolicy())
    save_run(workspace,run)
    capabilities=ProductionHostCapabilities(host_kind="codex-app-server",max_parallelism=6,production_verified=True,
        permissions_verified=True,parent_child_receipts_verified=True,release_verified=True)
    with pytest.raises(ValueError,match="JSON"):
        resume_production_run(workspace,run.run_id,capabilities=capabilities)


def test_process_provider_alias_does_not_override_reserved_builtin(tmp_path):
    from knowledge_product_workflow.production_host import codex_app_server_command
    command=codex_app_server_command(tmp_path/"codex.exe",tmp_path/"home")
    assert 'model_provider="kp_native"' in command
    assert 'model_providers.kp_native.request_max_retries=0' in command
    assert 'model_providers.kp_native.stream_max_retries=0' in command
    assert not any(value.startswith("model_providers.openai.") for value in command)


def test_session_retains_one_connection_and_default_zero_model_budget(tmp_path):
    import io

    from knowledge_product_workflow.production_controller import (
        NativeProductionSession,
        serve_native_session,
    )
    from knowledge_product_workflow.workspace import Workspace
    workspace = Workspace.create(tmp_path, "session")
    fixture = tmp_path / "server.py"
    fixture.write_text("import os,sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n if 'id' in r: print(json.dumps({'id':r['id'],'result':{'codexHome':os.environ['CODEX_HOME']}}),flush=True)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=workspace.root,private_home=workspace.root/"runs/native") as client:
        from knowledge_product_workflow.production_controller import NativeInlineBridge
        bridge = NativeInlineBridge(client, model="fixture", max_children=4)
        session = NativeProductionSession(workspace, bridge)
        output = io.StringIO()
        serve_native_session(session, io.StringIO('{"op":"status"}\n{"op":"start"}\n{"op":"status"}\n{"op":"close"}\n'), output)
        frames = [json.loads(line) for line in output.getvalue().splitlines()]
        assert frames[0]["result"]["model_turns_requested"] == 0
        assert not frames[1]["ok"] and "资格" in frames[1]["error"]
        assert frames[2]["ok"] and frames[2]["result"]["model_turns_requested"] == 0
        assert not client.native_origin


def test_receiving_one_ready_child_does_not_wait_for_the_other(tmp_path):
    from knowledge_product_workflow.production_controller import NativeInlineBridge
    fixture=tmp_path/"server.py"
    fixture.write_text("import sys,time\ntime.sleep(10)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home") as client:
        bridge=NativeInlineBridge(client,model="fixture",max_children=4)
        bridge.parent_id="parent"
        bridge.ledger=NativeTaskLedger("parent",{key:{"candidate_version":1,"input_manifest_sha256":"a"*64} for key in ["one","two"]})
        for key in ["one","two"]:
            bridge.ledger.consume(event("parent",{"type":"subAgentActivity","kind":"started","agentThreadId":key,"agentPath":"/root/"+key}))
            start_turn(bridge.ledger,key)
        payload=json.dumps({"task_id":"one","candidate_version":1,"input_manifest_sha256":"a"*64,"result":{"body":"先完成的章节"}})
        client._events.extend([
            event("one",{"type":"agentMessage","phase":"final_answer","text":payload}),
            {"method":"turn/completed","params":{"threadId":"one","turn":{"id":"turn-one","status":"completed"}}},
            event("parent",{"type":"collabAgentToolCall","tool":"closeAgent","status":"completed","senderThreadId":"parent","receiverThreadIds":["one"],"agentsStates":{"one":{"status":"shutdown"}}}),
        ])
        result=bridge.collect(timeout=.2)
        assert set(result["results"])=={"one"}
        assert not result["released"] and bridge.ledger.active_children=={"two"}


def test_shell_rpc_is_never_granted_and_quiet_running_task_is_not_restarted(tmp_path):
    from knowledge_product_workflow.production_controller import NativeInlineBridge
    fixture=tmp_path/"server.py"
    fixture.write_text("import time\ntime.sleep(10)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home") as client:
        with pytest.raises(NativeProtocolError,match="白名单"):
            client.request("thread/shellCommand",{"threadId":"parent","command":"unsafe"})
        bridge=NativeInlineBridge(client,model="fixture",max_children=4)
        bridge.ledger=ledger()
        result=bridge.collect(timeout=.02)
        assert result["status"]=="pending" and client.model_turns==0 and not client._blocked
        assert not client.request_receipts


def test_final_json_array_and_malformed_event_cannot_crash_into_acceptance():
    state=ledger()
    state.consume(event("parent",{"type":"subAgentActivity","kind":"started","agentThreadId":"child","agentPath":"/root/writer-one"}))
    start_turn(state,"child")
    state.consume(event("child",{"type":"agentMessage","phase":"final_answer","text":"[]"}))
    state.consume({"method":"turn/completed","params":{"threadId":"child","turn":{"id":"turn-child","status":"completed"}}})
    with pytest.raises(NativeProtocolError,match="对象"):
        state.result_for("writer-one")
    with pytest.raises(NativeProtocolError,match="字段"):
        state.consume({"method":"item/completed","params":[]})


def test_permission_evidence_requires_exact_native_commands_and_all_scopes():
    state=NativeTaskLedger("parent",{"one":{"candidate_version":1,"input_manifest_sha256":"a"*64,"permission_probe_commands":{"read":"fixed-read","write":"fixed-write","network":"fixed-network"}}})
    state.consume(event("parent",{"type":"subAgentActivity","kind":"started","agentThreadId":"child","agentPath":"/root/one"}))
    start_turn(state,"child")
    for scope in ["read","write","network"]:
        state.consume(event("child",{"type":"commandExecution","id":scope,"command":"fixed-"+scope,"status":"failed","exitCode":1,"aggregatedOutput":"Permission denied"}))
    assert set(state.permission_denials["child"])=={"read","write","network"}
    assert state.active_children=={"child"}


def test_interrupted_closed_sibling_does_not_hold_completed_result(tmp_path):
    from knowledge_product_workflow.production_controller import NativeInlineBridge
    fixture=tmp_path/"server.py"
    fixture.write_text("import time\ntime.sleep(10)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home") as client:
        bridge=NativeInlineBridge(client,model="fixture",max_children=4)
        bridge.ledger=NativeTaskLedger("parent",{key:{"candidate_version":1,"input_manifest_sha256":"a"*64} for key in ["good","interrupted"]})
        for key in ["good","interrupted"]:
            bridge.ledger.consume(event("parent",{"type":"subAgentActivity","kind":"started","agentThreadId":key,"agentPath":"/root/"+key}))
            start_turn(bridge.ledger,key)
            if key=="good":
                payload={"task_id":key,"candidate_version":1,"input_manifest_sha256":"a"*64,"result":{"body":"完成正文"}}
                bridge.ledger.consume(event(key,{"type":"agentMessage","phase":"final_answer","text":json.dumps(payload)}))
            bridge.ledger.consume({"method":"turn/completed","params":{"threadId":key,"turn":{"id":"turn-"+key,"status":"completed" if key=="good" else "interrupted"}}})
            bridge.ledger.consume(event("parent",{"type":"collabAgentToolCall","tool":"closeAgent","status":"completed","senderThreadId":"parent","receiverThreadIds":[key],"agentsStates":{key:{"status":"shutdown"}}}))
        result=bridge.collect(timeout=.02)
        assert set(result["results"])=={"good"} and set(result["failed"])=={"interrupted"}
        assert result["released"] and client.model_turns==0


def test_malformed_rpc_result_blocks_further_model_calls(tmp_path):
    fixture=tmp_path/"server.py"
    fixture.write_text("import sys,json\nr=json.loads(sys.stdin.readline())\nprint(json.dumps({'id':r['id'],'result':[]}),flush=True)\n",encoding="utf-8")
    with NativeAppServerClient([sys.executable,"-u",str(fixture)],cwd=tmp_path,private_home=tmp_path/"home",model_turn_limit=1) as client:
        with pytest.raises(NativeProtocolError,match="结构"):
            client.request("initialize",{})
        with pytest.raises(NativeProtocolError,match="模型"):
            client.request("turn/start",{"threadId":"parent","input":[]})
        assert client.model_turns==0 and client._blocked
