# 前后端联调笔记（2026-08-18）

工作区根目录备忘，不是任一子仓正式知识链的一部分。事实以当时代码、配置和现场命令为准。

## 当时可用入口

| 入口 | 地址 |
| --- | --- |
| 对外同源入口（nginx） | `:3002`（页面 + `/api` + `/xbh-media`） |
| Flutter 开发服 | `127.0.0.1:3003`（不要直接给外网） |
| Gateway | `127.0.0.1:8888` |
| 测试账号 | `xiaobaihe` / `XbhTest123`（密码登录） |

nginx 配置当时在 `/tmp/xbh-dev-proxy.conf`，容器名 `xbh-dev-proxy`（`--network host`）。  
本机 `net-transfer` 用 `3002.<host>:3000` 转到容器 3002。

前端默认相对路径 `/api/v1/...`、`/api/v2/...`，必须同源或反代；`make dev-real SERVER_HOST=http://127.0.0.1:8888` 才打绝对地址。

## 已踩坑 / 未收口

### 后端启动与发现

1. **etcd 登记成 docker 网桥 IP**  
   RPC `ListenOn: 0.0.0.0:port` 时 go-zero 把 `172.18.0.2` 写进 etcd。宿主机 Gateway 连这个地址会断路器，接口秒回 `服务不可用`。  
   当时用 `/tmp/xbh-etc` 覆盖成 `ListenOn: 127.0.0.1:port` 后恢复。仓库 yaml 未改。

2. **Gateway 未接 CORS / OPTIONS**  
   浏览器跨源打 `:8888` 会失败；仅 `server.Use` 中间件时 OPTIONS 仍 405。需要 `rest.WithCors`。  
   改动在后端工作树 `app/gateway/gateway.go`，当时**未提交**。同源反代后 CORS 不再是硬依赖。

3. **interaction / media 未 InitSnowflake**  
   点赞/收藏：`snowflake not initialized`。  
   图片压缩成功后上传：同样错误。  
   测试里有 `InitSnowflake`，`NewServiceContext` 没有。当时补了 interaction worker=3、media worker=4，**未提交**。

4. **content-cleanup 消费组已存在**  
   重复启动 `content-cleanup-service-group` 直接 fatal。删帖异步清理当时不可用，不影响读写帖。

5. **搜索先 503，重建后才通**  
   旧 ES 文档 + Content 缺 `revision` 时 visibility 失败，客户端熔断。  
   `go run ./app/search/mq/cmd/rebuild` 后综合搜索可命中。新帖依赖 search-mq，冷启动要重建或等消费。

6. **推荐只有规则降级**  
   OnlineInfer / embedding-service 未启。`modelVersion` 为 `rules-v2+infer-unavailable`。Milvus 已起但向量召回未验证。

### 数据与中间件

7. **旧 MySQL 卷 root 密码对不上 compose 默认值**  
   `docker exec` 走 localhost socket 能进，宿主机 TCP 显示为 `root@172.28.3.1`。  
   密码里的 `@`/`!` 不编码会拆 DSN。当时建了 `xbh` / `xbhdev`。

8. **旧库缺列缺表**  
   `xbh_content.post.revision`、`idempotency`，`xbh_user.personalization_preference`，`xbh_message.message.media_id`。  
   只跑 `CREATE TABLE IF NOT EXISTS` 不会改已有表，要对旧表 `ALTER`。

9. **SeaweedFS 健康检查 unhealthy，S3 可用**  
   健康检查 wget `localhost:9333` 曾 Connection refused；Master/S3 实际可访问。上传成功后原图/缩略图 HTTP 200。

10. **Loki `latest` 与仓库 v11 schema 不兼容**  
    retention / structured metadata / tsdb 校验失败，容器重启循环。不影响业务。

11. **端口冲突**  
    Grafana 默认 3000 与前端冲突（已映到 33000）。SeaweedFS 8080 与 `sub2api` 冲突（已映到 18080）。3000/3001 本机另有进程。

### 产品与契约（不一定是 bug）

12. **关注流不回填历史帖**  
    先发帖再关注，inbox 为空。符合当前 fanout 语义。

13. **行为事件校验严**  
    客户端不能报 `like`/`impression`。`exposure` 必须带 `position`，且从 1 起。缺字段 202 + `rejectedCount`。

14. **关注流匿名请求**  
    未登录打 `/api/v2/feed/recommend` 缺 `anonymousId` 会参数错误。

### 前端 / 反代

15. **相对路径必须同源**  
    `SERVER_HOST` 默认空。没有反代时浏览器会打到前端端口的 `/api`，不是 8888。

16. **开发反代只在本机**  
    `/tmp/xbh-dev-proxy.conf` + `xbh-dev-proxy` 未进仓库。重启机器后要重拉。

## 建议后续

- 后端提交：Gateway CORS、interaction/media snowflake、本地 ListenOn 或 etcd 发布 IP 约定。
- 启动脚本：中间件、RPC 覆盖配置、反代、搜索 rebuild 一条龙。
- schema：旧卷迁移或文档写明必须对齐 `deploy/sql`。
- Loki：钉镜像或改 schema，避免 `latest`。
- content-cleanup：可重置消费组或换 group 名后再启。
