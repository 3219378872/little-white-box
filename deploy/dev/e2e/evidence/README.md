# 跨仓联调证据

本目录只保存必须由根编排提交、后端 gitlink 与前端 gitlink 共同解释的黑盒联调证据。单仓可以独立
复现和解释的设计、实现与验证仍归对应子仓 `docs/knowledge/`，这里不复制产品意图、规格或实现语义。

每份证据直接存为本目录下的独立 Markdown 文件，并记录：

- 与文件名一致的 `id: EVD-*`、`status: active|superseded`、
  `result: passed|partial|failed|blocked` 和 `updated_at: YYYY-MM-DD`；
- 执行时的根提交 `observed_commit` 和该提交内两个 gitlink 对应的 `backend_commit`、
  `frontend_commit`，三者都使用 40 位 SHA；
- 非空 `covers` 条款列表、`commands` 命令列表、实际结果、未覆盖边界及可选 `artifacts` 列表；
  受控列表项不得为空或重复；
- 非空 `scope` 列表，取值只能是 `static`、`unit`、`integration`、`e2e`、`browser`、
  `device`、`synthetic`、`human-review`、`live-provider` 或 `production`；
- front matter `external_upstream` 列表，格式为
  `repo@<40位提交SHA>:<正式文档ID或已批准SPEC条款ID>`，并至少各引用一个前端与后端目标。
  每个 `covers` 条款必须同时作为一项精确的 `external_upstream` 条款目标出现；

子仓正式目标必须是 `docs/knowledge/<layer>/<ID>.md` 的直接页面，layer 为 `intent`、`spec`、
`design`、`implementation` 或 `evidence`；层级 README、嵌套页、模板、提案、归档和后端旧
`implementation/evidence/` 均不能作为正式目标。

证据只陈述实际执行结果。未运行的外部 provider、浏览器、设备、容量或生产门禁必须明确标为未验证，
不能由本地命令通过推导为完成。`active` 证据的两个子仓提交必须仍等于根仓当前 gitlink；从
`observed_commit` 到当前 HEAD 只允许本目录新增或修订证据，且工作树在本目录外必须干净。任一根编排
资产或 gitlink 前进后，把旧证据标为 `superseded` 并增加新证据。`active + passed` 若记录产物，不能
全部位于易失的 `/tmp`。
