from datetime import date

import pytest

from knowledge_product_workflow.presentation import (
    PresentationOverrides,
    PublicationMetadata,
    PublicationProfile,
    PublicationUpdateInput,
)


def fields():
    return {"subtitle": "学习主线", "cover_label": "葬爱西兰花", "footer_label": "葬爱西兰花",
        "copyright_line": "© 2026 葬爱西兰花", "edition_label": "v1.0.0","publication_date": date(2026,10,2),
        "cover_layout_revision": 2, "cover_summary_zh": "学习完整、可执行的设计方法。",
        "cover_summary_en": "Learn to create executable design specifications.","cover_focus_items": ["输入边界","验收标准"],
        "cover_content_note": "具体设计需要验证。","illustration_style_revision": 2}


def test_cover_fields_roundtrip_four_models_and_optional_update():
    p=PublicationProfile(**fields())
    m=PublicationMetadata(**p.model_dump(exclude={"schema_version","revision","reading_context","product_promise","visual_direction"}),profile_revision=p.revision,title="主题",audience_label="进阶读者")
    assert m.cover_summary_zh==p.cover_summary_zh and m.illustration_style_revision==2
    assert PublicationUpdateInput(version_bump="none",cover_summary_zh="新的梗概").cover_layout_revision is None
    assert not PresentationOverrides().has_values()
    assert PublicationProfile(**{k:v for k,v in fields().items() if not k.startswith('cover_') or k=='cover_label'}).cover_layout_revision==1


def test_cover_content_limits():
    with pytest.raises(ValueError):
        PublicationProfile(**(fields() | {"cover_focus_items":["x"]*5}))
    with pytest.raises(ValueError):
        PublicationProfile(**(fields() | {"cover_focus_items":["x"*25]}))
    with pytest.raises(ValueError):
        PublicationProfile(**(fields() | {"cover_summary_zh":" "}))
