import os

from git import Repo, GitCommandError

import difflib

def get_string_diff(string1: str, string2: str) -> str:
    """
    Compare two strings and return a string showing the differences.

    :param string1: The first string to compare.
    :param string2: The second string to compare.
    :return: A formatted string highlighting the differences.
    """
    diff = difflib.unified_diff(
        [string.strip() for string in string1.splitlines() if string.strip()],
        [string.strip() for string in string2.splitlines() if string.strip()],
        lineterm="",
        fromfile="Previous",
        tofile="New"
    )
    return "\n".join(diff)

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
               - previous_version (str): The file content in the parent of the specified commit.
    """
    try:
        # Initialize the repository
        repo = Repo(repo_path)

        # Get the current commit object
        current_commit = repo.commit(commit_hash)

        # Get the parent commit (the previous commit)
        parent_commit = current_commit.parents[0] if current_commit.parents else None

        # Get the current version of the file
        current_version = current_commit.tree[file_path].data_stream.read().decode("utf-8")

        # Get the previous version of the file
        if parent_commit:
            previous_version = parent_commit.tree[file_path].data_stream.read().decode("utf-8")
        else:
            previous_version = None  # No parent commit (e.g., this is the first commit)

        return current_version, previous_version
    except KeyError:
        raise FileNotFoundError(f"The file '{file_path}' does not exist in the specified commit or its parent.")
    except GitCommandError as e:
        raise RuntimeError(f"Git command failed: {e}") from e

if __name__ == "__main__":
    pass
    # path = os.getcwd()
    # filename = 'test.txt'
    # print(get_file_versions_with_gitpython(repo_path=path, file_path=filename))
    # old, new = get_file_versions_with_gitpython(repo_path=path, file_path=filename)
    # print(get_string_diff(old, new))
    #
