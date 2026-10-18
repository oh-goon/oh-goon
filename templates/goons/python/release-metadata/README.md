# python/release-metadata

Run the repository's `release-metadata` command to derive explicit release values from project metadata plus caller-supplied channel and revision.

The semantic step id remains because later outputs refer to it. The versioning program itself is project logic and no longer lives as an embedded `python -c` program in YAML.
