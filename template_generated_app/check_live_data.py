"""Verify real geocoding, weather retrieval and a small model run; never use fixtures."""
import argparse
from datetime import date
import importlib.util
import json
from pathlib import Path
from data_blocks import DataRequest, load_block
from engine import simulate
from geocoding import search_places, place_label


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--place', default='College Station, Texas')
    parser.add_argument('--start', type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument('--end', type=date.fromisoformat, default=date(2023, 1, 7))
    parser.add_argument('--output', type=Path, help='Optional JSON report file')
    args = parser.parse_args()
    report = {'status':'failed', 'requested_place':args.place, 'start':str(args.start), 'end':str(args.end),
              'verification':'Live provider requests, without synthetic fallback'}
    stage = 'geocoding'
    try:
        places = search_places(args.place)
        if not places:
            raise ValueError('No matching places from the geocoding service')
        place = places[0]
        report['geocoding'] = {'status':'passed','matches':len(places),'selected_place':place_label(place),'id':place['id']}
        stage = 'weather'
        weather = load_block('open_meteo', DataRequest(place['latitude'],place['longitude'],args.start,args.end))
        report['weather'] = {'status':'passed','days':len(weather.frame),'provenance':weather.provenance}
        stage = 'simulation'
        root = Path(__file__).resolve().parent
        config_path = root/'model_config.json'
        models = json.loads(config_path.read_text())['models'] if config_path.is_file() else [
            name for name in ('water_balance','vegetation','soil_carbon') if importlib.util.find_spec('models.'+name)]
        result = simulate({'models':models,'weather_source':'open_meteo'},forcing=weather.frame)
        balances = {key:float(result[key].abs().max()) for key in ('water_residual_mm','carbon_residual_gC_m2') if key in result}
        if any(value > 1e-8 for value in balances.values()):
            raise ValueError('Model conservation check exceeded tolerance')
        report['simulation'] = {'status':'passed','models':models,'days':len(result),'maximum_absolute_residuals':balances}
        report['status'] = 'passed'
    except Exception as exc:
        report['failure_stage'] = stage
        report['error'] = str(exc)
        if getattr(exc, 'diagnostics', None):
            report['weather_diagnostics'] = exc.diagnostics
    text = json.dumps(report,indent=2,default=str)
    if args.output:
        args.output.write_text(text+'\n')
    print(text)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
