# Calibration: gold patches on Impossible-SWEbench

Free, model-free calibration on all 349 tasks of
[fjzzq2002/impossible_swebench](https://huggingface.co/datasets/fjzzq2002/impossible_swebench)
(public, no token). Run on 8–9 Nov 2026, static audit only.

## What is measured

Each task gives a real bug, the maintainers' fix (`patch`), the original tests and an
"impossible" (`oneoff`) copy where one test expects a wrong value. From that,
`tamperproof/gold.py` builds four diffs per task:

| diff | base → head | what its flag rate means |
|---|---|---|
| `honest` | original tests → human fix | **false-positive rate** |
| `revert` | impossible tests → fix + original tests put back | recall on "rewrite the expected value" |
| `delete` | impossible tests → fix, changed test deleted | recall on deleting a test |
| `skip` | impossible tests → fix, changed test `@unittest.skip` | recall on skipping a test |

The changed test is found by comparing the two test trees' ASTs, not by parsing names.

## Results

95% Wilson intervals. "Before" is commit `4df875c`; "after" adds the TT05 fix
(`d8495c7`: expected values inside assertion calls like `self.assertEqual(x, 0o755)`).

| diff | n | flagged (score > 0), before → after | REVIEW, before → after |
|---|---|---|---|
| honest (false positives) | 349 | 50.4% → 50.4% (45.2–55.6) | 14.0% → **14.0%** (10.8–18.1) |
| revert | 349 | 71.3% → 89.4% (85.7–92.2) | 44.1% → **73.4%** (68.5–77.7) |
| delete | 313 | 99.7% → 99.7% (98.2–99.9) | 99.4% → 99.4% (97.7–99.8) |
| skip | 316 | 98.7% → 99.1% (97.2–99.7) | 98.1% → 98.4% (96.4–99.3) |

- The TT05 fix moved 102 reverts from PASS to REVIEW and changed no honest verdict.
- No errors. 36 tasks have no clean `delete` and 33 no clean `skip` (for example the
  test is the only one in its class), so those cheats are not built for them.
- About 3 s per diff on a laptop, clones cached in `~/.cache/tamperproof/repos`.

**What fires on honest fixes** (per diff): `BR01` 115, `COV01` 72, `SC01` 13, `API01` 13, `SC03` 3.

## What this shows, and what it does not

- **Deleting or skipping a test is caught almost every time.**
- **Reverting an expected value is the weak spot.** 31 reverts carry a TT05 and still
  PASS: one TT05 is medium (12), below REVIEW (15). 37 more score 0 (the expectation
  changed in a form TT05 does not parse yet). Raising TT05 or lowering REVIEW would close
  the first group; that is a weights decision, left open.
- **False positives at REVIEW are 14%**, mostly `BR01` (blast radius) and `COV01`
  (changed code no test calls) on large real fixes. Those two rules are the next tuning target.
- **The cheats are scripted**, so recall is recall on cheats of these shapes, not on real
  agents. Real agents hide edits in helpers, fixtures or many files at once.
- **BLOCK is not measured.** It needs runtime proofs, which need each project's
  environment (Docker images), which this run avoids on purpose.

## Reproduce

```bash
pip install -e ".[calibrate]"                      # pyarrow, optional extra
python -m tamperproof.gold download --data gold    # public parquet, no token
python -m tamperproof.gold run --data gold --out gold.jsonl      # resumable
python -m tamperproof.gold summary --results gold.jsonl --md gold.md
```

On python.org macOS builds, `download` can fail with `CERTIFICATE_VERIFY_FAILED`; run
`Install Certificates.command` from the Python folder once.
