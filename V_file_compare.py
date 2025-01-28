import os.path

import custom_modules.utilities as utils
import ZZ_config as config
from typing import Union
import difflib
from git import Repo, GitCommandError

def check_if_chapter_is_already_present(narou_uid: Union[int, str]) -> bool:
    file_name_to_check = f'{str(narou_uid).zfill(5)}.txt'
    file_list = utils.list_files(config.RAW_TEXT_SAVE_FOLDER)
    file_name_list = [utils.get_file_name(file_path) for file_path in file_list]

    return True if file_name_to_check in file_name_list else False

def get_absolute_raw_file_path(narou_uid: Union[int, str], mode='txt') -> str:
    file_name = f'{str(narou_uid).zfill(5)}.txt' if mode == 'txt' else f'{str(narou_uid).zfill(5)}.html'
    folder = config.RAW_TEXT_SAVE_FOLDER if mode == 'txt' else config.RAW_HTML_SAVE_FOLDER
    return str(os.path.join(folder, file_name))

def get_relative_raw_file_path(narou_uid: Union[int, str], mode='txt') -> str:
    absolute_file_path = get_absolute_raw_file_path(narou_uid, mode)
    cwd = os.getcwd()
    relative_file_path = absolute_file_path.replace(cwd, '')
    if relative_file_path[0] == '\\':
       relative_file_path = relative_file_path[1:]
       # print(absolute_file_path)
    return relative_file_path.replace("\\", "/")

def get_string_diff(old: str, new: str) -> str:
    """
    Compare two strings and return a string showing the differences.

    :param old: The first string to compare.
    :param new: The second string to compare.
    :return: A formatted string highlighting the differences.
    """
    diff = difflib.unified_diff(
        [string.strip() for string in old.splitlines() if string.strip()],
        [string.strip() for string in new.splitlines() if string.strip()],
        lineterm="",
        fromfile="Previous",
        tofile="New"
    )

    formatted_diffs = []

    for difference in diff:
        if difference.startswith('-'):
           formatted_diffs.append(f'~~{difference[1:]}~~')
        elif difference.startswith('+'):
            formatted_diffs.append(f'**{difference[1:]}**\n')
        else:
            formatted_diffs.append(f'{difference[1:]}\n')

    return "\n".join(formatted_diffs)


def get_current_and_prev_version_of_file(repo_path, file_path, commit_hash="HEAD"):
    """
    Get the current version and the previous version of a file in a Git repository using GitPython.

    Parameters:
        repo_path (str): Path to the Git repository.
        file_path (str): Path to the file relative to the repository root.
        commit_hash (str): The commit hash to compare. Default is "HEAD" (latest commit).

    Returns:
        tuple: (current_version, previous_version)
               - current_version (str): The file content in the specified commit.
               - previous_version (str): The file content in the most recent commit where the file was modified.
    """
    try:
        # Initialize the repository
        repo = Repo(repo_path)

        # Get the current commit object
        current_commit = repo.commit(commit_hash)

        # Get the current version of the file
        try:
            current_version = current_commit.tree[file_path].data_stream.read().decode("utf-8")
        except KeyError:
            raise FileNotFoundError(f"The file '{file_path}' does not exist in the specified commit.")

        # Find the most recent commit where the file was modified
        commits_touching_file = list(repo.iter_commits(paths=file_path, max_count=2))

        if len(commits_touching_file) < 2:
            previous_version = None  # No earlier version available
        else:
            previous_commit = commits_touching_file[1]
            previous_version = previous_commit.tree[file_path].data_stream.read().decode("utf-8")

        return current_version, previous_version

    except GitCommandError as e:
        raise RuntimeError(f"Git command failed: {e}") from e

def get_differences(narou_uid: Union[str, int]) -> str:

    repo = os.getcwd()
    file_path = get_relative_raw_file_path(narou_uid)
    current_version, previous_version = get_current_and_prev_version_of_file(repo, file_path)
    return get_string_diff(previous_version, current_version)

if __name__ == "__main__":
    # print(get_relative_raw_file_path(500))
    # print(open(get_relative_raw_file_path(500), 'r', encoding='utf-8').read())
    repo = os.getcwd()
    file_path = get_relative_raw_file_path(522)
    print(file_path)
    current_version, previous_version = get_current_and_prev_version_of_file(repo, file_path)

    print(current_version == previous_version)
    # print(current_version, previous_version)
    print(get_string_diff(previous_version, current_version))

    pass