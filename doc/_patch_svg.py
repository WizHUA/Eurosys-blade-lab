#!/usr/bin/env python3
"""Replace the §2 architecture SVG in v6.html with a new dual-Agent layout."""

FILEPATH = '/home/quantum/Eurosys-blade-lab/doc/agent_design_report_v6.html'

with open(FILEPATH, 'r', encoding='utf-8') as f:
    html = f.read()

# Find the first <svg within <section id="s2">
s2_start = html.index('<section id="s2">')
svg_start = html.index('<svg width="800"', s2_start)
svg_end = html.index('</svg>', svg_start) + len('</svg>')

old_svg = html[svg_start:svg_end]

new_svg = '''<svg width="820" height="680" viewBox="0 0 820 680" xmlns="http://www.w3.org/2000/svg" style="font-family:-apple-system,sans-serif;display:block">
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="#64748b"/></marker>
    <marker id="ahp" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="#7c3aed"/></marker>
    <marker id="aho" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="#f97316"/></marker>
    <marker id="ahg" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="#059669"/></marker>
    <marker id="ahr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="#dc2626"/></marker>
  </defs>
  <rect width="820" height="680" fill="#f8fafc"/>

  <!-- ═══════ Input ═══════ -->
  <rect x="640" y="8" width="165" height="56" rx="7" fill="#fff" stroke="#94a3b8" stroke-width="1.2"/>
  <text x="723" y="26" text-anchor="middle" font-size="10.5" fill="#475569" font-weight="700">输入数据</text>
  <text x="723" y="43" text-anchor="middle" font-size="11" fill="#475569">metrics.csv</text>
  <text x="723" y="57" text-anchor="middle" font-size="11" fill="#475569">jobinfo.csv</text>

  <!-- ═══════ Stage 0: KB ═══════ -->
  <rect x="10" y="8" width="618" height="56" rx="7" fill="#dbeafe" stroke="#3b82f6" stroke-width="1.5"/>
  <text x="22" y="24" font-size="10.5" fill="#1e40af" font-weight="700">Offline Knowledge Base — 离线构建</text>
  <rect x="22" y="30" width="185" height="26" rx="5" fill="#3b82f6"/>
  <text x="114" y="48" text-anchor="middle" fill="white" font-size="11">Metric KB（YAML + 向量索引）</text>
  <rect x="220" y="30" width="195" height="26" rx="5" fill="#3b82f6"/>
  <text x="317" y="48" text-anchor="middle" fill="white" font-size="11">Fault Pattern Library（初始为空）</text>

  <!-- KB → Triage -->
  <line x1="317" y1="64" x2="317" y2="82" stroke="#64748b" stroke-width="1.5" marker-end="url(#ah)"/>
  <line x1="640" y1="58" x2="618" y2="92" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="4,3" marker-end="url(#ah)"/>

  <!-- ═══════ Stage 1: Triage ═══════ -->
  <rect x="10" y="82" width="618" height="60" rx="7" fill="#d1fae5" stroke="#059669" stroke-width="1.5"/>
  <text x="22" y="98" font-size="10.5" fill="#064e3b" font-weight="700">Stage 1  Triage — 确定性分诊，纯 Python，零 LLM Token</text>
  <rect x="22" y="104" width="148" height="26" rx="5" fill="#059669"/>
  <text x="96" y="122" text-anchor="middle" fill="white" font-size="10.5">Step1 异常指标筛选</text>
  <line x1="170" y1="117" x2="186" y2="117" stroke="#064e3b" stroke-width="1.5" marker-end="url(#ahg)"/>
  <rect x="186" y="104" width="148" height="26" rx="5" fill="#059669"/>
  <text x="260" y="122" text-anchor="middle" fill="white" font-size="10.5">Step2 时序因果排序</text>
  <line x1="334" y1="117" x2="420" y2="117" stroke="#064e3b" stroke-width="1.5" marker-end="url(#ahg)"/>
  <rect x="420" y="104" width="170" height="26" rx="5" fill="#065f46"/>
  <text x="505" y="122" text-anchor="middle" fill="white" font-size="11" font-weight="700">→ FocusContext</text>

  <!-- Triage → Stage 2 -->
  <line x1="317" y1="142" x2="317" y2="168" stroke="#64748b" stroke-width="1.5" marker-end="url(#ah)"/>
  <text x="330" y="160" font-size="9.5" fill="#64748b">FocusContext + causal_order</text>

  <!-- ═══════════════════════════════════════════════════════════ -->
  <!-- Stage 2: Orchestrator outer frame (dark border, subtle bg) -->
  <!-- ═══════════════════════════════════════════════════════════ -->
  <rect x="6" y="168" width="808" height="388" rx="10" fill="#f5f5ff" stroke="#334155" stroke-width="2.5"/>
  <text x="20" y="186" font-size="12" fill="#1e293b" font-weight="800">Stage 2  Orchestrator（确定性协调）</text>
  <text x="280" y="186" font-size="10" fill="#64748b">{ Diagnosis Agent ↔ Audit Agent }</text>

  <!-- ─── Diagnosis Agent box ─── -->
  <rect x="18" y="196" width="525" height="128" rx="7" fill="#ede9fe" stroke="#7c3aed" stroke-width="1.5"/>
  <text x="30" y="212" font-size="11" fill="#4c1d95" font-weight="700">Diagnosis Agent（diagnosis_graph · 独立 DiagnosisState）</text>

  <!-- HYPOTHESIZE -->
  <rect x="30" y="220" width="120" height="28" rx="5" fill="#7c3aed"/>
  <text x="90" y="239" text-anchor="middle" fill="white" font-size="10.5" font-weight="700">HYPOTHESIZE</text>
  <line x1="150" y1="234" x2="168" y2="234" stroke="#7c3aed" stroke-width="1.5" marker-end="url(#ahp)"/>

  <!-- ReAct Loop -->
  <rect x="168" y="218" width="290" height="64" rx="6" fill="rgba(109,40,217,.07)" stroke="#7c3aed" stroke-width="1" stroke-dasharray="5,3"/>
  <text x="180" y="232" font-size="9" fill="#6d28d9" font-weight="700">Main ReAct Loop</text>
  <rect x="178" y="238" width="68" height="24" rx="4" fill="#6d28d9"/>
  <text x="212" y="255" text-anchor="middle" fill="white" font-size="10">THINK</text>
  <line x1="246" y1="250" x2="260" y2="250" stroke="#c4b5fd" stroke-width="1.3" marker-end="url(#ahp)"/>
  <rect x="260" y="238" width="60" height="24" rx="4" fill="#6d28d9"/>
  <text x="290" y="255" text-anchor="middle" fill="white" font-size="10">ACT</text>
  <line x1="320" y1="250" x2="338" y2="250" stroke="#c4b5fd" stroke-width="1.3" marker-end="url(#ahp)"/>
  <rect x="338" y="238" width="72" height="24" rx="4" fill="#6d28d9"/>
  <text x="374" y="255" text-anchor="middle" fill="white" font-size="10">OBSERVE</text>
  <!-- iterate arc -->
  <path d="M410 262 Q412 286 290 286 Q178 286 178 262" fill="none" stroke="#c4b5fd" stroke-width="1.2" stroke-dasharray="4,3" marker-end="url(#ahp)"/>
  <text x="290" y="298" text-anchor="middle" fill="#6d28d9" font-size="9">iterate</text>

  <!-- conclude output label -->
  <rect x="470" y="234" width="62" height="20" rx="4" fill="#4c1d95"/>
  <text x="501" y="249" text-anchor="middle" fill="white" font-size="9">conclude</text>

  <!-- Diagnosis Agent → ConclusionProposal -->
  <line x1="501" y1="254" x2="501" y2="310" stroke="#4c1d95" stroke-width="1.5" stroke-dasharray="3,2" marker-end="url(#ahp)"/>

  <!-- ─── Diagnosis Tools (right side) ─── -->
  <rect x="555" y="196" width="145" height="128" rx="7" fill="#fff7ed" stroke="#f97316" stroke-width="1.5"/>
  <text x="628" y="213" text-anchor="middle" font-size="10.5" fill="#7c2d12" font-weight="700">工具（Diagnosis Agent）</text>
  <rect x="565" y="220" width="125" height="24" rx="4" fill="#f97316"/>
  <text x="628" y="237" text-anchor="middle" fill="white" font-size="10.5">MetricQueryTool</text>
  <rect x="565" y="250" width="125" height="24" rx="4" fill="#f97316"/>
  <text x="628" y="267" text-anchor="middle" fill="white" font-size="10.5">KBRetrievalTool</text>
  <rect x="565" y="280" width="125" height="24" rx="4" fill="#f97316"/>
  <text x="628" y="297" text-anchor="middle" fill="white" font-size="10.5">DataAnalysisTool</text>
  <!-- ACT → tools dashed -->
  <line x1="543" y1="250" x2="555" y2="250" stroke="#f97316" stroke-width="1.2" stroke-dasharray="4,2"/>

  <!-- ═══ Orchestrator 连接层 ═══ -->
  <rect x="110" y="328" width="240" height="26" rx="5" fill="#334155"/>
  <text x="230" y="345" text-anchor="middle" fill="white" font-size="11" font-weight="700">ConclusionProposal</text>

  <rect x="360" y="328" width="158" height="26" rx="5" fill="#64748b"/>
  <text x="439" y="345" text-anchor="middle" fill="white" font-size="10.5">Orchestrator 强制提交</text>

  <!-- ConclusionProposal arrow down to Audit Agent -->
  <line x1="300" y1="354" x2="300" y2="374" stroke="#334155" stroke-width="1.8" marker-end="url(#ah)"/>

  <!-- ── 信息隔离虚线 ── -->
  <line x1="18" y1="362" x2="700" y2="362" stroke="#dc2626" stroke-width="1" stroke-dasharray="8,4"/>
  <text x="714" y="366" font-size="9" fill="#dc2626" font-weight="700">信息隔离边界</text>

  <!-- ─── Audit Agent box ─── -->
  <rect x="18" y="374" width="525" height="82" rx="7" fill="rgba(220,38,38,.06)" stroke="#dc2626" stroke-width="1.5"/>
  <text x="30" y="390" font-size="11" fill="#7f1d1d" font-weight="700">Audit Agent（audit_graph · 独立 AuditState）</text>

  <rect x="80" y="400" width="100" height="26" rx="4" fill="#4c1d95"/>
  <text x="130" y="418" text-anchor="middle" fill="white" font-size="10.5">GATE_THINK</text>
  <line x1="180" y1="413" x2="200" y2="413" stroke="#64748b" stroke-width="1.3" marker-end="url(#ah)"/>
  <rect x="200" y="400" width="90" height="26" rx="4" fill="#f97316"/>
  <text x="245" y="418" text-anchor="middle" fill="white" font-size="10.5">GATE_ACT</text>
  <line x1="290" y1="413" x2="310" y2="413" stroke="#64748b" stroke-width="1.3" marker-end="url(#ah)"/>
  <rect x="310" y="400" width="105" height="26" rx="4" fill="#f97316"/>
  <text x="362" y="418" text-anchor="middle" fill="white" font-size="10.5">GATE_OBSERVE</text>

  <!-- Audit Agent output label -->
  <rect x="430" y="400" width="100" height="26" rx="4" fill="#7f1d1d"/>
  <text x="480" y="418" text-anchor="middle" fill="white" font-size="10">AuditDecision</text>

  <!-- Audit Agent → AuditDecision out -->
  <line x1="480" y1="426" x2="480" y2="460" stroke="#7f1d1d" stroke-width="1.5" marker-end="url(#ah)"/>

  <!-- ─── Audit Tools (right side) ─── -->
  <rect x="555" y="374" width="145" height="82" rx="7" fill="#fff0f0" stroke="#dc2626" stroke-width="1.5"/>
  <text x="628" y="391" text-anchor="middle" font-size="10.5" fill="#7f1d1d" font-weight="700">工具（Audit Agent）</text>
  <rect x="565" y="398" width="125" height="24" rx="4" fill="#dc2626"/>
  <text x="628" y="415" text-anchor="middle" fill="white" font-size="10.5">MetricQueryTool</text>
  <rect x="565" y="428" width="125" height="24" rx="4" fill="#dc2626"/>
  <text x="628" y="445" text-anchor="middle" fill="white" font-size="10.5">KBRetrievalTool</text>

  <!-- GATE_ACT → Audit tools dashed -->
  <line x1="543" y1="413" x2="555" y2="413" stroke="#dc2626" stroke-width="1.2" stroke-dasharray="4,2"/>

  <!-- ═══ Orchestrator 路由层 ═══ -->
  <rect x="80" y="462" width="460" height="28" rx="5" fill="#334155"/>
  <text x="310" y="481" text-anchor="middle" fill="white" font-size="11" font-weight="700">Orchestrator 路由（确定性）</text>

  <!-- 4 output arrows from Orchestrator routing -->
  <line x1="130" y1="490" x2="100" y2="516" stroke="#059669" stroke-width="1.4" marker-end="url(#ahg)"/>
  <line x1="240" y1="490" x2="240" y2="516" stroke="#d97706" stroke-width="1.4" marker-end="url(#ah)"/>
  <line x1="370" y1="490" x2="400" y2="516" stroke="#dc2626" stroke-width="1.4" marker-end="url(#ahr)"/>
  <line x1="470" y1="490" x2="502" y2="516" stroke="#64748b" stroke-width="1.4" marker-end="url(#ah)"/>

  <!-- pass -->
  <rect x="40" y="516" width="120" height="28" rx="5" fill="#059669"/>
  <text x="100" y="535" text-anchor="middle" fill="white" font-size="11" font-weight="700">pass</text>
  <!-- continue+hint -->
  <rect x="170" y="516" width="140" height="28" rx="5" fill="#d97706"/>
  <text x="240" y="535" text-anchor="middle" fill="white" font-size="10.5" font-weight="700">continue + hint</text>
  <!-- rehypothesize -->
  <rect x="330" y="516" width="140" height="28" rx="5" fill="#dc2626"/>
  <text x="400" y="535" text-anchor="middle" fill="white" font-size="10.5" font-weight="700">rehypothesize</text>
  <!-- degrade -->
  <rect x="480" y="516" width="100" height="28" rx="5" fill="#64748b"/>
  <text x="530" y="535" text-anchor="middle" fill="white" font-size="10.5" font-weight="700">degrade</text>

  <!-- Routing back arcs -->
  <!-- continue → back to Diagnosis Agent (left arc) -->
  <path d="M195 544 Q10 544 10 350 Q10 230 30 230" fill="none" stroke="#d97706" stroke-width="1.3" stroke-dasharray="5,3" marker-end="url(#ah)"/>
  <text x="4" y="400" font-size="9" fill="#d97706" writing-mode="tb">hint → Diagnosis</text>

  <!-- rehypothesize → back to HYPOTHESIZE -->
  <path d="M470 544 Q780 544 780 320 Q780 220 543 220 Q530 220 530 230" fill="none" stroke="#dc2626" stroke-width="1.3" stroke-dasharray="5,3" marker-end="url(#ahr)"/>
  <text x="754" y="350" font-size="9" fill="#dc2626" writing-mode="tb">→ HYPOTHESIZE</text>

  <!-- Outer Stage 2 frame end (Orchestrator) -->

  <!-- ═══════ FINALIZE ═══════ -->
  <rect x="245" y="558" width="140" height="28" rx="5" fill="#4c1d95"/>
  <text x="315" y="577" text-anchor="middle" fill="white" font-size="11">FINALIZE（LLM）</text>

  <!-- pass + degrade → FINALIZE -->
  <line x1="100" y1="544" x2="280" y2="558" stroke="#059669" stroke-width="1.3" marker-end="url(#ah)"/>
  <line x1="530" y1="544" x2="350" y2="558" stroke="#64748b" stroke-width="1.3" marker-end="url(#ah)"/>

  <!-- FINALIZE → Reflect -->
  <line x1="315" y1="586" x2="315" y2="600" stroke="#64748b" stroke-width="1.5" marker-end="url(#ah)"/>

  <!-- ═══════ Stage 3: Reflect ═══════ -->
  <rect x="10" y="600" width="618" height="42" rx="7" fill="#fef3c7" stroke="#d97706" stroke-width="1.5"/>
  <text x="22" y="617" font-size="10.5" fill="#78350f" font-weight="700">Stage 3  Reflect — 规则提炼 → 写回 Fault Pattern Library</text>
  <text x="22" y="633" font-size="10" fill="#92400e">LLM 提炼症状签名 → 去重 → fpl.jsonl 写入</text>

  <!-- Reflect → output -->
  <line x1="317" y1="642" x2="317" y2="654" stroke="#64748b" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="80" y="654" width="480" height="24" rx="6" fill="#1e293b"/>
  <text x="320" y="670" text-anchor="middle" fill="#e2e8f0" font-size="11">输出：DiagnosisReport (JSON) + ExecutionTrace</text>
  </svg>'''

html = html[:svg_start] + new_svg + html[svg_end:]

with open(FILEPATH, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"Replaced SVG: old {len(old_svg)} chars → new {len(new_svg)} chars")
print("New SVG: 820×680")
