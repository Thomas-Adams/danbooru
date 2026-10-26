#!/usr/bin/env python3
import argparse, json, re
from pathlib import Path
from datetime import datetime
import psycopg
from psycopg.rows import dict_row

POST_ID_RE = re.compile(r"/posts/(\d+)(?:\D|$)")


def iter_items(json_path: Path):
    text = json_path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        data = json.loads(text)
        for obj in data:
            yield obj
    else:
        # JSON Lines
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def upsert_wiki_tag(cur, item):
    cur.execute(
        """
        INSERT INTO wiki_tag (tag, title, url, body_text, last_scraped_at)
        VALUES (%(tag)s, %(title)s, %(url)s, %(body_text)s, %(now)s)
        ON CONFLICT (tag) DO UPDATE
          SET title = EXCLUDED.title,
              url = EXCLUDED.url,
              body_text = EXCLUDED.body_text,
              last_scraped_at = EXCLUDED.last_scraped_at
        RETURNING id
        """,
        {
            "tag": item["tag"],
            "title": item.get("title"),
            "url": item["url"],
            "body_text": item.get("body_text"),
            "now": datetime.utcnow(),
        },
    )
    return cur.fetchone()["id"]


def upsert_related(cur, wiki_tag_id, related):
    cur.execute("DELETE FROM wiki_tag_related WHERE wiki_tag_id = %s", (wiki_tag_id,))
    for pos, r in enumerate(related or [], start=1):
        cur.execute(
            """
            INSERT INTO wiki_tag_related (wiki_tag_id, rel_title, rel_url, position)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (wiki_tag_id, rel_url) DO UPDATE
              SET rel_title = EXCLUDED.rel_title,
                  position = EXCLUDED.position
            """,
            (wiki_tag_id, r.get("title"), r["url"], pos),
        )


def upsert_examples(cur, wiki_tag_id, example_images):
    cur.execute("DELETE FROM wiki_tag_example_image WHERE wiki_tag_id = %s", (wiki_tag_id,))
    for pos, url in enumerate(example_images or [], start=1):
        cur.execute(
            """
            INSERT INTO wiki_tag_example_image (wiki_tag_id, url, position)
            VALUES (%s, %s, %s)
            ON CONFLICT (wiki_tag_id, url) DO UPDATE
              SET position = EXCLUDED.position
            """,
            (wiki_tag_id, url, pos),
        )


def upsert_posts(cur, wiki_tag_id, posts):
    # Replace per-scrape to keep order identical to the page
    cur.execute("DELETE FROM wiki_tag_post WHERE wiki_tag_id = %s", (wiki_tag_id,))
    for pos, p in enumerate(posts or [], start=1):
        cur.execute(
            """
            INSERT INTO wiki_tag_post (wiki_tag_id, post_url, position, post_title, post_thumb_url)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (wiki_tag_id, post_url) DO UPDATE
              SET position       = EXCLUDED.position,
                  post_title     = EXCLUDED.post_title,
                  post_thumb_url = EXCLUDED.post_thumb_url
            """,
            (wiki_tag_id, p["post_url"], pos, p.get("title"), p.get("thumb_url")),
        )



def upsert_groups(cur, wiki_tag_id, tag_groups):
    for g in tag_groups or []:
        cur.execute(
            """
            INSERT INTO tag_group (slug, title, url)
            VALUES (%s, %s, %s)
            ON CONFLICT (slug) DO UPDATE
              SET title = COALESCE(EXCLUDED.title, tag_group.title),
                  url   = COALESCE(EXCLUDED.url,   tag_group.url)
            RETURNING id
            """,
            (g["slug"], g.get("title"), g.get("url")),
        )
        group_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO tag_group_member (wiki_tag_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (wiki_tag_id, group_id),
        )


def upsert_group_paths(cur, wiki_tag_id, group_paths):
    cur.execute("DELETE FROM tag_group_path WHERE wiki_tag_id = %s", (wiki_tag_id,))
    for path_ord, steps in enumerate(group_paths or [], start=0):
        cur.execute(
            "INSERT INTO tag_group_path (wiki_tag_id, ord) VALUES (%s, %s) RETURNING id",
            (wiki_tag_id, path_ord),
        )
        path_id = cur.fetchone()["id"]
        for step_ord, s in enumerate(steps or [], start=0):
            cur.execute(
                """
                INSERT INTO tag_group_path_step (path_id, step_ord, slug, title, url)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (path_id, step_ord, s.get("slug"), s.get("title"), s.get("url")),
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=False, default='postgresql://postgres:postgres@localhost:5432/danbooru',
                    help='e.g. postgresql://user:pass@localhost:5432/danbooru_db')
    ap.add_argument("json_file", type=Path)
    ap.add_argument("--batch", type=int, default=100, help="commit every N items")
    args = ap.parse_args()

    conn = psycopg.connect(args.dsn, autocommit=False, row_factory=dict_row)
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO danbooru, public")
            for item in iter_items(args.json_file):
                wiki_tag_id = upsert_wiki_tag(cur, item)
                upsert_related(cur, wiki_tag_id, item.get("related"))
                upsert_examples(cur, wiki_tag_id, item.get("example_images"))
                upsert_posts(cur, wiki_tag_id, item.get("posts"))
                upsert_groups(cur, wiki_tag_id, item.get("tag_groups"))
                upsert_group_paths(cur, wiki_tag_id, item.get("group_paths"))

                total += 1
                if total % args.batch == 0:
                    conn.commit()
                    print(f"Committed {total} items...")
            conn.commit()
        print(f"Done. Imported {total} items.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
