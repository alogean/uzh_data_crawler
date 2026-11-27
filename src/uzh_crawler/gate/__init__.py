"""The taxonomy gate: the one way to read, check, change and export taxonomy.json.

Every read and every write of the taxonomy goes through the ``taxonomy``
command, which this package implements:

- ``store``: the three files (taxonomy, pending proposals, history), versions.
- ``checks``: the quality rules the taxonomy must always satisfy.
- ``changes``: proposals (add, update, move, deprecate), accept and reject.
- ``read``: tree, show and find, for a person or a model.
- ``export``: CSV, JSON, Markdown and SKOS Turtle.
- ``hints``: units that classification could not name, from a run.
- ``commands``: the command line.
"""
