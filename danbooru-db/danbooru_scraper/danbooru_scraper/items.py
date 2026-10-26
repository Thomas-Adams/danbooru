# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy

class TagDefinitionItem(scrapy.Item):
    tag = scrapy.Field()
    title = scrapy.Field()
    url = scrapy.Field()

    body_text = scrapy.Field()

    # Links found in the body to other wiki pages
    related = scrapy.Field()          # list[{"title": str, "url": str}]

    # “Posts” section: first 8 items with link, title, and thumbnail
    posts = scrapy.Field()            # list[{"post_url": str, "title": str|None, "thumb_url": str|None}]

    # “Examples” section: all example thumbnail URLs
    example_images = scrapy.Field()   # list[str]

    # All groups this tag belongs to (deduped)
    tag_groups = scrapy.Field()       # list[{"slug": str, "title": str, "url": str}]

    # All unique group paths (index → … → leaf group) that led to this tag
    group_paths = scrapy.Field()      # list[list[{"slug": str, "title": str, "url": str}]]





