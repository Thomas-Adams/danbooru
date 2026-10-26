-- One row per wiki page you scrape
CREATE TABLE IF NOT EXISTS wiki_page (
  id               BIGSERIAL PRIMARY KEY,
  slug             TEXT UNIQUE NOT NULL,              -- e.g. "umineko_no_naku_koro_ni"
  title            TEXT,
  category         TEXT,                              -- if you capture it
  source_url       TEXT NOT NULL,
  body_html        TEXT,
  body_text        TEXT,
  post_count       INT,
  last_scraped_at  TIMESTAMPTZ DEFAULT now(),
  checksum         TEXT                               -- hash of the main payload to detect changes
);

-- Danbooru tags referenced by wiki pages / groups
CREATE TABLE IF NOT EXISTS tag (
  id        BIGSERIAL PRIMARY KEY,
  name      TEXT UNIQUE NOT NULL,                     -- e.g. "harukana_receive"
  category  INT,                                      -- Danbooru numeric category if you have it
  meta      JSONB DEFAULT '{}'::jsonb
);

-- Many-to-many: wiki page ↔ tags used on that page (if you collect them)
CREATE TABLE IF NOT EXISTS wiki_page_tag (
  wiki_page_id BIGINT REFERENCES wiki_page(id) ON DELETE CASCADE,
  tag_id       BIGINT REFERENCES tag(id)       ON DELETE CASCADE,
  PRIMARY KEY (wiki_page_id, tag_id)
);

-- Recent posts shown on the wiki page (the “Posts” gallery)
CREATE TABLE IF NOT EXISTS post (
  post_id     BIGINT PRIMARY KEY,                     -- Danbooru post id
  rating      TEXT,
  md5         TEXT,
  file_url    TEXT,
  preview_url TEXT,
  width       INT,
  height      INT,
  score       INT,
  tags        TEXT[]                                  -- if you grab the tag list
);

CREATE TABLE IF NOT EXISTS wiki_page_post (
  wiki_page_id BIGINT REFERENCES wiki_page(id) ON DELETE CASCADE,
  post_id      BIGINT REFERENCES post(post_id) ON DELETE CASCADE,
  position     INT,                                   -- order in the gallery
  PRIMARY KEY (wiki_page_id, post_id)
);

-- Images embedded on the wiki page (examples, inline diagrams, etc.)
CREATE TYPE image_kind AS ENUM ('example','inline','other');
-- If the type already exists, skip creating it.

CREATE TABLE IF NOT EXISTS image (
  id            BIGSERIAL PRIMARY KEY,
  wiki_page_id  BIGINT REFERENCES wiki_page(id) ON DELETE CASCADE,
  url           TEXT NOT NULL,
  alt           TEXT,
  data_title    TEXT,                                 -- from data-title (needed in your note)
  width         INT,
  height        INT,
  position      INT,
  kind          image_kind NOT NULL DEFAULT 'other',
  md5           TEXT,
  UNIQUE (wiki_page_id, url)
);

-- Optional: Tag groups (if you scrape them)
CREATE TABLE IF NOT EXISTS tag_group (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,
  description TEXT,
  source_url  TEXT
);

CREATE TABLE IF NOT EXISTS tag_group_member (
  group_id BIGINT REFERENCES tag_group(id) ON DELETE CASCADE,
  tag_id   BIGINT REFERENCES tag(id)       ON DELETE CASCADE,
  ord      INT,
  role     TEXT,
  PRIMARY KEY (group_id, tag_id)
);

-- Keep the original JSON as an audit trail (optional but very handy)
CREATE TABLE IF NOT EXISTS raw_capture (
  id            BIGSERIAL PRIMARY KEY,
  wiki_slug     TEXT NOT NULL,
  captured_at   TIMESTAMPTZ DEFAULT now(),
  payload       JSONB NOT NULL
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_wiki_page_last_scraped ON wiki_page(last_scraped_at);
CREATE INDEX IF NOT EXISTS idx_post_score ON post(score);
CREATE INDEX IF NOT EXISTS idx_image_kind ON image(kind);
