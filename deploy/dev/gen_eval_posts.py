#!/usr/bin/env python3
"""Batch-generate synthetic community posts via OpenCode Go Responses API.

Reads ASSISTANT_LLM_API_KEY from little-white-box-content-community/.env.
Does not replace the frozen eval/corpus.json (ids 1001-1300).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "little-white-box-content-community"
ENV_PATH = BACKEND / ".env"
DEFAULT_OUT = Path(__file__).resolve().parent / "corpus_2000.json"
DEFAULT_CHECKPOINT = Path("/tmp/xbh-gen-corpus-2000.chunks.json")

API_URL = "https://opencode.ai/zen/go/v1/responses"
MODEL = "muse-spark-1.2-contributor"
START_ID = 2001
TOTAL = 2000
CHUNK = 20
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

TOPICS = [
    "容器化部署与线上故障复盘",
    "Python 小工具与装饰器实战",
    "自建 NAS、硬盘健康和备份",
    "家庭红烧肉与家常菜改良",
    "减脂早餐与一周便当",
    "城市周边两日游路线",
    "跑步入门与膝盖保护",
    "亲子阅读与睡前故事",
    "基金定投与生活记账",
    "养猫日常与驱虫经验",
    "手工皮具和木作入门",
    "K8s 服务发现排障",
    "C++ 内存泄漏排查思路",
    "咖啡豆烘焙与手冲参数",
    "租房改造与收纳技巧",
    "相机选购和出片后期",
    "职场沟通和周报写法",
    "考研自习室作息分享",
    "电动车保养与通勤",
    "家用投影仪观影体验",
    "SQL 慢查询优化笔记",
    "周末菜市场买菜记账",
    "露营装备避坑清单",
    "力量训练新手计划",
    "二手书市场淘书故事",
    "宝宝辅食添加记录",
    "信用卡积分实用玩法",
    "遛狗社交与行为训练",
    "十字绣和编织解压",
    "家庭 Wi-Fi 与组网",
    "前端性能优化小记",
    "火锅底料自己熬",
    "博物馆半日游览攻略",
    "羽毛球拍线与护具",
    "播客和有声书推荐",
    "暑假作业陪读心得",
    "副业接单时间管理",
    "仓鼠和兔子饲养注意事项",
    "旧家具翻新油漆",
    "机械键盘轴体体验",
    "Go 并发踩坑实录",
    "早餐店探店对比",
    "高原反应和行程规划",
    "居家拉伸缓解久坐",
    "推理小说书单",
    "幼儿园入园适应",
    "公积金提取实操",
    "猫咪绝育前后护理",
    "黏土手作小物件",
    "显示器色彩校准",
    "Redis 缓存雪崩处理",
    "空气炸锅减油菜谱",
    "古镇淡季住宿体验",
    "游泳换气练习",
    "纪录片观后感",
    "小学生错题本方法",
    "房租水电分摊表格",
    "狗粮成分怎么看",
    "手机摄影构图练习",
    "树莓派家庭实验室",
    "Linux 桌面日常使用",
    "路边摊夜宵测评",
    "雨季徒步防滑装备",
    "办公室工位改造",
    "英语阅读打卡",
    "二胎家庭时间分配",
    "保险条款阅读笔记",
    "宠物医保理赔流程",
    "乐高零件收纳",
    "耳机选购听感对比",
    "Git 误操作恢复",
    "素食便当搭配",
    "高铁沿线城市短途",
    "骑行通勤路线",
    "话剧和展览票根",
    "青少年手机使用约定",
    "换工作谈薪准备",
    "鱼缸开缸与水质",
    "阳台种植蔬菜",
    "平板电脑笔记软件",
    "Elasticsearch 分词调参",
    "凉拌菜夏季菜单",
    "海岛防晒与潮汐",
    "居家有氧跟练",
    "科普书摘抄",
    "家长会沟通记录",
    "生活费结余习惯",
    "流浪猫救助经历",
    "布艺改造旧衣服",
    "智能家居场景",
    "开源项目贡献第一 PR",
    "早餐粥与小菜",
    "夜市小吃卫生观察",
    "滑雪租装备注意",
    "黑胶唱片入门",
    "实习周记真实吐槽",
    "装修报价对比",
    "鹦鹉喂养噪音处理",
    "3D 打印家用小件",
    "密码管理与 2FA",
]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def api_key() -> str:
    env = load_env(ENV_PATH)
    key = os.environ.get("ASSISTANT_LLM_API_KEY") or env.get("ASSISTANT_LLM_API_KEY", "")
    if not key:
        raise SystemExit(f"missing ASSISTANT_LLM_API_KEY in {ENV_PATH} or environment")
    return key


def extract_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if block.get("type") in ("output_text", "text"):
                parts.append(str(block.get("text") or ""))
    text = "".join(parts).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def parse_posts(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, list):
        posts = data
    elif isinstance(data, dict):
        posts = data.get("posts")
        if posts is None:
            raise ValueError("JSON missing posts[]")
    else:
        raise ValueError("unexpected JSON root")
    if not isinstance(posts, list):
        raise ValueError("posts is not a list")
    cleaned: list[dict] = []
    for post in posts:
        if not isinstance(post, dict):
            raise ValueError("post is not an object")
        title = str(post.get("title") or "").strip()
        content = str(post.get("content") or "").strip()
        if not (6 <= len(title) <= 80):
            raise ValueError(f"bad title length {len(title)}: {title[:40]}")
        if not (40 <= len(content) <= 800):
            raise ValueError(f"bad content length {len(content)}: {title[:40]}")
        cleaned.append({"title": title, "content": content, "status": 1})
    return cleaned


def responses_create(key: str, prompt: str, max_output_tokens: int) -> dict:
    body = {
        "model": MODEL,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": "minimal"},
        "text": {"format": {"type": "json_object"}},
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {exc.code}: {err}") from exc


def chunk_prompt(topic: str, n: int, avoid: list[str]) -> str:
    avoid_block = ""
    if avoid:
        listed = "；".join(avoid[:12])
        avoid_block = f"\n不要与这些已有标题重复或高度相似：{listed}"
    return f"""你是中文社区内容作者。请生成 {n} 篇已发布帖子，主题围绕「{topic}」。
要求：第一人称、具体细节、真实生活感、篇与篇互不重复；像普通用户发帖，不要广告腔。
只输出 JSON：{{"posts":[{{"title":str,"content":str}}]}}
title 10-30 字；content 80-160 字；简体中文；禁止 markdown 与额外文字。{avoid_block}"""


def generate_chunk(key: str, topic: str, n: int, avoid: list[str]) -> list[dict]:
    last_err: Exception | None = None
    max_tokens = 8000
    for attempt in range(1, 5):
        try:
            payload = responses_create(key, chunk_prompt(topic, n, avoid), max_tokens)
            status = payload.get("status")
            if status == "incomplete":
                reason = (payload.get("incomplete_details") or {}).get("reason")
                if reason == "max_output_tokens":
                    max_tokens = min(max_tokens * 2, 16000)
                    raise RuntimeError(f"truncated ({reason})")
                raise RuntimeError(f"incomplete: {reason}")
            if status not in (None, "completed"):
                raise RuntimeError(f"status={status} error={payload.get('error')}")
            posts = parse_posts(extract_text(payload))
            if len(posts) < n:
                raise RuntimeError(f"got {len(posts)} posts, want {n}")
            return posts[:n]
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(min(8, 2 * attempt))
    raise RuntimeError(f"chunk {topic!r} failed: {last_err}")


def load_checkpoint(path: Path) -> dict[str, list[dict]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    chunks = data.get("chunks") if isinstance(data, dict) else None
    if not isinstance(chunks, dict):
        return {}
    return {str(k): v for k, v in chunks.items() if isinstance(v, list)}


def save_checkpoint(path: Path, chunks: dict[str, list[dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"chunks": chunks}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def assemble(chunks: dict[str, list[dict]], n_chunks: int) -> list[dict]:
    posts: list[dict] = []
    for idx in range(n_chunks):
        key = str(idx)
        if key not in chunks:
            raise RuntimeError(f"missing chunk {idx}")
        batch = chunks[key]
        start = START_ID + idx * CHUNK
        for offset, post in enumerate(batch):
            posts.append(
                {
                    "id": start + offset,
                    "title": post["title"],
                    "content": post["content"],
                    "status": 1,
                }
            )
    return posts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--total", type=int, default=TOTAL)
    args = parser.parse_args()
    if args.total % CHUNK != 0:
        print(f"--total must be multiple of {CHUNK}", file=sys.stderr)
        return 1
    n_chunks = args.total // CHUNK
    if n_chunks > len(TOPICS):
        print(f"need {n_chunks} topics, have {len(TOPICS)}", file=sys.stderr)
        return 1

    key = api_key()
    lock = threading.Lock()
    chunks = load_checkpoint(args.checkpoint)
    pending = [i for i in range(n_chunks) if str(i) not in chunks]
    print(
        f"generating {args.total} posts in {n_chunks} chunks; "
        f"resume {n_chunks - len(pending)} done, {len(pending)} remaining",
        flush=True,
    )
    if pending:
        avoid_seed = []
        if (BACKEND / "eval/corpus.json").is_file():
            frozen = json.loads((BACKEND / "eval/corpus.json").read_text(encoding="utf-8"))
            avoid_seed = [str(p.get("title") or "") for p in frozen.get("posts", [])[:20]]

        def work(idx: int) -> tuple[int, list[dict]]:
            topic = TOPICS[idx]
            return idx, generate_chunk(key, topic, CHUNK, avoid_seed)

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(work, idx) for idx in pending]
            for fut in as_completed(futures):
                idx, posts = fut.result()
                with lock:
                    chunks[str(idx)] = posts
                    save_checkpoint(args.checkpoint, chunks)
                    done = sum(1 for i in range(n_chunks) if str(i) in chunks)
                    print(f"  chunk {idx + 1}/{n_chunks} ok ({done}/{n_chunks})", flush=True)

    posts = assemble(chunks, n_chunks)
    payload = {
        "version": 1,
        "frozen": False,
        "note": (
            "LLM-generated local seed posts (not the frozen eval corpus). "
            f"model={MODEL} via Responses API. ids {START_ID}-{START_ID + args.total - 1}."
        ),
        "model": MODEL,
        "endpoint": API_URL,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posts": posts,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(posts)} posts to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
