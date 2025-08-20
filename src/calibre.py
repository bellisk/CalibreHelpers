import json
import os.path
from errno import ENOENT
from os import devnull
from subprocess import CalledProcessError, call
from urllib.parse import urlparse

from .utils import Bcolors, check_subprocess_output, log

ADD_GROUPED_SEARCH_SCRIPT = """from calibre.library import db

db = db("%s").new_api
db.set_pref("grouped_search_terms", {"allseries": ["series", "#series00", "#series01", "#series02", "#series03"]})
print(db.pref("grouped_search_terms"))
"""


class CalibreException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


def clean_output(process_output):
    return process_output.replace("Initialized urlfixer\n", "")


def check_and_clean_output(command):
    """Runs a command as a subprocess, raising CalledProcessError if necessary,
    and removes the cruft calibredb adds to the output.
    """
    return clean_output(check_subprocess_output(command))


def collate_search_terms(
    saved_search=None,
    authors=None,
    book_formats=None,
    exclude_book_formats=None,
    series=None,
    urls=None,
    incomplete=False,
    after_date=None,
    exclude_identifier_types=None,
):
    """Turn lists of search terms of different kinds into a search query for calibredb.

    All search terms of the same kind are joined with OR; each set of search terms
    is joined with AND. This is because this matches the current use cases I have
    for this method, and may well come back to bite me.
    """
    search_term_sets = []
    if saved_search:
        search_term_sets.append(f'search:"\\"={saved_search}\\""')
    if authors:
        # author:"=author or \(author\)"
        # This catches both exact use of the author name and use of a pseud,
        # e.g. "MyPseud (MyUsername)"
        search_term_sets.append(
            " OR ".join([f'author:"={author} or \\({author}\\)"' for author in authors])
        )
    if urls:
        search_term_sets.append(f'identifiers:url:"={" OR ".join(urls)}"')
    if series:
        # Calibre seems to escape only the character & in series titles
        search_term_sets.append(
            f'allseries:"=\\"{" OR ".join([s.replace("&", "&amp;") for s in series])}\\""'
        )
    if book_formats:
        search_term_sets.append(
            f'formats:"={" OR ".join([book_format.upper() for book_format in book_formats])}"'
        )
    if exclude_book_formats:
        search_term_sets.append(
            " AND ".join(
                [
                    f'NOT formats:"={book_format.upper()}"'
                    for book_format in exclude_book_formats
                ]
            )
        )
    if incomplete:
        search_term_sets.append("#status:=In-Progress")
    if after_date:
        search_term_sets.append(f'date:">={after_date}"')
    if exclude_identifier_types:
        search_term_sets.append(
            " AND ".join(
                [
                    f'NOT identifiers:"={id_type}"'
                    for id_type in exclude_identifier_types
                ]
            )
        )

    return " AND ".join(search_term_sets)


class CalibreHelper(object):
    """Calls calibredb CLI commands."""

    def __init__(self, library_path, user=None, password=None):
        self.path = library_path
        self.user = user
        self.password = password

        self.library_access_string = f'--with-library="{self.path}" '
        if user:
            self.library_access_string += f'--user="{self.user}" '
        if password:
            self.library_access_string += f'--password="{self.password}" '

    def check_library(self):
        # First, check if we have calibredb locally
        try:
            with open(devnull, "w") as nullout:
                call(["calibredb"], stdout=nullout, stderr=nullout)
        except OSError as e:
            if e.errno == ENOENT:
                raise CalibreException(
                    "Calibredb is not installed on this system. Cannot search the "
                    "Calibre library or update it.",
                )

        # Check whether there is a library at the specified path
        parsed_path = urlparse(self.path)
        path_is_url = parsed_path.scheme and parsed_path.netloc
        path_is_dir = os.path.isdir(self.path)

        if not (path_is_url or path_is_dir):
            log(
                f"There is no Calibre library at the path '{self.path}', "
                f"so Calibre will create one",
                Bcolors.WARNING,
            )

        # Check if there is another Calibre instance running
        command = f"calibredb list --limit 1 {self.library_access_string}"

        try:
            check_and_clean_output(command)
        except CalledProcessError as e:
            raise CalibreException(clean_output(e.output))

    def search(
        self,
        saved_search=None,
        authors=None,
        urls=None,
        series=None,
        book_formats=None,
        exclude_book_formats=None,
        incomplete=False,
        after_date=False,
        exclude_identifier_types=None,
    ):
        """Accepts lists of authors/urls/series/formats to search calibredb for.

        All search terms of the same kind are joined with OR; each set of search terms
        is joined with AND. This is because this matches the current use cases I have
        for this method, and may well come back to bite me.

        Returns a list of book ids that match the search.
        """
        search_terms = collate_search_terms(
            saved_search,
            authors,
            book_formats,
            exclude_book_formats,
            series,
            urls,
            incomplete,
            after_date,
            exclude_identifier_types,
        )

        command = f"calibredb search {search_terms} {self.library_access_string}"
        print(command)

        try:
            result = check_and_clean_output(command)

            return result.split(",")
        except CalledProcessError as e:
            if "No books matching the search expression" in e.output:
                return []
            else:
                raise CalibreException(e.output)

    def get_author_works_count(self, author):
        log(f"Getting work count for {author} in calibre")
        result = self.search(authors=[author])

        return len(result)

    def get_series_works_count(self, series_title):
        log(f"Getting work count for {series_title} in calibre")
        result = self.search(series=[series_title])

        return len(result)

    def export(self, book_id, location):
        command = (
            f'calibredb export {book_id} --to-dir "{location}" --template={{id}} '
            f"--dont-save-cover --dont-write-opf --single-dir "
            f"{self.library_access_string}"
        )

        try:
            check_and_clean_output(command)
        except CalledProcessError as e:
            raise CalibreException(e.output)

        # Return the filepath to the new epub file
        return os.path.join(location, f"{book_id}.epub")

    def list_titles_and_urls(
        self, authors=None, urls=None, series=None, book_formats=None, incomplete=False
    ):
        search_terms = collate_search_terms(
            authors, book_formats, series, urls, incomplete
        )

        command = (
            f"calibredb list --search {search_terms} {self.library_access_string} "
            f"--fields title,*identifier --for-machine"
        )

        try:
            result = check_and_clean_output(command)

            result_json = json.loads(result)
        except CalledProcessError as e:
            if "No books matching the search expression" in e.output:
                return []
            else:
                raise CalibreException(e.output)

        return [
            {"title": r["title"], "url": r["*identifier"].replace("url:", "")}
            for r in result_json
        ]

    def add(self, book_filepath, options=None):
        """Add a book to the Calibre library.

        options is a dictionary of option_name: option_value, which will be converted to
        CLI options.
        """
        if options is None:
            options = {}
        options_strings = [f'--{k}="{v}"' for k, v in options.items()]

        command = (
            f'calibredb add -d "{book_filepath}" {" ".join(options_strings)} '
            f"{self.library_access_string}"
        )

        try:
            check_and_clean_output(command)
        except CalledProcessError as e:
            raise CalibreException(e.output)

    def remove(self, book_id):
        command = f"calibredb remove {book_id} {self.library_access_string}"

        try:
            check_and_clean_output(command)
        except CalledProcessError as e:
            raise CalibreException(e.output)

    def set_metadata(self, book_id, options):
        """Set metadata fields on an existing book in the Calibre library.

        options is a dictionary of field: value, which will be converted to CLI options.
        NB: custom fields must be prefaced with a '#' character for Calibre to
        recognise them.
        NB: values with spaces in them must include quotation marks in the string.

        Example: {
            "series": '"My Series"',
            "#series00": '"My Other Series"',
            "tags": "tag1,tag2",
            "#characters": '"Jane Grey","Captain Scarlet"'
        }
        """
        options_strings = [f'--field={k}:"{v}"' for k, v in options.items()]

        command = (
            f"calibredb set_metadata {book_id} {' '.join(options_strings)} "
            f"{self.library_access_string}"
        )

        try:
            check_and_clean_output(command)
        except CalledProcessError as e:
            raise CalibreException(e.output)
