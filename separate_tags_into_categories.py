# encoding: utf-8
import csv
import json
import os.path
import sys
from pprint import pprint
from subprocess import PIPE, STDOUT, check_output
from sys import argv

PATH = '--with-library "/home/rae/Calibre Fanfic Library"'
TAG_TYPES = [
    "ao3categories",
    "characters",
    "fandoms",
    "freeformtags",
    "rating",
    "ships",
    "status",
    "warnings",
]

exported_tags = {}


def check_or_create_extra_tag_type_columns(path):
    res = check_output(
        f"calibredb custom_columns {path}",
        shell=True,
        stderr=STDOUT,
        stdin=PIPE,
    )
    # Get rid of the number after each column name, e.g. "columnname (1)"
    columns = [c.split(" ")[0] for c in res.decode("utf-8").split("\n")]
    if set(columns).intersection(TAG_TYPES) == set(TAG_TYPES):
        print("All AO3 tag types are columns in Calibre library")
        return
    print("Adding AO3 tag types as columns in Calibre library")
    for tag in TAG_TYPES:
        check_output(
            f"calibredb add_custom_column {path} {tag} {tag} text --is-multiple",
            shell=True,
            stderr=STDOUT,
            stdin=PIPE,
        )


def load_tag_type_from_file(tag_type):
    with open(os.path.join("exported_tags", tag_type + ".csv")) as f:
        exported_tags[tag_type] = [
            line.strip("\n").strip('"') for line in f.readlines()
        ]


def get_all_untransformed_fic_data():
    res = check_output(
        f"calibredb list "
        f'--search="#ao3categories:false #freeformtags:false #fandoms:false tags:true" '
        f"{path} --for-machine",
        shell=True,
        stdin=PIPE,
        stderr=STDOUT,
    )
    return json.loads(res.decode("utf-8").replace("Initialized urlfixer\n", ""))


def get_existing_tags(story_id):
    metadata = check_output(
        f"calibredb show_metadata {path} {story_id}",
        shell=True,
        stdin=PIPE,
        stderr=STDOUT,
    )
    metadata = metadata.split(b"\n")
    for line in metadata:
        line = line.decode("utf-8")
        if line.startswith("Tags"):
            tags = line[len("Tags                : ") :].split(", ")
            return tags


def fix_tags_for_fic(fic_id, path):
    update_command = f"calibredb set_metadata {str(fic_id)} {path} "
    existing_tags = get_existing_tags(fic_id)
    pprint(existing_tags)
    tags_to_keep = existing_tags
    if not existing_tags:
        return
    for tag_type in TAG_TYPES:
        tags = [
            '"' + tag.replace('"', '""') + '"'
            for tag in existing_tags
            if tag.replace('"', '""') in exported_tags[tag_type]
        ]
        update_command += f"--field=#{tag_type}:{','.join(tags)} "
        tags_to_keep = [
            tag.replace('"', '""')
            for tag in tags_to_keep
            if not tag.replace('"', '""') in exported_tags[tag_type]
        ]

    tags_to_keep = [f'"{tag}"' for tag in tags_to_keep]
    update_command += f"--field=tags:{','.join(tags_to_keep)}"

    check_output(
        update_command,
        shell=True,
        stderr=STDOUT,
        stdin=PIPE,
    )


if __name__ == "__main__":
    path = PATH
    if len(argv) > 1:
        path = argv[1]

    check_or_create_extra_tag_type_columns(path)
    for tag_type in TAG_TYPES:
        load_tag_type_from_file(tag_type)

    fics = get_all_untransformed_fic_data()
    print(f"Fixing tags for {len(fics)} fics")

    n = 0
    for fic in fics:
        print(f"Fixing tags for fic {fic['id']} {fic['title']}")
        try:
            fix_tags_for_fic(fic["id"], path)
        except Exception as e:
            print(f"Got exception, skipping this fic for now: {e}")
        n += 1
        if n % 100 == 0:
            print(f"PROGRESS: {n}/{len(fics)}, {n * 100 / len(fics)}%")

        break
