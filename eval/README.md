# 评测说明

`questions.json` 是与开发样例同源的 20 题开发回归集，覆盖明确待办、负责人未定、预算建议、无依据问题及恶意工具请求。它不是独立验收集。

## 不调用模型的检查

```bash
uv run --no-sync python scripts/evaluate.py --dry-run
uv run --no-sync pytest -q
```

## 真实模型评测

配置 `.env` 后执行。先用 `--limit 3` 检查服务协议，再跑完整集。

```bash
# 本机 llama.cpp
uv run --no-sync python scripts/evaluate.py --limit 3
# 云端 API：明确允许发送虚构样例，会产生实际请求
uv run --no-sync python scripts/evaluate.py --allow-cloud --limit 3
```

报告保存在忽略目录 `data/eval/<时间>/report.json`，包含 provider、模型、运行平台、纪要、调用记录、回答和错误。原始报告保留会议文字，不自动上传。

人工逐题填写 `human_supported`、`human_task_success` 与备注。空值代表未评测，不应按正确计数。引用与预期证据交集只是辅助检查；`succeeded` 状态仅表示结构和来源检查通过，不能当作语义正确率。

后续验收需要另录 3 段真实音频、另写未参与调提示的会议样例。比较云端与本地模型时分开统计。记录 ASR 加载时间、推理时间、音频时长和 RTF；CER 需要人工参考转录，当前不提供未经标注的数字。

目前没有云端/本地 LLM 质量结果。测试中的 scripted transport 只用于检验程序边界，不能作为模型质量证据。
