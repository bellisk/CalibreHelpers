import logging
import re
import subprocess
import sys
from os.path import join
from subprocess import PIPE, STDOUT, check_output
from tempfile import mkdtemp
from time import sleep

import click
import pdf2doi

from src.calibre import CalibreException, CalibreHelper

supported_fields = {"doi", "title"}
library_path = "/home/rae/Calibre Library"
pdf2doi_errors = []


class FormattedDateType(click.ParamType):
    name = "date"

    def convert(self, value, param, ctx):
        if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
            return value
        self.fail(f"{value!r} is not a date in the format YYYY-MM-DD", param, ctx)


class SaveErrorHandler(logging.Handler):
    """pdf2doi logs when it gets an exception from Google (e.g. an HTTP response code
    of 429, too many requests), but it doesn't raise it or give us any way to catch it.
    Here we catch any exception logs and put them in a list we can check later.
    """

    def emit(self, record):
        if record.levelno == logging.ERROR and record.exc_info:
            pdf2doi_errors.append(record.getMessage())


def set_up_logging():
    pdf2doi_logger = logging.getLogger("pdf2doi")
    requests_handler = SaveErrorHandler()
    pdf2doi_logger.addHandler(requests_handler)


def get_publication_metadata(book_id, fields):
    loc = mkdtemp()
    try:
        check_output(
            f"calibredb export --dont-save-cover --dont-write-opf --single-dir "
            f'--to-dir "{loc}" --template="{id}" {library_path} {book_id}',
            shell=True,
            stdin=PIPE,
            stderr=STDOUT,
        )
    except subprocess.CalledProcessError as e:
        sys.exit(e.output)

    pdf_filepath = join(loc, f"{book_id}.pdf")
    result = {}

    if "doi" in fields:
        result.update(**pdf2doi.pdf2doi_singlefile(pdf_filepath))

    if "title" in fields:
        with open(pdf_filepath, "rb") as pdf:
            result["possible_titles"] = pdf2doi.finders.find_possible_titles(pdf)

    if len(pdf2doi_errors) > 0:
        click.echo(
            f"Got an error from pdf2doi for book {book_id}, "
            f"stopping for now: {pdf2doi_errors[0]}"
        )
        if input(f"Add book {book_id} to the skip list? Y/n") != "n":
            add_to_skip_list(book_id)
            click.echo("Added!")
        sys.exit()

    return result


def get_work_ids(date, calibre):
    work_ids = calibre.search(
        saved_search="Needs tagging",
        book_formats=["PDF"],
        exclude_book_formats=["EPUB"],
        after_date=date,
        exclude_identifier_types=["doi", "arxiv doi"],
    )

    with open("skip_ids.txt") as f:
        ids_to_skip = [line.strip("\n") for line in f.readlines()]

    if work_ids is None:
        return []

    work_ids = work_ids[2].split(",")
    work_ids = [i for i in work_ids if i not in ids_to_skip]

    return work_ids


def add_to_skip_list(book_id):
    with open("skip_ids.txt", "a") as f:
        f.write(book_id + "\n")


def validate_fields(fields):
    unsupported_fields = set(fields).difference(supported_fields)
    if len(unsupported_fields) > 0:
        click.echo(
            f"Unsupported field(s) input: {', '.join(unsupported_fields)}."
            f"Supported fields are: {', '.join(supported_fields)}."
        )
        sys.exit()


@click.command()
@click.option(
    "--date",
    default="2025-06-01",
    type=FormattedDateType(),
    help="The earliest added date to filter by in Calibre. Format: YYYY-MM-DD. "
    "Default: 2025-06-01.",
)
@click.option(
    "--fields",
    "-f",
    multiple=True,
    default=["doi"],
    help="Fields to update in Calibre based on metadata derived from the document. "
    f"Default: doi. Options: {', '.join(supported_fields)}.",
)
@click.option(
    "--use-web-search",
    is_flag=True,
    help="Search for the document's title on the web to find its DOI.",
)
def run(date, fields, use_web_search):
    set_up_logging()

    calibre = CalibreHelper(library_path=library_path)
    try:
        calibre.check_library()
    except CalibreException as e:
        click.echo(e)
        sys.exit()

    validate_fields(fields)
    click.echo(f"Metadata fields to update: {', '.join(fields)}")

    ids = get_work_ids(date, calibre)
    click.echo(f"Got {len(ids)} works to find metadata for:")
    click.echo(ids)
    click.echo("------------------------------------")

    pdf2doi.config.set("websearch", use_web_search)
    pdf2doi.config.set("webvalidation", True)
    click.echo("pdf2doi config:")
    click.echo(pdf2doi.config.print())

    n = 0
    for book_id in ids:
        n += 1
        click.echo("------------------------------------")
        click.echo(f"### Finding DOI for book {book_id} ({n} out of {len(ids)})")
        metadata = get_publication_metadata(book_id, fields)
        new_metadata = {}
        fields_update_options = {}

        if "doi" in fields:
            if not metadata.get("identifier"):
                click.echo(
                    f"No DOI found for document {book_id}, adding id to the skip list"
                )
                add_to_skip_list(book_id)
            else:
                fields_update_options["identifiers"] = (
                    f'"{metadata["identifier_type"]}:{metadata["identifier"]}"'
                )

        if "title" in fields:
            fields_update_options.update(
                get_title_update_option(book_id, metadata, new_metadata)
            )

        if len(fields_update_options) == 0:
            click.echo("Nothing to update.")
            continue

        calibre.set_metadata(book_id, fields_update_options)
        click.echo(
            f"Updated book {book_id} with metadata "
            f"{[k + ': ' + v for k, v in fields_update_options.items()]}"
        )

        sleep(15)


def get_title_update_option(book_id, metadata, new_metadata):
    possible_titles = metadata.get("possible_titles")
    if not possible_titles:
        click.echo(f"No possible titles found for document {book_id}")
        return ""

    message = "Which of the possible titles found in the document should be used?\n"
    for no, title in list(enumerate(possible_titles)):
        message += f"\t{no}. {title}\n"
    message += f"\t{len(possible_titles)}. Don't update title\n"
    value = click.prompt(message, type=int)

    while value not in range(len(possible_titles) + 1):
        value = click.prompt("Please enter a number from the list above", type=int)

    if value == len(possible_titles):
        click.echo("Title will not be updated.")
        return ""
    else:
        chosen_title = possible_titles[value]
        new_metadata["title"] = chosen_title
        return {"title": chosen_title}


if __name__ == "__main__":
    run()
