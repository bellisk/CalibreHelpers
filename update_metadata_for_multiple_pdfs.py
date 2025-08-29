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

SUPPORTED_FIELDS = {"doi", "title"}
IDENTIFIER_FIELDS = {"doi"}
LIBRARY_PATH = "/home/rae/Calibre Library"
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


def get_publication_metadata(book_id, fields, calibre):
    loc = mkdtemp()
    calibre.export(book_id, loc)

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


def get_work_ids(date, fields, calibre):
    kwargs = {
        "saved_search": "Needs tagging",
        "book_formats": ["PDF"],
        "exclude_book_formats": ["EPUB"],
        "after_date": date,
    }
    if "doi" in fields:
        kwargs["exclude_identifier_types"] = ["doi", "arxiv doi"]

    work_ids = calibre.search(**kwargs)

    with open("skip_ids.txt") as f:
        ids_to_skip = [line.strip("\n") for line in f.readlines()]

    work_ids = [i for i in work_ids if i not in ids_to_skip]

    return work_ids


def add_to_skip_list(book_id):
    with open("skip_ids.txt", "a") as f:
        f.write(book_id + "\n")


def validate_fields(fields):
    unsupported_fields = set(fields).difference(SUPPORTED_FIELDS)
    if len(unsupported_fields) > 0:
        click.echo(
            f"Unsupported field(s) input: {', '.join(unsupported_fields)}."
            f"Supported fields are: {', '.join(SUPPORTED_FIELDS)}."
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
    f"Default: doi. Options: {', '.join(SUPPORTED_FIELDS)}.",
)
@click.option(
    "--use-web-search",
    is_flag=True,
    help="Search for the document's title on the web to find its DOI.",
)
@click.option(
    "--no-auto-skip",
    is_flag=True,
    help="Don't automatically add document id to the skip list if no DOI is found. "
    "Ask each time.",
)
def run(date, fields, use_web_search, no_auto_skip):
    set_up_logging()

    # It makes sense for auto_skip to be the default and no_auto_skip to be the flagged
    # behaviour, but writing 'if not no_auto_skip' below is horrible, so let's define it
    auto_skip = not no_auto_skip

    calibre = CalibreHelper(library_path=LIBRARY_PATH)
    try:
        calibre.check_library()
    except CalibreException as e:
        click.echo(e)
        sys.exit()

    validate_fields(fields)
    click.echo(f"Metadata fields to update: {', '.join(fields)}")

    ids = get_work_ids(date, fields, calibre)
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
        click.echo(f"### Finding metadata for book {book_id} ({n} out of {len(ids)})")
        doc_metadata = get_publication_metadata(book_id, fields, calibre)
        existing_metadata = calibre.list_metadata(
            fields_to_show=[f for f in fields if f not in IDENTIFIER_FIELDS],
            identifiers_to_show=[f for f in fields if f in IDENTIFIER_FIELDS],
            book_id=book_id,
        )[0]
        fields_update_options = {}

        if "doi" in fields:
            if not doc_metadata.get("identifier"):
                if auto_skip:
                    click.echo(
                        f"No DOI found for document {book_id}: adding id to the skip "
                        f"list."
                    )
                    add_to_skip_list(book_id)
                elif (
                    input(
                        f"No DOI found for document {book_id}. Add book {book_id} to "
                        f"the skip list? Y/n"
                    )
                    != "n"
                ):
                    add_to_skip_list(book_id)
                    click.echo("Added!")
            else:
                fields_update_options["identifiers"] = (
                    f'"{doc_metadata["identifier_type"]}:{doc_metadata["identifier"]}"'
                )

        if "title" in fields:
            fields_update_options.update(
                get_title_update_option(
                    book_id, doc_metadata, existing_metadata["title"]
                )
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


def get_title_update_option(book_id, metadata, existing_title):
    possible_titles = [
        t for t in metadata.get("possible_titles") if t != existing_title
    ]
    if not possible_titles:
        click.echo(
            f"""
Document {book_id}'s title in Calibre is "{existing_title}".
No other potential titles were found in the document."""
        )
        return {}

    message = f"""
Document's title in Calibre is "{existing_title}".
Which of the possible titles found in the document should be used?
"""
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
        return {"title": chosen_title}


if __name__ == "__main__":
    run()
