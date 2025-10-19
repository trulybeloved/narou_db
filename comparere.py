import json
from bs4 import BeautifulSoup

with open('datastores/chapters/https___ncode.syosetu.com_n2267be_217_.json', 'r', encoding='utf-8') as json_file:
    scrape_results = json.loads(json_file.read())

soup = BeautifulSoup(scrape_results['scrape_results']['#novel_honbun'], 'html.parser')

print(soup.text)

with open('output_text.txt', 'w', encoding='utf-8') as text_file:
    text_file.write(soup.text)