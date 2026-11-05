"""Metadata for the six server-registered SET-38 schemas.

Loaded during registration; all unlisted tools retain their original schema.
"""
from copy import deepcopy
import json
from pathlib import Path

METADATA = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))


def enrich(name, schema):
    metadata = METADATA.get(name)
    if metadata is None:
        return schema
    enriched = deepcopy(schema)
    enriched["returns"] = deepcopy(metadata["returns"])
    properties = enriched["parameters"]["properties"]
    for parameter, example in metadata["parameter_examples"].items():
        if parameter not in properties:
            raise ValueError(f"server metadata references absent parameter: {name}.{parameter}")
        properties[parameter]["example"] = deepcopy(example)
    return enriched
