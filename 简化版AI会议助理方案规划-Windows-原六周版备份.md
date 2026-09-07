# 简化版多模态 AI 会议助理——Windows＋RTX 2080 Ti 22GB 部署规划

> 文档用途：课程作业项目规划与单机部署依据  
> 目标平台：Windows 11 64 位；兼容 Windows 10 22H2，但不建议作为 2026 年的新部署系统  
> GPU：NVIDIA GeForce RTX 2080 Ti，系统实际识别 22GB 显存  
> 项目形态：单用户、本地运行、浏览器访问的 Web 应用  
> 处理方式：以会后批处理为主，GPU 加速转录、说话人分离和本地文本分析  
> 文档状态：Windows/NVIDIA 版本规划，独立于原 macOS 规划文件  
> 技术资料核对日期：2026-08-24；正式实施时以锁文件与官方文档复核结果为准

---

## 一、规划结论

### 1.1 一句话目标

在一台 Windows 10/11、RTX 2080 Ti 22GB 显存电脑上，完成“导入或录制会议音频—GPU 转录—说话人区分—人工校正—生成摘要、决定和行动项—引用原文—导出纪要”的本地闭环。

### 1.2 推荐技术基线

| 层级 | 推荐方案 | 主要理由 |
|---|---|---|
| 操作系统 | Windows 11 64 位 | 当前受支持，驱动、浏览器和本地 AI 工具体验更稳 |
| 运行方式 | 原生 Windows＋PowerShell | 麦克风、文件选择、CUDA 和课堂演示链路最短 |
| Python | Python 3.11 64 位 | AI 包兼容性成熟，避免追新版本造成依赖缺口 |
| 依赖管理 | `uv`＋锁文件 | 安装快、可复现，保留 `requirements.lock` 作为备份 |
| 页面 | Streamlit | 单人课程项目开发量小，适合本地交互 |
| 音频 | FFmpeg＋ffprobe | 统一格式、探测媒体、生成可播放文件 |
| 语音识别 | faster-whisper＋`large-v3`＋FP16 | 适合 NVIDIA CUDA，中文质量和速度平衡较好 |
| 说话人分离 | pyannote `speaker-diarization-community-1` | 本地 GPU 推理，支持匿名说话人时间区间 |
| 本地大模型 | Ollama＋`qwen3:14b-q4_K_M` | 约 9.3GB 权重，中文结构化分析能力与显存余量平衡 |
| LLM 备用 | `qwen3:8b` 量化版或兼容 API | 显存、驱动或质量不稳定时可快速降级 |
| 数据 | SQLite＋本地目录 | 无需独立数据库服务，适合单用户持久化 |
| 导出 | Markdown；P1 增加 DOCX/PDF | P0 易实现、易检查、易提交 |

### 1.3 与原 macOS 方案的关键差异

| 原 macOS/M1 方案 | 本 Windows/NVIDIA 方案 |
|---|---|
| MLX Whisper | faster-whisper/CTranslate2/CUDA |
| `base` 或 `small` 模型 | 默认 `large-v3`，失败时降级 `medium` |
| 8GB 统一内存，严格小模型 | 22GB 独立显存，可使用 14B Q4 LLM |
| pyannote 因内存风险列为较后 P1 | 仍为可降级模块，但应作为首要加分项验证 |
| 所有任务受统一内存限制 | GPU 显存与系统内存分别监控 |
| macOS 路径、权限和启动方式 | Windows 路径、DLL、PowerShell 和 Defender 规则 |
| Apple Silicon 专用依赖 | NVIDIA 驱动、CUDA 运行库、cuDNN 与 PyTorch wheel |

### 1.4 关键工程原则

1. 以 Windows 11 原生部署为主线，不同时维护 WSL2、Docker 和原生 Windows 三套环境。
2. 以 `nvidia-smi` 实际结果为准，不仅凭“22GB”硬件描述选择模型。
3. ASR、说话人分离、LLM 默认串行执行；完成一个阶段后释放模型或结束工作进程。
4. 自动结果始终允许人工修订，模型失败不应破坏音频、转录或历史数据。
5. 首版坚持会后处理，不把实时字幕作为 P0 发布条件。
6. 模型名、驱动、Python 包和提示词版本全部落库或写入运行记录。
7. 所有性能数字先作为候选门槛，必须用目标电脑实测后确认。

---

## 二、硬件与操作系统基线

### 2.1 已知硬件

- GPU：RTX 2080 Ti，Turing 架构，Compute Capability 7.5。
- 显存：用户给定为 22GB；标准 RTX 2080 Ti 通常为 11GB，因此本规划将其视为改装或扩容卡。
- 操作系统候选：Windows 10 或 Windows 11，均要求 64 位。

### 2.2 尚未给出的硬件与建议值

| 项目 | 最低可试 | 推荐配置 | 说明 |
|---|---:|---:|---|
| 系统内存 | 16GB | 32GB 或更多 | 音频缓存、Python、浏览器和模型加载都需要内存 |
| CPU | 4 核 8 线程 | 6 核 12 线程或更高 | 解码、文本预处理、SQLite 和模型前后处理依赖 CPU |
| SSD 可用空间 | 50GB | 100GB 或更多 | Python 环境、模型、缓存、音频和导出文件 |
| 系统盘空间 | 20GB 余量 | 40GB 余量 | Windows 更新、临时文件和模型下载解压 |
| 麦克风 | 任意可用输入 | USB 麦克风或独立会议麦 | 录音质量直接影响转录质量 |

如果只有 16GB 系统内存，仍可完成短会议演示，但必须关闭大型 IDE、浏览器多余标签、游戏和其他模型服务，并限制长音频并行预处理。

### 2.3 Windows 版本决策

#### 主部署：Windows 11

推荐 Windows 11 的理由：

- 截至本规划日期仍处于微软支持周期内。
- NVIDIA 新驱动、本地 AI 工具和浏览器长期兼容性更好。
- 新部署无需承担 Windows 10 普通版本停止安全支持的风险。

#### 兼容部署：Windows 10

仅在现有机器无法升级或课程环境固定时使用 Windows 10 22H2。微软已于 2025-10-14 结束普通 Windows 10 支持；继续联网使用时应具有适用的 ESU/LTSC 支持方案，并在报告中说明风险。

#### 不采用的主路线

- WSL2：可作为后续开发实验，但 P0 不依赖它；否则麦克风、路径、端口和 GPU 环境会多一层排错。
- Docker Desktop：不作为首版依赖；单用户项目无需为容器、卷、GPU passthrough 和内存配额增加复杂度。
- 双系统并行维护：不为同一课程作业同时维护 Windows 和 Linux 两套锁文件。

### 2.4 22GB 改装显卡部署 Gate

在下载大模型前必须完成以下检查：

1. `nvidia-smi` 正确识别 RTX 2080 Ti、约 22GB 总显存、驱动版本和温度。
2. PyTorch 可返回 `torch.cuda.is_available() == True`，设备名与预期一致。
3. 连续进行至少 30 分钟 GPU 压力或模型循环测试，无花屏、驱动重置、CUDA 错误和系统重启。
4. 显存分配与释放正常，连续三轮 ASR/LLM 测试后没有持续累积。
5. 满载温度、风扇、供电和功耗稳定；不在答辩前超频或临时改 BIOS。
6. 若 22GB 仅能被部分程序识别，模型选择退回标准 11GB 档位，不尝试绕过检查。

Gate 未通过时的安全配置：`faster-whisper medium`＋FP16/INT8、Qwen3 8B Q4、关闭自动说话人分离。

### 2.5 驱动与 CUDA 原则

- RTX 2080 Ti 的 Compute Capability 为 7.5，满足 Ollama 当前 NVIDIA GPU 最低要求。
- 安装稳定的 NVIDIA 驱动，驱动版本同时满足所选 PyTorch wheel、CTranslate2 和 Ollama。
- faster-whisper 当前主线的 GPU 运行依赖 CUDA 12 的 cuBLAS 与 cuDNN 9；实际版本必须在 Spike 后锁定。
- PyTorch 使用官方提供的 Windows CUDA wheel，不自行编译。
- 不因系统安装了某个 CUDA Toolkit 就假定 Python 包一定兼容；分别运行 PyTorch和 CTranslate2 的真实推理验证。
- 不盲目升级到最新 CUDA 大版本。RTX 2080 Ti 属于较早的 Turing 卡，应以目标包明确支持的组合为准。

---

## 三、项目定位、范围与优先级

### 3.1 项目定位

本项目是面向个人用户的本地多模态 AI 会议助理。“多模态”首版主要指音频与文本的联合处理：音频提供原始内容和时间位置，文本提供可编辑转录、摘要、决定、行动项和引用。

系统不追求企业级并发、在线会议接入或多用户协作。RTX 2080 Ti 的价值用于提高单机模型质量和处理速度，而不是扩大产品边界。

### 3.2 P0 必须完成

1. 新建、打开、删除本地会议。
2. 导入 MP3、WAV、M4A、MP4 等常见媒体。
3. FFmpeg 预处理为 16kHz、单声道 PCM WAV。
4. 使用 GPU 完成普通话转录并输出分段时间戳。
5. 显示并编辑每段文字和说话人名称。
6. 使用本地 LLM 生成摘要、关键决定、行动项和未解决问题。
7. 决定和行动项引用有效转录片段，点击引用可定位原文。
8. SQLite 与本地文件持久化，应用重启后可以继续查看。
9. 导出 Markdown 会议纪要。
10. 任务状态、错误说明、取消、重试和降级。
11. 环境诊断显示驱动、GPU、显存、FFmpeg、ASR、Ollama、模型与数据目录状态。

### 3.3 P1 优先加分项

1. pyannote 自动说话人分离，并允许输入预计说话人数范围。
2. 浏览器麦克风录音与录音设备选择。
3. 词级时间戳和音频片段循环播放。
4. DOCX/PDF 导出。
5. 批量导入多个会议。
6. 会议图片或文档 OCR 后与转录联合总结。
7. CUDA/CPU 模式切换和模型档位选择。
8. 简单全文搜索。

### 3.4 P2 后续展望

- 实时或准实时字幕。
- 屏幕共享、视频画面和幻灯片内容理解。
- 跨会议本地知识库与语义检索。
- 多用户、账号、权限、协作编辑。
- Zoom、腾讯会议、Teams 等平台接入。
- 声纹实名、情绪分析、绩效评价。
- 移动端、云端同步和远程访问。

### 3.5 明确不做

- 不将模型输出视为无需复核的正式会议决议。
- 不开放公网端口。
- 不实现医疗、司法、政务等高风险用途的合规承诺。
- 不将 30B Q4 模型作为 P0 默认。其约 19GB 权重会让 22GB 显存几乎没有 KV 缓存和运行余量。
- 不在 P0 同时加载 ASR、pyannote 和 LLM。
- 不为追求“多模态”名称而强行加入低价值的图片上传页面。

### 3.6 首版使用约束

- 推荐会议：2～6 人、普通话为主。
- 演示音频：3～5 分钟。
- 常规测试：不超过 60 分钟。
- 60 分钟以上输入：提示分段处理，不承诺一次完成。
- 重叠说话、强方言、远场混响和背景音乐会降低准确率。
- 首次安装和下载模型需要网络；缓存完成后 P0 主流程应可断网运行。

---

## 四、核心用户流程

### 4.1 主流程

```text
启动环境检查
    ↓
新建会议并导入媒体/录音
    ↓
ffprobe 探测＋FFmpeg 统一音频
    ↓
faster-whisper GPU 转录
    ↓
pyannote 说话人分离（启用时）
    ↓
时间区间对齐并生成匿名说话人标签
    ↓
用户校正文字、说话人和片段
    ↓
Ollama/Qwen3 生成结构化会议成果
    ↓
程序验证引用和字段
    ↓
用户复核并导出 Markdown
```

### 4.2 导入与预处理

1. 用户选择本地媒体并填写会议标题。
2. 程序复制原文件到会议目录，使用 UUID 作为内部文件名。
3. ffprobe 获取时长、编码、采样率、声道和音频流信息。
4. 文件无音频流、损坏或超过硬限制时停止处理并说明原因。
5. FFmpeg 输出 `processed.wav` 临时文件；校验成功后再原子改名。
6. 原文件永不被转码覆盖。

建议参数：

```text
-vn -ac 1 -ar 16000 -c:a pcm_s16le
```

参数由程序以数组传入子进程，不拼接未转义的用户文件名。

### 4.3 GPU 转录

1. 加载 `large-v3`，`device="cuda"`，默认 `compute_type="float16"`。
2. 固定中文场景可传 `language="zh"`；混合语言场景允许自动检测。
3. 开启 VAD 过滤长静音，保留原始时间轴。
4. 输出段级时间戳；词级时间戳作为 P1。
5. 每批片段在事务中写入，失败时不把半成品标记为成功。
6. 完成后释放模型；如显存未释放，则结束独立 ASR worker。

降级顺序：

```text
large-v3 FP16
  → large-v3 INT8_FLOAT16
  → medium FP16
  → medium INT8_FLOAT16
  → CPU INT8（仅应急，不作为现场主流程）
```

### 4.4 说话人分离

1. 使用 `pyannote/speaker-diarization-community-1`。
2. 首次使用前在 Hugging Face 接受模型条件并准备访问令牌。
3. 模型缓存成功后支持离线运行；令牌只用于下载，不写入数据库或日志。
4. 可选输入最少/最多说话人数，减少聚类偏差。
5. 将每个 ASR 片段与说话人区间按重叠时长对齐。
6. 无明显重叠时使用最近有效说话人或“未知说话人”，不伪造身份。
7. 输出仅为 `SPEAKER_00` 等匿名标签，真实姓名由用户手工设置。
8. 模型失败时保留完整转录并进入人工标注模式。

### 4.5 人工校正

- 每段同时保存 `model_text` 与 `edited_text`。
- 当前有效文字为 `edited_text ?? model_text`。
- 说话人改名与片段文字编辑立即显示“未保存”状态。
- 保存后递增修订号，并把已有 AI 分析标记为“基于旧转录”。
- 用户可恢复单段模型原稿，但不能无提示覆盖全部修改。

### 4.6 AI 分析与引用

输入给 LLM 的每个片段包含稳定编号、时间戳、说话人和有效文字，例如：

```text
[S0001][00:00:02-00:00:08][张同学] 本周先完成上传和转录。
[S0002][00:00:09-00:00:15][李同学] 我负责界面，周五前提交。
```

输出使用固定 JSON Schema：

```json
{
  "summary": "...",
  "decisions": [
    {"content": "...", "evidence_segment_ids": ["S0001"]}
  ],
  "action_items": [
    {
      "task": "...",
      "owner": "李同学",
      "deadline": "周五",
      "evidence_segment_ids": ["S0002"]
    }
  ],
  "open_questions": ["..."]
}
```

程序必须验证：

- JSON 可以解析并符合 Schema。
- 引用编号存在且属于当前会议、当前转录修订。
- 负责人和截止时间没有证据时返回 `null` 或“未明确”。
- 单项内容为空时丢弃该项，不保存伪造占位内容。
- 无效引用触发一次纠错重试；仍失败则拒绝该次结果。

### 4.7 导出

Markdown 至少包含：

1. 标题、日期、时长和参与者。
2. 模型与生成时间。
3. 会议摘要。
4. 关键决定及时间戳引用。
5. 行动项表格。
6. 未解决问题。
7. 完整转录。
8. “AI 结果需人工复核”的提示。

---

## 五、页面与交互设计

### 5.1 页面结构

```text
会议列表
├─ 新建会议
├─ 最近会议
└─ 设置与诊断

会议工作区
├─ 概览与处理进度
├─ 转录与校正
├─ AI 会议成果
├─ 音频与文件
└─ 导出
```

### 5.2 会议列表页

- 显示标题、创建时间、时长、处理状态和最近修改时间。
- 支持按标题筛选和按日期排序。
- 删除需要二次确认；先移动到回收区，P0 可提供“永久删除”单独操作。
- 异常中断的会议显示“可恢复/可重试”，而不是永远显示“处理中”。

### 5.3 新建会议页

- 标题可为空，系统按日期时间生成默认值。
- 上传后先显示文件名、大小、格式和探测结果，不立即加载模型。
- 开始处理前显示预计步骤与当前模型。
- 麦克风录音被拒绝时提示 Windows 隐私设置和浏览器站点权限，并允许改用文件。

### 5.4 进度页

处理阶段使用真实阶段而非伪造百分比：

```text
排队 → 媒体探测 → 音频转换 → 加载 ASR → 转录
→ 说话人分离（可选）→ 对齐 → 等待校正 → AI 分析 → 完成
```

每个阶段显示：

- 开始时间和已用时间。
- 使用设备：CUDA/CPU。
- 当前模型与计算精度。
- GPU 已用/总显存（可取得时）。
- 取消按钮和失败后的重试入口。

### 5.5 转录与校正页

- 左侧音频播放器，右侧可编辑片段列表。
- 点击片段从开始时间播放。
- 显示时间范围、说话人下拉框、文字输入框和修订状态。
- 支持批量把匿名标签改名。
- 保存成功后更新转录修订号。
- 长会议采用分页或虚拟化显示，避免 Streamlit 一次渲染数千输入框。

### 5.6 AI 成果页

- 顶部显示分析模型、分析版本、输入转录修订和生成时间。
- 旧结果不自动删除，明确标注“转录已修改，建议重新生成”。
- 每条决定和行动项旁显示引用按钮。
- 点击引用切换到转录页并定位片段。
- 生成期间禁止重复提交同一任务。

### 5.7 设置与诊断页

| 检查项 | 正常状态 | 异常提示 |
|---|---|---|
| Windows | 版本、构建号、64 位 | Windows 10 停止普通支持提示 |
| NVIDIA | 型号、驱动、温度可读 | 未识别 GPU 或驱动异常 |
| 显存 | 约 22GB 且测试通过 | 仅识别约 11GB 时自动建议降档 |
| PyTorch | CUDA 可用、设备正确 | wheel/驱动不匹配 |
| CTranslate2 | 能完成 10 秒真实转录 | cuBLAS/cuDNN DLL 缺失 |
| FFmpeg | ffmpeg/ffprobe 可执行 | PATH 或安装说明 |
| Ollama | `localhost:11434` 可访问 | 服务未启动或端口被占 |
| Qwen3 | 模型存在且能生成 JSON | 下载命令和磁盘提示 |
| pyannote | 包、缓存、令牌状态 | 作为可选项降级 |
| 数据目录 | 可读写且空间充足 | 权限、路径过长或磁盘不足 |

诊断页默认只读；不自动升级驱动、不清理整个模型缓存、不下载大型模型。

---

## 六、系统架构

### 6.1 总体架构

```text
浏览器
  │ localhost
  ▼
Streamlit UI
  │
  ├─ MeetingService ── SQLite Repository
  ├─ AudioService ──── ffprobe / FFmpeg
  ├─ TranscriptService ─ ASR Worker / faster-whisper / CUDA
  ├─ DiarizationService ─ pyannote Worker / PyTorch / CUDA
  ├─ AnalysisService ── Ollama HTTP / Qwen3 / CUDA
  ├─ ExportService ──── Markdown（P1: DOCX/PDF）
  └─ DiagnosticService ─ GPU、进程、路径、磁盘检测

本地文件目录
  ├─ 原始媒体
  ├─ processed.wav
  ├─ 导出文件
  ├─ 日志
  └─ 临时文件
```

### 6.2 进程模型

首版采用“一个 UI 主进程＋可结束的重任务 worker＋独立 Ollama 服务”：

- UI 主进程只负责页面、轻量业务逻辑和任务调度。
- FFmpeg 使用子进程。
- ASR 建议使用独立 worker 进程；任务结束后退出，确保释放 CTranslate2 显存。
- pyannote 使用独立 worker 进程，避免与 ASR 的 CUDA/DLL 生命周期互相影响。
- Ollama 是独立本地服务，通过 `http://127.0.0.1:11434` 调用。
- 同一时刻最多一个 GPU 重任务获得锁。

若课程时间有限，可先把 ASR 放在主进程完成 Spike；但进入稳定版前应验证多次运行是否能释放显存。

### 6.3 模块边界

```text
src/
├─ app.py
├─ pages/
├─ domain/
│  ├─ models.py
│  ├─ enums.py
│  └─ schemas.py
├─ services/
│  ├─ meeting_service.py
│  ├─ audio_service.py
│  ├─ transcript_service.py
│  ├─ diarization_service.py
│  ├─ analysis_service.py
│  └─ export_service.py
├─ adapters/
│  ├─ ffmpeg_adapter.py
│  ├─ faster_whisper_adapter.py
│  ├─ pyannote_adapter.py
│  └─ ollama_adapter.py
├─ repositories/
│  ├─ database.py
│  └─ meeting_repository.py
├─ workers/
│  ├─ asr_worker.py
│  └─ diarization_worker.py
└─ diagnostics/
   ├─ gpu.py
   ├─ environment.py
   └─ storage.py
```

页面不得直接执行 SQL、拼 FFmpeg 命令或调用模型。外部工具全部通过 adapter，便于测试和替换。

### 6.4 任务状态机

```text
PENDING
  → RUNNING
  → SUCCEEDED
  → FAILED
  → CANCELLED
  → INTERRUPTED
```

- 应用启动时把遗留的 `RUNNING` 标记为 `INTERRUPTED`。
- 重试创建新 `task_run`，不覆盖旧日志。
- 相同会议、阶段、模型、输入修订已有成功结果时提示复用。
- 取消只终止明确的 worker/FFmpeg 子进程，不结束 Ollama 或其他用户程序。

### 6.5 本地端口

- Streamlit：默认 `127.0.0.1:8501`。
- Ollama：默认 `127.0.0.1:11434`。
- 只绑定回环地址；P0 不添加 Windows 防火墙公网入站规则。
- 端口被占时诊断页显示占用 PID，不自动终止未知进程。

---

## 七、模型与资源配置

### 7.1 ASR 默认配置

```yaml
asr:
  provider: faster-whisper
  model: large-v3
  device: cuda
  compute_type: float16
  language: zh
  beam_size: 5
  vad_filter: true
  word_timestamps: false
```

选择 `large-v3` 的理由：

- 22GB 显存足够容纳模型并保留合理工作区。
- 中文与中英混合会议通常比 `base/small` 更有质量余量。
- 课程项目更值得展示“GPU 提升质量与速度”，而不是沿用 M1 低配模型。

不把批量大小写死为高值。先用 `batch_size=4` 或不启用批量管线实测，再逐步提高；长音频吞吐提升不能以稳定性为代价。

### 7.2 LLM 默认配置

```yaml
llm:
  provider: ollama
  base_url: http://127.0.0.1:11434
  model: qwen3:14b-q4_K_M
  temperature: 0.1
  stream: false
  context_tokens: 16384
  timeout_seconds: 300
```

说明：

- 官方 Ollama 模型库中 `qwen3:14b-q4_K_M` 权重约 9.3GB，适合作为 22GB 显存的主档。
- 不默认使用 40K 最大上下文；上下文越长，KV 缓存越大。首版限制 16K，并对长会议分层总结。
- 若目标电脑的 22GB 显存不稳定，切到 Qwen3 8B Q4。
- 30B A3B Q4 权重约 19GB，虽可能勉强装入 22GB，但运行余量太小，不作为 P0。
- 结构化输出优先使用 Ollama 支持的 JSON Schema/格式约束；仍需在应用层用 Pydantic 校验。

### 7.3 长会议分层总结

当转录超过上下文预算时：

1. 按时间和语义边界切成块，每块保留片段 ID。
2. 每块提取候选摘要、决定、行动项和引用。
3. 合并候选项并去重，但不丢失原始引用。
4. 最终模型只整理候选，不允许产生候选中不存在的新事实。
5. 最后再次运行引用存在性检查。

### 7.4 显存预算候选

以下仅用于规划，必须以 `nvidia-smi` 实测峰值替换：

| 阶段 | 候选显存范围 | 是否与其他重模型并行 |
|---|---:|---|
| faster-whisper large-v3 FP16 | 约 5～8GB | 否 |
| pyannote diarization | 约 2～6GB | 否 |
| Qwen3 14B Q4＋上下文 | 约 11～17GB | 否 |
| UI、SQLite、FFmpeg | 主要使用 CPU/内存 | 可与单个 GPU 阶段共存 |

显存保护策略：

- 开始任务前保留至少 2GB 安全余量。
- 可用显存不足时拒绝启动并提示关闭其他 GPU 程序。
- 不通过 Windows 共享 GPU 内存“凑够”模型容量；进入共享内存通常会显著降速。
- 每阶段记录开始、峰值、结束显存和耗时。
- 任务后若显存未回落，结束对应 worker，而不是继续叠加任务。

### 7.5 GPU 锁

使用文件锁或数据库锁实现单 GPU 排他调度：

```text
ASR 请求 ─┐
Diar 请求 ├─ GPU Task Lock ─→ 一次只运行一个
LLM 请求 ─┘
```

Ollama 可能在后台保留模型。进入 ASR 或 pyannote 前，应通过受控配置缩短模型保留时间，或在 Spike 中确认其空闲占用不会影响任务。不要粗暴结束所有 `ollama` 进程。

### 7.6 CPU 降级

- FFmpeg、SQLite、导出始终可在 CPU 运行。
- ASR CPU INT8 只用于环境诊断和短音频应急。
- pyannote CPU 模式可能很慢，不作为现场演示路径。
- LLM GPU 不可用时优先切换兼容 API或已保存结果，而不是现场等待 CPU 运行 14B。

---

## 八、Windows 环境与部署方案

### 8.1 推荐目录

避免中文、空格、OneDrive 同步目录和过深路径：

```text
D:\meeting-assistant\        项目源码
D:\meeting-data\             会议数据
D:\meeting-models\ollama\   Ollama 模型
D:\meeting-cache\            Hugging Face/ASR 缓存
```

程序内部使用 `pathlib.Path`，数据库保存相对于数据根目录的路径。导出时不泄露绝对路径。

### 8.2 安装顺序

1. 安装 Windows 11 更新并重启。
2. 安装稳定的 NVIDIA 驱动并运行 `nvidia-smi`。
3. 完成 22GB 显存稳定性 Gate。
4. 安装 Git、Python 3.11 64 位和 `uv`。
5. 创建项目虚拟环境并安装锁定依赖。
6. 按 faster-whisper 要求准备兼容的 CUDA 12 cuBLAS/cuDNN 9 运行库。
7. 安装 FFmpeg，并确认 `ffmpeg` 与 `ffprobe` 在 PATH。
8. 安装 Ollama Windows 原生版，修改模型目录（如需要）。
9. 拉取并验证 Qwen3 14B Q4。
10. 接受 pyannote 模型条件、下载缓存并验证 GPU 推理（P1）。
11. 运行全部 Spike，再启动页面开发。

### 8.3 环境变量

推荐使用用户级变量：

```text
MEETING_DATA_DIR=D:\meeting-data
MEETING_CACHE_DIR=D:\meeting-cache
OLLAMA_MODELS=D:\meeting-models\ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
HF_HOME=D:\meeting-cache\huggingface
```

Hugging Face 令牌放在 `.env` 或系统凭据中，`.env` 必须进入 `.gitignore`。日志只显示“已配置/未配置”，不显示令牌值。

### 8.4 PowerShell 验证清单

```powershell
nvidia-smi
python --version
uv --version
ffmpeg -version
ffprobe -version
ollama --version
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

PyTorch 验证：

```powershell
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0)); print(torch.cuda.get_device_properties(0).total_memory)"
```

不能只验证导入成功，必须再跑：

- 10 秒真实中文音频的 faster-whisper GPU 转录。
- 30～60 秒双人音频的 pyannote GPU 分离。
- Ollama 一次固定 Schema JSON 生成。

### 8.5 启动脚本

提供以下脚本：

```text
scripts\setup.ps1       创建/同步环境，不下载未确认的大模型
scripts\check.ps1       只读环境诊断
scripts\prepare.ps1     明确下载模型并校验
scripts\start.ps1       检查 Ollama 后启动 Streamlit
scripts\test-fast.ps1   快速自动测试
scripts\test-models.ps1 真实模型慢测试
```

脚本要求：

- 使用 `$PSScriptRoot` 定位项目，不依赖当前终端目录。
- 失败时返回非零退出码。
- 路径参数加引号，不拼接不可信用户输入。
- 不要求管理员权限，除非安装系统级驱动/运行库。
- 不静默修改防火墙、执行策略或系统 PATH。

### 8.6 Windows 特有事项

- Windows Defender 可能扫描大模型与大量小缓存文件；先实测，不默认排除整个磁盘。
- OneDrive 可能锁定 SQLite 或临时文件；数据目录不要放在同步文件夹。
- 麦克风需要同时允许“系统隐私权限”和“浏览器站点权限”。
- 电源计划避免 GPU 任务中休眠；笔记本必须接电，台式机确保电源和散热。
- 长路径支持即使已开启，也应保持内部目录简短。
- 终端统一 UTF-8，导出 Markdown 使用 UTF-8 无 BOM或项目统一约定。
- PowerShell、CMD 和 Git Bash 的激活命令不同，README 只以 PowerShell 为主。

### 8.7 依赖隔离策略

首选一个锁定的 Python 3.11 环境。若 CTranslate2 的 CUDA DLL 与 PyTorch/pyannote 组合冲突，则拆为：

```text
.venv-app    Streamlit、业务、SQLite、faster-whisper
.venv-diar   PyTorch、pyannote.audio、diarization worker
Ollama       独立原生服务
```

主程序通过 JSON 文件或本机受控子进程参数与 diarization worker 通信。拆环境是故障隔离手段，不是首日默认复杂化。

---

## 九、数据与文件设计

### 9.1 本地目录结构

```text
D:\meeting-data\
├─ app.db
├─ meetings\
│  └─ {meeting_uuid}\
│     ├─ source\original.ext
│     ├─ audio\processed.wav
│     ├─ exports\minutes.md
│     └─ temp\
├─ logs\app.log
├─ backups\
└─ trash\
```

临时目录与正式目录在同一磁盘，便于原子移动。程序启动时只清理超过保留期且确认没有活动任务的临时文件。

### 9.2 核心表

| 表 | 主要职责 |
|---|---|
| `schema_version` | 数据库迁移版本 |
| `meeting` | 标题、状态、时间、时长、修订号 |
| `audio_asset` | 原始和处理后音频元数据、相对路径、摘要 |
| `speaker` | 匿名标签、显示名称、颜色 |
| `transcript_segment` | 时间、模型原文、修订文字、说话人 |
| `task_run` | 阶段、状态、耗时、错误、硬件快照 |
| `analysis_run` | 模型、参数、提示版本、输入修订、状态 |
| `meeting_analysis` | 摘要和未解决问题 |
| `decision` | 决定内容 |
| `action_item` | 任务、负责人、截止时间、状态 |
| `evidence` | 成果与片段的引用关系 |

### 9.3 必备运行记录

每个模型任务记录：

- Python 包版本。
- 模型完整标识或摘要。
- GPU 型号、驱动、可见总显存。
- CUDA/PyTorch/CTranslate2 运行信息。
- 计算精度、批量大小、语言和 VAD 参数。
- 开始、结束、耗时、输入音频时长。
- 峰值显存（能够可靠取得时）。
- 错误类型与用户可读信息。

不得记录：访问令牌、API 密钥、完整环境变量、用户绝对路径和不必要的会议全文。

### 9.4 一致性规则

1. 先复制/写临时文件并校验，再提交数据库路径。
2. 转录整批成功后才更新会议状态。
3. 人工修改递增 `transcript_revision`。
4. AI 分析绑定输入修订号，旧结果可查看但标为过期。
5. 删除会议先移入 `trash`，数据库标记删除；恢复需同时恢复文件与记录。
6. SQLite 开启外键、合理 busy timeout 和 WAL；所有写操作使用短事务。
7. 不把大音频、模型权重或日志 BLOB 存进 SQLite。

### 9.5 备份

- 备份范围：`app.db`、`meetings`、必要配置说明，不包括可重新下载的模型。
- 使用 SQLite 在线备份 API或先停止应用再复制数据库。
- 每个里程碑保存一份可恢复备份。
- 答辩前保留一份离线备份和一份已处理主演示会议。

---

## 十、开发实施计划

### 10.1 M0：硬件与环境确认（1～2 天）

任务：

- 确认 Windows 版本、CPU、系统内存、SSD 空间。
- 验证 RTX 2080 Ti 22GB 稳定性。
- 固定驱动版本。
- 准备 10 秒、1 分钟、3 分钟和 30 分钟测试音频。
- 确认课程允许提前缓存模型和使用本地服务。

Gate：显卡、驱动、磁盘与测试数据明确；未通过不进入大模型安装。

### 10.2 M1：六个技术 Spike（第 1 周）

#### Spike A：FFmpeg

- 探测 WAV/MP3/M4A/MP4。
- 转为 16kHz 单声道 PCM。
- 覆盖中文路径、空格路径、损坏文件和无音频流视频。

#### Spike B：faster-whisper

- `large-v3` FP16 转录 1 分钟与 3 分钟中文音频。
- 记录 RTF、加载时间、峰值显存和 CER。
- 验证任务后显存释放。
- 再测一次 `medium` 降级档。

#### Spike C：pyannote

- 接受模型条件并缓存 community-1。
- 处理双人样例并记录 DER 候选值和峰值显存。
- 验证无令牌但已有缓存时能否离线运行。

#### Spike D：Ollama/Qwen3

- 拉取 `qwen3:14b-q4_K_M`。
- 固定 3 组会议文本测试 Schema、引用和中文质量。
- 测试 8B 降级档。
- 记录 4K、8K、16K 上下文的显存和耗时。

#### Spike E：SQLite 与文件

- 建表、事务、迁移、相对路径、恢复和删除。
- 验证 OneDrive 外普通 NTFS 目录。

#### Spike F：命令行端到端

- 串联 FFmpeg、ASR、可选 diarization、LLM 和 Markdown。
- 不做页面，先证明真实链路成立。
- 连续执行三次，检查显存和数据一致性。

Gate M1：主样例三次成功；失败项有明确降级，不允许以假数据替代真实模型结论。

### 10.3 M2：骨架、数据库和基础页面（第 2 周）

- 建立模块结构与配置加载。
- 完成数据库迁移和 Repository。
- 完成会议列表、新建、设置与诊断页。
- 使用假 adapter 测试状态机。
- 实现 GPU 排他锁和中断恢复。

Gate M2：页面可管理会议，刷新/重启不丢状态，未安装模型时也能进入诊断页。

### 10.4 M3：音频与 ASR 闭环（第 3 周）

- 完成安全文件导入、ffprobe、FFmpeg 和校验。
- 完成 ASR worker、超时、取消、重试和批量写入。
- 展示音频播放器、片段和时间戳。
- 运行真实模型慢测试。

Gate M3：3～5 分钟样例可稳定导入和 GPU 转录，关闭/重开应用后直接查看结果。

### 10.5 M4：校正、说话人和导出（第 4 周）

- 完成文字编辑、说话人命名和恢复原稿。
- 接入 pyannote 并实现时间对齐；若阻塞则保持 P1 插件状态。
- 完成 Markdown 导出和引用跳转基础。
- 完成回收区与备份。

Gate M4：没有自动说话人分离时，人工标注仍能完成全流程。

### 10.6 M5：AI 分析与可信引用（第 5 周）

- 定义 Pydantic Schema 与提示版本。
- 接入 Ollama，限制上下文和温度。
- 完成 JSON 校验、引用验证、重试和旧版本标记。
- 实现长会议分层分析。
- 完成成果页和导出升级。

Gate M5：固定测试集能生成有效 Schema；不存在的片段 ID 不会写入数据库。

### 10.7 M6：测试、优化与交付（第 6 周）

- 完整功能回归和异常恢复。
- 三次连续端到端演示。
- 一次断网演示。
- 一次 GPU 不可用/显存不足降级演示。
- 更新性能数据、风险、README 和模型许可清单。
- 准备已处理会议、截图和录屏。

Gate M6：P0 无发布阻断缺陷，目标电脑可按 README 从已准备环境启动。

### 10.8 四周压缩方案

| 周 | 交付重点 |
|---|---|
| 第 1 周 | M0＋全部 Spike＋命令行闭环 |
| 第 2 周 | 数据库、页面、上传、FFmpeg、ASR |
| 第 3 周 | 校正、AI、引用、Markdown；pyannote 有余力再做 |
| 第 4 周 | 测试、修复、文档、演示，不新增范围 |

压缩时首先移除 DOCX/PDF、录音、批量导入和 OCR；不得移除数据持久化、人工校正、引用验证和错误降级。

---

## 十一、测试与验收

### 11.1 测试分层

1. 单元测试：时间、路径、Schema、引用、状态机。
2. Repository 测试：SQLite 事务、迁移、修订和恢复。
3. 假 adapter 集成测试：不加载模型的完整业务流。
4. 真实工具测试：FFmpeg、faster-whisper、pyannote、Ollama。
5. 端到端测试：浏览器完成完整会议闭环。

### 11.2 固定测试音频

| 编号 | 内容 | 用途 |
|---|---|---|
| A01 | 10 秒清晰单人普通话 | 快速真实 ASR |
| A02 | 3 分钟、2 人、明确行动项 | 主演示与引用 |
| A03 | 5 分钟、4 人、少量重叠 | 说话人测试 |
| A04 | 30 分钟会议 | 性能与稳定 |
| A05 | 中英混合、数字、日期、专有名词 | ASR 质量 |
| A06 | 静音/噪声/无语音 | 异常边界 |
| A07 | 损坏文件/无音频流视频 | 导入安全 |

音频优先自录虚构内容，所有参录人员授权用于课程测试。

### 11.3 功能验收

- [ ] 新建会议、导入文件、转码和转录成功。
- [ ] 每段包含稳定 ID、开始时间、结束时间和有效文字。
- [ ] 用户可修改文字、命名说话人并恢复原稿。
- [ ] pyannote 关闭或失败时可人工完成说话人处理。
- [ ] AI 输出符合 Schema。
- [ ] 所有决定/行动项引用均存在并可跳转。
- [ ] 修改转录后旧分析被标记过期。
- [ ] 应用重启后无需重新运行模型即可读取结果。
- [ ] Ollama 关闭时仍可查看、校正和导出转录。
- [ ] Markdown 导出不包含密钥和绝对路径。

### 11.4 性能候选门槛

性能应以实时因子 `RTF = 处理秒数 / 音频秒数` 记录：

| 项目 | 候选门槛 |
|---|---|
| 应用冷启动到页面可用 | ≤ 15 秒，不含首次下载 |
| 3 分钟 FFmpeg 转码 | ≤ 30 秒 |
| large-v3 转录 | RTF ≤ 0.5，Spike 后按实测调整 |
| 3 分钟双人说话人分离 | ≤ 3 分钟 |
| 3 分钟会议 Qwen3 分析 | ≤ 2 分钟 |
| 连续三次主演示 | 无崩溃、无驱动重置、无数据丢失 |
| 重型 GPU 任务并行数 | 1 |

不在没有目标机实测的情况下宣称“实时”或具体倍速。

### 11.5 质量候选门槛

- 清晰普通话主样例 CER ≤ 15%；专有名词单独记录。
- 所有片段时间范围满足 `0 ≤ start < end ≤ audio_duration + tolerance`。
- 说话人分离用 DER 或人工逐段正确率记录，不只展示最好片段。
- AI Schema 成功率 ≥ 95%（固定 20 条测试输入，允许一次自动纠错重试）。
- 无效引用写入率为 0。
- 无依据的负责人/截止时间伪造率为 0。

### 11.6 Windows/GPU 专项测试

- 驱动重启或 CUDA worker 崩溃后保留已写数据。
- 显存不足时任务失败可理解，不导致 UI 崩溃。
- 实际只识别 11GB 时自动建议低档模型。
- Ollama 占用显存时 ASR 不会盲目并行。
- 中文、空格、长路径输入可处理或给出明确限制。
- Defender 扫描期间不会把数据库临时错误误判为永久损坏。
- 麦克风权限拒绝时上传路径仍可用。
- 断网且模型已缓存时完成 P0。

### 11.7 发布阻断条件

出现任一情况不得宣称 P0 完成：

- GPU/驱动在连续测试中重置或系统崩溃。
- 主样例无法真实转录。
- 转录或人工修改在重启后丢失。
- 模型生成不存在的引用仍被保存。
- 单个模型失败导致会议文件或数据库损坏。
- 主演示依赖现场首次下载模型。
- 提交包包含密钥、真实敏感会议或大型模型权重。
- 只有预制文字，没有真实音频到转录的证据。

---

## 十二、风险与应对

### 12.1 风险总览

| 风险 | 概率 | 影响 | 应对 |
|---|---:|---:|---|
| 22GB 改装显卡不稳定 | 中 | 极高 | 硬件 Gate、降至 11GB 档、取消超频 |
| 驱动/CUDA/cuDNN/CTranslate2 不兼容 | 高 | 高 | 第 1 周 Spike、锁版本、真实推理验证 |
| PyTorch 与 faster-whisper DLL 冲突 | 中 | 高 | 独立 worker，必要时拆虚拟环境 |
| Windows 10 已停止普通支持 | 高 | 中 | 优先 Windows 11；Win10 说明 ESU/LTSC 风险 |
| 14B 模型上下文挤占显存 | 中 | 高 | 限 16K、串行、8B 降级 |
| Ollama 后台保留显存 | 中 | 中 | 监控、控制保留时间、GPU 锁 |
| pyannote 下载条件/令牌阻塞 | 中 | 中 | 提前接受并缓存；人工标注降级 |
| 说话人分离错误 | 高 | 中 | 匿名标签、人数提示、人工修订 |
| 转录专有名词错误 | 高 | 中 | 热词提示、large-v3、人工修订 |
| AI 幻觉/伪造引用 | 中 | 高 | Schema、确定性引用校验、人工复核 |
| OneDrive/杀毒软件锁文件 | 中 | 中 | 本地非同步目录、短事务、重试 |
| 长音频耗时或上下文超限 | 中 | 中 | 分块转录、分层总结、时长提示 |
| 范围膨胀 | 高 | 高 | P0 冻结，P1 插件化，最后两周不加功能 |

### 12.2 版本漂移

预防：

- 固定 Python 小版本、包版本和模型标识。
- 保存 `uv.lock`、实际安装清单和驱动截图/文本。
- 记录 faster-whisper、CTranslate2、PyTorch、CUDA runtime、cuDNN、Ollama 组合。
- 答辩前一周冻结；不更新驱动、Ollama、模型或 Windows 大版本。

发生后：

1. 使用已验证锁文件重建新虚拟环境，不直接在坏环境上反复升级。
2. 恢复已记录的驱动/包组合。
3. 先关闭 pyannote，再降 ASR/LLM 档位。
4. 仍失败时展示同一输入的已处理会议和录屏，并如实说明。

### 12.3 数据与隐私

- 默认本地处理，不上传会议内容。
- 兼容 API 默认关闭；启用前明确提示数据将离开本机。
- API 密钥使用环境变量或系统凭据，不写数据库。
- 日志不记录完整会议文本。
- 删除流程覆盖原始媒体、处理音频、导出与数据库记录。
- Windows 账户和数据盘权限应阻止其他普通账户随意读取。
- 课程样例使用虚构会议，避免真实个人和商业敏感信息。

### 12.4 许可与学术规范

- 分别核对代码库、Python 包、模型权重和样例素材许可。
- pyannote community-1 当前模型卡标注 CC-BY-4.0，并要求接受访问条件；提交报告中注明来源。
- Ollama 模型库展示的模型标签不等于自动解决所有上游许可问题，仍需核对对应模型说明。
- 不把模型权重放进源码提交包。
- 报告注明 AI 辅助开发范围、模型限制和人工校验机制。

### 12.5 三级演示方案

#### A 级：完整现场演示

导入 3 分钟音频，现场展示转码、转录、校正、AI 分析、引用和导出。

#### B 级：同源预处理会议

现场模型阶段超过预期时，打开同一音频提前处理的会议，从校正、AI 成果、引用和导出继续，并明确说明切换原因。

#### C 级：录屏与静态结果

驱动或硬件故障时播放提前录制的完整过程，同时展示源码、数据库记录、性能报告和导出文件。C 级不是替代测试，只是现场容灾。

---

## 十三、最终交付与演示

### 13.1 必须交付

| 编号 | 交付物 |
|---|---|
| D01 | 源代码与清晰目录结构 |
| D02 | `pyproject.toml`、`uv.lock` 和版本清单 |
| D03 | Windows 11 主部署 README；Windows 10 兼容说明 |
| D04 | PowerShell 安装、检查、模型准备和启动脚本 |
| D05 | 数据库迁移与样例配置 |
| D06 | 自录测试音频和授权说明 |
| D07 | 自动测试与真实模型测试报告 |
| D08 | 性能、显存和质量评估数据 |
| D09 | 示例 Markdown 会议纪要 |
| D10 | 第三方依赖、模型、素材和许可清单 |
| D11 | 课程报告、答辩 PPT 与演示录屏 |

### 13.2 不进入提交包

- `.venv` 和 Python 缓存。
- Hugging Face、faster-whisper、pyannote 和 Ollama 模型权重。
- `.env`、令牌和 API 密钥。
- 个人真实会议、数据库、日志和临时文件。
- 未确认许可的音视频。
- NVIDIA 安装包、驱动安装包和 CUDA 大型离线包。

### 13.3 README 必备内容

1. 项目目标与截图。
2. Windows 版本与硬件要求。
3. 为什么优先 Windows 11。
4. RTX 2080 Ti 22GB 改装卡检查要求。
5. NVIDIA 驱动、CUDA 运行库、FFmpeg、Ollama 准备。
6. Python/uv 环境创建与锁文件同步。
7. ASR、LLM、pyannote 模型准备。
8. 环境诊断和启动。
9. 数据目录、备份和删除。
10. 快速测试、真实模型慢测试和完整验收。
11. 常见错误：DLL、显存、端口、路径、Defender、麦克风权限。
12. 断网运行条件。
13. 已知限制、隐私与许可。

### 13.4 七分钟演示脚本

| 时间 | 内容 | 重点 |
|---|---|---|
| 0:00～0:35 | 背景与目标 | 单机、本地、可校正、可引用 |
| 0:35～1:10 | 环境诊断 | Windows 11、RTX 2080 Ti、22GB、CUDA 可用 |
| 1:10～1:45 | 架构 | Streamlit＋FFmpeg＋faster-whisper＋pyannote＋Ollama＋SQLite |
| 1:45～2:35 | 导入与转录 | `large-v3` GPU、时间戳、任务状态 |
| 2:35～3:25 | 说话人与校正 | 匿名标签、人工改名、保留原稿 |
| 3:25～4:35 | AI 会议成果 | Qwen3 14B Q4、摘要、决定、行动项 |
| 4:35～5:20 | 可信引用 | 点击定位、无效 ID 拒绝、未明确不补全 |
| 5:20～5:50 | 导出与恢复 | Markdown、重启读取、数据目录 |
| 5:50～6:30 | 性能与降级 | 显存峰值、串行调度、8B/人工降级 |
| 6:30～7:00 | 限制与展望 | 非实时、单用户、模型需复核 |

### 13.5 答辩关键表述

#### 为什么不用原来的 MLX Whisper？

MLX 面向 Apple Silicon。Windows 上的目标显卡是 NVIDIA RTX 2080 Ti，因此使用 faster-whisper/CTranslate2/CUDA，能够利用其 Compute Capability 7.5 和独立显存。

#### 为什么使用 large-v3？

目标机实际提供 22GB 显存，较原 M1 8GB 环境有明显余量。`large-v3` 能提高中文和中英混合转录质量；同时保留 `medium` 与低精度作为稳定性降级。

#### 为什么不使用 30B？

30B A3B Q4 权重本身接近 19GB，还需要 KV 缓存和运行工作区。22GB 能否加载不等于能稳定处理长上下文，14B Q4 更符合课程演示的稳定优先原则。

#### 22GB 显存是否可靠？

标准 2080 Ti 通常不是 22GB，因此项目不直接信任标称值。部署前通过 `nvidia-smi`、PyTorch 分配、持续负载、温度和三轮端到端测试建立可用基线；失败则按 11GB 配置降级。

#### 如何控制 AI 幻觉？

模型只能读取带稳定 ID 的转录，返回固定 JSON Schema；程序验证引用存在性和修订归属。负责人、截止时间没有依据时必须标记未明确，最终结果仍由用户复核。

### 13.6 最终完成标准

满足以下全部条件，才算本 Windows 版本规划落实：

1. 目标 Windows 电脑可按 README 启动，无需现场重新安装或下载。
2. `nvidia-smi` 与应用诊断正确识别 GPU 和实际显存。
3. A02 样例完成 FFmpeg、large-v3 GPU 转录和时间戳保存。
4. 自动说话人可用，或明确降级为人工说话人标注。
5. Qwen3 14B Q4 生成有效摘要、决定和行动项；无效引用写入率为零。
6. 修改、保存、重启、导出链路完整。
7. 关闭 Ollama、关闭 pyannote、断网三种情况下不丢已有数据。
8. 连续三次端到端运行无驱动重置、崩溃或显存持续泄漏。
9. 测试报告填入真实 RTF、CER、Schema 成功率、峰值显存和失败记录。
10. 提交包不含密钥、个人会议、日志和模型权重。

---

## 附录 A：建议配置文件

```yaml
app:
  host: 127.0.0.1
  port: 8501
  data_dir: D:\meeting-data
  max_audio_minutes: 60
  gpu_heavy_task_limit: 1

audio:
  sample_rate: 16000
  channels: 1
  codec: pcm_s16le

asr:
  provider: faster-whisper
  model: large-v3
  fallback_model: medium
  device: cuda
  compute_type: float16
  fallback_compute_type: int8_float16
  language: zh
  vad_filter: true

diarization:
  enabled: true
  required: false
  model: pyannote/speaker-diarization-community-1
  device: cuda

llm:
  provider: ollama
  base_url: http://127.0.0.1:11434
  model: qwen3:14b-q4_K_M
  fallback_model: qwen3:8b
  context_tokens: 16384
  temperature: 0.1
  timeout_seconds: 300

privacy:
  external_api_enabled: false
  log_transcript_content: false
```

配置文件只保存非秘密项；令牌和密钥由 `.env` 或系统凭据提供。

---

## 附录 B：正式实施前必须填入的实测表

| 项目 | 规划值 | 目标机实测 | 是否通过 |
|---|---|---|---|
| Windows 版本/构建 | Windows 11 64 位 | 待测 | 待定 |
| CPU/系统内存 | ≥6C12T/32GB 推荐 | 待测 | 待定 |
| GPU | RTX 2080 Ti CC 7.5 | 待测 | 待定 |
| 可见显存 | 约 22GB | 待测 | 待定 |
| NVIDIA 驱动 | 选定稳定版 | 待测 | 待定 |
| PyTorch/CUDA wheel | Spike 锁定 | 待测 | 待定 |
| CTranslate2/cuDNN | CUDA 12＋cuDNN 9 兼容组合 | 待测 | 待定 |
| large-v3 峰值显存 | 约 5～8GB 候选 | 待测 | 待定 |
| A02 ASR RTF | ≤0.5 候选 | 待测 | 待定 |
| A02 CER | ≤15% 候选 | 待测 | 待定 |
| pyannote 峰值显存/DER | 候选待定 | 待测 | 待定 |
| Qwen3 14B 峰值显存 | 约 11～17GB 候选 | 待测 | 待定 |
| Qwen3 分析耗时 | ≤2 分钟候选 | 待测 | 待定 |
| 连续三次端到端 | 3/3 成功 | 待测 | 待定 |

---

## 附录 C：官方资料

- [Microsoft：Windows 10 已于 2025-10-14 结束支持](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025)
- [NVIDIA：CUDA GPU Compute Capability，RTX 2080 Ti 为 7.5](https://developer.nvidia.com/cuda/gpus)
- [PyTorch：Windows/CUDA 官方安装选择器](https://pytorch.org/get-started/locally/)
- [faster-whisper：安装、CUDA 12/cuDNN 9 要求与 large-v3 示例](https://github.com/SYSTRAN/faster-whisper)
- [pyannote community-1 模型卡与本地 GPU 用法](https://huggingface.co/pyannote/speaker-diarization-community-1)
- [Ollama：Windows 原生运行与模型目录](https://docs.ollama.com/windows)
- [Ollama：NVIDIA GPU 支持范围](https://docs.ollama.com/gpu)
- [Ollama：Qwen3 模型标签与体积](https://ollama.com/library/qwen3/tags)

---

# 规划完成状态

本文件已经完成面向 Windows 10/11 与 RTX 2080 Ti 22GB 显存电脑的独立重写。下一步应先执行 M0 硬件 Gate 和 M1 六个技术 Spike，再用目标机实测数据替换本文中的候选性能与显存范围。
