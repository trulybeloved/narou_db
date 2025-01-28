from datetime import datetime, timezone, timedelta
import time
import git
import backoff
import re
import os
import json

from loguru import logger
from tqdm import tqdm


def convert_to_unix_timestamp(timestamp_str: str, offset_hours: float) -> int:
    # Define the format of the input timestamp
    date_format = "%Y/%m/%d %H:%M"

    # Parse the timestamp string into a datetime object
    dt = datetime.strptime(timestamp_str, date_format)

    # Create a timezone offset
    offset = timedelta(hours=offset_hours)

    # Set the datetime object to the specified timezone (UTC+9)
    dt_with_offset = dt.replace(tzinfo=timezone(offset))

    # Convert the datetime object to UTC
    dt_utc = dt_with_offset.astimezone(timezone.utc)

    # Convert the datetime object to a Unix timestamp
    unix_timestamp = int(dt_utc.timestamp())

    return unix_timestamp

def get_current_unix_timestamp(mode: int = 1) -> int:
    current_time = datetime.utcnow()
    if mode == 1:
        current_time = datetime.fromtimestamp(timestamp=time.time())
    return int(current_time.timestamp())

def to_filename_friendly(string):
    """
    Convert any string into a filename friendly format.
    """
    # Replace invalid characters with underscores
    filename_friendly = re.sub(r'[^\w\-_. ]', '_', string)
    # Remove leading and trailing spaces
    filename_friendly = filename_friendly.strip()
    # Replace multiple spaces with a single space
    filename_friendly = re.sub(r'\s+', ' ', filename_friendly)
    # Replace spaces with underscores
    filename_friendly = filename_friendly.replace(' ', '_')
    return filename_friendly

def list_files(folder_path):
    """
    Returns a list of paths of all files in a folder and its subfolders.

    Args:
    folder_path (str): The path to the folder.

    Returns:
    list: A list of paths of all files.
    """
    file_paths = []
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            file_paths.append(os.path.join(root, file))

    return file_paths


from loguru import logger
from tqdm import tqdm
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

def read_text_file(file_path: str) -> str:
    # Validate file path
    if not isinstance(file_path, str):
        logging.error("Invalid file path: file path must be a string.")
        raise ValueError("file_path must be a string")

    # Check if the file exists
    if not os.path.isfile(file_path):
        logging.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"No such file: '{file_path}'")

    # Check for potential path traversal attacks
    if os.path.relpath(file_path).startswith('..'):
        logging.error("Path traversal detected.")
        raise ValueError("Invalid file path: path traversal detected")

    try:
        # Read the file
        with open(file_path, 'r', encoding='utf-8') as text_file:
            text = text_file.read()
        logging.info(f"Successfully read file: {file_path}")
        return text
    except Exception as e:
        logging.error(f"Error reading file: {file_path}, Error: {str(e)}")
        raise

def get_file_name(absolute_file_path):
    return os.path.basename(absolute_file_path)

def get_file_extension(file_path):
    _, file_extension = os.path.splitext(file_path)
    return str(file_extension)

def get_immediate_subfolders(directory):
    """
    Returns a list of immediate subfolders within the given directory.

    Parameters:
    directory (str): The path to the directory.

    Returns:
    list: A list of immediate subfolders within the directory.
    """
    subfolders = []
    for entry in os.listdir(directory):
        full_path = os.path.join(directory, entry)
        if os.path.isdir(full_path):
            subfolders.append(full_path)
    return subfolders
def get_folder_name(path):
    # Normalize the path to remove any trailing slashes
    normalized_path = os.path.normpath(path)

    # Get the last component of the path
    last_component = os.path.basename(normalized_path)

    # If the last component is a file, get the directory containing it
    if os.path.isfile(normalized_path):
        last_component = os.path.basename(os.path.dirname(normalized_path))

    return last_component

def get_filename_friendly_timestamp():
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M")
    return timestamp

def create_directory_if_not_exists(directory_path: str) -> None:
    """
    Creates a directory if it doesn't exist.

    Args:
        directory_path (str): The path of the directory to create.

    Raises:
        OSError: If there is an error creating the directory.
    """
    try:
        if not os.path.exists(directory_path):
            os.makedirs(directory_path)
            print(f"Directory created: {directory_path}")
        else:
            print(f"Directory already exists: {directory_path}")
    except OSError as e:
        print(f"Error creating directory: {e}")
        raise

def delete_all_files_in_folder(folder_path):
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Failed to delete {file_path}. Reason: {e}')


def save_to_json(data, filepath):
    """
    Save a list or dictionary to a JSON file.

    Args:
        data: List or dictionary to save
        filepath: Path where JSON file should be saved
    """
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_from_json(filepath):
    """
    Load data from a JSON file.

    Args:
        filepath: Path to JSON file

    Returns:
        Loaded data (list or dictionary)
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)



class Git:

    @staticmethod
    @backoff.on_exception(
        backoff.expo,
        exception=git.GitCommandError,
        max_tries=3,
        max_time=30
    )
    def git_commit_all(repository_path, commit_message):
        try:
            repo = git.Repo(repository_path)
            repo.git.add(all=True)
            repo.index.commit(commit_message)
            print("Committed all changes successfully.")
        except git.GitCommandError as e:
            print("Error:", e)

    @staticmethod
    @backoff.on_exception(
        backoff.expo,
        exception=git.GitCommandError,
        max_tries=3,
        max_time=30
    )
    def git_push(repository_path, branch_name):
        try:
            repo = git.Repo(repository_path)
            origin = repo.remotes.origin
            origin.push(refspec=branch_name)
            print("Pushed changes successfully.")
        except git.GitCommandError as e:
            print("Error:", e)


def sleep_with_progress(sleep_interval):
    logger.info(f'Sleeping for {sleep_interval} seconds...')
    for i in tqdm(range(sleep_interval), desc="Sleeping", unit="sec"):
        time.sleep(1)

def remove_whitespace(s):
    return re.sub(r'\s+', '', s)
