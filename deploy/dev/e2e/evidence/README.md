# 跨仓联调证据

这里只保存必须由根编排提交和两个子仓 gitlink 共同解释的证据。单仓的设计、实现与验证仍归对应子仓，
这里不复制产品语义。页面直接存为 `EVD-*.md`，文件名与 id 一致。

## 最小记录

- `id`、`status: active|superseded`、`result: passed|partial|failed|blocked`、`updated_at: YYYY-MM-DD`。
- `observed_commit` 为实际运行时的根仓完整 SHA，且可从当前 HEAD 到达；子仓版本从该提交的 gitlink
  推导，不再手填 backend_commit/frontend_commit。
- `commands`、`scope` 是非空、无重复的文本列表。scope 仅限
  `static/unit/integration/e2e/browser/device/synthetic/human-review/live-provider/production`。
- `coverage` 是非空覆盖组列表，每组只有 `requirements` 和 `paths`；同一条款只出现一次。
  requirements 使用 `little-white-box-front:FQ-002` 等仓库限定 ID，从观察提交的 gitlink 导出批准条款。
  paths 是观察时存在的根仓相对路径；子仓前缀后跟子仓路径，也可用整个子仓目录表示全部输入。
- 可选 `external_upstream` 只保留覆盖关系之外的语义依赖，仍固定为 `repo@<40sha>:<ID>`，不重复列出
  coverage 条款。覆盖关系与外部引用合起来必须涉及两个子仓；外部引用不得晚于观察提交对应的版本。
- 正文记录实际结果和未覆盖边界，可选 `artifacts` 使用仓库文件或稳定 URI；passed 产物不能全在 `/tmp`。

## 有效性

`active` 表示记录仍可引用，不表示所有组仍能证明当前版本。根仓逐组比较输入内容和条款定义指纹，报告
哪些组已过期；其他领域、无关文档或单纯 gitlink 前进不会自动使整页失效。历史结果不被改写或删除。
旧 partial 若没有可恢复输入，使用空 paths 明确表示未知，不能用于当前通过证明；passed 组必须有路径。

输入路径必须覆盖命令依赖的源码、测试、契约、配置和共享工具，不能为了避免失效而缩小快照。
根仓只解释跨仓证据，不拥有子仓 IMP；子仓的 aligned 仍由各自知识门禁根据本仓证据独立判定。
根检查器只消费子仓固定版本 JSON 导出，不解析子仓 Markdown，也不执行历史脚本。

先提交被验证变更，再在最终提交运行命令，最后用独立证据提交记录结果。结构检查、契约生成一致或
本地测试通过，不替代 provider、浏览器、设备、容量与生产验证。未执行范围保持未验证。
