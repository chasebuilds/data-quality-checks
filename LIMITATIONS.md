# Limitations

What data-quality-checks does not do, or does poorly. Read this before relying on a green report.

## What a passing report means

- **A pass means no row broke a rule you wrote.** It does not mean the data is right. A wrong value that still looks valid (a quantity of 7 that should be 4, an email with one wrong letter) passes every check. The benchmark plants two such faults on purpose and catches none of them.
- **Checks are only as good as their rules.** The email pattern in the samples accepts `name@mail.example`, so a domain cut short from `mail.example.net` passes. The benchmark counts these misses.
- **No profiling or anomaly detection.** The tool does not learn what normal data looks like, compare runs, or flag a column whose average suddenly moved. Every rule is written by hand.

## Check behavior

- **not_null** treats empty and whitespace-only text as null by default (`blank_is_null: false` turns that off). Placeholder text such as `N/A` or `none` is not treated as null; use `accepted_values`, `regex` or a SQL check for those.
- **unique** ignores rows where any key column is null, so two rows with a null key are not reported as duplicates. Pair it with `not_null`. Every copy of a duplicated key is listed, not only the extra ones.
- **accepted_values** compares text exactly. `Shipped`, `shipped` and `shipped ` are three different values. Values are compared as text, so `1` and `1.0` differ.
- **range** is numeric only. Values that cannot be read as a number are reported as failures. Dates are not supported by `range`; use `freshness` or a SQL check.
- **regex** must match the whole value (DuckDB `regexp_full_match`, RE2 syntax). The pattern is checked with Python's `re` when the file is loaded, so a pattern that Python accepts but RE2 does not (for example look-ahead) fails at run time and is reported as an error for that check.
- **foreign_key** compares keys as text. `42` and `042` do not match, and neither do `P-1` and `p-1`. Composite keys are not supported; use a SQL check.
- **freshness** reads the column as a date or timestamp and ignores values it cannot parse. If no value parses, the check fails. Time zones are ignored. Without `as_of` it measures from the machine's local date, so the same file can pass today and fail next week, which is the point, but it also means saved reports are not reproducible unless `as_of` or `--as-of` is set.
- **row_count** bounds are fixed numbers in the checks file. There is no "within 10% of last run" rule, because the tool keeps no history.
- **sql** checks run whatever query is given against the loaded tables. The query must return the bad rows; a query that returns the good rows will fail on every run. The checks file is trusted input: a SQL check can read any file DuckDB can reach.

## Files

- CSV types and the separator are detected by DuckDB's sniffer from a sample of the file. A column that is numeric for the first rows and text later can be read with the wrong type; a check on it may then report an error. Encodings other than UTF-8 are not handled.
- Excel: one sheet per table (the first, or the one named with `sheet:`). The first row must be the header. Merged cells, title rows above the header and multi-row headers are not detected. Formulas are read as their last saved value. A column that mixes numbers, dates and text is read as text. Legacy `.xls` and password-protected workbooks are not supported.
- Parquet is read as stored, with its own types.
- JSON, databases and remote files (S3, HTTP) are not supported as table sources, though a SQL check can call DuckDB's own readers.

## Reports and scale

- Each failing check keeps a sample of bad rows (5 by default). The full list of bad rows is not written; rerun the SQL shown with `--show-sql` to get all of them.
- Excel files are loaded into memory. CSV and Parquet are scanned by DuckDB; the benchmark measures one run on 1,000,000 orders. Much larger inputs are untested.
- Each check scans its table again. There is no shared scan across checks.
