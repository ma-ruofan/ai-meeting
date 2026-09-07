# Meetnote · AI Meeting Assistant

单用户 AI 会议助理：音频转录、可追溯纪要、行动项提取，以及支持只读工具调用的会议问答。

**状态：首版代码已实现。Mac 开发使用云端 API，Windows 最终接本机 llama.cpp。当前未配置真实 LLM，模型质量与 Windows/CUDA 尚未验收。**

## 已实现

- WAV / MP3 导入与时长检查，faster-whisper 独立子进程转录。
- 转录文字编辑、乐观并发校验、SQLite 持久化。
- 结构化纪要、一次纠错重试、片段引用校验。
- 不可变分析输入快照；编辑或重新转录后旧纪要自动显示过期。
- 点击引用查看对应版本原文，存在音频时定位播放；Markdown 导出。
- 三个只读工具：关键词检索、片段查询、行动项查询。
- 结构化动作 / 原生 tool_calls 两种 Agent 协议，最多 3 轮模型响应、4 次工具执行。
- 工具参数白名单、重复调用拦截、已返回证据范围检查及执行记录。
- 进程间单任务锁、失败记录与中断恢复；已有成功结果保留。

尚未提供：自动说话人分离、实时字幕、多用户、跨会议向量检索、强制取消按钮。关键词搜索不等于语义检索；引用存在不保证语义结论正确。

## 快速开始

使用 Python 3.12（项目兼容范围 3.11～3.13），并准备 uv。两台机器分别创建环境，不能复制 `.venv`。

```bash
# 安装页面、业务与测试依赖
uv sync --frozen

# 需要真实语音识别时安装可选依赖
uv sync --frozen --extra asr
```

Mac：

```bash
cp .env.example .env
bash scripts/start.sh
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
.\scripts\start.ps1
```

已有 `.env` 时保留自己的配置。启动脚本不自动同步依赖，因此不会意外移除已经安装的 ASR 包。

访问 **http://127.0.0.1:8501**，点击“打开文字样例”。无需模型即可查看、编辑和导出转录；纪要生成与问答需要真实服务。样例明确标记为人工编写的虚构文字，不是模型输出，没有配套音频。

当前 Mac 已建立 `.venv`，可直接执行 `bash scripts/start.sh`。如果全局没有 uv，也可使用 `.venv/bin/uv` 管理此环境。

## 配置模型

编辑本机 `.env`，不要提交真实密钥。

云端开发（实际服务商需支持聊天接口，差异可在 adapter 中适配）：

```dotenv
LLM_PROVIDER=cloud
LLM_BASE_URL=https://你的服务商地址/v1
LLM_MODEL=实际模型标识
LLM_API_KEY=仅在本机填写
LLM_JSON_MODE=schema
AGENT_PROTOCOL=structured
```

地址应包含服务要求的版本前缀，程序仅追加 `/chat/completions`。部分服务商并不使用 `/v1`，按其实际地址配置。

Windows 本地 llama.cpp：

```dotenv
LLM_PROVIDER=llamacpp
LLM_BASE_URL=http://127.0.0.1:8080/v1
LLM_MODEL=本地服务实际模型标识
LLAMACPP_API_KEY=
LLM_JSON_MODE=schema
AGENT_PROTOCOL=structured
ASR_DEVICE=cuda
ASR_COMPUTE_TYPE=float16
```

本地模式使用独立 `LLAMACPP_API_KEY`，不会继承云端密钥或回退到云端。首版仅允许连接本机地址。llama-server 需自行启动，GGUF 模型、模板、上下文和 GPU 配置根据实际 build 验证。

### 服务差异

- `LLM_JSON_MODE=schema`：请求 Schema 约束输出。cloud 使用 json_schema 包装，llamacpp 使用其 schema 字段。
- `json`：只请求 JSON 对象，结构由应用验证。
- `prompt`：纯聊天加 Schema 提示，应用仍校验；用于不接受 response_format 的服务。
- `AGENT_PROTOCOL=structured`：模型返回结构化动作，由程序选择白名单工具执行。
- `native`：使用服务的 tools/tool_calls，依赖模型和聊天模板支持。

不会因参数不兼容而静默多次调用付费服务；HTTP 错误直接提示。纪要仅针对结构/引用不合法最多纠错一次。

`LLM_CONTEXT_TOKENS` 必须不大于实际部署预算。程序以请求 UTF-8 字节数加预留量作保守输入估算，并为输出预留空间；它不是模型专用 tokenizer，可能提前拒绝可容纳的输入。超限不静默截断。

API 阶段页面明确提示文字将发送给配置的服务商。试验请使用虚构样例；许可勾选只针对该页面的模型操作。

## 音频与系统准备

- 首版最长 10 分钟、最大 100 MB，一次一个音频或模型任务。
- 16kHz / 单声道 / 16-bit PCM WAV 可以直接验证后导入。
- 其他 WAV 和 MP3 需要 FFmpeg，MP3 探测需要 ffprobe。安装后加入 PATH，或在 `.env` 设置 `FFMPEG_PATH` / `FFPROBE_PATH`。
- Mac 先使用 `ASR_DEVICE=cpu`、`ASR_COMPUTE_TYPE=int8`；首次转录可能下载模型。
- Windows CUDA 需要与 faster-whisper/CTranslate2 匹配的驱动和运行库；仅安装 Python 包不能保证 CUDA 可用。
- llama-server 即使空闲也可能占显存。放不下时降低档位，或手动停止项目专用 LLM 服务、完成转录后再启动。
- 音频处理采用子进程超时，转录结束后子进程退出。强制终止整个宿主进程时，仍应检查遗留 worker 后再启动重任务。

```bash
uv run --no-sync python scripts/check.py
```

检查命令不发送模型请求、不打印密钥。当前 FFmpeg 或模型未安装时，文字样例仍可使用。

## 验证与评测

```bash
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync python scripts/evaluate.py --dry-run
```

测试覆盖快照一致性、编辑冲突、非法引用、一次纠错、服务失败保留数据、处理锁、Agent 调用预算、重复与非法工具、过期行动项、未返回证据、请求适配和 UI。

`tests/` 中的 scripted client 与 HTTP MockTransport 只检验程序逻辑，不替代真实模型验收。真实评测步骤与 20 题开发集见 [eval/README.md](eval/README.md)。模型报告默认保存在忽略目录 `data/eval/`。

## 数据与代码

```text
app.py                    Streamlit 页面
meeting_assistant/
  config.py               平台与 provider 配置
  storage.py              SQLite、快照和处理锁
  llm.py                  通用聊天接口 / llama.cpp 适配
  services.py             纪要生成与校验
  agent.py                受限工具执行循环
  audio.py / asr_worker.py 音频预处理与 ASR
  export.py / prompts.py  导出与提示版本
samples/                  明确标注的虚构开发文字
scripts/                  启动、环境检查与真实评测
```

默认 `data/app.db` 自动创建，无需安装数据库服务。原始音频与转换文件保存在 `data/meetings/`。运行记录和问答可能包含会议文字，随本地数据保存；不自动上传。

备份时停止应用后复制整个 `data/`。模型与 `.env` 单独管理，不进入 Git。数据库当前 schema 为 v1，不包含跨版本迁移工具。

## 参考与规划

- [当前两周规划](简化版AI会议助理方案规划-Windows.md)
- [原六周方案备份](简化版AI会议助理方案规划-Windows-原六周版备份.md)
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [llama.cpp 工具调用](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)

项目仍需真实 API 接入、独立会议样例评测与 Windows 完整验收，之后再填写简历上的模型效果指标。
