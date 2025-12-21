import json
import os.path
from errno import ENOENT
from os import devnull
from subprocess import PIPE, STDOUT, CalledProcessError, call, check_output
from urllib.parse import urlparse

import click

ADDITIONAL_SERIES_KEYS = ["series00", "series01", "series02", "series03"]
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
    return clean_output(
        check_output(command, shell=True, stderr=STDOUT, stdin=PIPE, text=True)
    )


def collate_search_terms(
    saved_search=None,
    authors=None,
    book_formats=None,
    exclude_book_formats=None,
    series=None,
    urls=None,
    incomplete=False,
    after_date=None,
    before_date=None,
    exclude_identifier_types=None,
    book_id=None,
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
    if before_date:
        search_term_sets.append(f'date:"<{before_date}"')
    if exclude_identifier_types:
        search_term_sets.append(
            " AND ".join(
                [
                    f'NOT identifiers:"={id_type}:"'
                    for id_type in exclude_identifier_types
                ]
            )
        )
    if book_id:
        search_term_sets.append(f"id:{book_id}")

    return " AND ".join(search_term_sets)


class CalibreHelper(object):
    """Calls calibredb CLI commands."""

    def __init__(
        self,
        library_path,
        user=None,
        password=None,
        words_column=True,
        multiseries_search=True,
        extra_tag_columns=None,
    ):
        """Init CalibreHelper.

        :param library_path:        str     Path to or url for Calibre library
        :param user:                str     Username, if library is password protected
        :param password:            str     Password, if library is password protected
        :param words_column:        bool    If we expect an int-type column for
                                            wordcount in the library. Will create one if
                                            it is not present.
        :param multiseries_search:  bool    If we expect the library to handle books
                                            belonging to multiple series. Will set this
                                            up if it is not already done.
        :param extra_tag_columns:   list    A list of extra tag-type columns that the
                                            library should have. Will create these if
                                            they do not exist.
        """
        self.path = library_path
        self.user = user
        self.password = password
        self.words_column = words_column
        self.multiseries_search = multiseries_search
        self.extra_tag_columns = (
            extra_tag_columns if extra_tag_columns is not None else []
        )

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
            click.echo(
                f"There is no Calibre library at the path '{self.path}', "
                f"so Calibre will create one",
            )

        # Check we can connect to Calibre and there is no other Calibre instance running
        command = f"calibredb list --limit 1 {self.library_access_string}"

        try:
            check_and_clean_output(command)
        except CalledProcessError as e:
            output = clean_output(e.output)

            if "urllib.error.URLError" in output:
                # The path is a url and it's wrong
                message = f"Error connecting to the url {self.path}"
            elif "Not Found" in output:
                # The path is the url to a Calibre server, but the library name is wrong
                message = f"No Calibre library found at the url {self.path}"
            else:
                # If the username or password is wrong, calibredb gives us a nice error
                # message, so we can just output that.
                # If there's a new kind of error not already handled, we get a stack
                # trace. Just output it and deal with it then.
                message = output

            raise CalibreException(f"Error accessing Calibre library: {message}")

        try:
            # Check that our custom columns are set up, and set them up if not.
            custom_columns = self.get_custom_column_names()
            if self.words_column:
                self.check_or_create_words_column(custom_columns)
            if self.multiseries_search:
                self.set_up_multiseries_search(custom_columns)
            if self.extra_tag_columns:
                self.check_or_create_extra_tag_columns(custom_columns)
        except CalledProcessError as e:
            output = clean_output(e.output)

            raise CalibreException(
                f"Error while making sure custom columns exist in Calibre library: "
                f"{output}",
            )

    def get_custom_column_names(self):
        res = check_and_clean_output(
            f"calibredb custom_columns {self.library_access_string}"
        )
        # Get rid of the number after each column name, e.g. "columnname (1)"
        return [c.split(" ")[0] for c in res.split("\n")]

    def set_up_multiseries_search(self, custom_columns):
        if set(custom_columns).intersection(ADDITIONAL_SERIES_KEYS) == set(
            ADDITIONAL_SERIES_KEYS
        ):
            click.echo("Additional series columns are already in Calibre Library")
        else:
            click.echo("Adding additional series columns to Calibre library")
            for c in ADDITIONAL_SERIES_KEYS:
                check_and_clean_output(
                    f"calibredb add_custom_column {self.library_access_string} "
                    f"{c} {c} series"
                )

            click.echo("Adding grouped search term 'allseries' to Calibre Library")
            script = ADD_GROUPED_SEARCH_SCRIPT % self.path
            check_and_clean_output(
                f"calibre-debug -c '{script}'",
            )

    def check_or_create_words_column(self, custom_columns):
        for c in custom_columns:
            if c.startswith("words"):
                return

        click.echo("Adding custom column 'words' to Calibre library")
        check_and_clean_output(
            f"calibredb add_custom_column {self.library_access_string} words Words int"
        )

    def check_or_create_extra_tag_columns(self, custom_columns):
        if set(custom_columns).intersection(self.extra_tag_columns) == set(
            self.extra_tag_columns
        ):
            click.echo(
                f"Custom tag-type columns are in Calibre Library: "
                f"{self.extra_tag_columns}"
            )
        else:
            click.echo(
                f"Adding tag-type columns in Calibre library: {self.extra_tag_columns}"
            )
            for tag in self.extra_tag_columns:
                check_and_clean_output(
                    f"calibredb add_custom_column {self.library_access_string} "
                    f"{tag} {tag} text --is-multiple"
                )

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
        before_date=False,
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
            before_date,
            exclude_identifier_types,
        )

        command = f"calibredb search {search_terms} {self.library_access_string}"
        click.echo(command)

        try:
            result = check_and_clean_output(command)

            return result.split(",")
        except CalledProcessError as e:
            if "No books matching the search expression" in e.output:
                return []
            else:
                raise CalibreException(e.output)

    def get_author_works_count(self, author):
        click.echo(f"Getting work count for {author} in calibre")
        result = self.search(authors=[author])

        return len(result)

    def get_series_works_count(self, series_title):
        click.echo(f"Getting work count for {series_title} in calibre")
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

    def list_metadata(
        self,
        fields_to_show=None,
        identifiers_to_show=None,
        book_id=None,
        authors=None,
        urls=None,
        series=None,
        book_formats=None,
        incomplete=False,
    ):
        if fields_to_show is None:
            fields_to_show = []

        fields_to_search_for = fields_to_show

        if identifiers_to_show is not None:
            fields_to_search_for.append("*identifier")

        search_terms = collate_search_terms(
            book_id=book_id,
            authors=authors,
            book_formats=book_formats,
            series=series,
            urls=urls,
            incomplete=incomplete,
        )

        command = (
            f"calibredb list --search {search_terms} {self.library_access_string} "
            f"--fields {','.join(fields_to_search_for)} --for-machine"
        )

        try:
            output = check_and_clean_output(command)

            output_json = json.loads(output)
        except CalledProcessError as e:
            if "No books matching the search expression" in e.output:
                return []
            else:
                raise CalibreException(e.output)

        def collate_result_data(single_result):
            result = {field: single_result[field] for field in fields_to_show}
            for id_to_show in identifiers_to_show:
                identifiers = single_result["*identifier"].split(", ")
                for i in identifiers:
                    if i.split(":")[0] == id_to_show:
                        result[id_to_show] = i.split(":")[1]
            return result

        return [collate_result_data(r) for r in output_json]

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
        options = {k: v.replace('"', '\\"') for k, v in options.items()}
        options_strings = [f'--field={k}:"{v}"' for k, v in options.items()]

        command = (
            f"calibredb set_metadata {book_id} {' '.join(options_strings)} "
            f"{self.library_access_string}"
        )

        try:
            check_and_clean_output(command)
        except CalledProcessError as e:
            raise CalibreException(e.output)
