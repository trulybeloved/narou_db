import os.path

from bs4 import BeautifulSoup
from custom_modules.utilities import convert_to_unix_timestamp
import pprint

JAPANESE_NUMBERS_UNICODE_MAP = {
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4", "５": "5", "６": "6", "７": "7", "８": "8", "９": "9"
}

JAPANESE_FULL_WIDTH_ALPHABETS_UNICODE_MAP = {
    "Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E", "Ｆ": "F", "Ｇ": "G", "Ｈ": "H", "Ｉ": "I", "Ｊ": "J", "Ｋ": "K",
    "Ｌ": "L", "Ｍ": "M", "Ｎ": "N", "Ｏ": "O", "Ｐ": "P", "Ｑ": "Q", "Ｒ": "R", "Ｓ": "S", "Ｔ": "T", "Ｕ": "U", "Ｖ": "V",
    "Ｗ": "W", "Ｘ": "X", "Ｙ": "Y", "Ｚ": "Z", "ａ": "a", "ｂ": "b", "ｃ": "c", "ｄ": "d", "ｅ": "e", "ｆ": "f", "ｇ": "g",
    "ｈ": "h", "ｉ": "i", "ｊ": "j", "ｋ": "k", "ｌ": "l", "ｍ": "m", "ｎ": "n", "ｏ": "o", "ｐ": "p", "ｑ": "q", "ｒ": "r",
    "ｓ": "s", "ｔ": "t", "ｕ": "u", "ｖ": "v", "ｗ": "w", "ｘ": "x", "ｙ": "y", "ｚ": "z"
}

RZ_JAPANESE_ARC_LABELS_MAP = {
    "第一章": "1",
    "第二章": "2",
    "第三章": "3",
    "第四章": "4",
    "第五章": "5",
    "第六章": "6",
    "第七章": "7",
    "第八章": "8",
    "第九章": "9",
    "第十章": "10",
    "第十一章": "11",
    "第十二章": "12"
}

RZ_JAPANESE_EXTRA_LABEL = "リゼロＥＸ"

RZ_JAPANESE_INTERLUDE_LABEL = "幕間"

RZ_JAPANESE_FINAL_LABEL = "終幕"

RZ_JAPANESE_PROLOGUE_LABEL = "プロローグ"

def parse_chapter_title(chapter_title: str) -> dict:
    results = {}

    def get_arc_number(_jp_chapter_label: str) -> str:
        for key, value in RZ_JAPANESE_ARC_LABELS_MAP.items():
            if key in _jp_chapter_label:
                return value

    def get_chapter_number(_jp_chapter_label: str) -> str:
        if RZ_JAPANESE_INTERLUDE_LABEL in _jp_chapter_label:
            return 'Interlude'

        if RZ_JAPANESE_FINAL_LABEL in _jp_chapter_label:
            return 'Final'

        if RZ_JAPANESE_PROLOGUE_LABEL in _jp_chapter_label:
            return 'Prologue'

        for key, value in RZ_JAPANESE_ARC_LABELS_MAP.items():
            _jp_chapter_label = _jp_chapter_label.replace(key, '')

        for key, value in JAPANESE_NUMBERS_UNICODE_MAP.items():
            _jp_chapter_label = _jp_chapter_label.replace(key, value)

        for key, value in JAPANESE_FULL_WIDTH_ALPHABETS_UNICODE_MAP.items():
            _jp_chapter_label = _jp_chapter_label.replace(key, value)

        chapter_number = _jp_chapter_label

        return chapter_number

    title_components = chapter_title.split('　')
    jp_chapter_label = title_components[0]
    jp_chapter_name = title_components[1]

    if jp_chapter_label == RZ_JAPANESE_EXTRA_LABEL:
        results['chapter_type'] = 'EX'
        results['arc_number'] = None
        results['chapter_number'] = None
        results['jp_chapter_name'] = jp_chapter_name
        return results

    else:
        arc_number = get_arc_number(jp_chapter_label)
        chapter_number = get_chapter_number(jp_chapter_label)

        results['chapter_type'] = 'MAIN'
        results['arc_number'] = arc_number
        results['chapter_number'] = chapter_number
        results['jp_chapter_name'] = jp_chapter_name
        return results


def generate_chapter_id(chapter_parse_results) -> str:
    if chapter_parse_results['chapter_type'] == 'EX':
        chapter_name = chapter_parse_results['jp_chapter_name']
        return f'rzex{chapter_name}'

    if chapter_parse_results['chapter_type'] == 'MAIN':
        arc_number = chapter_parse_results['arc_number']
        chapter_number = chapter_parse_results['chapter_number']
        return f'rza{arc_number}c{chapter_number}'


def parse_chapter_body(chapter_html: str) -> str:
    chapter_soup = BeautifulSoup(chapter_html, 'html.parser')
    paragraphs = chapter_soup.find_all('p')

    # Needs an implementation to detect ruby tags
    trimmed_paragraphs = [f'{paragraph.text}' for paragraph in paragraphs if paragraph.text]
    chapter_text = ''
    for paragraph in trimmed_paragraphs:
        chapter_text += f'{paragraph}\n\n'

    return chapter_text


def parse_narou_chapter_html(scraper_result: dict, chapter_title_selector: str, chapter_text_selector: str) -> dict:
    parse_result = {}
    parse_result['narou_link'] = scraper_result['scraped_url']
    parse_result['chapter_uid'] = str(scraper_result['scraped_url']).replace(
        'https://ncode.syosetu.com/n2267be/', '').replace('/', '')
    chapter_title = BeautifulSoup(scraper_result['scrape_results'][chapter_title_selector], 'html.parser').text
    parse_result['chapter_title'] = chapter_title
    chapter_title_parse_results = parse_chapter_title(chapter_title)
    parse_result['chapter_id'] = generate_chapter_id(chapter_title_parse_results)
    parse_result.update(chapter_title_parse_results)

    chapter_text = str()
    # chapter_text += f'{chapter_title}\n\n'
    chapter_text += parse_chapter_body(scraper_result['scrape_results'][chapter_text_selector])
    parse_result['chapter_body'] = chapter_text

    return parse_result


def parse_narou_index_html(index_html, index_entry_class_name: str, entry_published_timestamp_class_name) -> list:

    # print(index_html)

    index_soup = BeautifulSoup(index_html, 'html.parser')

    # print(index_soup)
    # print(index_soup.find_all('.p-eplist'))
    # print(index_soup.find('div').get(key='class'))

    index_entries = index_soup.find_all('div', class_=index_entry_class_name.replace('.', ''))
    print(f'Index Entries: {len(index_entries)}')
    parse_results = []

    for index_entry in index_entries:

        chapter_entry = {}
        entry_soup = BeautifulSoup(str(index_entry), 'html.parser')
        # print(entry_soup)

        chapter_title = entry_soup.find('a').text.strip()
        chapter_href = entry_soup.find('a').get(key='href')
        chapter_uid = int(chapter_href.replace('/n2267be/', '').replace('/', '').strip())
        chapter_url = f'https://ncode.syosetu.com{chapter_href}'
        chapter_timestamp_string = entry_soup.find('div', class_=entry_published_timestamp_class_name.replace('.', '')).text.strip()
        chapter_published_timestamp = chapter_timestamp_string.replace('（改）', '').strip()
        chapter_published_unix_timestamp = convert_to_unix_timestamp(chapter_published_timestamp, 9)

        # If edited
        if '（改）' in chapter_timestamp_string:
            chapter_edited = True
            edit_timestamp = entry_soup.find('span').get_attribute_list(key='title')[0].replace('改稿', '').strip()
            chapter_edited_unix_timestamp = convert_to_unix_timestamp(edit_timestamp, 9)
        else:
            chapter_edited = False
            chapter_edited_unix_timestamp = None

        chapter_entry['chapter_uid'] = chapter_uid
        chapter_entry['chapter_title'] = chapter_title
        chapter_entry['narou_link'] = chapter_url
        chapter_entry['publication_timestamp'] = chapter_published_unix_timestamp
        chapter_entry['chapter_edited'] = 1 if chapter_edited else 0
        chapter_entry['edit_timestamp'] = chapter_edited_unix_timestamp if chapter_edited_unix_timestamp else 0

        # pprint.pp(chapter_entry)

        parse_results.append(chapter_entry)

    return parse_results

def get_narou_uid_from_url(url:str) -> int:
    return int(url.replace('https://ncode.syosetu.com/n2267be/', '').replace('/',''))


if __name__ == "__main__":
    import json

    NAROU_INDEX_SELECTOR = '.p-eplist'
    INDEX_ENTRY_CLASS_NAME = '.p-eplist__sublist'
    INDEX_LINK_TITLE_SELECTOR = '.p-eplist__subtitle'
    ENTRY_PUBLISHED_TIMESTAMP_CLASS_NAME = '.p-eplist__update'
    CHAPTER_TITLE_SELECTOR = '.p-novel__title'
    CHAPTER_TEXT_SELECTOR = '.p-novel__body'

    with open(os.path.join('C:\\Users\\prav9\\OneDrive\\Desktop\\Coding\\Projects\\narou_db\\datastores\\index_scrape_results.json'), 'r', encoding='utf-8') as index_scrape_file:
        index_scrape_results = json.loads(index_scrape_file.read())

    index_entries = []

    for index_page in index_scrape_results:
        # print(index_page)
        if index_page['scrape_results'][NAROU_INDEX_SELECTOR]:
            parse_results = parse_narou_index_html(
                index_html=index_page['scrape_results'][NAROU_INDEX_SELECTOR],
                index_entry_class_name=INDEX_ENTRY_CLASS_NAME,
                entry_published_timestamp_class_name=ENTRY_PUBLISHED_TIMESTAMP_CLASS_NAME)
            # print(parse_results)
            for result in parse_results:
                index_entries.append(result)

    with open('index_parse.json', 'w', encoding='utf-8') as parse_file:
        parse_file.write(json.dumps(index_entries, indent=4, ensure_ascii=False))
