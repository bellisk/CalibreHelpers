import click

from metadata import FormattedDateType, extract_and_add, supported_fields


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
    f"Default: doi. Options: {', '.join(supported_fields)}.",
)
@click.option(
    "--use-web-search",
    is_flag=True,
    help="Search for the document's title on the web to find its DOI.",
)
def extract_metadata(date, fields, use_web_search):
    """Extract metadata from PDF files in Calibre library and update it in Calibre.

    Example usage:

    calibre-helpers extract-metadata --date 2025-06-01 --fields doi --fields title --use-web-search
    """
    return extract_and_add(date, fields, use_web_search)
