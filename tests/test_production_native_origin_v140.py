"""原生审稿来源由宿主账本绑定，不能由空上下文审稿者猜填。"""
import hashlib
import json

import pytest

from knowledge_product_workflow.production_controller import NativeInlineBridge
from knowledge_product_workflow.production_host import NativeProtocolError


class Client:
    native_origin = False
    model_turns = 0
    _blocked = False

    def request(self, method, params):
        assert method == 'turn/start'
        self.params = params
        self.model_turns += 1
        return {'turn': {'id': 'parent-turn'}}


def completed_review(*, forged=False, reported_model='observed-child-model'):
    client = Client()
    bridge = NativeInlineBridge(client, model='requested-model', max_children=6)
    bridge.parent_id = 'parent'
    bridge._root_scope_verified = True
    task = {'task_id':'review-one','role':'reviewer','candidate_version':1,'input_manifest_sha256':'a'*64}
    bridge.dispatch([task])
    ledger = bridge.ledger
    def item(thread, value):
        ledger.consume({'method':'item/completed','params':{'threadId':thread,'turnId':'child-turn','item':value}})
    item('parent', {'type':'subAgentActivity','kind':'started','agentThreadId':'child',
        'agentPath':'/root/'+ledger.expected_tasks['review-one']['agent_name'],'model':reported_model})
    ledger.consume({'method':'thread/started','params':{'thread':{'id':'child','environments':[]}}})
    ledger.consume({'method':'turn/started','emittedAtMs':10,'params':{'threadId':'child','turn':{'id':'child-turn'}}})
    review = {'status':'passed','scores':{},'unit_evidence':[]}
    if forged:
        review.update({k:'invented-by-child' for k in ('model','model_version','prompt_hash','reviewer_identity','reviewer_session_id')})
    payload = {k:task[k] for k in ('task_id','candidate_version','input_manifest_sha256')}
    payload['result'] = {'review':review}
    item('child', {'type':'agentMessage','phase':'final_answer','text':json.dumps(payload)})
    ledger.consume({'method':'turn/completed','emittedAtMs':20,'params':{'threadId':'child','turn':{'id':'child-turn','status':'completed'}}})
    item('parent', {'type':'collabAgentToolCall','tool':'closeAgent','status':'completed','senderThreadId':'parent',
        'receiverThreadIds':['child'],'agentsStates':{'child':{'status':'shutdown'}}})
    return bridge, client


@pytest.mark.parametrize('forged', [False, True])
def test_native_review_overrides_child_provenance_and_marks_parent_input_scope(forged):
    bridge, client = completed_review(forged=forged)
    receipt = bridge.verified_receipts()[0]
    review = receipt['result']['result']['review']
    expected_sha = hashlib.sha256(client.params['input'][0]['text'].encode()).hexdigest()
    assert review['model'] == 'observed-child-model'
    assert review['model_version'] == 'host-not-reported'
    assert review['prompt_hash'] == expected_sha
    assert review['reviewer_identity'] == review['reviewer_session_id'] == 'child'
    assert receipt['host_provenance']['prompt_hash_scope'] == 'native_parent_dispatch_input'
    assert receipt['host_provenance']['child_prompt_observed'] is False
    assert receipt['host_provenance']['parent_turn_id'] == 'parent-turn'
    assert receipt['host_provenance']['requested_model'] == 'requested-model'


def test_absent_native_model_is_unknown_instead_of_requested_model():
    bridge, _ = completed_review(reported_model=None)
    assert bridge.verified_receipts()[0]['result']['result']['review']['model'] == 'host-not-reported'


@pytest.mark.parametrize('change', ['missing', 'version', 'manifest', 'parent'])
def test_native_provenance_requires_current_controller_binding(change):
    bridge, _ = completed_review()
    if change == 'missing':
        bridge._review_origins.clear()
    else:
        field = {'version':'candidate_version','manifest':'input_manifest_sha256','parent':'parent_turn_id'}[change]
        bridge._review_origins['review-one'][field] = 'foreign'
    with pytest.raises(NativeProtocolError, match='来源'):
        bridge.verified_receipts()


def test_conflicting_started_model_is_not_silently_overwritten():
    bridge, _ = completed_review()
    with pytest.raises(NativeProtocolError, match='模型'):
        bridge.ledger.consume({'method':'item/completed','params':{'threadId':'parent','item':{
            'type':'subAgentActivity','kind':'started','agentThreadId':'child',
            'agentPath':'/root/'+bridge.ledger.expected_tasks['review-one']['agent_name'],'model':'different'}}})


def test_same_parent_later_dispatch_cannot_supply_earlier_review_origin():
    bridge, _ = completed_review()
    bridge.ledger.parent_status = 'completed'
    bridge.dispatch([{'task_id':'other-review','role':'reviewer','candidate_version':1,
                      'input_manifest_sha256':'b'*64}])
    bridge._review_origins['review-one']['prompt_sha256'] = bridge._review_origins['other-review']['prompt_sha256']
    with pytest.raises(NativeProtocolError, match='来源'):
        bridge.verified_receipts()


def test_foreign_parent_cannot_fill_an_unreported_child_model():
    bridge, _ = completed_review(reported_model=None)
    bridge.ledger.consume({'method':'item/completed','params':{'threadId':'foreign-parent','item':{
        'type':'subAgentActivity','kind':'started','agentThreadId':'child',
        'agentPath':'/foreign/other','model':'foreign-injected-model'}}})
    assert bridge.verified_receipts()[0]['result']['result']['review']['model'] == 'host-not-reported'
