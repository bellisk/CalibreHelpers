# encoding: utf-8
import logging
import re
import subprocess
import sys
from os import listdir
from os.path import isfile, join
from subprocess import PIPE, STDOUT, check_output
from sys import argv
from tempfile import mkdtemp
from time import sleep

import pdf2doi

path = '--with-library "/home/rae/Calibre Library"'
pdf2doi_errors = []


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


def get_pdf_file(mypath):
    ans = [f for f in listdir(mypath) if isfile(join(mypath, f)) and f.endswith(".pdf")]

    return join(mypath, ans[0])


def get_publication_metadata():
    loc = mkdtemp()
    try:
        check_output(
            'calibredb export --dont-save-cover --dont-write-opf --single-dir --to-dir "{}" {} {}'.format(
                loc, path, book_id
            ),
            shell=True,
            stdin=PIPE,
            stderr=STDOUT,
        )
    except subprocess.CalledProcessError as e:
        sys.exit(e.output)

    pdf_file = get_pdf_file(loc)
    result = pdf2doi.pdf2doi_singlefile(pdf_file)

    if len(pdf2doi_errors) > 0:
        print(
            f"Got an error from pdf2doi for book {book_id}, "
            f"stopping for now: {pdf2doi_errors[0]}"
        )
        if input(f"Add book {book_id} to the skip list? Y/n") != "n":
            add_to_skip_list(book_id)
            print("Added!")
        sys.exit()

    return result


def get_work_ids():
    calibre_command = (
        f'calibredb search {path} formats:"=PDF" and '
        f'not formats:"=EPUB" and search:"\\"=Needs tagging\\"" and '
        f'not identifiers:"=doi:" and not identifiers:"=arxiv doi:" and date:">={date}"'
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


if __name__ == "__main__":
    set_up_logging()

    date = "2024-06-01"
    if len(argv) > 1:
        date = argv[1]

    if not re.match(r"\d{4}-\d{2}-\d{2}", date):
        print(
            """Call like this:
        
python ./add_dois_to_multiple_books.py 2024-06-01

where 2024-06-01 is the earliest date to filter by. 
        """
        )
        sys.exit()

    ids = get_work_ids()
    print(f"Got {len(ids)} works to find DOIs for:")
    print(ids)
    print("------------------------------------")

    pdf2doi.config.set("websearch", True)
    pdf2doi.config.set("webvalidation", True)
    print(pdf2doi.config.print())

    n = 0
    for book_id in ids:
        n += 1
        print("------------------------------------")
        print(f"### Finding DOI for book {book_id} ({n} out of {len(ids)})")
        metadata = get_publication_metadata()

        if not metadata or not metadata.get("identifier"):
            print(
                "No doi found for book {}, adding id to the skip list".format(book_id)
            )
            add_to_skip_list(book_id)
            continue

        command = 'calibredb set_metadata {} --field identifiers:"{}:{}" {}'.format(
            path, metadata["identifier_type"], metadata["identifier"], book_id
        )
        result = check_output(
            command,
            shell=True,
            stderr=STDOUT,
            stdin=PIPE,
        )
        print(
            "Updated book {} with identifier {} {}".format(
                book_id, metadata["identifier_type"], metadata["identifier"]
            )
        )

        sleep(15)
