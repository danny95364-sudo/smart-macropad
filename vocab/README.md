# vocab

## tsl.csv

Source: TOEIC Service List (TSL) 1.2, Browne & Culligan, newgeneralservicelist.com, CC BY-SA 4.0
(https://creativecommons.org/licenses/by-sa/4.0/).

- Headwords and `rank` come from TSL 1.2 (`TSL_12_stats.csv`, 1,250 headwords). Accented headwords are folded to ASCII in `word` (résumé → resume, café → cafe, entrée → entree); `id` keeps the original TSL headword in lowercase, so `résumé` (noun) and `resume` (verb) stay separate.
- `pos`, `zh`, `example`, `topic` are original additions, shared under the same CC BY-SA 4.0 licence.
- The file starts with `#` comment lines (source/licence); skip lines starting with `#` before parsing the CSV.
