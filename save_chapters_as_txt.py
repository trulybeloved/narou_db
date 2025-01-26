import json
from bs4 import BeautifulSoup
import custom_modules.utilities as utils
import custom_modules.narou_parser as narou_parser

with open('datastores/chapter_scrape_resuslt.json', 'r', encoding='utf-8') as chapter_scrape_results_json:
    chapter_scrape_results = json.loads(chapter_scrape_results_json.read())

for chapter in chapter_scrape_results:

    narou_uid = narou_parser.get_narou_uid_from_url(chapter['scraped_url'])
    print(narou_uid)

    chapter_title_soup = BeautifulSoup(chapter['scrape_results']['.p-novel__title'], 'html.parser')
    chapter_soup = BeautifulSoup(chapter['scrape_results']['.p-novel__body'], 'html.parser')

    chapter_text = f'{chapter_title_soup.text}\n{chapter_soup.text}'
    chapter_html = f'{chapter_title_soup.prettify()}\n{chapter_soup.prettify()}'

    chapter_title_parse_results = narou_parser.parse_chapter_title(chapter_title_soup.text)
    print(chapter_title_parse_results)

    with open(f'datastores/chapter_raws_txt/{narou_uid}.txt', 'w', encoding='utf-8') as raw_text_file:
        raw_text_file.write(chapter_text)

    with open(f'datastores/chapter_raws_html/{narou_uid}.html' , 'w', encoding='utf-8') as raw_html_file:
        raw_html_file.write(chapter_html)


