# LLM 智能诊断模块使用指南

`llm` 模块是本项目的分析核心，它负责接收 Phase 1 生成的监控数据，构建 Prompt，调用大语言模型 (如 GPT-4, Claude 3.5 Sonnet, DeepSeek) 进行根因分析，并输出结构化的诊断报告。

## 1. 核心逻辑与流程

诊断流程由 `run.py` (及 `src/main.py`) 驱动，主要步骤如下：

1.  **数据加载 (Data Ingestion)**:
    *   读取 `dataset_builder` 生成的 `metrics.csv` (系统指标) 和 `jobinfo.csv` (作业状态)。
    *   **关键逻辑**: `get_in_period_job` 函数会自动过滤出与监控数据时间段重叠的作业，排除无关干扰。

2.  **Prompt 构建 (Prompt Engineering)**:
    *   加载 `ref/prompt.md` 作为 **System Prompt**，定义诊断专家的角色和输出格式。
    *   加载 `ref/fewshot/` 下的示例文件 (Few-Shot Learning)，让模型学习如何分析类似故障。
    *   将 CSV 数据转换为 JSON 格式，嵌入到 User Prompt 中。

3.  **模型推理 (Inference)**:
    *   通过 OpenAI 兼容接口 (OpenRouter) 调用指定的 LLM。
    *   支持的模型包括但不限于: `gpt-4`, `claude-3-sonnet`, `deepseek-chat`。

4.  **结果解析 (Parsing)**:
    *   接收模型的 JSON 响应。
    *   解析并保存为 `data/<experiment_name>/diagnosis_report.json`。

## 2. 目录结构说明

```
llm/
├── src/               # 核心代码
│   ├── main.py        # 分析器类定义 (LLMAnalyzer)
│   └── run.py         # 命令行入口
├── ref/               # Prompt 资源
│   ├── prompt.md      # System Prompt 模板
│   └── fewshot/       # Few-Shot 示例 (shot1.txt, shot2.txt...)
├── data/              # 诊断结果输出目录
└── run.py             # 模块入口脚本
```

## 3. 配置与运行

### 3.1 环境配置
确保项目根目录下的 `.env` 文件已配置正确的 API Key：

```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
```

### 3.2 运行诊断
使用 `run.py` 指定实验名称（对应 `dataset_builder` 生成的数据文件夹名）：

```bash
# 确保在 llm 目录下
cd llm
python run.py --experiment test
```

### 3.3 切换模型
目前模型配置在 `src/run.py` 代码中。若需更换模型，请修改 `run_analysis` 函数中的 `model` 参数：

```python
# llm/src/run.py

completion = client.chat.completions.create(
    # model="deepseek/deepseek-chat",  # 选项 A
    model="anthropic/claude-3.5-sonnet", # 选项 B (推荐)
    # ...
)
```

## 4. 自定义诊断能力

### 4.1 优化 System Prompt
修改 `ref/prompt.md` 可以调整 AI 的诊断风格（例如更激进或更保守）。

### 4.2 添加 Few-Shot 示例
为了提高特定故障的诊断准确率，您可以添加新的 Few-Shot 示例：
1.  在 `ref/fewshot/` 下新建 `shotN.txt`。
2.  格式必须包含输入数据和 `Expected Output:` 分隔的诊断结果。
3.  系统会自动加载所有 `shot*.txt` 文件。

## 5. 常见问题

*   **JSONDecodeError**: 模型输出的不是合法 JSON。
    *   *解决*: 检查 `prompt.md` 中的格式约束是否够强，或尝试更换更智能的模型 (如 Claude 3.5)。
*   **FileNotFoundError**: 找不到 `metric.csv`。
    *   *解决*: 确认 Phase 1 (`dataset_builder`) 是否成功运行，且 `experiment` 参数名称一致。
