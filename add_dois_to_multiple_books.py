import logging
import re
import subprocess
import sys
from os import listdir
from os.path import isfile, join
from subprocess import PIPE, STDOUT, check_output
from tempfile import mkdtemp
from time import sleep

import click
import pdf2doi

supported_fields = {"doi", "title"}
path = '--with-library "/home/rae/Calibre Library"'
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


def get_pdf_filepath(mypath):
    ans = [f for f in listdir(mypath) if isfile(join(mypath, f)) and f.endswith(".pdf")]

    return join(mypath, ans[0])


def get_publication_metadata(book_id, fields):
    loc = mkdtemp()
    try:
        check_output(
            f"calibredb export --dont-save-cover --dont-write-opf --single-dir "
            f'--to-dir "{loc}" --template="{id}" {path} {book_id}',
            shell=True,
            stdin=PIPE,
            stderr=STDOUT,
        )
    except subprocess.CalledProcessError as e:
        sys.exit(e.output)

    pdf_filepath = get_pdf_filepath(loc)
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


def get_work_ids(date):
    calibre_command = (
        f'calibredb search {path} formats:"=PDF" and '
        f'not formats:"=EPUB" and search:"\\"=Needs tagging\\"" and '
        f'not identifiers:"=doi:" and not identifiers:"\\"=arxiv doi:\\"" and '
        f'date:">={date}"'
    )
    work_ids_output = check_output(
        calibre_command,
        shell=True,
        stderr=STDOUT,
        stdin=PIPE,
    )
    with open("skip_ids.txt") as f:
        ids_to_skip = [line.strip("\n") for line in f.readlines()]

    work_ids_result = re.search(
        "b'(Initialized urlfixer\\\\n)?(.*)'", str(work_ids_output)
    )
    if work_ids_result is None:
        return []

    work_ids = work_ids_result[2].split(",")
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
    "Default: doi. Options: doi, title.",
)
@click.option(
    "--use-web-search",
    is_flag=True,
    help="Search for the document's title on the web to find its DOI.",
)
def run(date, fields, use_web_search):
    set_up_logging()

    validate_fields(fields)
    click.echo(f"Metadata fields to update: {', '.join(fields)}")

    ids = get_work_ids(date)
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
        fields_update_options = ""

        if "doi" in fields:
            if not metadata.get("identifier"):
                click.echo(
                    f"No DOI found for document {book_id}, adding id to the skip list"
                )
                add_to_skip_list(book_id)
            else:
                fields_update_options += (
                    f"--field identifiers:"
                    f'"{metadata["identifier_type"]}:{metadata["identifier"]}" '
                )
                new_metadata[metadata["identifier_type"]] = metadata["identifier"]

        if "title" in fields:
            if not metadata.get("possible_titles"):
                click.echo(f"No possible titles found for document {book_id}")
            else:
                enumerated_titles = list(enumerate(metadata["possible_titles"]))
                message = (
                    "Which of the possible titles found in the document should "
                    "be used?\n"
                )
                for no, title in enumerated_titles:
                    message += f"\t{no}. {title}\n"
                message += f"\t{len(enumerated_titles)}. Don't update title\n"
                value = click.prompt(message, type=int)

                if value == len(enumerated_titles):
                    click.echo("Title will not be updated.")
                elif value not in range(len(enumerated_titles)):
                    click.echo("Please enter a number from the list above.")
                else:
                    chosen_title = metadata["possible_titles"][value]
                    fields_update_options += f'--field title:"{chosen_title}" '
                    new_metadata["title"] = chosen_title

        if len(fields_update_options) == 0:
            click.echo("Nothing to update.")
            continue

        command = f"calibredb set_metadata {path} {fields_update_options} {book_id}"
        result = check_output(
            command,
            shell=True,
            stderr=STDOUT,
            stdin=PIPE,
        )
        click.echo(
            f"Updated book {book_id} with metadata "
            f"{[k + ': ' + v for k, v in new_metadata.items()]}"
        )

        sleep(15)


if __name__ == "__main__":
    run()
