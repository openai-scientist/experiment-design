# Experiment Design — Product Requirements & Agent Architecture

**Phiên bản:** 1.3 — Core Experiment Design First · **Ngày:** 10/10/2026  
**Trạng thái:** Baseline thiết kế cho repo mới, chưa implementation  
**Sản phẩm:** **Experiment Design** độc lập; ASE là định hướng mở rộng, không phải phạm vi triển khai hiện tại  
**Đầu ra chính:** **Experiment Design Plan (EDP)** hoàn chỉnh để researcher đánh giá, phản biện, chỉnh sửa và bàn giao.

## 1. Quyết định sản phẩm

Xây dựng **một sản phẩm thiết kế thí nghiệm khoa học có các agent thật sự hợp tác**, không phải chuỗi `LLM(prompt) → JSON`, không phải hệ thống ASE đầy đủ.

> Từ một **Scientific Package**, các agent tự tìm hiểu bài toán, đề xuất thiết kế, kiểm tra phương pháp, phản biện những giả định quan trọng, dùng công cụ và bằng chứng, sửa các sai sót khoa học, xác minh thay đổi và tạo **một EDP mạch lạc** để researcher duyệt.

**Ưu tiên chứng minh giá trị trước khi tối ưu/hardening.** Có thể tạo một EDP trung thực ghi rõ thiếu sót và điều kiện NO-GO; không được biến checklist pass thành bằng chứng thiết kế có giá trị.

### Trong phạm vi pilot đầu tiên

- Nhận research question, hypothesis/prediction/failure condition, constraints, available data, evidence và assumptions.
- Tạo **một phương án thiết kế ban đầu** với lập luận rõ ràng, chưa cần `k > 1`.
- Có **các agent khác nhau về cơ chế làm việc ngay từ đầu**, dùng chung runtime nhưng có task, state, công cụ, vòng phản hồi và quyền hạn riêng.
- Scientific validation và tính toán có căn cứ, không bịa precision, power hoặc giả định.
- Critique → revision → independent verification; có thể tiếp tục khi thực sự thu được thông tin mới.
- Researcher có thể hỏi, trả lời yêu cầu quan trọng, review, request changes và approve đúng version.
- Xuất một tài liệu khoa học human-first, canonical JSON và manifest tối thiểu.
- Một pilot case được người có chuyên môn đánh giá; cùng một scorecard nhỏ qua các vòng phát triển.
- **Quan sát realtime vừa đủ:** SSE đưa hoạt động agent/task/tool, scientific issue, revision và verification lên một run viewer nhỏ; không phải điều kiện để workflow vận hành hay tạo EDP.

### Ngoài phạm vi ban đầu

Full experiment implementation/execution, confirmatory verdict, paper writing, multi-study management platform, microservices, full dashboard/platform UI, multi-candidate tournament/Elo, tree search, learning across runs, benchmark/evals platform lớn, general-purpose statistics framework, nhiều lớp governance/hardening chưa có consumer. **Diagnostic/pilot scientific execution** chỉ bổ sung khi có một câu hỏi thiết kế cụ thể cần kiểm tra thực nghiệm và có authorization; không mặc định là stage phải chạy.

## 2. Người dùng và giá trị sản phẩm

**Primary user:** Researcher/PI có câu hỏi khoa học và cần phương án thí nghiệm có thể triển khai, phản biện và kiểm chứng.  
**Secondary user:** Engineer hoặc người triển khai protocol sau khi design được phê duyệt.

**Trải nghiệm chính:** Nộp Scientific Package → các agent tự thiết kế, kiểm tra, phản biện và cải tiến → researcher có thể **quan sát tiến trình qua giao diện realtime** và trả lời quyết định cần thiết → đọc EDP → trao đổi/yêu cầu sửa → duyệt → xuất bản theo version. **Giá trị chính nằm ở EDP và quá trình cải thiện khoa học, không ở lượng hoạt động được stream.**

Một EDP dùng được phải trả lời cụ thể: nghiên cứu kiểm định điều gì; baseline/đối chứng nào; đơn vị ngẫu nhiên hóa và phân tích là gì; đo gì và đo bằng cách nào; estimator, uncertainty và decision rule là gì; quy trình thực hiện, nguồn lực, giới hạn và điều kiện có thể làm kết luận sai. Reviewer không cần đọc log nội bộ mới hiểu được thiết kế.

**Không đồng nhất** `workflow completed`, `EDP ready for review`, `researcher approved` và `hypothesis scientifically verified`.

## 3. Agent thật sự là gì trong sản phẩm này?

**Chỉ đổi system prompt/persona không đủ để gọi là một agent chuyên trách.** Mỗi core agent phải có:

1. **Objective/task contract** rõ và điều kiện kết thúc/escalate được.
2. **Toolset và hành động riêng** phù hợp nhiệm vụ, không được tùy tiện chỉnh sửa phần agent không sở hữu.
3. **Working context/state** và quyền đọc các version, issue/evidence liên quan.
4. Vòng **observe → tự lựa chọn bước khảo sát/thao tác → gọi tool → quan sát kết quả → điều chỉnh kế hoạch → tiếp tục/kết thúc** trong ngân sách cho phép.
5. Output có cấu trúc, trích dẫn evidence/đầu ra tool thực tế, uncertainty và lý do quyết định.
6. Có thể đổi hướng dựa trên observation, **không chỉ gọi LLM một lần để điền biểu mẫu**; không áp đặt số lần tool call hình thức.

Các agent dùng chung **một tool-agent runtime thực sự**. Không dựng runtime, database, orchestration hay service riêng cho từng role. Tính khác biệt đến từ **workflow nội bộ, tool permissions, cơ chế phản hồi và tiêu chí hoàn thành**, không phải nhân cách hóa prompt.

### Bốn core agents ngay từ pilot đầu tiên

| Agent | Cơ chế hoạt động riêng biệt | Công cụ và quyền | Kết quả |
|---|---|---|---|
| **Design Agent** | Sở hữu bản thiết kế đang soạn; phát hiện khoảng trống; khảo sát phương án, giả định, khả thi; tính và chỉnh các phần liên đới nhau | Đọc package/evidence; kiểm tra design; gọi calculator; đề xuất patch canonical | Initial design hoặc targeted revision có rationale; xin researcher quyết định nếu phải đổi scientific claim |
| **Methodologist Agent** | **Truy ngược chuỗi suy luận:** hypothesis → estimand → unit → sampling/allocation → metric → SAP → uncertainty/decision rule; tự kiểm tra phép tính và điều kiện áp dụng | Đọc section liên quan; validators, statistical calculators, evidence lookup, issue ledger | Objection về lỗi phương pháp với cơ chế gây sai, ảnh hưởng đến kết luận và hướng sửa khả thi |
| **Skeptic Agent** | **Stress-test giả định và conclusion scope:** dựng counterexample, alternative explanation, sensitivity scenario; phân biệt nguy cơ có căn cứ với giả định chưa biết | Đọc claim, assumptions, issue ledger; evidence lookup; targeted scenario checks | Một vài mối lo **mới và material**, không tái diễn issue cũ hoặc thổi severity |
| **Verifier Agent** | **Kiểm chứng độc lập** issue + before/after diff + kết quả tool; tìm regression trên vùng bị tác động, không viết patch của chính mình | Xem snapshot/diff/evidence; validators/calculator; quyền read-only với design | Verdict **đề xuất** `resolved / reopen / uncertain` và căn cứ, không tự có quyền phê duyệt |

**Investigator/Engineer Agent (chưa bắt buộc):** Khi một mối lo cần diagnostic có giới hạn để cải thiện design, agent đề xuất protocol, được researcher cho phép, dùng sandbox thực thi và trả về observed evidence. Không tự chạy main experiment, không lấy số pilot làm confirmatory evidence.

**Hợp đồng task dùng chung (tối thiểu):**

```text
AgentTask {
  objective, package_ref, design_version, focus_paths,
  issue_refs, allowed_tools, budget, exit_conditions
}
AgentResult {
  findings_or_changes, rationale, evidence_refs,
  tool_observations, uncertainty, completion_status
}
```

Agent scratchpad/observations nằm trong journal. **Chỉ có một canonical Design State**; mọi chỉnh sửa đi qua patch đã validate. Không tạo nhiều bản design authoritative theo từng agent.

## 4. Kiến trúc tối giản và quyền quyết định

```text
 Scientific Package + References
              │
              ▼
      Experiment Design Workflow ◄──► Researcher
              │                         (answer/review/approve)
    ┌─────────┼─────────────────────────┐
    │         │                         │
 Design    Methodologist / Skeptic    Verifier
 Agent      Agents                    Agent
    │         │                         │
    └─────────┴───────┬─────────────────┘
                     │
           Scientific tools / evidence
                     │
           JEV — chọn WHAT / NEXT
                     │
       Code — validate WHAT IS VALID
                     │
                     ▼
          Canonical Design + Issues
                     │
                     ▼
       Human-first EDP + JSON + manifest

 Shared Runtime: agent tool loop | LLM adapter | journal
                 bounded budget | retry | minimal resume
```

**Quyền hạn tách biệt:**

- **Agents/HOW:** tự điều tra, tìm căn cứ, thiết kế, critique, patch, verify trong task được giao.
- **JEV/WHAT-NEXT:** **giữ yêu cầu kiến trúc đã chốt:** mọi lựa chọn hành động và outcome ở **cấp workflow** qua JEV (next action, issue disposition, batch, keep/revert/repair, verification outcome). Agent/verifier chỉ **đề xuất** kết luận; không tự commit state transition khoa học.
- **Code/VALID:** liệt kê allowed options, kiểm typed contracts/guard/invariants, tính toán tất định, áp dụng hành động hợp lệ. Không biến if/else thành bộ chọn thay JEV.
- **Agent-local tool selection:** là hành vi thực thi task trong quyền hạn đã được giao, không phải một quyết định chuyển trạng thái workflow; vì vậy agent vẫn có vòng tool/observation tự chủ mà không cần JEV cho từng lệnh tool.
- **Researcher:** sở hữu mục tiêu nghiên cứu, claim, primary outcome, meaningful threshold/null, inference scope, chấp thuận resource trade-off và approval.

**Tối ưu JEV bằng chứng thực tế:** gọi tại các mốc quyết định có ý nghĩa; payload gồm trạng thái ngắn, các lựa chọn hợp lệ, issue/evidence liên quan; không gửi cả EDP liên tục. Đo chi phí rồi mới cân nhắc thay đổi quyền hạn — mọi thay đổi kiến trúc JEV phải được researcher chấp thuận rõ.

**Một process Python** cho agent workflow, một journal/store và một production path. HTTP API/SSE + CLI chỉ là giao diện truy cập và quan sát cùng trạng thái đó; không tạo Management platform, message bus hay microservices ở giai đoạn này.

## 5. Contract và những bất biến khoa học bắt buộc

**ScientificPackage** chứa câu hỏi, hypotheses và prediction/failure conditions, population/scope nếu đã được xác định, outcome, ngưỡng/null nếu có, constraints, budget, known assumptions, sources/available data. Không ép researcher điền những trường chưa biết; engine phải nhận diện gap.

**Canonical Design** chứa các nội dung logic, **không phải mỗi mục một artifact**:

- Claim/inference scope; comparisons, controls/baselines, experiment conditions/matrix.
- Sampling, inclusion, allocation, randomization, unit of experiment/analysis, repetitions.
- Primary/secondary metric, operationalization, units, data collection, missingness.
- Statistical Analysis Plan: estimand, test/model, null/margin, alpha/CI/SE, multiplicity/stopping/sensitivity khi phù hợp.
- Execution protocol, resources, feasibility, validity risks, unresolved assumptions.
- Version, issue links, source/evidence refs, scenario calculations nếu đủ điều kiện.

**Records tối thiểu:** `DesignVersion`, `ReviewIssue`, `ResearcherRequest` (kind `decision` hoặc `input`), `Approval`. Có thể đặt trong một store thay vì tạo nhiều service/registry/artifact khác nhau.

**Chỉ hard guard cho những thứ làm hỏng giá trị khoa học:**

1. **Không tự đổi researcher-owned claim** (hypothesis, primary outcome, null/margin, meaningful threshold, estimand/inference scope đã xác nhận) để tạo power cao hoặc cho dễ pass. Default của designer phải được gắn nhãn chưa phê duyệt.
2. **Không bịa bằng chứng và số liệu.** Phân biệt source evidence, assumption/scenario, computed quantity và LLM interpretation.
3. **Một định nghĩa scientific calculation:** contrast/null/margin/effect và unit normalization nhất quán giữa validation, tool và EDP. Unsupported/missing inputs → ghi `not computed`, không xuất con số giả chính xác.
4. **Implemented ≠ verified:** verifier độc lập phải kiểm actual diff và các affected paths; blocker chưa giải quyết phải thể hiện trung thực.
5. **Approval/freeze gắn với đúng version.** Trace và hash nằm trong journal/manifest, không làm bẩn nội dung EDP.
6. **Diagnostic/pilot ≠ confirmatory evidence** nếu bổ sung ở giai đoạn sau.

Không mở thêm hàng loạt guard/rule cho edge cases chưa quan sát được. Những bất biến trên giữ ở mức vừa đủ để kết quả không sai hay bị giả mạo.

## 6. Một workflow sản phẩm, agent tự chủ bên trong

1. **Intake:** Đọc package, validate đầu vào, phân loại cái đã biết/chưa biết. Thiếu dữ kiện thì ghi assumption hoặc request; không tự chế dữ liệu.
2. **Design:** Design Agent tự khảo sát và gọi các tool cần thiết để tạo một thiết kế coherent, có rationale và SAP cụ thể.
3. **Scientific review:** Methodologist kiểm chuỗi suy luận; Skeptic tìm counterexample và validity threat. Validators chạy code trực tiếp. Cả hai thấy ledger để tránh lặp issue.
4. **Decision:** Code đưa ra options hợp lệ; JEV chọn `REVISE / REVIEW / VERIFY / ASK_RESEARCHER / MARK_REVIEWABLE / STOP` dựa trên state gọn.
5. **Refinement:** Designer sửa **một nhóm mối lo material có liên quan** bằng patch nhất quán; không ép viết thêm để tăng score. Nếu patch tạo MAJOR/BLOCKER mới, JEV chọn keep/repair/revert sau khi đọc evidence.
6. **Verification:** Verifier kiểm issue gốc, diff, calculation và regression; JEV chọn outcome có hiệu lực. Chỉ lặp khi có thông tin mới hoặc cơ hội sửa hợp lý.
7. **Human review:** Researcher đọc EDP, nhận đúng câu hỏi `decision`/`input`, request changes hoặc approve. Các thiếu sót cần cho implementation nhưng không ảnh hưởng khả năng đánh giá có thể đặt ở handoff.
8. **Export:** `experiment_design.md`, `experiment_design.json`, `manifest.json` của cùng một canonical version.

**Trạng thái đầu ra:** `REVIEWABLE`, `NEEDS_RESEARCHER`, `BLOCKED/INCONCLUSIVE` hoặc `BUDGET_STOP`. Dừng vì ngân sách hoặc không tiến bộ là thông tin pilot, không phải cái cớ để auto-approve. Một thiết kế ban đầu tốt có thể không cần sửa.

**Không buộc tuần tự cứng** mỗi agent đúng một call. Mỗi agent có vòng tool riêng, JEV quyết định các bước workflow, và số vòng phụ thuộc evidence/progress chứ không phải mục tiêu đóng đủ issue.

## 7. EDP phải thật sự là một scientific proposal

**Một tài liệu dễ đọc, mạch lạc, không phải JSON dump.** Markdown render từ canonical scientific content, mỗi thông tin chỉ xuất hiện đúng chỗ; kiểm tra consistency để không sinh nội dung tách khỏi source of truth.

Bố cục gợi ý:

1. **Tóm tắt và mục tiêu khoa học:** RQ, hypothesis, prediction/failure condition, scope.
2. **Rationale và lựa chọn thiết kế:** vì sao chọn comparison, controls/baselines, trade-offs material.
3. **Thiết kế thí nghiệm:** conditions, matrix, sampling, randomization, unit, repetitions và nguồn lực.
4. **Đo lường & protocol:** metric, operationalization, units, thao tác thực hiện, missingness và stop conditions.
5. **Statistical Analysis Plan:** estimand, primary analysis, null/margin, uncertainty, decision criteria; power/precision theo **scenario có nhãn** khi tính được.
6. **Validity, assumptions và giới hạn:** điều gì có thể làm sai inference; cách giảm thiểu hoặc nội dung chưa thể giải quyết.
7. **Việc cần researcher và handoff:** quyết định chờ duyệt, điều kiện trước thực thi, readiness/approval đúng nghĩa.

Output: `experiment_design.md`, `experiment_design.json`, `manifest.json`. Nội dung user-facing **không có raw debate, hash, issue ID, full journal, JSON string lồng nhau, lặp scientific context**. Đường handoff yêu cầu Engineer khác có thể lên implementation outline mà không phải chọn lại primary metric, estimator hoặc method.

## 8. Roadmap theo khả năng chứng minh, không theo độ phức tạp framework

### Pilot 0 — First useful agentic EDP (làm trước)

Repo sạch, contracts tối thiểu, **một shared tool-agent runtime thực**, bốn agent có operational mechanisms khác nhau, JEV gọn, công cụ tính toán cần thiết, một canonical state, renderer và human review. **Run viewer SSE tối giản** quan sát cùng journal, không chặn việc hoàn thiện scientific loop. Chọn **một case đủ input**; chạy trọn từ package → initial draft → critique có tool/evidence → targeted revision hoặc rebuttal → independent verification → EDP → researcher review. Không xây framework cho nhu cầu sau này.

### Pilot 1 — Improve on observed failures

Dựa trên run thật, sửa bottleneck khoa học/sản phẩm đo được (statistical correctness, thiếu scenario input, critique yếu, chi phí JEV, human feedback hỏng, EDP chưa handoffable). Viết một regression test nhỏ cho mỗi lỗi correctness đã xác nhận; chạy lại cùng case, so initial/final và ghi rõ kết quả.

### Pilot 2 — Check generality

Chỉ sau khi pilot đầu có tín hiệu sử dụng thực tế, chạy một case thuộc dạng phương pháp/domain khác. Nếu cần chuyên môn hóa, dùng **skill + tool/calculator adapter cấu hình được**, không hardcode `if domain == ML/HEP/...` vào core.

### Later — Evidence-driven diagnostic

Bổ sung bounded Investigator Agent/sandbox khi có **vấn đề khoa học cụ thể cần pilot/diagnostic** mà công cụ hiện tại không giải quyết; yêu cầu authorization và provenance của kết quả.

**G0–G3** vẫn có thể dùng làm nhãn capability: G0 minimal runtime/contracts; G1 coherent first EDP; G2 scientifically meaningful multi-agent refinement; G3 justified evidence-driven diagnostic. **Cơ chế agent thật và hợp tác phải hiện diện ngay trong pilot đầu**, không đợi G2 rồi mới thay prompt persona bằng agent.

## 9. Chỉ một Eval nhỏ phục vụ sản phẩm

**Không xây benchmark platform trước pilot.** Giữ **một bộ scorecard duy nhất** qua các phiên bản, có bốn chiều:

| Chiều | Câu hỏi đánh giá |
|---|---|
| **Scientific alignment** | EDP có kiểm đúng hypothesis/claim, giữ primary outcome/null/estimand không? |
| **Methodological soundness** | Unit, control, sampling, estimation, uncertainty và decision rules có hợp lý không? |
| **Executability** | Researcher/Engineer khác có thể triển khai mà không phải thiết kế lại khoa học không? |
| **Clarity & evidence honesty** | EDP có dễ duyệt và trung thực về evidence, scenario, assumption, limitation không? |

Mỗi pilot ghi: input/source snapshot + config; initial/final EDP; mối lo material, change/diff và kết quả verify; nhận xét của **human reviewer**; token/calls/latency; lỗi/thành công. Judge dùng cùng model chỉ là tín hiệu tham khảo, **không phải ground truth độc lập**.

**Tests tối thiểu nhưng không được bỏ:** contract round-trip, claim invariants, vài statistical reference cases (đặc biệt power có null/margin), production input → calculator → EDP, patch verification, resumability/version binding, CLI answer path, renderer consistency. Không đặt mục tiêu số lượng seeded flaws, Elo, 3 lần judge mặc định hoặc hàng loạt thresholds dựa trên 1–3 case.

**Pilot được xem là có giá trị khi:**

- Task/tool/evidence records cho thấy các agent **thực sự thích nghi dựa trên observation/tool result**, không chỉ thay role prompt; SSE chỉ trình bày các record đó, không dùng số events/tool calls làm thước đo giá trị.
- Có EDP đủ nội dung để researcher đánh giá khoa học; giới hạn còn tồn tại được chỉ rõ.
- Ít nhất một vấn đề material được sửa và verify **hoặc** được bác bỏ/giữ unresolved có căn cứ; không có thay đổi claim âm thầm.
- Human reviewer đánh giá được usefulness, blocking gaps, improvement/regression và effort.
- Pilot không đạt nhưng chỉ ra failure mode cụ thể vẫn được ghi nhận trung thực, không ép READY.

## 10. Giữ bài học từ các lỗi đã xác nhận của repo cũ

Những mục sau là **audit evidence của repo trước**, không phải lời hứa repo mới đã giải quyết:

| Lỗi/quan sát trước đây | Yêu cầu rút ra cho repo mới |
|---|---|
| Validation power với margin gần **100%**, nhưng EDP trình bày khoảng **5%** cho cùng case | Một công thức/contrast/null dùng xuyên calculator–validator–renderer; kiểm bằng reference cases |
| Cluster calculator chỉ tính được qua test subclass; ICC/cỡ cụm không đi từ production input; unit normalization không thống nhất | Không tuyên bố hỗ trợ phương pháp nếu package → calculator → EDP chưa chạy thật; normalize ở một chỗ |
| Q01–Q03 lịch sử đều NO-GO; khoảng một lượt REVISE; JEV/triage tiêu **48–55%** token | Context JEV gọn; sửa tập trung vào material concern; đo chi phí trên pilot tương thích source mới |
| Patch có thể tạo MAJOR mới; review bị lặp; issue sinh ID khác khi critic diễn đạt lại | Targeted patch check; issue ledger gọn; independent verify; không lặp critique vô ích |
| Q01 thêm bound **[0,100] g** không có evidence, trong khi inferential question vẫn bỏ ngỏ | Kiểm source của quantitative assumption và ưu tiên estimator/uncertainty/decision rule hơn văn xuôi |
| EDP cũ ~100 KB dạng dump, hash lặp | Human-first renderer, một canonical design, log nội bộ, manifest cho provenance |
| Hướng dẫn CLI để researcher trả lời không khớp parser; designer defaults không đi hết production flow | Test user command thực sự dùng và request → answer → resume trên đường production |
| Scripted acceptance pass nhưng scientific quality chưa được đánh giá; judge cùng model | Tách plumbing tests khỏi scientific/product value được human reviewer đánh giá |
| Fast suite lịch sử **14 failures**, Ruff **4 lỗi** ở snapshot audit | Lưu làm bài học/testing reference, không copy hàng loạt test brittle/contract drift hoặc code lỗi sang repo mới |

Không tái tạo nguyên issue-state graph, toàn bộ A/B debate specs, vô số rules, artifacts và benchmarks. **Bảo tồn các scientific invariants và những lỗi thực sự đã quan sát**, không sao chép speculative hardening.

## 11. Repo sạch, dễ audit

Logical structure tham khảo — **chỉ tạo module khi có consumer**:

```text
experiment-design/
  src/experiment_design/
    contracts.py         # ScientificPackage, Design, Issue, Request, Approval
    workflow.py          # một đường EDP, liên kết JEV
    agents/              # design, methodologist, skeptic, verifier
    runtime/             # shared tool loop, provider, tools, journal
    science/             # validators, computation, evidence lookup
    output.py            # render/export human-first, một canonical source
    api.py               # researcher API và SSE projection từ journal
    cli.py               # run/status/review/answer/approve/export
  web/                   # minimal run viewer, không phải scientific workflow
  tests/                 # tests khoa học và hành vi product cần thiết
  pilot/                 # một case/input + human scorecard
  docs/PRD.md            # tài liệu này
  pyproject.toml
```

**Quy tắc phát triển:** Một production path, một canonical model, một journal; dependency rõ; không giữ cũ/mới song song; không fake provider/evidence trong live; có tests cho correctness trực tiếp; giữ retry/resume tối thiểu cho những bước đắt đã thành công; không tạo folder abstraction rỗng hoặc TODO framework. Code phải đọc và audit được ngay từ đầu.

## 12. Observability và SSE — chỉ là lớp hiển thị

**Vai trò:** giúp researcher hiểu *ai đang làm gì, vì sao design thay đổi, mối lo nào còn mở*; không phải agent coordinator, research state store, discussion engine hay bằng chứng rằng thiết kế tốt. Core vẫn chạy, resume, verify và export bình thường khi không có UI hoặc browser ngắt kết nối.

```text
Scientific Package → Core Experiment Design Workflow → Canonical EDP
                          │
                Shared Agent Runtime
                          │ append committed events
                    SQLite journal
                          │ read/replay
                    HTTP + SSE adapter → Live Run Viewer
```

- **Một nguồn dữ liệu:** runtime ghi `run`, `agent/task`, `tool`, `issue`, `decision (JEV)`, `design.version/patch`, `verification`, `researcher.request` vào journal; SSE chỉ phát projection. Không tạo database, orchestrator, message bus hay pipeline riêng cho UI.
- **Một run viewer nhỏ:** trạng thái run/version, activity theo agent, vấn đề khoa học theo issue, phản hồi/revision theo quan hệ `thread_id` và `parent_entry_id` *nếu có thật*, và diff EDP trước/sau. Có thể bắt đầu với timeline; bố cục debate nhiều cột là tùy chọn trình bày sau này.
- **Event envelope tối thiểu:** `event_id`, `run_id`, `timestamp`, `type`, `agent_id?`, `task_id?`, `design_version?`, `data`; tham chiếu `issue_id`, `thread_id`, `parent_entry_id`, `evidence_refs` khi thực sự phát sinh. Không đưa raw chain-of-thought hay toàn bộ nội dung prompt/tool output vào UI.
- **API tối thiểu:** `GET /runs/{id}` (snapshot), `GET /runs/{id}/events` (SSE + resume theo `Last-Event-ID`), `GET /runs/{id}/design` (EDP preview). Researcher commands dùng REST/CLI hiện có, **không** gửi lệnh qua SSE.
- **Kiểm chứng cơ bản:** event hiển thị phải trỏ được về journal và canonical version; refresh/reconnect không mất các sự kiện đã commit; UI không tạo nội dung debate/approval giả và không thay đổi trạng thái khoa học.

**Thứ tự triển khai:** core events có ngay khi làm runtime → SSE adapter → viewer cơ bản. Chỉ mở rộng threaded discussion UI, richer diffs hoặc dashboard sau khi pilot chứng minh người dùng cần. **Không tạo thêm agents, debate turns, votes, event types hoặc artifacts để phục vụ giao diện.**

## 13. Các quyết định chốt và nguồn tham khảo

**Đã chốt:** Product = Experiment Design (không phải ASE platform); pilot với **bốn agent có cơ chế hoạt động riêng**, một shared runtime; **realtime viewer là projection từ journal, không thuộc core decision logic**; JEV chọn workflow decisions; một candidate; human-first EDP; số liệu/statistical computation qua code; researcher sở hữu scientific intent; một scorecard nhỏ và một pilot có reviewer; không mặc định debate/tournament.

**Chưa cam kết (thử sau nếu có bằng chứng):** Pairwise debate có cải thiện hơn focused critique/verify không? Hai candidates có tốt hơn một không? Simulation/diagnostic nào thật sự đổi quyết định thiết kế? Có cần judge model khác hay benchmark lớn để so model/providers không?

**Học từ hệ thống đã tồn tại, không sao chép:**

- **AutoResearchClaw:** Experiment Design có gate, feedback/self-repair và HITL có chủ đích; không copy 23-stage pipeline. https://github.com/aiming-lab/AutoResearchClaw · https://arxiv.org/abs/2605.20025
- **Google AI Co-Scientist:** Các agent chuyên trách, phản biện/tiến hóa ý tưởng và adaptive supervision; không copy tournament/Elo ở pilot. https://arxiv.org/abs/2502.18864
- **AI Scientist-v2:** Experiment Manager và agentic tree search cho open-ended discovery; không dựng tree search lúc chưa có bằng chứng cần. https://arxiv.org/abs/2504.08066
- **Agent Laboratory:** Các specialist agents kết hợp nghiên cứu và human copilot; không mở sang paper writing. https://github.com/SamuelSchmidgall/AgentLaboratory

---

**Acceptance statement:** *Một pilot được xem là có giá trị khi các scientific agents thực sự phối hợp, sử dụng công cụ và phản hồi để biến Scientific Package thành **một Experiment Design Plan đầy đủ, có căn cứ, đọc được và đủ để researcher đánh giá**, với các cải tiến được kiểm chứng hoặc những mối lo chưa giải quyết được trình bày trung thực — mà không yêu cầu người đọc phải xem log nội bộ.*
