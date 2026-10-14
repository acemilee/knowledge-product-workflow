| 回答 | `answer_text`、`references` | 表达当前结论并列出所用段落；结论仍需对照摘录检查 |

例如，第一条原始记录是 `{"source_id": "S1", "paragraph_id": "P1", "text": "camera checkout requires approval"}`。本例把英文小写词组成 `tokens`，给定查询词集合 `{"camera", "approval"}`。两个查询词都出现，记为 `hits = 2`。当 `min_hits = 2` 时，它满足筛选条件；`top_k = 2` 则限制最多交接两条。阈值与条数只改变筛选结果，无法替读者判断规定是否仍适用。

第二条资料的段落是 `S1` 中的 `P2`，文本为 `camera return before friday`。它只命中 `camera`，因此在这组参数下不进入证据摘录。这一落选不能推出“资料没有归还要求”，只能说明当前查询没有选中它。回答应围绕已交接的文本说明借用需要批准；涉及归还时间时，需要换查询并再核对对应段落。

第三条文本为 `camera checkout requires approval and registration`，但 `source_id` 和 `paragraph_id` 都为空。它也命中两个词，仍不能据此完成出处交接。保留缺项能让复核者看见问题：文字足以匹配，定位信息却不够。用行号补位会把暂时的位置包装成来源，应交由人工找到原始资料、补齐标识，再重新检索并逐项核对原文。
