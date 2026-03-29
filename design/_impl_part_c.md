
## 第 9 章：FINALIZE (finalize.py)

### 9.1 职责

FINALIZE 在 Orchestrator 的末端执行，将结构化的诊断状态转化为面向用户的 `DiagnosisReport`。

### 9.2 函数签名

```python
def finalize_node(state: OrchestratorState) -> dict:
    """
    输入:
      - state["current_proposal"]: ConclusionProposal
      - state["focus_context"]: FocusContext
      - state["diagnosis_trace"]: list[ReActStep]
      - state["audit_trace"]: list[AuditStep]（如有）
      - state["audit_decision"]: AuditDecision
    
    LLM 调用:
      prompt = finalize.md 模板
      结构化输出: DiagnosisReport (JSON)
    
    处理逻辑:
      1. 确定 diagnosis_type:
         - audit_decision.decision == "pass" → 使用 proposal 的类型
         - audit_decision.decision == "degrade" → "partial" 或 "inconclusive"
         - ablation.enable_audit == False → 使用 proposal 的类型（无审查）
      
      2. 构建 finalize prompt:
         - 注入 ConclusionProposal 的结论与证据
         - 注入 FocusContext 的 leading_subsystem, top_metrics
         - 不注入完整 react_trace（token 节省）
         - 注入 Audit Agent 审查摘要（如有）
      
      3. 调用 LLM → DiagnosisReport JSON
      
      4. 后处理:
         - 填充 trace_summary（从 state 统计生成，不依赖 LLM）
         - 校验 Pydantic schema
         - 若 LLM 输出格式异常：重试 1 次，仍失败则用模板填充
    
    返回: {"diagnosis_report": DiagnosisReport}
    """
```

### 9.3 trace_summary 构建

```python
def _build_trace_summary(state: OrchestratorState) -> dict:
    """
    确定性构建，不消耗 LLM token。
    
    返回:
    {
        "triage_leading_subsystem": state["focus_context"].leading_subsystem,
        "main_tools_used": _count_tools(state["diagnosis_trace"]),
        "audit_tools_used": _count_tools(state.get("audit_trace", [])),
        "total_tool_calls": state["budget"]["tool_calls_used"]
                          + state.get("audit_budget", {}).get("tool_calls_used", 0),
        "main_iterations": len(state["diagnosis_trace"]),
        "audit_iterations": len(state.get("audit_trace", [])),
    }
    
    _count_tools 示例输出: ["MetricQueryTool x3", "DataAnalysisTool x1"]
    """
```

### 9.4 FINALIZE prompt 模板

`prompts/finalize.md`：

```markdown
## 任务
你是 HPC 诊断报告撰写者。基于以下诊断结论和证据，生成最终诊断报告。

## 诊断结论
- 诊断类型: {diagnosis_type}
- 确认的根因:
{confirmed_root_causes}
- 关键证据:
{key_evidence}

## Triage 上下文
- Leading Subsystem: {leading_subsystem}
- 异常指标概览: {top_metrics_brief}

## 审查结果
{audit_summary}

## 输出格式
返回 JSON，必须符合 DiagnosisReport schema。
```

---

## 第 10 章：Reflect (reflect.py)

### 10.1 触发条件

```python
def should_reflect(report: DiagnosisReport, config: AgentConfig) -> bool:
    """
    触发条件:
      1. diagnosis_type in ("single_fault", "composite_fault", "partial")
      2. 至少有 1 个 confirmed root_cause
    
    "inconclusive" 时不触发：无可信信号可提炼。
    """
    if report.diagnosis_type == "inconclusive":
        return False
    return len([rc for rc in report.root_causes if rc.confidence > 0.5]) > 0
```

### 10.2 执行步骤

```python
def run_reflect(
    report: DiagnosisReport,
    focus_context: FocusContext,
    existing_fpl: list[dict],
    llm_client: LLMClient,
    config: AgentConfig,
) -> list[dict]:
    """
    1. 规则提炼:
       - 为每个 confirmed root_cause 构建提炼 prompt
       - LLM 生成候选 FPL 规则（JSON）
       - Pydantic 校验（防污染）
    
    2. 去重比较:
       - 对每个候选规则，与 existing_fpl 按 fault_type 匹配
       - 若 fault_type 完全相同:
         - 比较 symptom_signature 的 required_metrics 重合度
         - 重合度 > 0.8 → 更新现有规则（version +1，合并 provenance_exp_ids）
         - 重合度 ≤ 0.8 → 作为同 fault_type 的变体新增
       - 若 fault_type 不同 → 新增
    
    3. 写回:
       - 追加写入 fpl.jsonl（每行一个 JSON）
       - 失败时记录 warning，不影响主流程
    
    返回: 更新后的 fpl_entries
    """
```

### 10.3 FPL 规则 Schema

与现有 `fpl.jsonl` 一致（8 条种子规则已定义），候选规则必须遵循：

```python
class FPLRule(BaseModel):
    pattern_id: str                          # "fpl_{nnn}" 自增
    fault_type: str                          # 标准故障类型名
    version: int = 1
    status: Literal["confirmed", "active", "deprecated"] = "active"
    source: Literal["manual", "reflected"] = "reflected"
    provenance_exp_ids: list[str]
    confidence: float                        # 0.0-1.0，反映验证结果初值=0.5
    symptom_signature: dict                  # {leading_subsystem, required_metrics, ...}
    verification_steps: list[str]            # 自然语言验证步骤
    solutions: list[str]                     # 推荐解决方案
    # 可选 (composite 时)
    composite_discrimination: dict | None = None
```

**Reflect 生成的规则与 manual 种子规则的区别**：
- `source = "reflected"`（manual = 人工编写）
- `confidence` 初始值 = 0.5（manual 种子更高）
- `status = "active"`（需要后续验证才能升级为 confirmed）

### 10.4 防污染校验

```python
def _validate_reflected_rule(rule: dict) -> FPLRule:
    """
    1. Pydantic 校验：字段类型、必填项
    2. confidence 强制设为 0.5（忽略 LLM 自行填写的数值）
    3. status 强制设为 "active"
    4. source 强制设为 "reflected"
    5. pattern_id 由系统分配（忽略 LLM 自行填写）
    """
```

---

## 第 11 章：Prompt 模板管理 (prompts/)

### 11.1 模板文件清单

| 文件 | 调用方 | 用途 |
|------|--------|------|
| `diagnosis_system.md` | THINK | Diagnosis Agent 的 system prompt，定义角色、约束、输出格式 |
| `diagnosis_hypothesize.md` | HYPOTHESIZE | 假设生成提示 |
| `diagnosis_think.md` | THINK | ReAct 循环推理提示（含 tool 描述） |
| `audit_system.md` | GATE_THINK | Audit Agent 的 system prompt |
| `finalize.md` | FINALIZE | 报告生成提示 |
| `reflect.md` | Reflect | FPL 规则提炼提示 |

### 11.2 模板变量注入机制

```python
def render_prompt(template_path: str, variables: dict) -> str:
    """
    简单字符串模板：{variable_name} 替换。
    
    不使用 Jinja2（避免额外依赖复杂度），使用 Python str.format_map()。
    - 所有变量名使用 snake_case
    - 未提供的变量保留原始 {placeholder}（便于调试）
    - 模板中的 {{ 和 }} 转义为字面量
    """
    with open(template_path, 'r') as f:
        template = f.read()
    return template.format_map(defaultdict(lambda: "{MISSING}", variables))
```

### 11.3 diagnosis_system.md 设计要点

```markdown
## 角色
你是 HPC 系统故障诊断 Agent。你遵循 ReAct（Think-Act-Observe）范式：
每一步你必须先思考（Think），然后决定是调用工具（Act）还是提出结论（Conclude）。

## 工具
你有以下三个工具可用:
{tool_descriptions}

## 约束
1. 你只能基于工具返回的证据做推理，不允许编造数据
2. 每次只能调用一个工具
3. 你必须在 {max_react_iterations} 步内完成诊断
4. 当证据充分时，立即 conclude，不要过度收集

## 输出格式
每步输出一个 JSON object:
{think_output_schema}
```

### 11.4 diagnosis_think.md 设计要点

```markdown
## 当前状态
Step {step_number} / {max_react_iterations}
Tool calls used: {tool_calls_used} / {tool_calls_limit}

## 假设状态
{hypotheses_formatted}

## 已收集证据
{evidence_formatted}

## 验证清单
{verification_status}

{gate_hint_section}

{force_conclude_section}

## 请推理并决定下一步
```

### 11.5 工具描述格式

注入到 system prompt 的工具描述，采用精简 JSON Schema：

```python
def format_tool_descriptions(tools: dict[str, BaseTool]) -> str:
    """
    生成统一格式的工具描述:
    
    ### MetricQueryTool
    查询指定指标在指定时间窗口内的聚合统计量。
    参数:
    - metrics (array[string]): 指标名列表
    - time_window ({start, end}): ISO8601 时间范围
    - aggregation (enum: mean|p95|max|duration_above_threshold): 聚合方式
    - threshold_value (number, optional): 仅 duration_above_threshold 时需要
    
    ### KBRetrievalTool ...
    ### DataAnalysisTool ...
    """
```

---

## 第 12 章：LLM Client (llm_client.py)

### 12.1 封装目标

统一 LLM 调用接口，屏蔽 OpenRouter / 本地模型差异，集中管理 token 计数与重试。

### 12.2 类定义

```python
class LLMClient:
    def __init__(self, config: LLMConfig):
        """
        config 包含:
          model: str                  # "deepseek/deepseek-chat"
          base_url: str               # "https://openrouter.ai/api/v1"
          api_key: str                # 从 .env 加载
          temperature: float          # 0.2
          max_tokens: int             # 4096
          timeout: int                # 60
          max_retries: int            # 2
        """
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        self.config = config
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    
    def call(
        self,
        messages: list[dict],
        response_format: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> tuple[str, dict]:
        """
        统一 LLM 调用。
        
        参数:
          messages: OpenAI 格式消息列表
          response_format: 若提供，使用 structured output（JSON mode）
          temperature: 覆盖默认温度
        
        处理:
          1. 构建 API 请求
          2. 调用 OpenAI SDK
          3. 重试逻辑:
             - RateLimitError → 指数退避重试
             - APIConnectionError → 重试
             - InvalidRequestError → 不重试，直接抛出
          4. 累计 token_usage
        
        返回: (content_str, usage_dict)
        
        异常:
          - LLMCallError: 重试耗尽后抛出
        """
    
    def call_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
    ) -> tuple[BaseModel, dict]:
        """
        带结构化输出的调用。
        
        处理:
          1. 调用 self.call() 获取 JSON 字符串
          2. json.loads() 解析
          3. schema.model_validate() 校验
          4. 解析失败 → 重试 1 次（在 prompt 末尾追加格式提示）
        
        返回: (parsed_model_instance, usage_dict)
        """
    
    def get_usage(self) -> dict:
        """返回累计 token 使用量"""
        return dict(self.token_usage)
    
    def reset_usage(self):
        """重置 token 计数器（每个实验重置）"""
        self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
```

### 12.3 结构化输出策略

优先使用 OpenAI SDK 的 `response_format={"type": "json_object"}` + prompt 中嵌入 schema。
若模型不支持 JSON mode，fallback 到 prompt-only 方式 + 后处理 regex 提取 JSON。

```python
def _extract_json_from_text(text: str) -> str:
    """
    Fallback JSON 提取:
    1. 尝试 json.loads(text)
    2. 匹配 ```json ... ``` 块
    3. 匹配第一个 { ... } 或 [ ... ] 块
    4. 全部失败 → 抛出 JSONParseError
    """
```

### 12.4 Token 分项计量

不同调用方的 token 应分开统计，便于消融分析：

```python
class TokenTracker:
    def __init__(self):
        self.categories = {
            "diagnosis_hypothesize": {"prompt": 0, "completion": 0},
            "diagnosis_think": {"prompt": 0, "completion": 0},
            "audit_think": {"prompt": 0, "completion": 0},
            "finalize": {"prompt": 0, "completion": 0},
            "reflect": {"prompt": 0, "completion": 0},
        }
    
    def record(self, category: str, usage: dict):
        self.categories[category]["prompt"] += usage.get("prompt_tokens", 0)
        self.categories[category]["completion"] += usage.get("completion_tokens", 0)
    
    def summary(self) -> dict:
        return {k: {**v, "total": v["prompt"] + v["completion"]} 
                for k, v in self.categories.items()}
```

---

## 第 13 章：评估框架 (eval/)

### 13.1 Ground Truth 格式

每个实验目录下的 `fault_info.txt` 格式：

```
Fault Type: cpu_fullload
Fault Start Time: 2025-09-20 07:01:15
Fault End Time: 2025-09-20 07:06:15
Fault Duration: 300 seconds
```

复合故障有多组：

```
Fault Type: cpu_fullload
Fault Start Time: 2025-09-20 07:01:15
...
Fault Type: mem_load_ram
Fault Start Time: 2025-09-20 07:01:30
...
```

### 13.2 Ground Truth 解析

```python
def parse_fault_info(path: str) -> list[dict]:
    """
    解析 fault_info.txt → list[{fault_type, start_time, end_time, duration}]
    
    解析规则:
      - 按 "Fault Type:" 分段
      - 每段提取 4 个字段
      - 时间格式: "%Y-%m-%d %H:%M:%S"
      - 若字段缺失 → 跳过该段，记录 warning
    
    特殊情况:
      - exp_029（无故障实验）: 文件内容为 "No fault injected" → 返回空列表
    """
```

### 13.3 评估指标计算

```python
class Evaluator:
    def evaluate_single(
        self,
        report: DiagnosisReport,
        ground_truth: list[dict],
    ) -> dict:
        """
        单实验评估。
        
        返回:
        {
            "hit_at_1": bool,          # Top-1 root_cause.fault_type 匹配 GT
            "hit_at_3": bool,          # Top-3 中任一匹配
            "composite_coverage": float, # 复合故障: matched_gt_faults / total_gt_faults
            "false_positives": int,    # report 中不在 GT 中的 fault_type 数量
            "tool_calls": int,
            "audit_tool_calls": int,
            "latency_seconds": float,
            "token_usage": dict,
            "diagnosis_type": str,
        }
        """
    
    def evaluate_batch(
        self,
        results_dir: str,
        gt_dir: str,
    ) -> pd.DataFrame:
        """
        批量评估所有实验。
        
        遍历 results_dir 下所有 exp_* 目录:
          1. 加载 diagnosis_report.json
          2. 加载对应 gt_dir 下的 fault_info.txt
          3. 调用 evaluate_single
          4. 汇总为 DataFrame
        
        聚合指标:
          - Hit@1 Accuracy = mean(hit_at_1)
          - Hit@3 Accuracy = mean(hit_at_3)
          - Composite Coverage = mean(composite_coverage) over composite exps
          - Avg Tool Calls = mean(tool_calls)
          - Avg Latency = mean(latency_seconds)
          - Total Token Usage
        """
```

### 13.4 fault_type 匹配规则

```python
def match_fault_type(predicted: str, ground_truth: str) -> bool:
    """
    允许灵活匹配:
      - 完全一致: cpu_fullload == cpu_fullload → True
      - 忽略大小写: CPU_Fullload == cpu_fullload → True
      - 别名映射:
        {"mem_load": ["mem_load_ram", "mem_load_buffer", "memory_load"],
         "network_loss": ["net_loss", "packet_loss"],
         "disk_burn": ["disk_io_burn", "io_burn"],
         "disk_fill": ["disk_space_fill", "filesystem_fill"]}
      - composite 匹配: "composite:cpu_fullload+mem_load" → 拆分为单独类型逐一匹配
    """
```

### 13.5 消融实验运行器 (eval/ablation.py)

```python
class AblationRunner:
    def __init__(self, base_config: AgentConfig, data_dir: str, output_dir: str):
        self.base_config = base_config
        self.data_dir = data_dir
        self.output_dir = output_dir
    
    def run_ablation_suite(self, experiments: list[str] | None = None):
        """
        运行消融实验矩阵。
        
        消融 ID → AblationFlags 映射（见 Ch3 实现）:
          "Full", "Abl-A", "Abl-B"
        
        流程:
          1. 遍历消融配置
          2. 对每组消融:
             a. 构建 AgentConfig（覆盖 ablation flags）
             b. 对每个实验运行 run_diagnosis()
             c. 保存结果到 output_dir/<ablation_id>/<exp_id>/
          3. 汇总评估
             a. 对每组消融运行 evaluate_batch()
             b. 生成对比表格
        
        输出:
          - output_dir/<ablation_id>/<exp_id>/diagnosis_report.json
          - output_dir/<ablation_id>/<exp_id>/execution_trace.json
          - output_dir/ablation_summary.csv  # 消融对比表
        """
    
    def run_single_ablation(
        self,
        ablation_id: str,
        flags: AblationFlags,
        experiments: list[str],
    ) -> pd.DataFrame:
        """运行单组消融实验"""
```

---

## 第 14 章：总入口脚本 (run_agent.py)

### 14.1 命令行接口

```python
import argparse

def main():
    parser = argparse.ArgumentParser(description="HPC Diagnosis Agent")
    parser.add_argument("--experiment", type=str, default="formaltest",
                        help="实验集名称")
    parser.add_argument("--exp-id", type=str, default=None,
                        help="单个实验 ID (如 exp_001), 不指定则跑全部")
    parser.add_argument("--model", type=str, default="deepseek/deepseek-chat",
                        help="LLM 模型名")
    parser.add_argument("--ablation", type=str, default="Full",
                        help="消融配置 ID (Full/Abl-A/Abl-B)")
    parser.add_argument("--output-dir", type=str, default="agent/data",
                        help="输出目录")
    parser.add_argument("--evaluate", action="store_true",
                        help="运行后自动评估")
    parser.add_argument("--ablation-suite", action="store_true",
                        help="运行完整消融实验矩阵")
    args = parser.parse_args()
```

### 14.2 运行流程

```python
    # 1. 加载配置
    config = AgentConfig.from_ablation_id(args.ablation)
    config.llm.model = args.model
    
    # 2. 确定数据路径
    data_base = f"dataset_builder/data/{args.experiment}/extracted_data"
    
    # 3. 枚举实验
    if args.exp_id:
        exp_ids = [args.exp_id]
    else:
        exp_ids = sorted([d for d in os.listdir(data_base) 
                         if os.path.isdir(os.path.join(data_base, d))])
    
    # 4. 逐实验运行
    for exp_id in exp_ids:
        metrics_path = os.path.join(data_base, exp_id, "metrics.csv")
        jobinfo_path = os.path.join(data_base, exp_id, "jobinfo.csv")
        
        report, trace = run_diagnosis(metrics_path, jobinfo_path, config)
        
        # 保存结果
        output_path = os.path.join(args.output_dir, args.ablation, exp_id)
        os.makedirs(output_path, exist_ok=True)
        save_report(report, output_path)
        if trace:
            save_trace(trace, output_path)
    
    # 5. 可选评估
    if args.evaluate:
        evaluator = Evaluator()
        results = evaluator.evaluate_batch(
            os.path.join(args.output_dir, args.ablation),
            data_base,
        )
        print(results.to_string())
    
    # 6. 消融套件
    if args.ablation_suite:
        runner = AblationRunner(config, data_base, args.output_dir)
        runner.run_ablation_suite(exp_ids)
```

### 14.3 输出目录结构

```
agent/data/
  Full/
    exp_001/
      diagnosis_report.json
      execution_trace.json
    exp_002/
      ...
  Abl-A/
    exp_001/
      ...
  ablation_summary.csv
```

---

## 第 15 章：实现分阶段与验收标准

### 15.1 Phase B: Triage

**产物**: `agent/triage.py`, `agent/config.py`, `agent/schema.py`（v6 升级）

**验收标准**:
1. `run_triage()` 接受 metrics.csv 路径，输出 FocusContext
2. 对 exp_001（cpu_fullload）: leading_subsystem == "cpu"
3. 对 exp_005（network_loss）: leading_subsystem == "network"
4. 对 exp_008（cpu+mem 复合）: top_metrics 同时包含 cpu 和 memory 指标
5. 对 exp_029（无故障）: top_metrics 为空或评分极低
6. 运行时间 < 2s（单实验）
7. 完全确定性（不调用 LLM）

### 15.2 Phase C: Diagnosis + Audit + Orchestrator

**产物**: `agent/diagnosis.py`, `agent/audit.py`, `agent/orchestrator.py`, `agent/tools/`, `agent/finalize.py`, `agent/llm_client.py`, `agent/prompts/`

**验收标准**:
1. 单实验端到端运行: `run_diagnosis(metrics_path, jobinfo_path, config)` 返回 DiagnosisReport
2. 对 exp_001（单故障 cpu_fullload）: Hit@1 == True
3. 对 exp_008（复合故障 cpu+mem）: composite_coverage >= 0.5
4. Audit Agent 可被消融禁用: `enable_audit=False` 时跳过审查
5. tool_calls_used 不超过 budget.tool_calls_limit
6. execution_trace.json 可追溯完整推理过程

**Phase C 内部分步验收**:

| 步骤 | 内容 | 验收 |
|------|------|------|
| C.1 | Diagnosis Graph + Tools（无 Audit） | 单实验可产出 ConclusionProposal |
| C.2 | 最小 Audit Agent（零工具） + Orchestrator | pass/continue 路由正常 |
| C.3 | 完整 Audit Agent + hint 协议 | continue → 补查 → re-submit 正常工作 |

### 15.3 Phase D: Reflect + 评估

**产物**: `agent/reflect.py`, `eval/evaluate.py`, `eval/ablation.py`

**验收标准**:
1. Reflect 可正常写回 fpl.jsonl
2. evaluate_batch() 输出完整评估 DataFrame
3. 消融 AblationRunner 可自动运行 Full + Abl-A + Abl-B

### 15.4 Phase E: 消融实验 + 论文

**产物**: 完整消融结果、论文表格与图表

**验收标准**:
1. 3 组消融实验（Full + Abl-A + Abl-B）全部有结果
2. 消融对比表可直接用于论文
3. 跨模型评估至少 2 个额外模型

---

## 附录 A：子系统前缀映射表

```python
SUBSYSTEM_PREFIX_MAP = {
    # CPU 子系统
    "cpu_": "cpu",
    "load_": "cpu",
    "context_switches": "cpu",
    "processes_running": "cpu",
    "processes_blocked": "cpu",
    "procs_": "cpu",
    "interrupts_": "cpu",
    "softirq_": "cpu",
    "entropy_": "cpu",
    
    # Memory 子系统
    "memory_": "memory",
    "anon_memory": "memory",
    "buffer_": "memory",
    "cache_memory": "memory",
    "swap_": "memory",
    "page_": "memory",
    "slab_": "memory",
    "mapped_": "memory",
    "shmem_": "memory",
    "hugepages_": "memory",
    "commit_": "memory",
    "vmalloc_": "memory",
    "writeback_": "memory",
    
    # Network 子系统
    "network_": "network",
    "tcp_": "network",
    "udp_": "network",
    "icmp_": "network",
    "socket_": "network",
    "netstat_": "network",
    
    # Disk 子系统
    "disk_": "disk",
    "filesystem_": "disk",
    
    # System (辅助)
    "boot_time": "system",
    "file_descriptor": "system",
    "nf_conntrack": "system",
    "time_offset": "system",
}
```

**归属规则**：按最长前缀匹配优先。若无前缀匹配 → 归入 `"system"` 。

---

## 附录 B：fault_info.txt 解析规范

### 格式定义

```
Fault Type: <fault_type_string>
Fault Start Time: <YYYY-MM-DD HH:MM:SS>
Fault End Time: <YYYY-MM-DD HH:MM:SS>
Fault Duration: <integer> seconds
```

### 复合故障

多组连续排列，以空行分隔（部分文件无空行分隔，需兼容）。

### 无故障实验

exp_029 的 fault_info.txt 内容：`No fault injected` 或空文件。

### 容错

- 字段名大小写不敏感
- 允许冒号后有额外空格
- Duration 字段可缺失（从 start/end 计算）

---

## 附录 C：已知风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM 不服从结构化输出格式 | THINK/HYPOTHESIZE 解析失败 | 重试机制 + fallback 模板 |
| OpenRouter API 限流 | 批量实验中断 | 指数退避 + 实验断点续跑 |
| FPL 反射规则质量差 | 知识库退化 | confidence 初值低 + 需验证升级 |
| 单节点数据前提 | 多节点场景不适用 | 保留 nodes 字段, 当前不实现 |
| Triage 假阴性 | 异常指标被遗漏 | top_k 可配置, Abl-A 验证 |
| Audit Agent 过度保守 | continue 循环浪费 budget | max_rounds=2 限制 |
| token 占用超预期 | 成本过高 | result_digest 压缩 + 分项监控 |
| ruptures 安装问题 | changepoint 降级 | 提供滚动均值 fallback |

---

## 附录 D：29 个实验 Ground Truth 速查

| 实验 | 故障类型 |
|------|---------|
| exp_001 | cpu_fullload |
| exp_002 | mem_load_ram |
| exp_003 | mem_load_ram |
| exp_004 | mem_load_buffer |
| exp_005 | network_loss |
| exp_006 | disk_burn |
| exp_007 | disk_fill |
| exp_008 | cpu_fullload + mem_load_ram |
| exp_009 | cpu_fullload + mem_load_ram |
| exp_010 | cpu_fullload + mem_load_ram |
| exp_011 | cpu_fullload + network_loss |
| exp_012 | cpu_fullload + disk_burn |
| exp_013 | cpu_fullload + disk_fill |
| exp_014 | mem_load_ram + network_loss |
| exp_015 | mem_load_ram + disk_burn |
| exp_016 | mem_load_ram + disk_fill |
| exp_017 | network_loss + disk_burn |
| exp_018 | network_loss + disk_fill |
| exp_019 | disk_burn + disk_fill |
| exp_020 | cpu_fullload + mem_load_ram + network_loss |
| exp_021 | cpu_fullload + mem_load_ram + disk_burn |
| exp_022 | cpu_fullload + mem_load_ram + disk_fill |
| exp_023 | cpu_fullload + network_loss + disk_burn |
| exp_024 | cpu_fullload + network_loss + disk_fill |
| exp_025 | cpu_fullload + disk_burn + disk_fill |
| exp_026 | mem_load_ram + network_loss + disk_burn |
| exp_027 | mem_load_ram + network_loss + disk_fill |
| exp_028 | mem_load_ram + disk_burn + disk_fill |
| exp_029 | 无故障 |

> 注意: 上表需在实现前通过逐一解析 fault_info.txt 做最终确认。
