#!/usr/bin/env python3
"""Emit idempotent MySQL for eval/corpus.json (frozen posts 1001-1300).

Seed conventions match the frozen-eval import used by live gates and
`scripts/gen_recommend_samples.py`:
  - author is local admin (looked up at apply time)
  - status=1, revision at least 1
  - view_count = id % 97
  - created_at/published_at start 2026-07-01 08:00:00, +137 min per id
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

START = datetime(2026, 7, 1, 8, 0, 0)
STEP = timedelta(minutes=137)
AUTHOR_SQL = "@eval_author_id"


def sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def post_timestamp(post_id: int) -> str:
    return (START + STEP * (post_id - 1001)).strftime("%Y-%m-%d %H:%M:%S")


def load_posts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    posts = payload.get("posts")
    if not isinstance(posts, list) or not posts:
        raise SystemExit(f"{path}: missing posts[]")
    return posts


def emit_sql(posts: list[dict]) -> str:
    rows: list[str] = []
    for post in posts:
        post_id = int(post["id"])
        title = str(post["title"])
        content = str(post["content"])
        status = int(post.get("status", 1))
        ts = sql_quote(post_timestamp(post_id))
        rows.append(
            "("
            f"{post_id}, {AUTHOR_SQL}, {sql_quote(title)}, {sql_quote(content)}, "
            f"{status}, 1, {post_id % 97}, {ts}, {ts}"
            ")"
        )
    values = ",\n".join(rows)
    return f"""-- eval corpus seed ({len(posts)} posts). Must be applied as utf8mb4.
SET @eval_author_id = COALESCE(
    (SELECT `id` FROM `xbh_user`.`user_profile` WHERE `username` = 'admin' LIMIT 1),
    1
);
USE `xbh_content`;
INSERT INTO `post` (
    `id`,
    `author_id`,
    `title`,
    `content`,
    `status`,
    `revision`,
    `view_count`,
    `published_at`,
    `created_at`
) VALUES
{values} AS `new`
ON DUPLICATE KEY UPDATE
    `title` = `new`.`title`,
    `content` = `new`.`content`,
    `status` = `new`.`status`,
    `revision` = IF(`post`.`revision` < 1, 1, `post`.`revision`),
    `view_count` = `new`.`view_count`,
    `published_at` = `new`.`published_at`;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "corpus",
        nargs="+",
        type=Path,
        help="one or more corpus JSON files (eval/corpus.json, corpus_2000.json, ...)",
    )
    args = parser.parse_args()
    posts: list[dict] = []
    seen: set[int] = set()
    for path in args.corpus:
        if not path.is_file():
            print(f"missing corpus: {path}", file=sys.stderr)
            return 1
        for post in load_posts(path):
            post_id = int(post["id"])
            if post_id in seen:
                print(f"duplicate post id {post_id} from {path}", file=sys.stderr)
                return 1
            seen.add(post_id)
            posts.append(post)
    sys.stdout.write(emit_sql(posts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
