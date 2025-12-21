import click

from metadata import SUPPORTED_FIELDS, FormattedDateType, extract_and_add


@click.group()
@click.version_option()
def cli():
    """Tool to manage metadata for Calibre ebook library"""


@cli.command(name="extract-metadata")
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
def extract_metadata(date, fields, use_web_search, no_auto_skip):
    """Extract metadata from PDF files in Calibre library and update it in Calibre.

    Example usage:

    calibre-helpers extract-metadata --date 2025-06-01 --fields doi --fields title --use-web-search
    """
    return extract_and_add(date, fields, use_web_search, no_auto_skip)
