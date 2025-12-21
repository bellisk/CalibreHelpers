# CalibreHelpers
Scripts to help me manage my Calibre library

## Installation

```shell
git clone git@github.com:bellisk/CalibreHelpers.git
cd CalibreHelpers
pip install -e .
```

## Usage

```shell
calibre-helpers --help
calibre-helpers extract-metadata --help
```

If the `extract-metadata` command can't find a DOI in a PDF file, it will offer to save its Calibre id in the file
`config/skip_ids.txt` so that file won't be repeatedly checked. To reset skip-ids, delete the contents of the file.
