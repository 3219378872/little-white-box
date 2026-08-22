# 前后端联调笔记（2026-08-18）

工作区根目录备忘，不是任一子仓正式知识链的一部分。事实以当时代码、配置和现场命令为准。

## 当时可用入口

| 入口 | 地址 |
| --- | --- |
| 对外同源入口（nginx） | `:3002`（页面 + `/api` + `/xbh-media`） |
| Flutter 开发服 | `127.0.0.1:3003`（不要直接给外网） |
| Gateway | `127.0.0.1:8888` |
| 测试账号 | `admin` / `123456`（密码登录，`just up` / `just seed-dev-user` 写入） |
| Eval 语料 | `eval/corpus.json` 300 帖（id 1001–1300），`just up` / `just seed-eval-corpus` 灌入 MySQL；搜索索引在 `app-up` 时若落后会 rebuild |

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

4. **content-cleanup 曾因同组双消费者 fatal**  
   同一进程里 cleanup 与 count-sync 共用 `content-cleanup-service-group`，第二次 `Start()` 直接退出，公开点赞/收藏计数无法回写 `post.like_count`。已拆成 `content-count-sync-service-group`；本地栈会拉 `content-cleanup`。

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
   ClickHouse `daily_aggregates` 同样不会随旧卷自动出现；`just up` 会把 `xbh_analytics.sql` 再执行一遍（`CREATE IF NOT EXISTS`）。

9. **SeaweedFS 健康检查 unhealthy，S3 可用**（2026-08-22 已修复）
   根因：容器内 `localhost` 同时解析到 `127.0.0.1` 和 `::1`，busybox wget 走 IPv6 的 `::1`，而 SeaweedFS 只监听 IPv4（无 tcp6），故 `wget localhost:9333` Connection refused；服务本身一直正常（上传原图/缩略图 HTTP 200）。修法：探针 URL 改为 `http://127.0.0.1:9333/cluster/status`，已改入后端 `deploy/docker-compose.middleware.yml` 并重建容器转 healthy。

10. **Loki 曾因 `latest` + v11/boltdb-shipper 起不来**  
    已钉 `grafana/loki:3.7.6`，配置改为 v13/tsdb，并补 `compactor.delete_request_store`。本地 `just up` 会等 `:3100`。

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
    （2026-08-22 起已收编为 `deploy/dev/proxy.conf` + stack.sh `proxy_up`。）

17. **外网唯一入口是 :3002**（2026-08-22）  
    `:3003` 是 `flutter run -d web-server` 的 shelf 静态服务，没有任何反代能力；
    直连它时所有 `/api/*` 都会命中 SPA 兜底返回 index.html（HTTP 200 但内容是 HTML）。
    外网访问必须走 `3002.<host>:3000`。

18. **nginx `location /` 必须用 `$http_host` 而不是 `$host`**（2026-08-22）  
    `$host` 会剥掉端口；DWDS 按 Host 头拼调试回调 URL，拿到无端口地址后
    WebSocket 握手失败 → webdev 启动链路中断 → Dart `main()` 永不执行 →
    白屏且控制台无报错。改用 `$http_host` 保留客户端原始 host:port。

19. **机器级 http_proxy 会劫持 DWDS 调试信道导致白屏**（2026-08-22）  
    本机全局代理指向 `172.18.0.1:17897` 时，flutter 工具内 DWDS 建立调试
    WebSocket/VM-service 连接被代理破坏（日志表现为反复
    `DartDevelopmentServiceException: ... not upgraded to websocket`），后端永远
    不发 RunRequest，浏览器侧 883 个模块全部加载完成但 main() 不执行——白屏、
    无红错、极易误判为前端代码问题。stack.sh `frontend_up` 已对前端进程剥离
    全部 proxy 变量；手工启动前端时也要同样处理。release 构建不受影响
    （无调试信道），可用来做快速对照。

20. **CanvasKit 已本地托管，浏览器不再依赖 gstatic**（2026-08-22）  
    Flutter 引擎默认从 `www.gstatic.com/flutter-canvaskit/<engineRev>/` 拉取，
    用户网络不通时会精确复现"加载条消失→全白→无红错"。现由
    `FLUTTER_WEB_CANVASKIT_URL=/canvaskit/` + nginx 挂载
    `flutter_web_sdk/canvaskit/`（stack.sh `proxy_up`）提供同源产物；
    SDK 升级后引擎版本自动跟随缓存目录。

21. **flutter run 重启后浏览器缓存会静默搞坏引导**（2026-08-22）  
    每次重启 dev server，`main.dart.js`/`main_module.bootstrap.js` 内的
    `$dartAppId`、入口路径等都会变化；浏览器若复用旧缓存模块可能混出不一致状态
    （症状：加载条消失后全白、脚本数异常）。验证渲染问题一律先硬刷新或 InPrivate
    窗口排除缓存。

## 建议后续

> 2026-08-22 更新：联调编排已收敛到根仓 `justfile` + `deploy/dev/`。proxy.conf 已进仓库并
> 由 stack 管理，搜索 rebuild 已自动化于 app-up；下文第 15/16 条中「未进仓库」等表述自此
> 过时，仅作当时代快照保留。

- 后端提交：Gateway CORS、interaction/media snowflake、本地 ListenOn 或 etcd 发布 IP 约定。
- schema：旧卷迁移或文档写明必须对齐 `deploy/sql`。
- Loki：已钉 3.7.6 并升到 v13/tsdb；不要改回 `latest`。
- content-cleanup：可重置消费组或换 group 名后再启。

## 黑盒 e2e 首轮发现（2026-08-22）

> 来源：`deploy/dev/e2e/` 套件（104 用例）对真实联调栈的首轮运行与契约核对。
> 每项标注对应测试名，修复落地后在此回写状态。

### 缺陷（已立项修复）

1. **新注册用户永久不可被搜索** — `user/rpc/internal/logic/register_logic.go`
   `newUser()` 未设置 `Status`，Go 零值 0 显式写入，覆盖 schema `DEFAULT 1`
   （`0:禁用 1:正常`）；而用户搜索 `SearchPublic` 过滤 `status=1`。全库 418 个
   注册用户 status 全为 0 且无任何置 1 路径。对应 e2e：
   `test_search.py::test_newly_registered_user_excluded_from_public_search`
   （固化现状，修复后需翻转为正向断言）。

2. **Assistant 上游 LLM 失败时行为不一致** — `assistant/rpc/internal/logic/chat_logic.go`
   `Generate` 直接用请求级 ctx，上游超时耗尽预算后降级路径
   （`sendEvidenceDegraded` → `persistAssistant`）在已取消的 ctx 上全部失败，
   日志见 `assistant evidence degradation persistence failed: context canceled`，
   网关侧表现为裸 HTTP 500 `{code:3}`；ctx 尚存活时则返回 200 + 单个 error 帧。
   对应 e2e：`test_assistant.py::test_stream_emits_tokens_then_done` 等。

3. **UpdatePostV2 与 `.api` 分歧** — `content/rpc/internal/logic/update_post_logic.go:46`
   实现无条件要求 content≥1，`.api` 声明 `optional`；决策表
   `rest_decision_table_test.go:478` 的 VALID 用例本就只传 title。收口方向：
   局部更新语义（空串=保持现值）。对应 e2e：
   `test_post.py::test_update_requires_non_empty_content`（修复后需反转）。

### 怪癖（记录存档，暂不修）

- 评论回复创建成功后无任何 REST 端点可读取（列表只返回根评论）。
- 超限上传返回 413 空 body，破坏统一 `{code,message}` 信封。
- `/auth/verify-code` 成功时 body 为字面量 `null`。
- 推荐流游标绑定首次请求的 `requestId`，换 id 即参数错误（未文档化强约束）。
- 行为事件校验严：exposure 需 scene+requestId+position≥1；客户端禁报 like/impression；
  空/超量批次报错文案含「行为事件数量」。

### 现场环境

- assistant 外部 LLM（opencode.ai zen）经本机代理当前不可达：失败时有时 500、
  有时 200+error 帧。e2e 套件对「无 token 帧」的用例保留 skip 语义，
  上游恢复后自动转严格断言。
