# pipelines.py
import re
from datetime import datetime
import psycopg
from psycopg.rows import dict_row

POST_ID_RE = re.compile(r"/posts/(\d+)(?:\D|$)")


class PostgresPipeline:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.conn = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.get("POSTGRES_DSN"))

    def open_spider(self, spider):
        self.conn = psycopg.connect(self.dsn, autocommit=False, row_factory=dict_row)

    def close_spider(self, spider):
        if self.conn:
            self.conn.close()

    def process_item(self, item, spider):
        with self.conn.cursor() as cur:
            wiki_tag_id = self._upsert_wiki_tag(cur, item)
            self._upsert_related(cur, wiki_tag_id, item.get("related") or [])
            self._upsert_examples(cur, wiki_tag_id, item.get("example_images") or [])
            self._upsert_posts(cur, wiki_tag_id, item.get("posts") or [])
            self._upsert_groups(cur, wiki_tag_id, item.get("tag_groups") or [])
            self._upsert_group_paths(cur, wiki_tag_id, item.get("group_paths") or [])
        self.conn.commit()
        return item

    # ---------- upserts ----------

    def _upsert_wiki_tag(self, cur, item):
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

    def _upsert_related(self, cur, wiki_tag_id, related):
        # keep order stable: replace whole set each scrape
        cur.execute("DELETE FROM wiki_tag_related WHERE wiki_tag_id = %s", (wiki_tag_id,))
        for pos, r in enumerate(related, start=1):
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

    def _upsert_examples(self, cur, wiki_tag_id, example_images):
        cur.execute("DELETE FROM wiki_tag_example_image WHERE wiki_tag_id = %s", (wiki_tag_id,))
        for pos, url in enumerate(example_images, start=1):
            cur.execute(
                """
                INSERT INTO wiki_tag_example_image (wiki_tag_id, url, position)
                VALUES (%s, %s, %s)
                ON CONFLICT (wiki_tag_id, url) DO UPDATE
                  SET position = EXCLUDED.position
                """,
                (wiki_tag_id, url, pos),
            )

    def _upsert_posts(self, cur, wiki_tag_id, posts):
        # Replace link table each time to preserve current order
        cur.execute("DELETE FROM wiki_tag_post WHERE wiki_tag_id = %s", (wiki_tag_id,))
        for pos, p in enumerate(posts, start=1):
            post_url = p["post_url"]
            m = POST_ID_RE.search(post_url or "")
            post_id = int(m.group(1)) if m else None

            # Upsert post_min if we could parse an id; if not, ensure a row via URL-derived id
            if post_id is not None:
                cur.execute(
                    """
                    INSERT INTO post_min (post_id, post_url, title, thumb_url)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (post_id) DO UPDATE
                      SET post_url  = EXCLUDED.post_url,
                          title     = COALESCE(EXCLUDED.title, post_min.title),
                          thumb_url = COALESCE(EXCLUDED.thumb_url, post_min.thumb_url)
                    """,
                    (post_id, post_url, p.get("title"), p.get("thumb_url")),
                )
            else:
                # If no id, ensure post_min row via unique post_url by synthesizing an id-less row:
                # we can't insert without post_id due to PK, so just skip post_min and rely on link-by-URL.
                pass

            cur.execute(
                """
                INSERT INTO wiki_tag_post (wiki_tag_id, post_id, post_url, position)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (wiki_tag_id, post_url) DO UPDATE
                  SET post_id  = COALESCE(EXCLUDED.post_id, wiki_tag_post.post_id),
                      position = EXCLUDED.position
                """,
                (wiki_tag_id, post_id, post_url, pos),
            )

    def _upsert_groups(self, cur, wiki_tag_id, tag_groups):
        # Maintain membership set (idempotent)
        for g in tag_groups:
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
                """
                INSERT INTO tag_group_member (wiki_tag_id, group_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (wiki_tag_id, group_id),
            )

    def _upsert_group_paths(self, cur, wiki_tag_id, group_paths):
        # Simplest: clear paths and reinsert to keep exact order/shape
        cur.execute("DELETE FROM tag_group_path WHERE wiki_tag_id = %s", (wiki_tag_id,))
        for path_ord, steps in enumerate(group_paths, start=0):
            cur.execute(
                "INSERT INTO tag_group_path (wiki_tag_id, ord) VALUES (%s, %s) RETURNING id",
                (wiki_tag_id, path_ord),
            )
            path_id = cur.fetchone()["id"]
            for step_ord, s in enumerate(steps, start=0):
                cur.execute(
                    """
                    INSERT INTO tag_group_path_step (path_id, step_ord, slug, title, url)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (path_id, step_ord, s.get("slug"), s.get("title"), s.get("url")),
                )
