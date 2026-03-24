#!/usr/bin/env python3
"""Patch remaining GATE references in Part X–XIII of v6.md."""

FILEPATH = '/home/quantum/Eurosys-blade-lab/design/v6.md'

with open(FILEPATH, 'r', encoding='utf-8') as f:
    text = f.read()

replacements = [
    # Part IX
    ('"GATE 到底做了多少额外工作"', '"Audit Agent 到底做了多少额外工作"'),

    # Part X - ablation
    ('| Abl-B | 无 GATE | THINK 输出 conclude 后直接 FINALIZE | Adversarial Evidence Gating |',
     '| Abl-B | 无 Audit Agent | Diagnosis Agent conclude 后直接 FINALIZE，跳过 Orchestrator 审查 | Adversarial Evidence Gating |'),

    ('| Sup-D | GATE 降级为单次 LLM call | 无工具、无循环，仅一次审查 | 验证 GATE 主动调查的贡献 |',
     '| Sup-D | Audit Agent 降级为单次 LLM call | 无工具、无循环，仅一次 LLM 审查 | 验证 Audit Agent 主动调查能力的贡献 |'),

    ('| Sup-F | 禁用 hint 协议 | GATE 只返回 decision+reason，不传 hint | 验证方案 C hint 机制的价值 |',
     '| Sup-F | 禁用 hint 协议 | Audit Agent 只返回 decision+reason，不传 hint | 验证方案 C hint 机制的价值 |'),

    ('| Sup-G | GATE 降级为 v5 式确定性规则 | Python Rule A/B/C/D | 直接比较 Adversarial LLM GATE vs Rule-based GATE |',
     '| Sup-G | Audit Agent 降级为 v5 式确定性规则 | Python Rule A/B/C/D | 直接比较 Adversarial LLM Audit vs 规则审查 |'),

    # Part XI - failure handling
    ('| GATE 工具超时 | GATE 可选择 `continue` 或 `degrade`，不得凭空补完证据 |',
     '| Audit Agent 工具超时 | Audit Agent 可选择 `continue` 或 `degrade`，不得凭空补完证据 |'),

    ('| GATE 输出不符合 Schema | 最多重试 2 次，仍失败则退化为 `degrade` |',
     '| Audit Agent 输出不符合 Schema | 最多重试 2 次，仍失败则退化为 `degrade` |'),

    # Part XII - implementation
    ('| Phase C | Diagnosis Agent：主 ReAct + GATE Sub-Agent + tools | `agent/diagnosis.py`, `agent/tools/`, `agent/gate.py` |',
     '| Phase C | Diagnosis Agent + Audit Agent + Orchestrator | `agent/diagnosis.py`, `agent/audit.py`, `agent/orchestrator.py`, `agent/tools/` |'),

    ('2. 再加入最小版 GATE（单轮、零工具）验证路由',
     '2. 再加入最小 Audit Agent（单轮、零工具）+ Orchestrator 验证路由'),

    ('3. 最后扩展成完整 GATE Sub-Agent（含工具调用与方案 C 的 hint 协议）',
     '3. 最后扩展成完整 Audit Agent（含工具调用与方案 C 的 hint 协议）'),

    # Part XIII - control flow narrative
    ('5. GATE 先做零工具逻辑审查',
     '5. Orchestrator 将 ConclusionProposal 提交给 Audit Agent'),

    ('8. 若当前假设空间失效，GATE 返回 `rehypothesize`',
     '8. 若当前假设空间失效，Audit Agent 返回 `rehypothesize`'),

    ('9. 若找不到漏洞，GATE 返回 `pass`',
     '9. 若找不到漏洞，Audit Agent 返回 `pass`'),

    # Fix step 6 if it mentions GATE
    ('6. 若发现缺口，返回 `continue + hint`',
     '6. Audit Agent 先做零工具逻辑审查；若发现缺口，返回 `continue + hint`'),
]

for old, new in replacements:
    if old in text:
        text = text.replace(old, new)
        print(f"OK: {old[:60]}...")
    else:
        print(f"SKIP (not found): {old[:60]}...")

with open(FILEPATH, 'w', encoding='utf-8') as f:
    f.write(text)

# Verify
import re
remaining = [m for m in re.finditer(r'\bGATE\b(?!_THINK|_ACT|_OBS)', text)]
print(f"\nRemaining standalone GATE references: {len(remaining)}")
for m in remaining:
    line_no = text[:m.start()].count('\n') + 1
    ctx = text[max(0,m.start()-40):m.end()+40].replace('\n', ' ')
    print(f"  Line {line_no}: ...{ctx}...")
