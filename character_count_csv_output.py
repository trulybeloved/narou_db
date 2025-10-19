import os
import csv
import re
from bs4 import BeautifulSoup

def generate_csv_from_text_files(folder_path, output_csv):
    """
    Reads all text files in a folder and generates a CSV file with file names and
    character counts (excluding all whitespace, including \u3000).

    :param folder_path: Path to the folder containing text files.
    :param output_csv: Path to save the output CSV file.
    """
    # Ensure the folder exists
    if not os.path.exists(folder_path):
        print(f"Folder '{folder_path}' does not exist.")
        return

    # Prepare the CSV data
    csv_data = []

    for file_name in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file_name)

        # Process only text files
        if os.path.isfile(file_path) and file_name.endswith(".txt"):
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()
                # Remove all whitespace, including \u3000
                stripped_content = re.sub(r"\s|\u3000", "", content)
                content_soup = BeautifulSoup(stripped_content, 'html.parser')
                stripped_content = content_soup.text
                char_count = len(stripped_content)

            print(file_name, char_count)
            # Add file name and character count to the CSV data
            csv_data.append([file_name, char_count])

    # Write data to CSV
    with open(output_csv, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        # Write the header
        writer.writerow(["File Name", "Character Count"])
        # Write the data
        writer.writerows(csv_data)

    print(f"CSV file has been generated at '{output_csv}'.")

# Example usage
folder_path = os.path.join(os.getcwd(), 'datastores', 'chapter_raws_txt')
generate_csv_from_text_files(folder_path, "output.csv")
