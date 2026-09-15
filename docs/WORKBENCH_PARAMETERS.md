# Workbench parameters (#788 W1)

`makerbench/cad_params.py` finds the named parameters of a CAD source and
writes new values back, without running OpenSCAD or Python. The Studio
workbench's Parameters tab is built on it; the module is usable on its own.

```python
from makerbench.cad_params import extract_parameters, apply_parameters

model = extract_parameters(source, "openscad")      # or "cadquery"
for p in model.parameters:
    print(p.name, p.kind, p.value, p.unit, p.range, p.notes)
new_source = apply_parameters(source, {"nut_width_in": 1.5}, "openscad")
```

## What counts as a parameter

- **OpenSCAD:** a top-level `name = value;` assignment at brace depth 0,
  outside `module` and `function` bodies (the Customizer convention the
  instrument masters already use). `$fn`-style specials are listed under
  state `special`.
- **CadQuery / build123d:** a module-level `NAME = <literal>` assignment (or
  `NAME: type = <literal>`), found with `ast.parse`. Dunder names are skipped.

A parameter is **editable** when its value is a literal: a number, `true`/
`false`, a quoted string, or a flat vector of numbers. Anything else
(`scale_lengths_in[variant]`, `fret_from_nut(14)`, `DIAMETER_MM / 2`) is
**derived**: shown read-only with its source text. A name assigned more than
once is **reassigned** and never edited (OpenSCAD keeps the last one and warns).

## Metadata comes only from the file

| Field | Source | When absent |
|---|---|---|
| group | nearest preceding `/* [Group] */` (`# [Group]`) | `None` |
| doc | whole-line comment directly above, else the trailing comment's text | `None` |
| range | trailing `// [min:max]` or `// [min:step:max]` | `None`, note "range not declared" |
| options | trailing `// [a, b, c]` | `None` |
| unit | name suffix `_mm _cm _in _deg _hz _count _n`, or `unit: xx` in the trailing comment | `None`, note "unit unknown" |

Nothing is guessed. As of 2026-09-15 none of the 134 instrument masters
declares a Customizer range, so the UI shows number fields, not sliders,
unless a master gains a `// [min:max]` comment.

## Applying values

`apply_parameters` validates each value against its parameter (kind, finite,
range, options, vector length) and rewrites only that literal's character
span. Comments and formatting survive. A value equal to the current one leaves
its bytes alone, so an apply with no changes is byte-identical; the sweep in
`tests/test_cad_params.py` (`MAKERBENCH_INSTRUMENTS_ROOT=...`) checks that on
every master. Errors raise `ParameterError` and change nothing.

## Limitations, stated in `model.limitations`

- Parameters inside `include`/`use` files are not scanned.
- Nested or mixed vectors are derived.
- More than 500 parameters are truncated.
- A CadQuery script that does not parse reports the syntax error and no
  parameters.
