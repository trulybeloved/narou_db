import json
import pprint

from custom_modules.narou_parser import parse_narou_index_html, parse_narou_chapter_html
#
# with open('datastores/index_scrape_resuslts.json', 'r', encoding='utf-8') as index_scrape_file:
#     index_scrape_results = json.loads(index_scrape_file.read())
#
# complete_index_parse_results = []
#
# for index_page in index_scrape_results:
#     if index_page['scrape_results']['.index_box']:
#         parse_results = parse_narou_index_html(index_page['scrape_results']['.index_box'])
#         for parse_result in parse_results:
#             complete_index_parse_results.append(parse_result)
#
# chapter_links = [parse_result['chapter_url'] for parse_result in complete_index_parse_results]
# print(chapter_links)
#
with open('datastores/chapter_scrape_resuslt.json', 'r', encoding='utf-8') as chapter_scrape_file:
    chapter_scrape_results = json.loads(chapter_scrape_file.read())

complete_index_parse_results = []

for chapter in chapter_scrape_results:
    if chapter['scrape_results']['.novel_subtitle']:
        chapter_parse_results = parse_narou_chapter_html(chapter)
        print(json.dumps(chapter_parse_results, ensure_ascii=False, indent=4))

chapter_links = [parse_result['chapter_url'] for parse_result in complete_index_parse_results]
print(chapter_links)

