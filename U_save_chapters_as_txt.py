import json
from bs4 import BeautifulSoup
import custom_modules.utilities as utils
import custom_modules.narou_parser as narou_parser
import ZZ_config as config

def process_chapter_for_save(chapter) -> tuple:

    narou_uid = narou_parser.get_narou_uid_from_url(chapter['scraped_url'])
    narou_uid = str(narou_uid).zfill(5)
    print(narou_uid)

    chapter_title_soup = BeautifulSoup(chapter['scrape_results']['.p-novel__title'], 'html.parser')
    chapter_soup = BeautifulSoup(chapter['scrape_results']['.p-novel__body'], 'html.parser')

    # chapter_text = f'{chapter_title_soup.text}\n{chapter_soup.text}'
    chapter_html = f'{chapter_title_soup.prettify()}\n{chapter_soup.prettify()}'

    for tag in chapter_soup.find_all(True):
        if tag.name not in ['ruby', 'rt', 'rp']:
            tag.unwrap()

    chapter_text = f'{chapter_title_soup.text}\n{chapter_soup}'

    chapter_title_parse_results = narou_parser.parse_chapter_title(chapter_title_soup.text)
    print(chapter_title_parse_results)

    return chapter_text, chapter_html, narou_uid

def save_chapter_text_html(chapter_text, chapter_html, narou_uid):

    with open(f'{config.RAW_TEXT_SAVE_FOLDER}/{narou_uid}.txt', 'w', encoding='utf-8') as raw_text_file:
        raw_text_file.write(chapter_text)

    with open(f'{config.RAW_HTML_SAVE_FOLDER}/{narou_uid}.html' , 'w', encoding='utf-8') as raw_html_file:
        raw_html_file.write(chapter_html)

def save_chapter(chapter):

    try:
        chapter_text, chapter_html, narou_uid = process_chapter_for_save(chapter)
        save_chapter_text_html(chapter_text, chapter_html, narou_uid)
        return True, narou_uid
    except Exception as e:
        print(e)
        return False, 0


if __name__ == "__main__":

    with open(config.ALL_CHAPTERS_SCRAPE_JSON, 'r', encoding='utf-8') as chapter_scrape_results_json:
        chapter_scrape_results = json.loads(chapter_scrape_results_json.read())

    for chapter in chapter_scrape_results:

        chapter_text, chapter_html, narou_uid = process_chapter_for_save(chapter)

        save_chapter_text_html(chapter_text, chapter_html, narou_uid)
