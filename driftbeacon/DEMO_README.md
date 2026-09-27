# DriftBeacon local phone demo

The served interface is rebuilt from the untouched Stitch export in `reference/code.html`. It preserves the exported welcome screen, phone frame, Drive/Analysis/Report tabs, and desktop companion panel. `build_stitch.py` applies the agreed content changes and generates `web/index.html`; behaviour lives in `web/stitch-app.js`.

## Run

From `D:\Projects\Synnapse\driftbeacon`:

```powershell
python -B server.py --port 8765
```

Open <http://127.0.0.1:8765>. The Python replay server needs no extra packages. The Stitch visual shell loads Tailwind and Lucide from their public CDNs. To regenerate the HTML after editing the transformation, run `python -B build_stitch.py`.

Run the numerical checks with `python -B -m unittest test_replay.py` and the frontend syntax check with `node --check web/stitch-app.js`.

## What works

- One server-owned sequential replay with independent original and simulated detector states; play, pause, reset, seek, and 1×/2×/4× speed.
- Drive figures and assessment from the active replay response. Missing fuel readings remain unavailable, and the chart preserves gaps.
- Server-calculated time sections integrated from the same full samples used by the detector. Selecting a section opens evidence and focuses the matching chart interval.
- Recorded VED drive with de-identified GPS, moving car, and clickable OpenStreetMap route. The route and fuel values use the same source timestamps. A separate generated fixture remains available for the missing-data preset.
- Driver-owned report with read-only calculated evidence, separately saved draft fields, PUC expiry reminder, preview, print, and user-initiated share.
- Estimated extra tailpipe CO₂ uses [US EPA's 8,887 g per US gallon gasoline factor](https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references), converted to kg/L by the backend. User-entered fuel price changes cost only. The comparable-trip card shows three clearly labeled synthetic examples alongside the current replay's calculated excess percentage.
- Drive groups fuel readings above emissions. NOx and CO are **synthetic examples** (ppm and percent), and extra CO₂ is estimated from fuel. Three illustrative markers, including estimated extra CO₂, trigger a PUC-test suggestion only after the sustained high-drift alert. They are not legal limits or a PUC result.
- The forward-chaining expert-system rules from the latest `main` screen demo diagnostic inputs. Clean has no fault alert, medium screens for fuel-delivery restriction, and high screens for a rich-mixture pattern. These are illustrative screening results, never confirmed component failures.
- The driver-owned report uses a plain inspection summary without replay timestamps or internal trip IDs. It labels synthetic evidence, keeps calculated fields read-only, and retains editable notes, print and share.

## Data and integration limits

The Stitch ZIP supplied HTML and a screenshot but no telemetry. The selected drive is prepared from the official VED first-week dynamic CSV and ICE/HEV metadata. `prepare_ved.py` estimates observed gasoline L/h from MAF and bank-1 fuel trims using the VED paper's E10 air/fuel ratio and a DOE gasoline density. A ridge baseline uses speed, RPM, and load, splits by vehicle, and calibrates its scale on the selected drive's opening four minutes. `data/prepared/ved_trip.json` includes the rows, provenance, coefficients, split counts, and train/validation/test errors. Raw VED downloads are intentionally ignored by Git. Rebuild only when raw data is present: `python -B prepare_ved.py`.

The prepared drive lasts 14:35 because the source trip contains a long initial logging gap; only its longest continuous segment is used. The GPS coordinates are de-identified by VED and should not be treated as a literal personal itinerary. Map tiles and the Stitch shell's icons/styles need internet access. [Official VED source](https://github.com/gsoh/VED).

The target fuel rate is **estimated from MAF**, not a direct fuel sensor reading. The fitted baseline's held-out vehicle error is recorded in the prepared metadata; its detector settings are provisional and not validated for real faults. A synthetic +15% ramp starts at minute 4 in the presentation preset. The clean original and modified channel are assessed independently. Comparable trip history, measured NOx/CO, and PUC test results remain unavailable. The trained model code is present in `main`, but its serialized model and detector artifacts are excluded from Git and have not been supplied locally; this demo continues to use its documented local ridge baseline. A measured CO value and applicable vehicle-specific limit would be needed for a CO threshold screen. CO₂ fuel estimates and NOx concentration are not petrol PUC pass/fail readings; the official [Form 59](https://parivahan.gov.in/sites/default/files/DownloadForm/cmvr/FORM-59.pdf) lists CO and HC for idling emissions.

The separate trained fault classifier and its model weights remain unavailable. EngineFaultDB and VED data are distinct sources. The synthetic expert-system example does not validate a real mechanical fault.
