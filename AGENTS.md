# AGENTS.md

这是小白盒前后端并排工作区（`little/`），不是单一 git 仓库。子仓各自有自己的 `AGENTS.md` 和知识链；本文件只做入口索引。

## 子仓

| 目录 | 说明 | 规则入口 |
| --- | --- | --- |
| [little-white-box-content-community](little-white-box-content-community/) | Go / go-zero 后端 | [little-white-box-content-community/AGENTS.md](little-white-box-content-community/AGENTS.md) |
| [little-white-box-front](little-white-box-front/) | Flutter 前端 | [little-white-box-front/AGENTS.md](little-white-box-front/AGENTS.md) |

改代码前先读对应子仓的 `AGENTS.md`。后端和前端的正式知识分别从各自 `docs/knowledge/README.md` 按需加载，不要把本工作区备忘当成规格。

## 联调备忘

现场启动、反代、账号和踩坑记在 [NOTES.md](NOTES.md)，不是正式意图/规范。联调或排障时先看该页，再下到子仓。

| 主题 | 位置 |
| --- | --- |
| 入口、账号、反代 | [NOTES.md](NOTES.md#当时可用入口) |
| etcd / CORS / snowflake / 搜索 | [NOTES.md](NOTES.md#后端启动与发现) |
| MySQL 旧卷、SeaweedFS、Loki、端口 | [NOTES.md](NOTES.md#数据与中间件) |
| 关注流、行为事件契约 | [NOTES.md](NOTES.md#产品与契约不一定是-bug) |
| 相对路径与开发反代 | [NOTES.md](NOTES.md#前端--反代) |
