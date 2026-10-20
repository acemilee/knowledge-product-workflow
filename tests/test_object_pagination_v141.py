"""原因筛查与章节尾豁免；最终图表分页另由真实PDF验收。"""
from knowledge_product_workflow.visual_audit import VisualPageMetric, _object_flow_findings


def metrics():
    return [VisualPageMetric(page_number=n,page_type='body',width_pt=595,height_pt=842,
        text_characters=800,bottom_gap_fraction=.55 if n==1 else .1) for n in (1,2)]


def test_vector_figure_pushback_requires_context_even_above_old_density_threshold():
    pages=metrics();pages[0].body_height_fraction=.354
    findings=_object_flow_findings(pages,['6. 当前章\n正文','6. 当前章\n图4: 固定分解链'],'')
    assert findings[0].code=='PDF_OBJECT_FLOW_REVIEW_REQUIRED'
    assert pages[0].layout_context_required_pages==[1,2]
    assert findings[0].severity.value=='warning', '候选不是因果自动定论'


def test_chapter_end_whitespace_is_not_an_object_pushback_failure():
    pages=metrics()
    assert not _object_flow_findings(pages,['6. 当前章\n收束','7. 下一章\n图5: 新内容'],'')


def test_sparse_closure_guidance_does_not_require_rewriting_an_allowed_chapter_end():
    from knowledge_product_workflow.visual_audit import _closure_findings
    pages=[VisualPageMetric(page_number=n,page_type='body',width_pt=595,height_pt=842,
        text_characters=100,body_height_fraction=.1) for n in (1,2,3,4)]
    findings=_closure_findings(pages)
    assert findings and all('章末偶发留白可容忍' in f.message and '不因' in f.message for f in findings)


def test_legal_table_continuation_and_unknown_cause_do_not_become_automatic_blockers():
    pages=metrics()
    findings=_object_flow_findings(pages,['6. 当前章\n表前说明','6. 当前章\n编号 条件\nA 固定值'],
        '| 编号 | 条件 |\n| --- | --- |\n| A | 固定值 |')
    assert findings and findings[0].severity.value=='warning'
    pages[0].bottom_gap_fraction=.1
    assert not _object_flow_findings(pages,['6. 当前章','6. 当前章\n编号 条件'],'| 编号 | 条件 |')
