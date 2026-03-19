# Evidence Gating 规则完整规格

> **⚠️ v5 变更说明（2026-03-19）**：v5 将 Evidence Gating 精简为三项核心原理（证据独立性、反驳优先、复合故障甄别），
> 不在设计文档中暴露具体阈值。本文件的 Rule A/B/C/D 详细规格和阈值参数保留为**实现参考**，
> 论文与设计概要以 `design/v5.md Part VI` 为准。

> **文档定位**：`design/v4.md §4.4` 的深化细化，重点解决复合故障场景下的
> Gate 决策问题。本文档是工程师实现 `agent/gate.py` 的直接依据。
>
> **核心问题**：v4 的 Rule A/B/C 解决了单假设收敛问题，
> 但对"如何判断 h1 和 h2 能否同时 pass"以及"如何区分复合故障与症状链"缺乏规范。
> 本文补齐这两个关键缺口。

---

## 1. v4 当前 Gate 规则分析

### 1.1 v4 原始 Rule A/B/C 摘要

来自 `design/v4.md §4.4`（GATE 节点代码块）：

| 规则 | 通过条件 | 核心逻辑 |
|------|---------|---------|
| Rule A | 2 条来自不同工具/指标的支持证据，至少 1 条 strong | 独立证源 + 高强度 |
| Rule B | 1 条 strong 证据 + 所有 `required_verifications` 完成 | 全面核实 |
| Rule C | FPL 规则先验 > 0.8 + 最低 1 条 verification 通过 | 知识库辅助 |

### 1.2 对复合故障的适用性分析

**问题 1：单假设视角，无多假设协调**

```python
# v4 原始代码：只要有任何一个 confirmed 假设通过 Rule X，就返回 "pass"
for h in confirmed:
    if Rule_A(h) or Rule_B(h) or Rule_C(h):
        return "pass"  # ← 问题：h2 可能还在 active，就提前放行了
```

这意味着：如果 h1（cpu_fullload）通过了 Rule A，Gate 立即返回 pass，
即使 h2（mem_load）的证据还未收集完毕，FINALIZE 会只输出 h1，漏掉 h2。

**问题 2：未区分"复合故障"与"症状链"**

exp_008～010 的实验（cpu_fullload + mem_load 同时注入）中，h2（mem_load）的
`memory_usage_percent` 异常可能有两种来源：
- **复合故障**：独立注入的 mem_load，anon_memory 增加是主因
- **症状链**：CPU 满载导致 page cache 被驱逐，memory_usage 轻微上升（续发效应）

v4 没有提供区分这两种情况的机制。若误将症状链认定为复合故障，
则论文的 `diagnosis_type=composite_fault` 评估指标会虚高。

**问题 3：budget 耗尽时的"部分核实"输出缺失规范**

v4 的 `degrade` 分支仅在 `budget 耗尽` 时触发，对此时的输出格式（`partial` 类型报告）
没有任何规范。工程师无法确定此时应输出什么。

**问题 4：矛盾假设持有共享证据时的处理**

虽然在 formaltest 的注入场景中不常见，但理论上可能出现：
h1（cpu_fullload）和 h2（网络风暴导致 CPU 高）共享 cpu_usage 证据，
但根因解释互斥——v4 未定义如何处理。

---

## 2. 复合故障 Gate 规则扩展（核心设计）

### 2.1 总体框架：三阶段 Gate 决策

```
Stage A: 单假设级评估（对每个 Hypothesis 独立判断 confirmed/refuted）
    ↓
Stage B: 继发症状甄别（对所有 confirmed 假设做 cascade vs independent 判断）
    ↓  
Stage C: 全局门控决策（基于 Stage A/B 结果输出 pass/continue/degrade 和 diagnosis_type）
```

### 2.2 Stage A：单假设级评估（扩展 Rule A/B/C/D）

沿用 v4 的 Rule A/B/C，新增 Rule D（覆盖矛盾假设场景）：

```python
def evaluate_single_hypothesis(h: Hypothesis, evidence: list[Evidence]) -> str:
    """
    对单个 Hypothesis 判断其应升级为 confirmed 还是 refuted，或维持 active。
    返回：'confirmed' | 'refuted' | 'active'
    """
    h_evidence = [e for e in evidence if h.id in e.hypothesis_ids]
    supporting = [e for e in h_evidence if e.type == "supporting"]
    refuting   = [e for e in h_evidence if e.type == "refuting"]

    # ---- 反驳优先：强反驳证据直接 refuted ----
    strong_refuting = [e for e in refuting if e.strength == "strong"]
    if len(strong_refuting) >= 2:
        return "refuted"
    if len(strong_refuting) >= 1 and len([e for e in supporting if e.strength in ("strong", "medium")]) == 0:
        return "refuted"

    # ---- Rule A：2 条独立支持证据，至少 1 条 strong ----
    # "独立"定义：来自不同工具调用且针对不同指标（query_summary 组合哈希去重）
    unique_sources = set(
        (e.source_tool, e.query_summary.split(",")[0].strip())  # (tool, metric)
        for e in supporting
    )
    has_strong = any(e.strength == "strong" for e in supporting)
    if len(unique_sources) >= GATE_CONFIG["min_independent_sources"] and has_strong:  # 默认 2
        return "confirmed"

    # ---- Rule B：1 条 strong + 所有 required_verifications 完成 ----
    all_verifications_done = all(
        v.status in ("verified", "unverifiable")
        for v in h.required_verifications
    )
    # 注：unverifiable 的 verification 不贡献置信度，但允许 Rule B 通过（防止数据缺失死锁）
    # 若 unverifiable 超过一半，则 Rule B 的通过会在 gate_result.reason 中标注不确定性
    if has_strong and all_verifications_done:
        return "confirmed"

    # ---- Rule C：FPL 先验高 + 最低核实通过 ----
    has_any_verified = any(v.status == "verified" for v in h.required_verifications)
    if (h.prior_confidence >= GATE_CONFIG["fpl_confidence_threshold"]  # 默认 0.80
            and has_any_verified
            and has_strong):
        return "confirmed"

    # ---- Rule D（新增）：矛盾假设消解 ----
    # 当两个假设 h1、h2 共享超过半数的支持证据时，置信度高者胜出
    # （此规则由 Stage C 调用，此函数不处理，返回 active 即可）

    return "active"
```

**`unverifiable` 处理原则**：若某 `VerificationItem` 因工具返回空数据被标记为 `unverifiable`，
该步骤不贡献置信度，但允许 Rule B 通过。当超半数的 verifications 为 unverifiable 时，
最终报告的 `uncertainties` 字段须包含说明。

---

### 2.3 Stage B：继发症状甄别（Cascade vs Composite 判断）

#### 算法描述

对所有 `status=active` 或刚被评估为 `confirmed` 的非主假设对（h_primary, h_secondary），
判断 h_secondary 是否是 h_primary 的继发效应：

```python
def check_cascade(
    h_primary: Hypothesis,
    h_secondary: Hypothesis,
    focus_context: FocusContext,
    evidence: list[Evidence],
    metric_kb: dict
) -> Literal["independent", "cascade", "uncertain"]:
    """
    判断 h_secondary 是否是 h_primary 的继发症状。
    返回：
      "independent" → 两者是独立故障，应保留为 composite_fault
      "cascade"     → h_secondary 降级为 derived symptom
      "uncertain"   → 无法确定，保留 h_secondary 为 active（继续收集证据）
    """

    # ---- 条件 1：时序先后性 ----
    onset_primary   = get_metric_onset(h_primary,   focus_context)  # 取 TopMetric.t_onset
    onset_secondary = get_metric_onset(h_secondary, focus_context)

    lag_sec = (onset_secondary - onset_primary).total_seconds()

    # 如果两者几乎同时出现（lag < CASCADE_LAG_THRESHOLD），几乎确定是独立注入
    if lag_sec < GATE_CONFIG["cascade_lag_threshold_sec"]:  # 默认 60 秒
        return "independent"

    # 如果 h_secondary 先出现（lag < 0），不可能是 h_primary 的继发，是独立故障
    if lag_sec < 0:
        return "independent"

    # ---- 条件 2：因果链检查（来自 Metric KB downstream_effects） ----
    primary_metric  = h_primary.fault_type  # 如 "cpu_fullload"
    secondary_subsystem = h_secondary.subsystem

    # 检查 h_primary 的核心指标是否有已知的 downstream_effects 涉及 h_secondary 的子系统
    primary_top_metric = get_leading_metric(h_primary, focus_context)  # 返回 metric name
    downstream_effects = metric_kb.get(primary_top_metric, {}).get("downstream_effects", [])
    known_cascade_subsystems = {
        eff["metric"].split("_")[0]  # 取子系统前缀（约定）
        for eff in downstream_effects
    }

    if secondary_subsystem not in known_cascade_subsystems:
        # h_primary 已知不会导致 h_secondary 子系统的异常 → 独立故障
        return "independent"

    # ---- 条件 3：异常幅度是否超过"继发上限" ----
    # 继发效应通常很轻微（CPU 满载导致的 cache eviction 不会使 memory_usage 上升超过 20%）
    secondary_evidence = [
        e for e in evidence
        if h_secondary.id in e.hypothesis_ids and e.type == "supporting"
    ]
    if not secondary_evidence:
        return "uncertain"

    max_secondary_magnitude = max(
        e.raw_stats.get("p95", e.raw_stats.get("value", 0))
        for e in secondary_evidence
    )
    secondary_metric_name = get_leading_metric(h_secondary, focus_context)
    # 从 Metric KB 获取该指标的 strong threshold（超过此值说明异常幅度"太大"，不可能是继发）
    strong_threshold = metric_kb.get(secondary_metric_name, {}).get(
        "strength_thresholds", {}
    ).get("strong", {}).get("condition", "")

    independent_threshold_value = parse_threshold(strong_threshold)  # 解析 "> 93" → 93.0
    if independent_threshold_value and max_secondary_magnitude >= independent_threshold_value:
        # 继发效应不可能达到 strong 阈值 → 判定为独立注入
        return "independent"

    # 也检查 anon_memory_percent 增幅（复合故障的 mem_load_ram 特征）
    # 这是 kb_schema.md fpl_007 中的 composite_discrimination 规则
    if (h_secondary.fault_type in ("mem_load_ram", "mem_load_buffer", "mem_load_stack")
            and h_primary.fault_type == "cpu_fullload"):
        anon_increase = get_stat_from_evidence(
            evidence, h_secondary, "anon_memory_percent", "p95"
        )
        baseline_anon = focus_context.top_metrics  # 需从 baseline profile 获取
        if anon_increase is not None:
            # anon_memory 增幅 > 20 percentage points → 独立注入的 mem_load（不是 CPU 引起的 cache eviction）
            if anon_increase > GATE_CONFIG["anon_memory_independence_threshold"]:  # 默认 20%
                return "independent"

    return "cascade"
```

#### 无法确定时的处理

若 `check_cascade` 返回 `"uncertain"`，h_secondary 维持 `active` 状态，继续分配工具调用预算收集更多证据。
若 budget 耗尽时仍为 `uncertain`，将其输出为 `uncertainties` 中的一条记录，不影响已 confirmed 的 h_primary 结论。

---

### 2.4 Stage C：全局 Gate 决策函数（完整伪代码）

```python
def gate(state: AgentState, metric_kb: dict) -> dict:
    """
    综合 Stage A（单假设评估）和 Stage B（继发症状甄别），
    输出全局 gate_result。

    返回 gate_result 字典：
    {
        "decision": "pass" | "continue" | "degrade",
        "reason": str,
        "triggered_rule": "Rule_A" | "Rule_B" | "Rule_C" | None,
        "active_hypothesis_ids": list[str],
        "confirmed_hypothesis_ids": list[str],
        "diagnosis_type": "single_fault" | "composite_fault" | "partial" | "inconclusive"
    }
    """
    hypotheses = state["hypotheses"]
    evidence   = state["evidence"]
    budget     = state["budget"]

    # ====================================================================
    # Stage A：对每个 active 假设执行单假设评估
    # ====================================================================
    for h in hypotheses:
        if h.status != "active":
            continue
        new_status = evaluate_single_hypothesis(h, evidence)
        if new_status != "active":
            h.status = new_status

    # ====================================================================
    # Stage B：对所有 confirmed 假设做继发症状甄别
    # 仅有 ≥ 2 个 confirmed 假设时才需要做此检查
    # ====================================================================
    confirmed_list = [h for h in hypotheses if h.status == "confirmed"]
    active_list    = [h for h in hypotheses if h.status == "active"]
    refuted_list   = [h for h in hypotheses if h.status == "refuted"]

    if len(confirmed_list) >= 2:
        for i, h_primary in enumerate(confirmed_list):
            for h_secondary in confirmed_list[i+1:]:
                if h_secondary.status != "confirmed":
                    continue  # 可能被前轮降级
                # 约定：onset 更早的假设为 primary（若同时，subsystem_score 高者为 primary）
                primary, secondary = _order_by_onset(h_primary, h_secondary, state["focus_context"])
                result = check_cascade(primary, secondary, state["focus_context"], evidence, metric_kb)
                if result == "cascade":
                    secondary.status = "derived"  # 降级为继发症状
                # result == "independent" → 两者均保留 confirmed
                # result == "uncertain"   → 不做状态变更，在 uncertainties 中记录

    # ====================================================================
    # Stage C：全局决策
    # ====================================================================
    confirmed_independent = [h for h in hypotheses if h.status == "confirmed"]
    still_active = [h for h in hypotheses if h.status == "active"]
    budget_exhausted = budget["tool_calls_used"] >= budget["tool_calls_limit"]

    # ---- 情况 1：所有非 refuted/derived 假设都已收敛 ----
    if len(still_active) == 0 and len(confirmed_independent) > 0:
        diagnosis_type = _determine_diagnosis_type(confirmed_independent, hypotheses)
        return {
            "decision": "pass",
            "reason": f"{len(confirmed_independent)} independent hypothesis(es) confirmed, 0 still active",
            "triggered_rule": _get_triggered_rule(confirmed_independent, evidence),
            "active_hypothesis_ids": [],
            "confirmed_hypothesis_ids": [h.id for h in confirmed_independent],
            "diagnosis_type": diagnosis_type
        }

    # ---- 情况 2：有已 confirmed 一切的假设，但还有 active 假设，且 budget 未耗尽 ----
    if len(confirmed_independent) > 0 and len(still_active) > 0 and not budget_exhausted:
        # 检查剩余 budget 是否足以继续处理 active 假设
        remaining = budget["tool_calls_limit"] - budget["tool_calls_used"]
        if remaining >= GATE_CONFIG["min_budget_per_hypothesis"] * len(still_active):
            return {
                "decision": "continue",
                "reason": (
                    f"{len(confirmed_independent)} confirmed, {len(still_active)} still active; "
                    f"budget remaining ({remaining}) sufficient to continue"
                ),
                "triggered_rule": None,
                "active_hypothesis_ids": [h.id for h in still_active],
                "confirmed_hypothesis_ids": [h.id for h in confirmed_independent],
                "diagnosis_type": "partial"  # 临时标记，按最终结果更新
            }
        else:
            # budget 不足以完整处理所有 active 假设 → 主动降级（不要浪费仅剩的 1-2 次调用）
            return {
                "decision": "degrade",
                "reason": (
                    f"budget insufficient ({remaining} calls left) to verify "
                    f"{len(still_active)} active hypothesis(es); "
                    f"partial result with {len(confirmed_independent)} confirmed"
                ),
                "triggered_rule": None,
                "active_hypothesis_ids": [h.id for h in still_active],
                "confirmed_hypothesis_ids": [h.id for h in confirmed_independent],
                "diagnosis_type": "partial"
            }

    # ---- 情况 3：budget 耗尽 ----
    if budget_exhausted:
        if len(confirmed_independent) > 0:
            diagnosis_type = "partial" if len(still_active) > 0 else (
                _determine_diagnosis_type(confirmed_independent, hypotheses)
            )
            return {
                "decision": "degrade",
                "reason": f"budget exhausted; {len(confirmed_independent)} confirmed",
                "triggered_rule": _get_triggered_rule(confirmed_independent, evidence),
                "active_hypothesis_ids": [h.id for h in still_active],
                "confirmed_hypothesis_ids": [h.id for h in confirmed_independent],
                "diagnosis_type": diagnosis_type
            }
        else:
            return {
                "decision": "degrade",
                "reason": "budget exhausted; no hypothesis confirmed",
                "triggered_rule": None,
                "active_hypothesis_ids": [h.id for h in still_active],
                "confirmed_hypothesis_ids": [],
                "diagnosis_type": "inconclusive"
            }

    # ---- 情况 4：所有假设均 refuted，没有 confirmed ----
    if len(confirmed_independent) == 0 and len(still_active) == 0:
        return {
            "decision": "degrade",
            "reason": "all hypotheses refuted; diagnosis inconclusive",
            "triggered_rule": None,
            "active_hypothesis_ids": [],
            "confirmed_hypothesis_ids": [],
            "diagnosis_type": "inconclusive"
        }

    # ---- 默认：继续循环 ----
    return {
        "decision": "continue",
        "reason": f"{len(still_active)} hypothesis(es) still active, budget available",
        "triggered_rule": None,
        "active_hypothesis_ids": [h.id for h in still_active],
        "confirmed_hypothesis_ids": [h.id for h in confirmed_independent],
        "diagnosis_type": "partial"  # 临时
    }


def _determine_diagnosis_type(confirmed: list[Hypothesis], all_hypotheses: list[Hypothesis]) -> str:
    """
    根据 confirmed 假设数量和 derived/partial 状态确定 diagnosis_type。
    """
    n_confirmed = len(confirmed)
    n_active = sum(1 for h in all_hypotheses if h.status == "active")

    if n_active > 0:
        return "partial"
    elif n_confirmed == 1:
        return "single_fault"
    elif n_confirmed >= 2:
        return "composite_fault"
    else:
        return "inconclusive"


def _get_triggered_rule(confirmed: list[Hypothesis], evidence: list[Evidence]) -> Optional[str]:
    """返回触发 PASS 的规则标识（取置信度最高的 confirmed 假设的触发规则）。"""
    # 实现逻辑：对 confirmed 列表中 current_confidence 最高的假设，回溯其证据特征判断触发规则
    if not confirmed:
        return None
    best = max(confirmed, key=lambda h: h.current_confidence)
    h_evidence = [e for e in evidence if best.id in e.hypothesis_ids and e.type == "supporting"]

    unique_sources = len(set(
        (e.source_tool, e.query_summary.split(",")[0].strip())
        for e in h_evidence
    ))
    has_strong = any(e.strength == "strong" for e in h_evidence)
    all_verified = all(v.status in ("verified", "unverifiable") for v in best.required_verifications)
    has_any_verified = any(v.status == "verified" for v in best.required_verifications)

    if unique_sources >= 2 and has_strong:
        return "Rule_A"
    elif has_strong and all_verified:
        return "Rule_B"
    elif best.prior_confidence >= GATE_CONFIG["fpl_confidence_threshold"] and has_any_verified and has_strong:
        return "Rule_C"
    else:
        return "Rule_D"  # 矛盾假设消解（fallback）
```

---

### 2.5 四种 `diagnosis_type` 的输出规范

| `diagnosis_type` | 条件 | `root_causes` | `uncertainties` | 对应 DiagnosisReport |
|------------------|------|---------------|-----------------|---------------------|
| `single_fault` | 恰好 1 confirmed，0 active，0 derived | 1 条 | 空或轻微 | 完整报告 |
| `composite_fault` | ≥ 2 confirmed independent，0 active | ≥ 2 条 | 空或轻微 | 完整报告 |
| `partial` | ≥ 1 confirmed，≥ 1 active（budget 耗尽） | 已 confirmed 的条目 | "h{x} could not be verified due to budget exhaustion" | 降级报告，标注 partial |
| `inconclusive` | 0 confirmed | 空列表 | "Top 2 candidate: h1 (cpu_fullload, conf=0.45), h2 (mem_load, conf=0.31)" | 降级报告，列出 top 候选假设 |

**注**：`partial` 和 `inconclusive` 的报告仍然有价值，应输出所有已观察的证据摘要
和置信度最高的未确认假设，帮助运维人员决策。

---

## 3. 复合故障四种特殊场景处理逻辑

### 3.1 多假设独立通过的 FINALIZE 触发

**场景**：h1（cpu_fullload）和 h2（network_loss）都满足 Rule A。
对应实验：`exp_011_cpu_fullload_network_loss`。

**处理逻辑**：Stage C 情况 1 的代码路径：
1. h1 通过 Stage A → `status = confirmed`
2. h2 通过 Stage A → `status = confirmed`
3. Stage B：`check_cascade(h1, h2)` → 返回 `independent`（CPU 不会导致网络丢包）
4. Stage C：`still_active=0, confirmed=2` → `decision=pass, diagnosis_type=composite_fault`
5. FINALIZE 节点接收 `decision=pass`，生成包含 h1 和 h2 的 `root_causes` 列表

**关键约束**：不允许在 h1 通过后立即触发 FINALIZE，必须等待 Stage A 对所有 active 假设评估完毕后才做全局决策。即 Gate 函数每次 ReAct 迭代后都被调用，但 `pass` 只在 Stage C 统一决策时输出。

---

### 3.2 关联性判断：复合故障 vs 症状链

**场景**：h1（cpu_fullload）confirmed，h2（mem_load）active，
但 memory_usage 仅上升 12%（幅度低，可能是 CPU 驱逐 page cache 的继发效应）。

**判断流程**（代码路径：Stage B `check_cascade`）：

```
Step 1: 时序 → onset(cpu) = 07:16:15, onset(memory) = 07:16:18
        lag = 3s < 60s（cascade_lag_threshold）→ 返回 "independent"
```

**结论**：即使幅度轻微，3 秒的时序差（两者几乎同时出现）强烈指向独立注入，直接判定 `independent`。这与 formaltest 实验的实际注入逻辑一致（ChaosBlade 同时注入两个故障）。

对比场景：若 onset(memory) = 07:19:00（cpu 出现 3 分钟后），则进入条件 2/3 的精细判断。

---

### 3.3 部分通过（h1 confirmed，h2 active，budget 耗尽）

**场景**：h1（cpu_fullload）confirmed，h2（disk_burn）还剩 1 条 verification 未完成，
此时 budget 已耗尽（12/12 tool calls used）。

**处理路径**：Stage C 情况 3：
```python
decision = "degrade"
diagnosis_type = "partial"  # 不是 single_fault，也不是 composite_fault
```

**FINALIZE 的输出**：
```json
{
    "diagnosis_type": "partial",
    "root_causes": [
        {"cause": "CPU fullload confirmed", "fault_type": "cpu_fullload", "confidence": 0.95}
    ],
    "uncertainties": [
        "h2 (disk_burn) could not be fully verified: verification 'confirm disk_io_usage_percent > 90%' pending (tool_calls budget exhausted)",
        "Recommendation: manually check disk IO on blade01 with: iostat -xz 1 10"
    ]
}
```

---

### 3.4 矛盾假设持有共享证据

**场景**（理论场景）：h1 和 h2 都以 cpu_usage_percent 的 e1 作为支持证据，
但 h1="cpu_fullload（进程失控）" 和 h2="cpu_fullload（内存换页引发高 iowait 间接导致）" 互相竞争解释。

**处理逻辑**（Rule D）：
```python
def resolve_contradicting_hypotheses(h1: Hypothesis, h2: Hypothesis, evidence: list[Evidence]):
    """
    当两个假设共享 > 50% 的支持证据，且两者都处于 active 状态时，
    保留 current_confidence 更高者，降级另一个为 refuted。
    """
    h1_supporting = set(h1.supporting_evidence_ids)
    h2_supporting = set(h2.supporting_evidence_ids)
    shared = h1_supporting & h2_supporting
    max_shared_ratio = max(
        len(shared) / max(len(h1_supporting), 1),
        len(shared) / max(len(h2_supporting), 1)
    )
    if max_shared_ratio > GATE_CONFIG["evidence_sharing_conflict_threshold"]:  # 默认 0.5
        # 保留置信度高者，降级另一个
        if h1.current_confidence >= h2.current_confidence:
            h2.status = "refuted"
        else:
            h1.status = "refuted"
```

**注意**：此场景在 formaltest 的注入实验中不会发生（故障类型是互斥的），
Rule D 主要用于工程鲁棒性保护，不影响评估指标。

---

## 4. 阈值参数化表格

所有 Gate 函数中的阈值统一存储在 `GATE_CONFIG` 字典中（对应配置文件 `config/gate_config.yaml`），
以便消融实验通过修改配置而非修改代码来调整阈值。

| 参数名 | 默认值 | 类型 | 说明 | 合理取值范围 |
|--------|--------|------|------|-------------|
| `min_independent_sources` | 2 | int | Rule A：最少独立证源数量 | 1-4 |
| `fpl_confidence_threshold` | 0.80 | float | Rule C：FPL 先验置信度门槛 | 0.6-0.95 |
| `min_budget_per_hypothesis` | 3 | int | 对每个 active 假设保留的最小剩余 budget | 2-5 |
| `cascade_lag_threshold_sec` | 60 | int (秒) | 两个故障 onset 差 < 此值认定为同时注入（独立） | 30-180 |
| `anon_memory_independence_threshold` | 20.0 | float (%) | anon_memory 增幅超此值时认定 mem_load 独立 | 10.0-30.0 |
| `evidence_sharing_conflict_threshold` | 0.50 | float | Rule D：共享证据比例超过此值触发矛盾消解 | 0.3-0.7 |
| `max_unverifiable_ratio` | 0.50 | float | Rule B 中 unverifiable 比例超过此值时报告不确定性 | 0.3-0.8 |
| `refuted_strong_count` | 2 | int | 多少条 strong 反驳证据触发 refuted | 1-3 |
| `confidence_strong_bonus` | 0.35 | float | strong 证据的置信度增量 | 0.2-0.5 |
| `confidence_medium_bonus` | 0.20 | float | medium 证据的置信度增量 | 0.1-0.3 |
| `confidence_weak_bonus` | 0.10 | float | weak 证据的置信度增量 | 0.05-0.15 |
| `default_prior_no_fpl` | 0.30 | float | 未命中 FPL 时的默认先验置信度 | 0.1-0.5 |
| `leading_subsystem_bonus` | 0.10 | float | leading_subsystem 匹配时的额外先验加成 | 0-0.2 |

**YAML 配置文件格式** (`config/gate_config.yaml`)：

```yaml
# Evidence Gating 参数配置
# 修改此文件即可进行消融实验，无需改动 agent/gate.py

gate:
  min_independent_sources: 2
  fpl_confidence_threshold: 0.80
  min_budget_per_hypothesis: 3
  cascade_lag_threshold_sec: 60
  anon_memory_independence_threshold: 20.0
  evidence_sharing_conflict_threshold: 0.50
  max_unverifiable_ratio: 0.50
  refuted_strong_count: 2

confidence_update:
  strong_bonus: 0.35
  medium_bonus: 0.20
  weak_bonus: 0.10
  default_prior_no_fpl: 0.30
  leading_subsystem_bonus: 0.10
```

---

## 5. 论文消融实验接口

### 5.1 消融 Ablation-B：无 Evidence Gating

在论文中，`Ablation-B` 消融 Evidence Gating：**移除 GATE 节点，允许 THINK 节点在任意时机输出 conclude 即触发 FINALIZE**。

实现方式：
```python
# config/gate_config.yaml 中添加覆盖字段（不修改 gate.py）：
ablation_b_no_gating: true  # true 时 gate() 直接返回 {"decision": "pass", ...}
```

**预期效果**：Hit@1 下降（LLM 会在证据不足时过早结论），
False Positive 上升（尤其是 inconclusive 场景被错误诊断为 single_fault）。

### 5.2 消融参数扫描

为了展示 Evidence Gating 的鲁棒性，论文中可以通过以下参数扫描验证阈值的合理范围：

| 消融维度 | 参数 | 扫描范围 | 观察指标 |
|----------|------|---------|---------|
| 门槛宽松化 | `min_independent_sources` | 1, 2, 3 | Hit@1 vs. avg_tool_calls |
| FPL 依赖程度 | `fpl_confidence_threshold` | 0.6, 0.7, 0.8, 0.9 | 有无 KB 的性能差（Ablation-C 交叉） |
| 级联判断敏感度 | `cascade_lag_threshold_sec` | 30, 60, 120 | composite_fault 召回率 vs. 误判率 |
| 预算利用效率 | `min_budget_per_hypothesis` | 2, 3, 4, 5 | avg_tool_calls vs. partial rate |

### 5.3 Evidence Gating 对应的论文 Novelty 陈述

**Novelty 1：确定性规则替代 LLM 自我报告置信度**

> "Unlike prior agent-based diagnosis systems where the LLM judges its own confidence,
> our Evidence Gating computes evidence strength from deterministic rules (Metric KB thresholds)
> and only allows FINALIZE when quantitative criteria are met. This eliminates 'hallucinated confidence'
> — a well-known failure mode in LLM-based reasoning."

消融支撑：Ablation-B（无 GATE）与 full system 对比，在低证据场景下的 precision 差异。

**Novelty 2：复合故障的多假设并行跟踪 + Cascade 甄别**

> "We extend single-hypothesis gating to multi-hypothesis coordination:
> each root cause candidate is independently confirmed before FINALIZE,
> and we introduce a cascade discrimination criterion (temporal onset gap + anomaly magnitude)
> to distinguish co-injected composite faults from symptom propagation chains."

消融支撑：在 compositetest 数据集上，去掉 Stage B（cascade 甄别）后的 composite_recall 下降。

**Novelty 3：`partial` 诊断类型的明确建模**

> "Rather than forcing a binary correct/incorrect output, our system explicitly models
> diagnostic incompleteness through the 'partial' diagnosis type, providing actionable
> partial results with uncertainty annotations when budget is exhausted."

评估指标：partial 场景下，已 confirmed 部分的 precision（期望接近 full-evidence 场景）。

---

## 6. Gate 节点在 LangGraph 中的集成方式

```python
# agent/diagnosis.py 中的节点路由（伪代码）

from langgraph.graph import StateGraph, END

def route_after_gate(state: AgentState) -> str:
    """LangGraph 条件路由函数：根据 gate_result.decision 决定下一节点。"""
    decision = state["gate_result"]["decision"]
    if decision == "pass":
        return "FINALIZE"
    elif decision == "continue":
        return "THINK"
    elif decision == "degrade":
        return "FINALIZE"  # FINALIZE 节点根据 diagnosis_type 自动调整输出格式
    else:
        raise ValueError(f"Unknown gate decision: {decision}")

# 图结构：
# HYPOTHESIZE → THINK → ACT → OBSERVE → GATE → route_after_gate
#                 ↑_________________________________|（continue）
#                                                   ↓（pass/degrade）
#                                               FINALIZE → REFLECT → END
```

Gate 节点本身是**纯 Python 函数**（无 LLM 调用），在 LangGraph 中注册为普通节点：

```python
builder = StateGraph(AgentState)
builder.add_node("GATE", gate_node)  # gate_node 包装 gate() 函数
builder.add_conditional_edges("GATE", route_after_gate, {
    "THINK": "THINK",
    "FINALIZE": "FINALIZE"
})
```
