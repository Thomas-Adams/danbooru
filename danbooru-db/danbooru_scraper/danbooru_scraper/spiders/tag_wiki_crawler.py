# danbooru_scraper/spiders/tag_wiki_crawler.py
from collections import defaultdict
import re
from urllib.parse import quote, unquote, parse_qs, urlparse
import scrapy
from ..items import TagDefinitionItem

BASE = "https://danbooru.donmai.us"


def is_group_url(url: str) -> bool:
    return "/wiki_pages/tag_group%3A" in url or "/wiki_pages/tag_group:" in url


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _first_from_srcset(srcset: str) -> str | None:
    if not srcset:
        return None
    first = srcset.split(",")[0].strip().split(" ")[0].strip()
    return first or None


def _https(u: str) -> str:
    if not u:
        return u
    u = u.strip()
    return ("https:" + u) if u.startswith("//") else u


def _first_from_srcset(srcset: str) -> str | None:
    if not srcset:
        return None
    # srcset like: "<url> 1x, <url2> 2x"
    first = srcset.split(",")[0].strip().split(" ")[0].strip()
    return first or None


def _group_slug_from_url(url: str) -> str:
    dec = unquote(url)
    if "tag_group:" in dec:
        return dec.split("tag_group:", 1)[-1]
    return dec.rsplit("/", 1)[-1]


class TagWikiCrawler(scrapy.Spider):
    name = "tag_wiki"
    allowed_domains = ["danbooru.donmai.us"]
    start_urls = [f"{BASE}/wiki_pages/tag_groups"]

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "USER_AGENT": "danbooru-db-scraper",
        "DOWNLOAD_DELAY": 0.75,
        "AUTOTHROTTLE_ENABLED": True,
        "FEED_EXPORT_ENCODING": "utf-8",
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_wiki = set()
        self.seen_tags = set()
        self.tag_groups_for_tag = defaultdict(set)  # tag -> set(group_info as tuple(items))
        self.tag_paths_for_tag = defaultdict(set)  # tag -> set( tuple(group_info_dicts) )

    # --- entry: collect all top-level group links with an empty path ---
    def parse(self, response):
        for href in response.css('a[href*="/wiki_pages/tag_group"]::attr(href)').getall():
            url = response.urljoin(href)
            if url not in self.seen_wiki and is_group_url(url):
                self.seen_wiki.add(url)
                yield response.follow(url, callback=self.parse_group, cb_kwargs={"group_path": []})

    # --- parse a tag_group:* page ---
    def parse_group(self, response, group_path):
        # identify this group
        group_url = response.url
        group_slug = _group_slug_from_url(group_url)
        title_raw = (response.css("h1::text").get() or "").strip()
        group_title = title_raw.split(":", 1)[-1].strip() if title_raw.lower().startswith("tag group:") else title_raw
        if not group_title:
            group_title = group_slug.replace("_", " ")
        group_info = {"slug": group_slug, "title": group_title, "url": group_url}

        # extend the current path
        cur_path = list(group_path) + [group_info]

        # 1) follow nested groups with the extended path
        for href in response.css('a[href*="/wiki_pages/tag_group"]::attr(href)').getall():
            url = response.urljoin(href)
            if url not in self.seen_wiki and is_group_url(url):
                self.seen_wiki.add(url)
                yield response.follow(url, callback=self.parse_group, cb_kwargs={"group_path": cur_path})

        # 2) discover tags via ?tags=...
        for href in response.css('a[href^="/posts?tags="]::attr(href)').getall():
            abs_url = response.urljoin(href)
            q = parse_qs(urlparse(abs_url).query)
            tag_slug = unquote((q.get("tags") or [""])[0]).strip()
            if not tag_slug:
                continue

            # record membership + path
            self.tag_groups_for_tag[tag_slug].add(tuple(group_info.items()))
            self.tag_paths_for_tag[tag_slug].add(tuple(tuple(d.items()) for d in cur_path))

            wiki_url = f"{BASE}/wiki_pages/{quote(tag_slug, safe='')}"
            if wiki_url not in self.seen_wiki:
                self.seen_wiki.add(wiki_url)
                yield scrapy.Request(wiki_url, callback=self.parse_tag)

        # 3) follow direct wiki links; if they look like tag pages, record path too
        for href in response.css('a[href^="/wiki_pages/"]::attr(href)').getall():
            url = response.urljoin(href)
            if url in self.seen_wiki:
                continue
            self.seen_wiki.add(url)
            if is_group_url(url):
                yield response.follow(url, callback=self.parse_group, cb_kwargs={"group_path": cur_path})
            else:
                slug = unquote(url.rsplit("/", 1)[-1])
                if slug:
                    self.tag_groups_for_tag[slug].add(tuple(group_info.items()))
                    self.tag_paths_for_tag[slug].add(tuple(tuple(d.items()) for d in cur_path))
                yield response.follow(url, callback=self.parse_tag)

    # --- parse an actual tag wiki page ---
    def parse_tag(self, response):
        # classify
        h1 = (response.css("h1::text").get() or "").strip()
        if h1.lower().startswith("tag group:"):
            # misclassified; it’s a group
            yield from self.parse_group(response, group_path=[])
            return

        slug = unquote(response.url.rsplit("/", 1)[-1])
        if slug in self.seen_tags:
            return
        self.seen_tags.add(slug)

        # BODY (paragraph aware)
        body_root = response.xpath('//*[@id="wiki-page-body"]')

        def _block_texts(sel):
            blocks = sel.xpath('.//p')
            if not blocks:
                blocks = sel.xpath('.//li')
            for b in blocks:
                t = " ".join(b.xpath('.//text()').getall())
                t = re.sub(r'\s+', ' ', t).strip()
                if t:
                    yield t

        if body_root:
            blocks = list(_block_texts(body_root))
            body_text = "\n\n".join(blocks) if blocks else (body_root.xpath('normalize-space(string(.))').get() or None)
        else:
            any_body = response.xpath('//div[contains(@class,"wiki-page-body") or contains(@class,"prose")]')
            blocks = list(_block_texts(any_body)) if any_body else []
            body_text = ("\n\n".join(blocks) if blocks else (
                any_body.xpath('normalize-space(string(.))').get() if any_body else None))

        # RELATED links (within body)
        related, seen_links = [], set()
        for a in response.xpath(
                '//*[@id="wiki-page-body"]//a[starts-with(@href, "/wiki_pages/")] | '
                '//div[contains(@class,"wiki-page-body")]//a[starts-with(@href, "/wiki_pages/")]'
        ):
            href = a.xpath('@href').get() or ""
            if "tag_group%3A" in href or "tag_group:" in href:
                continue
            url = response.urljoin(href)
            if url in seen_links:
                continue
            title = _normalize_ws("".join(a.xpath('.//text()').getall()))
            if not title:
                title = unquote(url.rsplit('/', 1)[-1]).replace('_', ' ')
            related.append({"title": title, "url": url})
            seen_links.add(url)

        # ---------- POSTS (first 8) ----------
        # Works for pages like /wiki_pages/umineko_no_naku_koro_ni and /wiki_pages/harukana_receive
        posts = []
        seen_posts = set()

        # scope to the "recent-posts" container → its post-gallery grid
        for a in response.xpath(
                '//div[contains(@class,"recent-posts")]'
                '//div[contains(@class,"post-gallery")]'
                '//a[starts-with(@href, "/posts/")]'
        ):
            post_href = a.xpath('@href').get()
            post_url = response.urljoin(post_href) if post_href else None
            if not post_url or post_url in seen_posts:
                continue
            seen_posts.add(post_url)

            # Title comes from the image's data-title (preferred), else img@title/alt
            title = (
                    a.xpath('.//img/@data-title').get()
                    or a.xpath('.//img/@title').get()
                    or a.xpath('.//img/@alt').get()
                    or None
            )
            if title:
                title = _normalize_ws(title)

            # Thumbnail: img@src or @data-src, else first URL from <source srcset>
            thumb = a.xpath('.//img/@src').get() or a.xpath('.//img/@data-src').get()
            if not thumb:
                srcset = a.xpath('.//source/@srcset').get()
                if srcset:
                    thumb = _first_from_srcset(srcset)
            thumb = _https(thumb) if thumb else None

            posts.append({"post_url": post_url, "title": title, "thumb_url": thumb})
            if len(posts) == 8:
                break

        # ---------- EXAMPLES(handles < h4 > Examples < / h4 > < media - gallery >) ----------
        # Find a heading whose normalized, lowercase text is "examples" (h2–h5)
        examples_heading = response.xpath(
            '(//h2|//h3|//h4|//h5)'
            '[translate(normalize-space(string(.)),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="examples"][1]'
        )
        # Prefer a <media-gallery> immediately following that heading
        ex_root = examples_heading.xpath('following-sibling::media-gallery[1]')

        # Fallback: legacy anchor
        if not ex_root:
            ex_root = response.xpath('//*[@id="wiki-page-examples"]')

        example_images, ex_seen = [], set()
        if ex_root:
            # Collect from <img> and <source srcset> inside the gallery/section
            candidates = []
            candidates += ex_root.xpath('.//img/@src').getall()
            candidates += ex_root.xpath('.//img/@data-src').getall()
            candidates += ex_root.xpath('.//source/@srcset').getall()

            for u in candidates:
                if u and ("," in u or " " in u):  # likely a srcset
                    u = _first_from_srcset(u) or ""
                u = _https(u)
                if u and u not in ex_seen:
                    ex_seen.add(u)
                    example_images.append(u)

        # As a last resort, take images from the very next sibling block after the heading
        if not example_images and examples_heading:
            sib = examples_heading.xpath('following-sibling::*[1]')
            if sib:
                candidates = []
                candidates += sib.xpath('.//img/@src').getall()
                candidates += sib.xpath('.//img/@data-src').getall()
                candidates += sib.xpath('.//source/@srcset').getall()
                for u in candidates:
                    if u and ("," in u or " " in u):
                        u = _first_from_srcset(u) or ""
                    u = _https(u)
                    if u and u not in ex_seen:
                        ex_seen.add(u)
                        example_images.append(u)

        # Assemble groups + all unique paths that led here
        raw_groups = [dict(t) for t in self.tag_groups_for_tag.get(slug, [])]
        tag_groups = sorted(raw_groups, key=lambda g: g["slug"])

        raw_paths = self.tag_paths_for_tag.get(slug, set())
        group_paths = []
        for tup in raw_paths:  # tup is a tuple of dict-items tuples
            path = [dict(items) for items in tup]
            group_paths.append(path)

        yield TagDefinitionItem(
            tag=slug,
            title=h1 or slug.replace("_", " "),
            url=response.url,
            body_text=body_text,
            related=related,
            posts=posts,
            example_images=example_images,
            tag_groups=tag_groups,
            group_paths=group_paths,
        )
